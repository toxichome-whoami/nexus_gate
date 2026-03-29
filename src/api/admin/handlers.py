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
from db.pool import DatabasePoolManager

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ─── API Key Management ───────────────────────────────────────────────────────

@router.get("/keys")
async def list_api_keys(request: Request, auth=Depends(require_admin)):
    config = ConfigManager.get()
    keys = []

    # 1. Static keys
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

    # 2. Dynamic DB keys
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
    name = body.get("name")
    mode = body.get("mode", "readwrite")
    db_scope = body.get("db_scope", ["*"])
    fs_scope = body.get("fs_scope", ["*"])
    rate_limit = body.get("rate_limit_override", 0)
    full_admin = False

    if not name:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Key name is required", 400)

    if SecurityStorage.get_api_key(name):
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, "Key name already exists", 400)

    # Generate a cryptographically secure key (32-64 chars A-Z, a-z, 0-9)
    alphabet = string.ascii_letters + string.digits
    length = secrets.choice(range(32, 65))
    raw_secret = ''.join(secrets.choice(alphabet) for _ in range(length))

    # Hash secret for DB storage
    secret_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()

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
    if key_name == auth.api_key_name:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, "You cannot revoke your own active API key", 400)

    config = ConfigManager.get()

    # DB Keys
    deleted_from_db = await SecurityStorage.delete_api_key(key_name)

    # If it's a static key, we can't delete it from file automatically, so we ban it
    banned = False
    if key_name in config.api_key:
        await BanList.ban_key(key_name, reason="Revoked via admin API", duration_seconds=None)
        banned = True

    if not deleted_from_db and not banned:
        raise NexusGateException(ErrorCodes.AUTH_INVALID_KEY, f"Key '{key_name}' not found", 404)

    msg = "Revoked from SQLite database." if deleted_from_db else "Banned static key (remove from config.toml to persist)."
    return success_response(request, {"revoked": key_name, "note": msg})


# ─── Ban Management ───────────────────────────────────────────────────────────

@router.get("/bans")
async def list_bans(request: Request, auth=Depends(require_admin)):
    return success_response(request, BanList.list_bans())


@router.post("/bans/ip")
async def ban_ip(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    ip = body.get("ip")
    reason = body.get("reason", "Manual ban via admin API")
    duration = body.get("duration_seconds")  # None = permanent

    if not ip:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "IP address is required", 400)

    await BanList.ban_ip(ip, reason=reason, duration_seconds=duration)
    return success_response(request, {"banned_ip": ip, "reason": reason, "duration_seconds": duration})


@router.delete("/bans/ip/{ip}")
async def unban_ip(request: Request, ip: str = Path(...), auth=Depends(require_admin)):
    removed = await BanList.unban_ip(ip)
    if not removed:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"IP '{ip}' is not banned", 404)
    return success_response(request, {"unbanned_ip": ip})


@router.post("/bans/key")
async def ban_key(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    key_name = body.get("key_name")
    reason = body.get("reason", "Manual ban via admin API")
    duration = body.get("duration_seconds")

    if not key_name:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "key_name is required", 400)

    if key_name == auth.api_key_name:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, "You cannot ban your own active API key (self-lockout protection)", 400)

    await BanList.ban_key(key_name, reason=reason, duration_seconds=duration)
    return success_response(request, {"banned_key": key_name, "reason": reason})


@router.delete("/bans/key/{key_name}")
async def unban_key(request: Request, key_name: str = Path(...), auth=Depends(require_admin)):
    removed = await BanList.unban_key(key_name)
    if not removed:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Key '{key_name}' is not banned", 404)
    return success_response(request, {"unbanned_key": key_name})


# ─── Circuit Breaker ──────────────────────────────────────────────────────────

@router.get("/circuit-breakers")
async def list_circuit_breakers(request: Request, auth=Depends(require_admin)):
    return success_response(request, {"circuits": CircuitBreaker.all_states()})


@router.post("/circuit-breakers/{key}/reset")
async def reset_circuit_breaker(
    request: Request,
    key: str = Path(...),
    auth=Depends(require_admin),
):
    await CircuitBreaker.reset(key)
    return success_response(request, {"reset": key, "state": "closed"})


# ─── Ext Management view (Databases, Webhooks) ──────────────────────────────────────────────────

@router.get("/databases")
async def view_databases(request: Request, auth=Depends(require_admin)):
    """Safe view of configured databases, omitting URLs"""
    config = ConfigManager.get()
    dbs = {}
    for name, db_cfg in config.database.items():
        dbs[name] = {
            "engine": db_cfg.engine.value,
            "mode": db_cfg.mode.value,
            "pool_min": db_cfg.pool_min,
            "pool_max": db_cfg.pool_max,
            "url": "***REDACTED***"
        }
    return success_response(request, {"databases": dbs})

@router.post("/databases")
async def create_database(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    name = body.get("name")
    if not name:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Database name requires", 400)

    await SecurityStorage.add_database(name, body)

    # If it was already loaded, hot-reload the pool
    await DatabasePoolManager.remove_engine(name)

    return success_response(request, {"created_database": name, "note": "Database added dynamically to SQLite backend."})

@router.delete("/databases/{name}")
async def delete_database(request: Request, name: str = Path(...), auth=Depends(require_admin)):
    removed = await SecurityStorage.delete_database(name)
    if not removed:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Dynamic Database '{name}' not found", 404)

    await DatabasePoolManager.remove_engine(name)

    return success_response(request, {"deleted_database": name})

@router.get("/webhooks")
async def view_webhooks(request: Request, auth=Depends(require_admin)):
    """Safe view of configured webhooks, omitting secrets"""
    config = ConfigManager.get()
    wh = {}
    for name, hook in config.webhook.items():
        wh[name] = {
            "url": hook.url,
            "rule": hook.rule,
            "enabled": hook.enabled,
            "secret": "***REDACTED***"
        }
    return success_response(request, {"webhooks": wh})

@router.post("/webhooks")
async def create_webhook(request: Request, body: dict = Body(...), auth=Depends(require_admin)):
    name = body.get("name")
    if not name:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Webhook name required", 400)

    await SecurityStorage.add_dynamic_webhook(name, body)
    return success_response(request, {"created_webhook": name})

@router.delete("/webhooks/{name}")
async def delete_webhook(request: Request, name: str = Path(...), auth=Depends(require_admin)):
    removed = await SecurityStorage.delete_dynamic_webhook(name)
    if not removed:
        raise NexusGateException(ErrorCodes.INPUT_VALUE_INVALID, f"Dynamic Webhook '{name}' not found", 404)

    return success_response(request, {"deleted_webhook": name})


# ─── Live Config View ─────────────────────────────────────────────────────────

@router.get("/config")
async def view_config(request: Request, auth=Depends(require_admin)):
    config = ConfigManager.get()
    data = config.model_dump()

    # Mask secrets
    for key_name in data.get("api_key", {}):
        data["api_key"][key_name]["secret"] = "***REDACTED***"
    for wh_name in data.get("webhook", {}):
        data["webhook"][wh_name]["secret"] = "***REDACTED***"
    for db_name in data.get("database", {}):
        data["database"][db_name]["url"] = "***REDACTED***"
    for srv_name in data.get("federation", {}).get("server", {}):
        data["federation"]["server"][srv_name]["api_key"] = "***REDACTED***"

    return success_response(request, {"config": data})


# ─── Rate Limit Overrides ─────────────────────────────────────────────────────

@router.get("/rate-limits")
async def view_rate_limits(request: Request, auth=Depends(require_admin)):
    config = ConfigManager.get()
    overrides = {}

    # Static keys
    for name, cfg in config.api_key.items():
        if cfg.rate_limit_override > 0:
            overrides[name] = {"rate_limit_override": cfg.rate_limit_override, "source": "config"}

    # Dynamic keys
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
