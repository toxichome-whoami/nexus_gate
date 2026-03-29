import os
from fastapi import APIRouter, Depends, Request, Query, Path
from typing import Optional, List

from config.loader import ConfigManager
from utils.types import AuthContext, ServerMode
from server.middleware.auth import get_auth_context
from api.responses import success_response
from api.errors import NexusGateException, ErrorCodes

from .router import router
from .schemas import StorageItem, ActionRequest
from .file_ops import get_file_info, rename_path, copy_path, delete_path, mkdir
from .streaming import serve_file
from .archive import stream_zip_folder
from .image_processor import process_image_and_stream
from api.federation.proxy import proxy_request

def _is_federated(alias: str) -> bool:
    config = ConfigManager.get()
    if not config.features.federation or not config.federation.enabled:
        return False
    for srv_alias in config.federation.server.keys():
        if alias.startswith(f"{srv_alias}_"):
            return True
    return False

def _get_storage_path(alias: str, rel_path: str, auth: AuthContext) -> str:
    if "*" not in auth.fs_scope and alias not in auth.fs_scope:
        raise NexusGateException(ErrorCodes.AUTH_SCOPE_DENIED, f"API key does not have access to storage '{alias}'", 403)

    config = ConfigManager.get()
    storage_cfg = config.storage.get(alias)
    if not storage_cfg:
        raise NexusGateException(ErrorCodes.FS_NOT_FOUND, f"Storage '{alias}' not found", 404)

    base_path = os.path.realpath(storage_cfg.path)

    # Strip leading slashes to prevent absolute path resolution escaping base_path
    rel_path = rel_path.lstrip('/')
    target_path = os.path.realpath(os.path.join(base_path, rel_path))

    if not target_path.startswith(base_path):
        raise NexusGateException(ErrorCodes.INPUT_PATH_TRAVERSAL, "Path traversal attempt detected", 400)

    return target_path

@router.get("/storages")
async def list_storages(request: Request, auth: AuthContext = Depends(get_auth_context)):
    config = ConfigManager.get()
    storages = []

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

    # Append federated storages from synced remote servers
    if config.features.federation and config.federation.enabled:
        from api.federation.sync import FederationState
        state = FederationState()
        for alias, srv_state in state.servers.items():
            if srv_state.get("status") != "up":
                continue
            for storage_name, storage_status in srv_state.get("storages", {}).items():
                fed_name = f"{alias}_{storage_name}"
                if "*" in auth.fs_scope or fed_name in auth.fs_scope:
                    storages.append({
                        "name": fed_name,
                        "mode": "proxy",
                        "status": storage_status if isinstance(storage_status, str) else "available",
                        "federated": True,
                        "remote_server": alias,
                    })

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

    items = []
    for item in os.listdir(target_path):
        item_path = os.path.join(target_path, item)
        info = await get_file_info(item_path)
        items.append(info)

    return success_response(request, {
        "storage": alias,
        "path": path,
        "items": items
    })

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

    return serve_file(target_path, inline)

@router.post("/{alias}/upload")
async def upload_file(
    request: Request,
    alias: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    if _is_federated(alias):
        return await proxy_request(alias, f"upload", request, False)

    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot execute storage uploads", 403)

    config = ConfigManager.get()
    storage_cfg = config.storage.get(alias)

    is_form = request.headers.get("content-type", "").startswith("multipart/form-data")
    if is_form:
        form = await request.form()
        action = form.get("action")

        if action == "direct":
            path = form.get("path")
            file = form.get("file")
            target = _get_storage_path(alias, path, auth)

            # Simplified direct write
            contents = await file.read()
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(contents)

            from webhook.emitter import emit_event, WebhookTrigger
            emit_event("fs", "write", alias, path, "UPLOAD_DIRECT", {"size": len(contents)},
                WebhookTrigger(
                    api_key=auth.api_key_name,
                    ip=request.client.host if request.client else "",
                    request_id=getattr(request.state, "request_id", "-"),
                    webhook_token=request.headers.get("X-NexusGate-Webhook-Token")
                ))
            return success_response(request, {"action": "direct", "status": "success", "file": {"path": target}})

        elif action == "chunk":
            upload_id = form.get("upload_id")
            chunk_index = int(form.get("chunk_index"))
            chunk_hash = form.get("chunk_hash")
            file = form.get("file")
            contents = await file.read()

            from api.storage.chunked_upload import ChunkedUploadManager
            await ChunkedUploadManager.write_chunk(upload_id, chunk_index, chunk_hash, contents)
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

    else:
        body = await request.json()
        action = body.get("action")
        from api.storage.chunked_upload import ChunkedUploadManager

        if action == "initiate":
            from utils.uuid7 import uuid7
            upload_id = f"upl_{uuid7().hex}"
            chunk_size_bytes = 10485760 # default 10mb fallback
            try:
                from utils.size_parser import parse_size
                chunk_size_bytes = parse_size(body.get("chunk_size", storage_cfg.chunk_size))
            except Exception: pass

            total_size = body.get("total_size", 0)
            total_chunks = (total_size + chunk_size_bytes - 1) // chunk_size_bytes

            session_data = {
                "upload_id": upload_id,
                "filename": body.get("filename"),
                "path": body.get("path"),
                "total_size": total_size,
                "chunk_size": chunk_size_bytes,
                "checksum_sha256": body.get("checksum_sha256"),
                "total_chunks": total_chunks,
                "uploaded_chunks": [],
                "uploaded_bytes": 0
            }
            await ChunkedUploadManager.initiate(upload_id, session_data)
            return success_response(request, {
                "upload_id": upload_id,
                "chunk_size": chunk_size_bytes,
                "total_chunks": total_chunks,
                "chunks": [{"index": i, "offset": i * chunk_size_bytes, "size": min(chunk_size_bytes, total_size - i * chunk_size_bytes), "status": "pending"} for i in range(total_chunks)]
            })

        elif action == "finalize":
            upload_id = body.get("upload_id")
            session = await ChunkedUploadManager.get_session(upload_id)
            target = _get_storage_path(alias, session["path"], auth)
            result = await ChunkedUploadManager.finalize(upload_id, target)

            from webhook.emitter import emit_event, WebhookTrigger
            emit_event("fs", "write", alias, session["path"], "UPLOAD_CHUNKED", result,
                WebhookTrigger(
                    api_key=auth.api_key_name,
                    ip=request.client.host if request.client else "",
                    request_id=getattr(request.state, "request_id", "-"),
                    webhook_token=request.headers.get("X-NexusGate-Webhook-Token")
                ))

            return success_response(request, {"file": {"name": session["filename"], "path": target, **result}})

        elif action == "status":
            upload_id = body.get("upload_id")
            session = await ChunkedUploadManager.get_session(upload_id)
            return success_response(request, session)

        elif action == "cancel":
            upload_id = body.get("upload_id")
            await ChunkedUploadManager.cancel(upload_id)
            return success_response(request, {"action": "cancel", "upload_id": upload_id})

    raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Invalid upload action or content-type", 400)

@router.post("/{alias}/action")
async def execute_action(
    request: Request,
    alias: str = Path(...),
    body: ActionRequest = ...,
    auth: AuthContext = Depends(get_auth_context)
):
    if _is_federated(alias):
        return await proxy_request(alias, f"action", request, False)

    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot execute storage actions", 403)

    if body.action == "delete":
        target = _get_storage_path(alias, body.source, auth)
        await delete_path(target)
        return success_response(request, {"action": "delete", "source": body.source, "status": "success"})

    elif body.action == "mkdir":
        target = _get_storage_path(alias, body.source, auth)
        await mkdir(target)
        return success_response(request, {"action": "mkdir", "source": body.source, "status": "success"})

    elif body.action in ("rename", "move", "copy"):
        source = _get_storage_path(alias, body.source, auth)
        target = _get_storage_path(alias, body.target, auth)

        if body.action == "copy":
            await copy_path(source, target)
        else:
            await rename_path(source, target)

        return success_response(request, {"action": body.action, "source": body.source, "target": body.target, "status": "success"})

    raise NexusGateException(ErrorCodes.SERVER_INTERNAL, f"Action {body.action} not yet implemented.", 501)
