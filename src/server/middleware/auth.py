import base64
import hmac
from fastapi import Request, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config.loader import ConfigManager
from utils.types import AuthContext, ServerMode
from api.errors import NexusGateException, ErrorCodes

security = HTTPBearer(auto_error=False)

async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> AuthContext:
    """Validate API key from base64 encoded Bearer token, or federation secret."""
    from security.ban_list import BanList
    from security.storage import SecurityStorage
    import hashlib

    config = ConfigManager.get()

    # ── Federation Secret Auth (separate path) ──────────────────
    fed_secret = request.headers.get("X-Federation-Secret")
    fed_node = request.headers.get("X-Federation-Node")

    if fed_secret and fed_node:
        # Only allow if federation is enabled
        if not config.features.federation or not config.federation.enabled:
            raise NexusGateException(
                code=ErrorCodes.AUTH_INVALID_KEY,
                message="Federation is disabled on this instance.",
                status_code=403,
            )

        incoming_key = config.federation.incoming.get(fed_node)
        if not incoming_key:
            raise NexusGateException(
                code=ErrorCodes.AUTH_INVALID_KEY,
                message="Unknown federation node.",
                status_code=403,
            )

        # Constant-time comparison to prevent timing attacks
        # Secret is Base64-encoded in transit, decode before comparing
        try:
            decoded_fed_secret = base64.b64decode(fed_secret).decode("utf-8")
        except Exception:
            raise NexusGateException(
                code=ErrorCodes.AUTH_INVALID_FORMAT,
                message="Malformed federation secret.",
                status_code=403,
            )

        if not hmac.compare_digest(
            incoming_key.secret.encode("utf-8"),
            decoded_fed_secret.encode("utf-8"),
        ):
            raise NexusGateException(
                code=ErrorCodes.AUTH_INVALID_SECRET,
                message="Invalid federation secret.",
                status_code=403,
            )

        # Return scoped AuthContext based on the incoming key's permissions
        return AuthContext(
            api_key_name=f"federation:{fed_node}",
            mode=incoming_key.mode,
            db_scope=incoming_key.db_scope,
            fs_scope=incoming_key.fs_scope,
            rate_limit_override=0,
            full_admin=False,  # Federation nodes are NEVER admin
        )

    # ── Standard Bearer Token Auth ──────────────────────────────
    if not credentials:
        raise NexusGateException(
            code=ErrorCodes.AUTH_INVALID_FORMAT,
            message="Missing authentication. Provide Authorization: Bearer <token> or federation headers.",
            status_code=401,
        )

    encoded_token = credentials.credentials
    try:
        decoded_token = base64.b64decode(encoded_token).decode("utf-8")
        key_name, secret = decoded_token.split(":", 1)
    except Exception:
        raise NexusGateException(
            code=ErrorCodes.AUTH_INVALID_FORMAT,
            message="Invalid Authorization header format. Expected Base64(key_name:secret)",
            status_code=401,
        )

    # Ban-list check — reject banned keys immediately
    is_banned, ban_reason = BanList.is_key_banned(key_name)
    if is_banned:
        raise NexusGateException(
            code=ErrorCodes.AUTH_INVALID_KEY,
            message=f"API key is suspended: {ban_reason}",
            status_code=403,
        )

    # IP ban check
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

    # 1. Check Dynamic Keys (SQLite DB via Cache)
    db_key = SecurityStorage.get_api_key(key_name)
    if db_key:
        provided_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(db_key["secret_hash"].encode("utf-8"), provided_hash.encode("utf-8")):
            try:
                from api.core.metrics import increment
                increment("auth_failures")
            except Exception:
                pass
            raise NexusGateException(code=ErrorCodes.AUTH_INVALID_SECRET, message="Invalid credentials.", status_code=401)
            
        return AuthContext(
            api_key_name=key_name,
            mode=ServerMode(db_key["mode"]),
            db_scope=db_key["db_scope"],
            fs_scope=db_key["fs_scope"],
            rate_limit_override=db_key["rate_limit_override"],
            full_admin=False,
        )

    # 2. Check Static Keys (config.toml)
    api_key_cfg = config.api_key.get(key_name)
    if not api_key_cfg:
        raise NexusGateException(
            code=ErrorCodes.AUTH_INVALID_KEY,
            message="The provided API key is invalid or expired.",
            status_code=401,
        )

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(api_key_cfg.secret.encode("utf-8"), secret.encode("utf-8")):
        try:
            from api.core.metrics import increment
            increment("auth_failures")
        except Exception:
            pass
        raise NexusGateException(
            code=ErrorCodes.AUTH_INVALID_SECRET,
            message="Invalid credentials.",
            status_code=401,
        )

    return AuthContext(
        api_key_name=key_name,
        mode=api_key_cfg.mode,
        db_scope=api_key_cfg.db_scope,
        fs_scope=api_key_cfg.fs_scope,
        rate_limit_override=api_key_cfg.rate_limit_override,
        full_admin=api_key_cfg.full_admin,
    )


async def require_admin(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Restrict endpoint to keys with full_admin=true flag."""
    if not auth.full_admin:
        raise NexusGateException(
            code=ErrorCodes.AUTH_INSUFFICIENT_MODE,
            message="Admin-level API key required for this action.",
            status_code=403,
        )
    return auth
