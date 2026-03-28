"""
Admin API: Restricted endpoints for managing API keys, bans,
rate limit overrides, circuit breaker state, and live config viewing.
Only accessible with a key that has admin-level privileges.
"""
from fastapi import APIRouter, Depends, Request, Path, Body
from typing import Optional

from server.middleware.auth import get_auth_context, require_admin
from api.responses import success_response
from api.errors import NexusGateException, ErrorCodes
from config.loader import ConfigManager
from security.ban_list import BanList
from security.circuit_breaker import CircuitBreaker
from utils.uuid7 import uuid7
import secrets

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ─── API Key Management ───────────────────────────────────────────────────────

@router.get("/keys")
async def list_api_keys(request: Request, auth=Depends(require_admin)):
    config = ConfigManager.get()
    keys = []
    for name, key_cfg in config.api_key.items():
        keys.append({
            "name": name,
            "mode": key_cfg.mode.value,
            "db_scope": key_cfg.db_scope,
            "fs_scope": key_cfg.fs_scope,
            "rate_limit_override": key_cfg.rate_limit_override,
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

    if not name:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Key name is required", 400)

    # Generate a cryptographically secure key
    raw_secret = secrets.token_urlsafe(48)

    # NOTE: In a full implementation this would write to the config file.
    # Here we return the generated secret for the caller to add to config.
    return success_response(request, {
        "name": name,
        "secret": raw_secret,
        "mode": mode,
        "db_scope": db_scope,
        "fs_scope": fs_scope,
        "note": "Add this key to your config.toml under [api_key.<name>]",
    })


@router.delete("/keys/{key_name}")
async def revoke_api_key(
    request: Request,
    key_name: str = Path(...),
    auth=Depends(require_admin),
):
    config = ConfigManager.get()
    if key_name not in config.api_key:
        raise NexusGateException(ErrorCodes.AUTH_INVALID_KEY, f"Key '{key_name}' not found", 404)

    # Ban the key immediately so it can't be used even before config reload
    BanList.ban_key(key_name, reason="Revoked via admin API", duration_seconds=None)
    return success_response(request, {"revoked": key_name, "note": "Also remove from config.toml to persist"})


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

    BanList.ban_ip(ip, reason=reason, duration_seconds=duration)
    return success_response(request, {"banned_ip": ip, "reason": reason, "duration_seconds": duration})


@router.delete("/bans/ip/{ip}")
async def unban_ip(request: Request, ip: str = Path(...), auth=Depends(require_admin)):
    removed = BanList.unban_ip(ip)
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

    BanList.ban_key(key_name, reason=reason, duration_seconds=duration)
    return success_response(request, {"banned_key": key_name, "reason": reason})


@router.delete("/bans/key/{key_name}")
async def unban_key(request: Request, key_name: str = Path(...), auth=Depends(require_admin)):
    removed = BanList.unban_key(key_name)
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
    from security.circuit_breaker import CircuitState
    if key in CircuitBreaker._circuits:
        CircuitBreaker._circuits[key]["state"] = CircuitState.CLOSED
        CircuitBreaker._circuits[key]["failures"] = 0
        CircuitBreaker._circuits[key]["successes"] = 0
    return success_response(request, {"reset": key, "state": "closed"})


# ─── Live Config View ─────────────────────────────────────────────────────────

@router.get("/config")
async def view_config(request: Request, auth=Depends(require_admin)):
    config = ConfigManager.get()
    # Mask secrets before returning
    data = config.model_dump()
    for key_name in data.get("api_key", {}):
        data["api_key"][key_name]["secret"] = "***REDACTED***"
    for wh_name in data.get("webhook", {}):
        data["webhook"][wh_name]["secret"] = "***REDACTED***"
    for srv_name in data.get("federation", {}).get("server", {}):
        data["federation"]["server"][srv_name]["api_key"] = "***REDACTED***"
    return success_response(request, {"config": data})


# ─── Rate Limit Overrides ─────────────────────────────────────────────────────

@router.get("/rate-limits")
async def view_rate_limits(request: Request, auth=Depends(require_admin)):
    config = ConfigManager.get()
    overrides = {
        name: {"rate_limit_override": cfg.rate_limit_override}
        for name, cfg in config.api_key.items()
        if cfg.rate_limit_override > 0
    }
    return success_response(request, {
        "global": {
            "window": config.rate_limit.window,
            "max_requests": config.rate_limit.max_requests,
            "burst": config.rate_limit.burst,
        },
        "per_key_overrides": overrides,
    })
