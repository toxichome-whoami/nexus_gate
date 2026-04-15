import os
import hashlib
import httpx
import base64
import asyncio
import aiofiles
import structlog
from fastapi import APIRouter, Depends, Request, Query, Path
from api.federation.sync import FederationState
from typing import Optional

from config.loader import ConfigManager
from utils.types import AuthContext, ServerMode
from utils.size_parser import parse_size
from server.middleware.auth import get_auth_context
from api.responses import success_response
from api.errors import NexusGateException, ErrorCodes

from .router import router
from .schemas import ActionRequest
from .file_ops import get_file_info, rename_path, copy_path, delete_path, mkdir, bulk_delete_paths, bulk_move_paths
from .streaming import serve_file
from .archive import stream_zip_folder
from .image_processor import process_image_and_stream
from .upload_scanner import UploadScanner, ScannerRejectError
from api.federation.proxy import proxy_request

from webhook.emitter import emit_event, WebhookTrigger
from api.storage.chunked_upload import ChunkedUploadManager
from utils.uuid7 import uuid7

logger = structlog.get_logger()
UPLOAD_BUFFER_SIZE = 65536

# ─────────────────────────────────────────────────────────────────────────────
# Core Utility Functions
# ─────────────────────────────────────────────────────────────────────────────

def _is_federated(alias: str) -> bool:
    """Checks if an alias belongs to a remote federated storage node."""
    config = ConfigManager.get()
    if not config.features.federation or not config.federation.enabled:
        return False
    return any(alias.startswith(f"{srv_alias}_") for srv_alias in config.federation.server.keys())

def _get_storage_path(alias: str, rel_path: str, auth: AuthContext) -> str:
    """Resolves and validates a storage request path securely."""
    if "*" not in auth.fs_scope and alias not in auth.fs_scope:
        raise NexusGateException(ErrorCodes.AUTH_SCOPE_DENIED, f"API key does not have access to storage '{alias}'", 403)

    config = ConfigManager.get()
    storage_cfg = config.storage.get(alias)
    if not storage_cfg:
        raise NexusGateException(ErrorCodes.FS_NOT_FOUND, f"Storage '{alias}' not found", 404)

    base_path = os.path.realpath(storage_cfg.path)
    rel_path = rel_path.lstrip('/')
    target_path = os.path.realpath(os.path.join(base_path, rel_path))

    # Path traversal protection
    if not target_path.startswith(base_path):
        raise NexusGateException(ErrorCodes.INPUT_PATH_TRAVERSAL, "Path traversal attempt detected", 400)

    return target_path

def _build_scanner(alias: str) -> UploadScanner:
    """Instantiates a scanner with rules retrieved from the storage configuration."""
    config = ConfigManager.get()
    storage_cfg = config.storage.get(alias)
    max_file_size = 0
    allowed_ext, blocked_ext = [], []
    
    if storage_cfg:
        try:
            max_file_size = parse_size(storage_cfg.max_file_size)
        except Exception:
            pass
        allowed_ext = storage_cfg.allowed_extensions or []
        blocked_ext = storage_cfg.blocked_extensions or []
        
    return UploadScanner(allowed_extensions=allowed_ext, blocked_extensions=blocked_ext, max_file_size=max_file_size)

async def _fetch_remote_storages(alias: str, server_state: dict, active_storages: list, auth: AuthContext):
    """Fetches federated storages from a single remote node and appends them if accessible."""
    config = ConfigManager.get()
    if server_state.get("status") != "up" or alias not in config.federation.server:
        return

    srv_config = config.federation.server[alias]
    remote_storages_map = server_state.get("storages", {})

    url = srv_config.url.rstrip("/")
    encoded_secret = base64.b64encode(srv_config.secret.encode("utf-8")).decode("utf-8")
    headers = {"X-Federation-Secret": encoded_secret, "X-Federation-Node": srv_config.node_id}

    try:
        async with httpx.AsyncClient(verify=(srv_config.trust_mode == "verify"), timeout=5) as client:
            resp = await client.get(f"{url}/api/v1/fs/storages", headers=headers)
            if resp.status_code == 200:
                fs_data = resp.json().get("data", {}).get("storages", [])
                
                new_storages = {}
                for fs in fs_data:
                    if fs.get("federated"): 
                        continue
                        
                    fs_name = fs.get("name")
                    health_status = remote_storages_map.get(fs_name, {})
                    new_storages[fs_name] = {
                        "status": health_status.get("status", "available") if isinstance(health_status, dict) else health_status,
                        "mode": fs.get("mode", "proxy"),
                        "limit": fs.get("limit", "10GB"),
                        "chunk_size": fs.get("chunk_size", "10MB"),
                        "max_file_size": fs.get("max_file_size", "100MB"),
                    }
                remote_storages_map = new_storages
    except Exception:
        pass

    for storage_name, status_info in remote_storages_map.items():
        federated_name = f"{alias}_{storage_name}"
        if "*" not in auth.fs_scope and federated_name not in auth.fs_scope:
            continue
            
        is_dict = isinstance(status_info, dict)
        raw_status = status_info.get("status", "available") if is_dict else status_info
        
        active_storages.append({
            "name": federated_name,
            "mode": status_info.get("mode", "proxy") if is_dict else "proxy",
            "status": "available" if raw_status == "up" else raw_status,
            "limit": status_info.get("limit", "10GB") if is_dict else "10GB",
            "chunk_size": status_info.get("chunk_size", "10MB") if is_dict else "10MB",
            "max_file_size": status_info.get("max_file_size", "100MB") if is_dict else "100MB",
            "federated": True,
            "remote_server": alias,
        })

# ─────────────────────────────────────────────────────────────────────────────
# Upload Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_direct_upload(request: Request, alias: str, form, scanner: UploadScanner, auth: AuthContext):
    """Processes a simple, single-part direct file upload stream."""
    path = form.get("path")
    file = form.get("file")
    filename = getattr(file, "filename", path)

    try:
        scanner.validate_filename(filename)
    except ScannerRejectError as e:
        raise NexusGateException(e.code, e.message, 400)

    target_path = _get_storage_path(alias, path, auth)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    total_written = 0
    sha256 = hashlib.sha256()
    scanned = False

    async with aiofiles.open(target_path, "wb") as f:
        while True:
            chunk = await file.read(UPLOAD_BUFFER_SIZE)
            if not chunk:
                break

            if not scanned:
                try:
                    scanner.scan_magic_bytes(chunk[:1024], filename)
                except ScannerRejectError as e:
                    await f.close()
                    if os.path.exists(target_path): os.remove(target_path)
                    raise NexusGateException(e.code, e.message, 400)
                scanned = True

            await f.write(chunk)
            sha256.update(chunk)
            total_written += len(chunk)

            if scanner.max_file_size > 0 and total_written > scanner.max_file_size:
                await f.close()
                if os.path.exists(target_path): os.remove(target_path)
                raise NexusGateException(ErrorCodes.FS_FILE_TOO_LARGE, f"Upload exceeds max size", 413)

    emit_event("fs", "write", alias, path, "UPLOAD_DIRECT", {"size": total_written, "sha256": sha256.hexdigest()},
        WebhookTrigger(
            api_key=auth.api_key_name,
            ip=request.client.host if request.client else "",
            request_id=getattr(request.state, "request_id", "-"),
            webhook_token=request.headers.get("X-NexusGate-Webhook-Token")
        ))

    return success_response(request, {"action": "direct", "status": "success", "file": {"path": target_path, "size": total_written, "sha256": sha256.hexdigest()}})

async def _handle_form_upload(request: Request, alias: str, scanner: UploadScanner, auth: AuthContext):
    """Parses multipart form uploads for Direct or Chunk actions."""
    form = await request.form()
    action = form.get("action")

    if action == "direct":
        return await _handle_direct_upload(request, alias, form, scanner, auth)
        
    if action == "chunk":
        upload_id = form.get("upload_id")
        chunk_index = int(form.get("chunk_index"))
        chunk_hash = form.get("chunk_hash")
        file_content = await form.get("file").read()

        await ChunkedUploadManager.write_chunk(upload_id, chunk_index, chunk_hash, file_content)
        session = await ChunkedUploadManager.get_session(upload_id)
        
        return success_response(request, {
            "upload_id": upload_id,
            "chunk_index": chunk_index,
            "status": "uploaded",
            "verified": True,
            "progress": {
                "uploaded_chunks": len(session["uploaded_chunks"]),
                "total_chunks": session["total_chunks"],
                "uploaded_bytes": session["uploaded_bytes"],
                "total_bytes": session["total_size"],
                "percent": round(session["uploaded_bytes"] / session["total_size"] * 100, 2)
            }
        })

    raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Invalid form action", 400)

async def _handle_json_upload(request: Request, alias: str, scanner: UploadScanner, auth: AuthContext):
    """Handles Chunked uploading lifecycle via JSON operations (initiate / finalize / status)."""
    body = await request.json()
    action = body.get("action")
    storage_cfg = ConfigManager.get().storage.get(alias)

    if action == "initiate":
        filename = body.get("filename", "")
        
        try:
            scanner.validate_filename(filename)
            scanner.validate_size(body.get("total_size", 0))
        except ScannerRejectError as e:
            raise NexusGateException(e.code, e.message, 400 if e.code != ErrorCodes.FS_FILE_TOO_LARGE else 413)

        upload_id = f"upl_{uuid7().hex}"
        try:
            chunk_size_bytes = parse_size(body.get("chunk_size", storage_cfg.chunk_size))
        except Exception:
            chunk_size_bytes = 10485760

        total_size = body.get("total_size", 0)
        total_chunks = (total_size + chunk_size_bytes - 1) // chunk_size_bytes

        session_data = {
            "upload_id": upload_id,
            "filename": filename,
            "path": body.get("path"),
            "total_size": total_size,
            "chunk_size": chunk_size_bytes,
            "checksum_sha256": body.get("checksum_sha256"),
            "total_chunks": total_chunks,
            "uploaded_chunks": [],
            "uploaded_bytes": 0
        }
        
        await ChunkedUploadManager.initiate(upload_id, session_data)
        chunks = [{"index": i, "offset": i * chunk_size_bytes, "size": min(chunk_size_bytes, total_size - i * chunk_size_bytes), "status": "pending"} for i in range(total_chunks)]
        return success_response(request, {"upload_id": upload_id, "chunk_size": chunk_size_bytes, "total_chunks": total_chunks, "chunks": chunks})

    if action == "finalize":
        upload_id = body.get("upload_id")
        session = await ChunkedUploadManager.get_session(upload_id)
        target = _get_storage_path(alias, session["path"], auth)
        result = await ChunkedUploadManager.finalize(upload_id, target)

        emit_event("fs", "write", alias, session["path"], "UPLOAD_CHUNKED", result,
            WebhookTrigger(
                api_key=auth.api_key_name,
                ip=request.client.host if request.client else "",
                request_id=getattr(request.state, "request_id", "-"),
                webhook_token=request.headers.get("X-NexusGate-Webhook-Token")
            ))
        return success_response(request, {"file": {"name": session["filename"], "path": target, **result}})

    if action == "status":
        return success_response(request, await ChunkedUploadManager.get_session(body.get("upload_id")))

    if action == "cancel":
        await ChunkedUploadManager.cancel(body.get("upload_id"))
        return success_response(request, {"action": "cancel", "upload_id": body.get("upload_id")})

    raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Invalid JSON action", 400)

# ─────────────────────────────────────────────────────────────────────────────
# Endpoint Registrations
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/storages")
async def list_storages(request: Request, auth: AuthContext = Depends(get_auth_context)):
    config = ConfigManager.get()
    storages = []

    # Local Storages
    for name, storage_cfg in config.storage.items():
        if "*" in auth.fs_scope or name in auth.fs_scope:
            storages.append({
                "name": name,
                "mode": storage_cfg.mode.value,
                "status": "available" if os.path.exists(storage_cfg.path) else "unavailable",
                "limit": storage_cfg.limit,
                "chunk_size": storage_cfg.chunk_size,
                "max_file_size": storage_cfg.max_file_size,
                "federated": False,
            })

    # Federated Storages
    if config.features.federation and config.federation.enabled:
        state = FederationState()
        tasks = [_fetch_remote_storages(alias, server_state, storages, auth) for alias, server_state in state.servers.items()]
        if tasks:
            await asyncio.gather(*tasks)

    return success_response(request, {"storages": storages})


@router.get("/{alias}/list")
async def list_folder(
    request: Request,
    alias: str = Path(...),
    path: str = Query("/"),
    recursive: bool = Query(False),
    auth: AuthContext = Depends(get_auth_context)
):
    if _is_federated(alias):
        return await proxy_request(alias, f"list", request, False)

    target_path = _get_storage_path(alias, path, auth)

    if not os.path.exists(target_path):
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, f"Path not found: {path}", 404)
    if not os.path.isdir(target_path):
        raise NexusGateException(ErrorCodes.FS_PATH_NOT_FOUND, "Path is not a directory", 400)

    items = [await get_file_info(os.path.join(target_path, item)) for item in os.listdir(target_path)]
    return success_response(request, {"storage": alias, "path": path, "items": items})


@router.get("/{alias}/download")
async def download_file(
    request: Request,
    alias: str = Path(...),
    path: str = Query(...),
    inline: bool = Query(False),
    width: Optional[int] = Query(None),
    height: Optional[int] = Query(None),
    format: Optional[str] = Query(None),
    auth: AuthContext = Depends(get_auth_context)
):
    if _is_federated(alias):
        return await proxy_request(alias, f"download", request, False)

    target_path = _get_storage_path(alias, path, auth)

    if os.path.isdir(target_path):
        return stream_zip_folder(target_path, os.path.basename(target_path))

    if width or height or format:
        return process_image_and_stream(target_path, width, height, format=format)

    req_headers = {"if-none-match": request.headers.get("if-none-match", ""), "range": request.headers.get("range", "")}
    return serve_file(target_path, inline, request_headers=req_headers)


@router.post("/{alias}/upload")
async def upload_file(request: Request, alias: str = Path(...), auth: AuthContext = Depends(get_auth_context)):
    if _is_federated(alias):
        return await proxy_request(alias, f"upload", request, False)

    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot upload files", 403)

    scanner = _build_scanner(alias)
    is_form = request.headers.get("content-type", "").startswith("multipart/form-data")

    if is_form:
        return await _handle_form_upload(request, alias, scanner, auth)
    return await _handle_json_upload(request, alias, scanner, auth)


@router.post("/{alias}/action")
async def execute_action(
    request: Request,
    alias: str = Path(...),
    body: ActionRequest = ...,
    auth: AuthContext = Depends(get_auth_context)
):
    if _is_federated(alias):
        return await proxy_request(alias, "action", request, False)

    if auth.mode == ServerMode.READONLY and body.action not in ("info", "exists"):
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot execute storage actions", 403)

    # Dictionary dispatch for cleaner action routing
    if body.action == "info":
        info = await get_file_info(_get_storage_path(alias, body.source, auth))
        return success_response(request, {"action": "info", "source": body.source, "info": info})

    if body.action == "exists":
        exists = os.path.exists(_get_storage_path(alias, body.source, auth))
        return success_response(request, {"action": "exists", "source": body.source, "exists": exists})
        
    if body.action == "bulk_delete":
        if not body.sources: raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Missing 'sources'", 400)
        
        targets = [_get_storage_path(alias, s, auth) for s in body.sources]
        results = await bulk_delete_paths(targets)
        for i, res in enumerate(results): res["source"] = body.sources[i]
        return success_response(request, {"action": "bulk_delete", "results": results})
        
    if body.action == "bulk_move":
        if not body.operations: raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Missing 'operations'", 400)
        
        real_ops = [{"source": _get_storage_path(alias, o.get("source"), auth), "target": _get_storage_path(alias, o.get("target"), auth)} for o in body.operations if o.get("source") and o.get("target")]
        results = await bulk_move_paths(real_ops)
        
        for i, res in enumerate(results):
            if i < len(body.operations):
                res["source"] = body.operations[i].get("source")
                if "target" in res: res["target"] = body.operations[i].get("target")
        return success_response(request, {"action": "bulk_move", "results": results})
        
    if body.action == "delete":
        await delete_path(_get_storage_path(alias, body.source, auth))
        return success_response(request, {"action": "delete", "source": body.source, "status": "success"})

    if body.action == "mkdir":
        await mkdir(_get_storage_path(alias, body.source, auth))
        return success_response(request, {"action": "mkdir", "source": body.source, "status": "success"})

    if body.action in ("rename", "move", "copy"):
        source = _get_storage_path(alias, body.source, auth)
        target = _get_storage_path(alias, body.target, auth)
        await copy_path(source, target) if body.action == "copy" else await rename_path(source, target)
        return success_response(request, {"action": body.action, "source": body.source, "target": body.target, "status": "success"})

    raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Action {body.action} not yet implemented.", 501)
