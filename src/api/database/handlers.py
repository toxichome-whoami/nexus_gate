import json
import httpx
import base64
import asyncio
from fastapi import APIRouter, Depends, Request, Query, Path
from api.federation.sync import FederationState
from pydantic import ValidationError

from config.loader import ConfigManager
from utils.types import AuthContext, ServerMode
from server.middleware.auth import get_auth_context
from api.responses import success_response
from api.errors import NexusGateException, ErrorCodes
from db.pool import DatabasePoolManager
from api.database.schemas import QueryRequest, InsertRequest, UpdateRequest, DeleteRequest, FetchRowsParams
from api.database.query_parser import validate_query
from db.dialect.transpiler import transpile_sql
from api.database.filter_builder import build_where_clause

from .router import router
from api.federation.proxy import proxy_request
from webhook.emitter import emit_event, WebhookTrigger

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _is_federated(alias: str) -> bool:
    """Checks if a database alias belongs to a remote federation node."""
    config = ConfigManager.get()
    if not config.features.federation or not config.federation.enabled:
        return False
    # Use any() to reduce nesting
    return any(alias.startswith(f"{srv_alias}_") for srv_alias in config.federation.server.keys())

async def get_db_engine(db_name: str, auth: AuthContext):
    """Verifies scope permissions and retrieves the active database engine cache."""
    # Guard clause: Verify access scope
    if "*" not in auth.db_scope and db_name not in auth.db_scope:
        raise NexusGateException(ErrorCodes.AUTH_SCOPE_DENIED, f"API key does not have access to database '{db_name}'", 403)

    engine = await DatabasePoolManager.get_engine(db_name)
    if not engine:
        raise NexusGateException(ErrorCodes.DB_NOT_FOUND, f"Database '{db_name}' not found", 404)

    config = ConfigManager.get().database[db_name]
    return engine, config

def _emit_db_webhook_event(request: Request, auth: AuthContext, db_name: str, table_name: str, action: str, affected_rows: int):
    """Emits an asynchronous webhook event for database mutations (INSERT/UPDATE/DELETE)."""
    trigger_context = WebhookTrigger(
        api_key=auth.api_key_name,
        ip=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", "-"),
        webhook_token=request.headers.get("X-NexusGate-Webhook-Token")
    )
    
    # Map SQL action to standard webhook event type
    event_type = "delete" if action == "DELETE" else "write"
    if action == "SELECT":
        event_type = "read"

    emit_event("db", event_type, db_name, table_name, action, {"affected": affected_rows}, trigger_context)

async def _fetch_remote_databases(alias: str, server_state: dict, active_dbs: list, auth: AuthContext):
    """
    Fetches the latest database schema stats from a federated node via HTTP.
    Reduces nesting for the list_databases endpoint.
    """
    config = ConfigManager.get()
    if server_state.get("status") != "up" or alias not in config.federation.server:
        return

    srv_config = config.federation.server[alias]
    databases_map = server_state.get("databases", {})

    url = srv_config.url.rstrip("/")
    encoded_secret = base64.b64encode(srv_config.secret.encode("utf-8")).decode("utf-8")
    headers = {"X-Federation-Secret": encoded_secret, "X-Federation-Node": srv_config.node_id}

    # Fetch live database list from remote server
    try:
        async with httpx.AsyncClient(verify=(srv_config.trust_mode == "verify"), timeout=5) as client:
            resp = await client.get(f"{url}/api/v1/db/databases", headers=headers)
            if resp.status_code == 200:
                remote_dbs = resp.json().get("data", {}).get("databases", [])
                
                # Update local map with remote payload
                new_databases = {}
                for remote_db in remote_dbs:
                    if remote_db.get("federated"): 
                        continue
                    db_name = remote_db.get("name")
                    new_databases[db_name] = {
                        "status": databases_map.get(db_name, "up"),
                        "engine": remote_db.get("engine", "unknown"),
                        "mode": remote_db.get("mode", "unknown"),
                        "tables_count": remote_db.get("tables_count", 0)
                    }
                databases_map = new_databases
    except Exception:
        pass  # Fail gracefully if remote node is unreachable
        
    # Append accessible remote DBs to response list
    for db_name, info in databases_map.items():
        federated_name = f"{alias}_{db_name}"
        if "*" in auth.db_scope or federated_name in auth.db_scope:
            active_dbs.append({
                "name": federated_name,
                "engine": info.get("engine", "unknown") if isinstance(info, dict) else "unknown",
                "mode": info.get("mode", "unknown") if isinstance(info, dict) else "unknown",
                "status": info.get("status", "unknown") if isinstance(info, dict) else info,
                "tables_count": info.get("tables_count", 0) if isinstance(info, dict) else 0,
                "federated": True,
                "remote_server": alias,
            })

# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/databases")
async def list_databases(request: Request, auth: AuthContext = Depends(get_auth_context)):
    """Lists locally configured databases and automatically appends federated databases if enabled."""
    config = ConfigManager.get()
    active_dbs = []

    # 1. Fetch Local Databases
    for name, db_cfg in config.database.items():
        if "*" not in auth.db_scope and name not in auth.db_scope:
            continue
            
        engine = await DatabasePoolManager.get_engine(name)
        status = "connected" if engine and await engine.health_check() else "disconnected"

        active_dbs.append({
            "name": name,
            "engine": db_cfg.engine.value,
            "mode": db_cfg.mode.value,
            "status": status,
            "tables_count": len(await engine.list_tables()) if status == "connected" else 0,
            "federated": False,
        })

    # 2. Fetch Federated Remote Databases
    if config.features.federation and config.federation.enabled:
        state = FederationState()
        tasks = [
            _fetch_remote_databases(alias, server_state, active_dbs, auth) 
            for alias, server_state in state.servers.items()
        ]
        if tasks:
            await asyncio.gather(*tasks)

    return success_response(request, {"databases": active_dbs})

@router.get("/{db_name}/tables")
async def list_tables(
    request: Request,
    db_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    """Retrieves standard column schemas for all tables in a target database."""
    # Guard clauses
    if _is_federated(db_name):
        return await proxy_request(db_name, "tables", request, True)

    if auth.mode == ServerMode.WRITEONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Write-only keys cannot list tables", 403)

    engine, _ = await get_db_engine(db_name, auth)

    # Process local tables
    tables = await engine.list_tables()
    formatted_tables = []
    
    for table in tables:
        columns = await engine.describe_table(table.name)
        formatted_tables.append({
            "name": table.name,
            "row_count_estimate": table.row_count_estimate,
            "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable, "primary_key": c.primary_key} for c in columns]
        })

    return success_response(request, {"database": db_name, "tables": formatted_tables})

@router.post("/{db_name}/query")
async def execute_query(
    request: Request,
    body: QueryRequest,
    db_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    """Executes an AST verified raw SQL query directly against the engine."""
    if _is_federated(db_name):
        return await proxy_request(db_name, "query", request, True)

    engine, db_cfg = await get_db_engine(db_name, auth)

    safe_sql, operations, target_table = validate_query(body.sql, db_cfg, auth.mode.value)
    transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

    try:
        result = await engine.execute(transpiled_sql, body.params)
        
        # Determine logical operation standard mapping
        webhook_action = "SELECT" if operations in ("select", "show", "describe") else operations.upper()
        _emit_db_webhook_event(request, auth, db_name, target_table, webhook_action, result.affected_rows or 0)

        return success_response(request, {
            "columns": result.columns,
            "rows": result.rows,
            "affected_rows": result.affected_rows
        })
    except Exception as e:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(e), 500)

@router.get("/{db_name}/{table_name}/rows")
async def get_rows(
    request: Request,
    db_name: str = Path(...),
    table_name: str = Path(...),
    params: FetchRowsParams = Depends(),
    auth: AuthContext = Depends(get_auth_context)
):
    """Retrieves rows via REST parameters, converting standard JSON queries to SQL AST safely."""
    if _is_federated(db_name):
        return await proxy_request(db_name, f"{table_name}/rows", request, True)

    if auth.mode == ServerMode.WRITEONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Write-only keys cannot read rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)

    # Convert parameter request to SQL String
    fields = params.fields if params.fields else "*"
    sql_parts = [f"SELECT {fields} FROM {table_name}"]
    sql_params = {}

    if params.filter:
        try:
            filter_json = json.loads(params.filter)
            where_sql, filter_params = build_where_clause(filter_json)
            if where_sql:
                sql_parts.append(f"WHERE {where_sql}")
                sql_params.update(filter_params)
        except Exception:
            raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Invalid filter JSON structure", 400)

    # Sort & Pagination
    if params.sort:
        direction = "DESC" if params.order.upper() == "DESC" else "ASC"
        sql_parts.append(f"ORDER BY {params.sort} {direction}")

    offset = (params.page - 1) * params.limit
    sql_parts.append(f"LIMIT {params.limit} OFFSET {offset}")

    # Validate AST and execute
    raw_sql = " ".join(sql_parts)
    safe_sql, _, _ = validate_query(raw_sql, db_cfg, auth.mode.value)
    transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

    try:
        result = await engine.execute(transpiled_sql, sql_params)
        return success_response(request, result.rows)
    except Exception as e:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(e), 500)

@router.post("/{db_name}/{table_name}/rows")
async def insert_rows(
    request: Request,
    body: InsertRequest,
    db_name: str = Path(...),
    table_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    """Inserts a single row or batch list of rows safely into the table."""
    if _is_federated(db_name):
        return await proxy_request(db_name, f"{table_name}/rows", request, True)

    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot insert rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)
    from api.database.filter_builder import construct_insert

    # Support parsing both `row` (singular) and `rows` (batch list) param payloads
    target_rows = body.rows if body.rows is not None else ([body.row] if body.row else [])
    if not target_rows:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "No payload rows provided for insert", 400)

    total_affected = 0
    try:
        for row in target_rows:
            sql, sql_params = construct_insert(table_name, row)
            safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
            
            transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)
            result = await engine.execute(transpiled_sql, sql_params)
            total_affected += (result.affected_rows or 0)

        _emit_db_webhook_event(request, auth, db_name, table_name, "INSERT", total_affected)
        return success_response(request, {"affected_rows": total_affected})
        
    except Exception as e:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(e), 500)

@router.put("/{db_name}/{table_name}/rows")
async def update_rows(
    request: Request,
    body: UpdateRequest,
    db_name: str = Path(...),
    table_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    """Updates rows securely bypassing direct SQL interface."""
    if _is_federated(db_name):
        return await proxy_request(db_name, f"{table_name}/rows", request, True)

    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot update rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)
    from api.database.filter_builder import construct_update

    try:
        sql, sql_params = construct_update(table_name, body.update, body.filter)
        safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
        transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

        result = await engine.execute(transpiled_sql, sql_params)
        _emit_db_webhook_event(request, auth, db_name, table_name, "UPDATE", result.affected_rows or 0)

        return success_response(request, {"affected_rows": result.affected_rows})
    except Exception as e:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(e), 500)

@router.delete("/{db_name}/{table_name}/rows")
async def delete_rows(
    request: Request,
    body: DeleteRequest,
    db_name: str = Path(...),
    table_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    """Deletes rows securely via the filter JSON standard."""
    if _is_federated(db_name):
        return await proxy_request(db_name, f"{table_name}/rows", request, True)

    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot delete rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)
    from api.database.filter_builder import construct_delete

    try:
        sql, sql_params = construct_delete(table_name, body.filter)
        safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
        transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

        result = await engine.execute(transpiled_sql, sql_params)
        _emit_db_webhook_event(request, auth, db_name, table_name, "DELETE", result.affected_rows or 0)

        return success_response(request, {"affected_rows": result.affected_rows})
    except Exception as e:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(e), 500)
