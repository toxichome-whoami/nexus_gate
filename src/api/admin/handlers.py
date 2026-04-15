"""
Admin API: Restricted endpoints for managing API keys, bans,
rate limit overrides, circuit breaker state, and live config viewing.
Only accessible with a key that has admin-level privileges.
"""
from fastapi import APIRouter, Depends, Request, Path, Body
from typing import Optional
import secrets
import string
import hashlib
import base64

from server.middleware.auth import require_admin
from api.responses import success_response
from api.errors import NexusGateException, ErrorCodes
from config.loader import ConfigManager
from security.ban_list import BanList
from security.circuit_breaker import CircuitBreaker
from security.storage import SecurityStorage
from db.pool import DatabasePoolManager

router = APIRouter(tags=["admin"])

# ─────────────────────────────────────────────────────────────────────────────
# Core Utility Functions
# ─────────────────────────────────────────────────────────────────────────────

def _require_fields(mapping: dict, *fields: str) -> None:
    """Validates that all specified fields are present and not empty in a mapping."""
    for field in fields:
        if not mapping.get(field):
            raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, f"'{field}' is required", 400)

def _generate_secure_secret() -> tuple[str, str]:
    """
    Generates a cryptographically secure random secret and its SHA-256 hash.
    Returns: (raw_secret, hex_hash)
    """
    alphabet = string.ascii_letters + string.digits
    length = secrets.choice(range(32, 65))
    raw_secret = "".join(secrets.choice(alphabet) for _ in range(length))
    secret_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()
    return raw_secret, secret_hash

def _enrich_federated_databases(local_dbs: dict, config) -> None:
    """Appends federated remote alias databases to the local response payload."""
    if not config.features.federation or not config.federation.enabled:
        return

    from api.federation.sync import FederationState
    state = FederationState()
    
    for alias, srv_state in state.servers.items():
        if srv_state.get("status") != "up":
            continue
            
        for db_name, db_info in srv_state.get("databases", {}).items():
            fed_name = f"{alias}_{db_name}"
            
            # Use isinstance checks cleanly
            info_dict = db_info if isinstance(db_info, dict) else {}
            
            local_dbs[fed_name] = {
                "engine": info_dict.get("engine", "unknown"),
                "mode": info_dict.get("mode", "unknown"),
                "status": info_dict.get("status", db_info) if not isinstance(db_info, dict) else info_dict.get("status", "unknown"),
                "tables_count": info_dict.get("tables_count", 0),
                "remote_server": alias,
                "url": "***FEDERATED***",
                "federated": True,
            }

# ─────────────────────────────────────────────────────────────────────────────
# API Key Management
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/keys")
async def list_api_keys(request: Request, auth=Depends(require_admin)):
    """Lists static (TOML) and dynamic (SQLite) API Keys without showing secrets."""
    config = ConfigManager.get()
    keys = []

    # 1. Static keys from config
    for name, key_cfg in config.api_key.items():
        keys.append({
            "name": name,
            "source": "config.toml",
            "mode": key_cfg.mode.value,
            "db_scope": key_cfg.db_scope,
            "fs_scope": key_cfg.fs_scope,
            "rate_limit_override": key_cfg.rate_limit_override,
            "full_admin": key_cfg.full_admin
        })

    # 2. Dynamic keys from DB
    db_keys = SecurityStorage.get_all_keys()
    for name, key_data in db_keys.items():
        keys.append({
            "name": name,
            "source": "sqlite",
            "mode": key_data["mode"],
            "db_scope": key_data["db_scope"],
            "fs_scope": key_data["fs_scope"],
            "rate_limit_override": key_data["rate_limit_override"],
            "full_admin": False
        })

    return success_response(request, {"keys": keys})


@router.post("/keys")
async def create_api_key(
    request: Request,
    body: dict = Body(...),
    auth=Depends(require_admin),
):
    """Creates a new dynamic secure API key."""
    _require_fields(body, "name")
    
    name = body.get("name")
    if SecurityStorage.get_api_key(name):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, "Key name already exists", 400)

    raw_secret, secret_hash = _generate_secure_secret()

    mode = body.get("mode", "readwrite")
    db_scope = body.get("db_scope", ["*"])
    fs_scope = body.get("fs_scope", ["*"])
    rate_limit = body.get("rate_limit_override", 0)

    await SecurityStorage.add_api_key(
        name=name,
        secret_hash=secret_hash,
        mode=mode,
        db_scope=db_scope,
        fs_scope=fs_scope,
        rate_limit=rate_limit
    )

    bearer = base64.b64encode(f"{name}:{raw_secret}".encode()).decode()

    return success_response(request, {
        "name": name,
        "mode": mode,
        "db_scope": db_scope,
        "fs_scope": fs_scope,
        "rate_limit_override": rate_limit,
        "secret": raw_secret,
        "bearer_token": bearer,
        "note": "Store this token now. The raw secret is not retrievable."
    })


@router.delete("/keys/{key_name}")
async def revoke_api_key(
    request: Request,
    key_name: str = Path(...),
    auth=Depends(require_admin),
):
    """Deletes dynamic keys or applies an infinite ban array to static overrides."""
    if key_name == auth.api_key_name:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, "You cannot revoke your active API key", 400)

    deleted_from_db = await SecurityStorage.delete_api_key(key_name)
    
    banned = False
    if key_name in ConfigManager.get().api_key:
        await BanList.ban_key(key_name, reason="Revoked via admin API", duration_seconds=None)
        banned = True

    if not deleted_from_db and not banned:
        raise NexusGateException(ErrorCodes.AUTH_INVALID_KEY, f"Key '{key_name}' not found", 404)

    msg = "Revoked from SQLite database." if deleted_from_db else "Banned static key (remove from config.toml to persist)."
    return success_response(request, {"revoked": key_name, "note": msg})


@router.patch("/keys/actions")
async def update_api_key(
    request: Request,
    body: dict = Body(...),
    auth=Depends(require_admin),
):
    """Updates non-secret field properties on dynamic API keys."""
    _require_fields(body, "name")
    name = body.get("name")

    # Reject static updates
    if name in ConfigManager.get().api_key:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Static key '{name}' cannot be modified", 400)

    updates = {k: v for k, v in body.items() if k in ["mode", "db_scope", "fs_scope", "rate_limit_override"]}
    if not updates:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "No updatable fields provided", 400)

    if not await SecurityStorage.update_api_key(name, updates):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Dynamic key '{name}' not found", 404)

    return success_response(request, {"updated_key": name, "changes": updates})

# ─────────────────────────────────────────────────────────────────────────────
# Ban Management
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/bans")
async def list_bans(request: Request, auth=Depends(require_admin)):
    return success_response(request, BanList.list_bans())

@router.post("/bans/ip")
async def ban_ip(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    _require_fields(body, "ip")
    ip = body.get("ip")
    reason = body.get("reason", "Manual ban via admin API")
    duration = body.get("duration_seconds")

    await BanList.ban_ip(ip, reason=reason, duration_seconds=duration)
    return success_response(request, {"banned_ip": ip, "reason": reason, "duration_seconds": duration})

@router.delete("/bans/ip/{ip}")
async def unban_ip(request: Request, ip: str = Path(...), auth=Depends(require_admin)):
    if not await BanList.unban_ip(ip):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"IP '{ip}' is not banned", 404)
    return success_response(request, {"unbanned_ip": ip})

@router.post("/bans/key")
async def ban_key(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    _require_fields(body, "key_name")
    key_name = body.get("key_name")
    
    if key_name == auth.api_key_name:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, "Self-lockout protection: cannot ban active key", 400)

    reason = body.get("reason", "Manual ban via admin API")
    duration = body.get("duration_seconds")

    await BanList.ban_key(key_name, reason=reason, duration_seconds=duration)
    return success_response(request, {"banned_key": key_name, "reason": reason})

@router.delete("/bans/key/{key_name}")
async def unban_key(request: Request, key_name: str = Path(...), auth=Depends(require_admin)):
    if not await BanList.unban_key(key_name):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Key '{key_name}' is not banned", 404)
    return success_response(request, {"unbanned_key": key_name})

# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker Operations
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/circuit-breakers")
async def list_circuit_breakers(request: Request, auth=Depends(require_admin)):
    return success_response(request, {"circuits": CircuitBreaker.all_states()})

@router.post("/circuit-breakers/{key}/reset")
async def reset_circuit_breaker(request: Request, key: str = Path(...), auth=Depends(require_admin)):
    await CircuitBreaker.reset(key)
    return success_response(request, {"reset": key, "state": "closed"})

# ─────────────────────────────────────────────────────────────────────────────
# Service Extension (Databases and Webhooks)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/databases")
async def view_databases(request: Request, auth=Depends(require_admin)):
    """Safe view of active databases masking connection URLs."""
    config = ConfigManager.get()
    dbs = {}
    
    for name, db_cfg in config.database.items():
        dbs[name] = {
            "engine": db_cfg.engine.value,
            "mode": db_cfg.mode.value,
            "pool_min": db_cfg.pool_min,
            "pool_max": db_cfg.pool_max,
            "url": "***REDACTED***",
            "federated": False,
        }

    _enrich_federated_databases(dbs, config)
    return success_response(request, {"databases": dbs})

@router.post("/databases")
async def create_database(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    _require_fields(body, "name")
    name = body.get("name")
    
    await SecurityStorage.add_database(name, body)
    await DatabasePoolManager.remove_engine(name) # Hot-reload hook

    return success_response(request, {"created_database": name, "note": "Database dynamically attached."})

@router.delete("/databases/{name}")
async def delete_database(request: Request, name: str = Path(...), auth=Depends(require_admin)):
    if not await SecurityStorage.delete_database(name):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Dynamic Database '{name}' not found", 404)

    await DatabasePoolManager.remove_engine(name)
    return success_response(request, {"deleted_database": name})

@router.patch("/databases/actions")
async def update_database(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    """Updates configurable database pools without restarting."""
    _require_fields(body, "name")
    name = body.get("name")
    
    # Reject blocked configurations dynamically
    if any(blocked in body for blocked in ["query_whitelist", "query_blacklist"]):
         raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, "'query_whitelist/blacklist' are config.toml only.", 400)

    allowed = {"engine", "url", "mode", "pool_min", "pool_max", "connection_timeout", "idle_timeout", "max_lifetime", "dangerous_operations"}
    updates = {k: v for k, v in body.items() if k in allowed}
    
    if not updates:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "No updatable fields provided", 400)

    if not await SecurityStorage.update_database(name, updates):
         raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Dynamic database '{name}' not found", 404)

    await DatabasePoolManager.remove_engine(name)
    return success_response(request, {"updated_database": name, "changes": updates})

@router.get("/webhooks")
async def view_webhooks(request: Request, auth=Depends(require_admin)):
    """Safe view array of defined system webhooks."""
    wh = {
        name: {"url": hook.url, "rule": hook.rule, "enabled": hook.enabled, "secret": "***REDACTED***"}
        for name, hook in ConfigManager.get().webhook.items()
    }
    return success_response(request, {"webhooks": wh})

@router.post("/webhooks")
async def create_webhook(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    _require_fields(body, "name", "url", "rule")
    
    name = body.get("name")
    url = body.get("url")
    rule = body.get("rule")
    enabled = body.get("enabled", True)

    raw_secret, _ = _generate_secure_secret()

    await SecurityStorage.add_webhook(name, {
        "url": url,
        "secret": raw_secret,
        "rule": rule,
        "enabled": enabled
    })

    return success_response(request, {
        "name": name,
        "url": url,
        "rule": rule,
        "enabled": enabled,
        "secret": raw_secret,
    })

@router.delete("/webhooks/{name}")
async def delete_webhook(request: Request, name: str = Path(...), auth=Depends(require_admin)):
    if not await SecurityStorage.delete_webhook(name):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Dynamic Webhook '{name}' not found", 404)
    return success_response(request, {"deleted_webhook": name})

@router.patch("/webhooks/actions")
async def update_webhook(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    _require_fields(body, "name")
    name = body.get("name")

    updates = {k: v for k, v in body.items() if k in ["url", "rule", "enabled"]}
    if not updates:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "No updatable fields provided", 400)

    if not await SecurityStorage.update_webhook(name, updates):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Dynamic hook '{name}' not found", 404)

    return success_response(request, {"updated_webhook": name, "changes": updates})

# ─────────────────────────────────────────────────────────────────────────────
# Introspection Views
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/config")
async def view_config(request: Request, auth=Depends(require_admin)):
    """Yields live application config state excluding system secrets."""
    data = ConfigManager.get().model_dump()

    for key_name in data.get("api_key", {}):
        data["api_key"][key_name]["secret"] = "***REDACTED***"
    for wh_name in data.get("webhook", {}):
        data["webhook"][wh_name]["secret"] = "***REDACTED***"
    for db_name in data.get("database", {}):
        data["database"][db_name]["url"] = "***REDACTED***"
    for srv_name in data.get("federation", {}).get("server", {}):
        data["federation"]["server"][srv_name]["api_key"] = "***REDACTED***"

    return success_response(request, {"config": data})

@router.get("/rate-limits")
async def view_rate_limits(request: Request, auth=Depends(require_admin)):
    """Exports global configuration along with any explicit key overrides."""
    config = ConfigManager.get()
    overrides = {}

    for name, cfg in config.api_key.items():
        if cfg.rate_limit_override > 0:
            overrides[name] = {"rate_limit_override": cfg.rate_limit_override, "source": "config"}

    for name, cfg in SecurityStorage.get_all_keys().items():
        if cfg["rate_limit_override"] > 0:
            overrides[name] = {"rate_limit_override": cfg["rate_limit_override"], "source": "sqlite"}

    return success_response(request, {
        "global": {
            "window": config.rate_limit.window,
            "max_requests": config.rate_limit.max_requests,
            "burst": config.rate_limit.burst,
        },
        "per_key_overrides": overrides,
    })
