import base64
import hmac
import hashlib
from fastapi import Request, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config.loader import ConfigManager
from utils.types import AuthContext, ServerMode
from api.errors import NexusGateException, ErrorCodes
from security.ban_list import BanList
from security.storage import SecurityStorage

security = HTTPBearer(auto_error=False)

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _record_auth_failure():
    """Silently records metric counters for unauthorized boundary events."""
    try:
        from api.core.metrics import increment
        increment("auth_failures")
    except Exception:
        pass

def _evaluate_network_bans(request: Request, key_name: str):
    """Executes pre-authorization checks against network blocks and IP bans."""
    is_banned, ban_reason = BanList.is_key_banned(key_name)
    if is_banned:
        raise NexusGateException(
            code=ErrorCodes.AUTH_INVALID_KEY,
            message=f"API key is suspended: {ban_reason}",
            status_code=403,
        )

    # Resolve active client IP safely bypassing proxies
    client_ip = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")
    if isinstance(client_ip, str) and "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    ip_banned, ip_reason = BanList.is_ip_banned(client_ip)
    if ip_banned:
        raise NexusGateException(
            code=ErrorCodes.RATE_LIMIT_BLOCKED,
            message=f"IP address is banned: {ip_reason}",
            status_code=403,
        )

def _get_federation_context(request: Request, config) -> AuthContext:
    """Verifies internal server-to-server TLS connections via X- headers."""
    fed_secret = request.headers.get("X-Federation-Secret")
    fed_node = request.headers.get("X-Federation-Node")

    # Federation globally disabled
    if not config.features.federation or not config.federation.enabled:
        raise NexusGateException(ErrorCodes.AUTH_INVALID_KEY, "Federation is disabled on this instance.", 403)

    incoming_key = config.federation.incoming.get(fed_node)
    if not incoming_key:
        raise NexusGateException(ErrorCodes.AUTH_INVALID_KEY, "Unknown federation node.", 403)

    try:
        decoded_fed_secret = base64.b64decode(fed_secret).decode("utf-8")
    except Exception:
        raise NexusGateException(ErrorCodes.AUTH_INVALID_FORMAT, "Malformed federation secret.", 403)

    # Secure constant-time comparison
    if not hmac.compare_digest(incoming_key.secret.encode("utf-8"), decoded_fed_secret.encode("utf-8")):
        raise NexusGateException(ErrorCodes.AUTH_INVALID_SECRET, "Invalid federation secret.", 403)

    return AuthContext(
        api_key_name=f"federation:{fed_node}",
        mode=incoming_key.mode,
        db_scope=incoming_key.db_scope,
        fs_scope=incoming_key.fs_scope,
        rate_limit_override=0,
        full_admin=False,  # Nodes are inherently isolated from administrative capacities
    )

def _get_dynamic_key_context(key_name: str, secret: str) -> AuthContext | None:
    """Authenticates the request against the fast-path SQLite security registry."""
    db_key = SecurityStorage.get_api_key(key_name)
    if not db_key:
        return None

    provided_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(db_key["secret_hash"].encode("utf-8"), provided_hash.encode("utf-8")):
        _record_auth_failure()
        raise NexusGateException(ErrorCodes.AUTH_INVALID_SECRET, "Invalid credentials.", 401)
        
    return AuthContext(
        api_key_name=key_name,
        mode=ServerMode(db_key["mode"]),
        db_scope=db_key["db_scope"],
        fs_scope=db_key["fs_scope"],
        rate_limit_override=db_key["rate_limit_override"],
        full_admin=False,
    )

def _get_static_key_context(key_name: str, secret: str, config) -> AuthContext:
    """Authenticates the request against statically injected config.toml keys."""
    api_key_cfg = config.api_key.get(key_name)
    if not api_key_cfg:
        raise NexusGateException(
            ErrorCodes.AUTH_INVALID_KEY, "The provided API key is invalid or expired.", 401
        )

    if not hmac.compare_digest(api_key_cfg.secret.encode("utf-8"), secret.encode("utf-8")):
        _record_auth_failure()
        raise NexusGateException(ErrorCodes.AUTH_INVALID_SECRET, "Invalid credentials.", 401)

    return AuthContext(
        api_key_name=key_name,
        mode=api_key_cfg.mode,
        db_scope=api_key_cfg.db_scope,
        fs_scope=api_key_cfg.fs_scope,
        rate_limit_override=api_key_cfg.rate_limit_override,
        full_admin=api_key_cfg.full_admin,
    )

def _parse_bearer_token(credentials: HTTPAuthorizationCredentials) -> tuple[str, str]:
    """Decodes the HTTP Basic/Bearer formatted token strings."""
    if not credentials:
        raise NexusGateException(ErrorCodes.AUTH_INVALID_FORMAT, "Missing authentication.", 401)

    try:
        decoded_token = base64.b64decode(credentials.credentials).decode("utf-8")
        key_name, secret = decoded_token.split(":", 1)
        return key_name, secret
    except Exception:
        raise NexusGateException(
            ErrorCodes.AUTH_INVALID_FORMAT, "Invalid Authorization header format. Expected Base64(key_name:secret)", 401
        )

# ─────────────────────────────────────────────────────────────────────────────
# Primary Dependency Injections
# ─────────────────────────────────────────────────────────────────────────────

async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> AuthContext:
    """Resolves security context for a request by combining all available mechanisms."""
    config = ConfigManager.get()

    # 1. Routing Edge Case: Handle native federation node bypasses
    if request.headers.get("X-Federation-Secret") and request.headers.get("X-Federation-Node"):
        return _get_federation_context(request, config)

    # 2. Extract standard API tokens
    key_name, secret = _parse_bearer_token(credentials)

    # 3. Apply IP/Key global firewall blocklists
    _evaluate_network_bans(request, key_name)

    # 4. Resolve Context
    dynamic_context = _get_dynamic_key_context(key_name, secret)
    if dynamic_context:
        return dynamic_context

    # 5. Fallback statically
    return _get_static_key_context(key_name, secret, config)

async def require_admin(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Dependency extension specifically blocking non-administrative requests."""
    if not auth.full_admin:
        raise NexusGateException(
            ErrorCodes.AUTH_INSUFFICIENT_MODE, "Admin-level API key required for this action.", 403
        )
    return auth
