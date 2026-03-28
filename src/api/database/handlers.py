import json
from fastapi import APIRouter, Depends, Request, Query, Path
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

async def get_db_engine(db_name: str, auth: AuthContext):
    # Check scope
    if "*" not in auth.db_scope and db_name not in auth.db_scope:
        raise NexusGateException(ErrorCodes.AUTH_SCOPE_DENIED, f"API key does not have access to database '{db_name}'", 403)

    engine = await DatabasePoolManager.get_engine(db_name)
    if not engine:
        raise NexusGateException(ErrorCodes.DB_NOT_FOUND, f"Database '{db_name}' not found", 404)

    config = ConfigManager.get().database[db_name]

    # Check mode intersection
    # Simplistic approach: if required operation is write, mode must be readwrite or writeonly
    # Handlers will check this specific to operation.
    return engine, config

@router.get("/databases")
async def list_databases(request: Request, auth: AuthContext = Depends(get_auth_context)):
    config = ConfigManager.get()

    dbs = []
    for name, db_cfg in config.database.items():
        if "*" in auth.db_scope or name in auth.db_scope:
            engine = await DatabasePoolManager.get_engine(name)
            status = "connected" if engine and await engine.health_check() else "disconnected"

            dbs.append({
                "name": name,
                "engine": db_cfg.engine.value,
                "mode": db_cfg.mode.value,
                "status": status,
                "tables_count": len(await engine.list_tables()) if status == "connected" else 0
            })

    return success_response(request, {"databases": dbs})

@router.get("/{db_name}/tables")
async def list_tables(
    request: Request,
    db_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    if auth.mode == ServerMode.WRITEONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Write-only keys cannot list tables", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)

    tables = await engine.list_tables()
    table_data = []
    for t in tables:
        cols = await engine.describe_table(t.name)
        table_data.append({
            "name": t.name,
            "row_count_estimate": t.row_count_estimate,
            "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable, "primary_key": c.primary_key} for c in cols]
        })

    return success_response(request, {
        "database": db_name,
        "tables": table_data
    })

@router.post("/{db_name}/query")
async def execute_query(
    request: Request,
    body: QueryRequest,
    db_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context)
):
    engine, db_cfg = await get_db_engine(db_name, auth)

    # Parse and validate AST
    safe_sql, op, table = validate_query(body.sql, db_cfg, auth.mode.value)

    # Transpile to target dialect
    transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

    try:
        result = await engine.execute(transpiled_sql, body.params)

        from webhook.emitter import emit_event, WebhookTrigger
        mod_type = "read" if op in ("select", "show", "describe") else ("delete" if op == "delete" else "write")
        emit_event("db", mod_type, db_name, table, op.upper(), {"affected": result.affected_rows or 0},
                WebhookTrigger(api_key=auth.api_key_name, ip=request.client.host if request.client else "", request_id=getattr(request.state, "request_id", "-")))

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
    if auth.mode == ServerMode.WRITEONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Write-only keys cannot read rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)

    # Very basic SQL builder
    fields = params.fields if params.fields else "*"
    # Security: should validate fields against describe_table
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
            raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "Invalid filter JSON", 400)

    if params.sort:
        direction = "DESC" if params.order.upper() == "DESC" else "ASC"
        sql_parts.append(f"ORDER BY {params.sort} {direction}")

    sql_parts.append(f"LIMIT {params.limit} OFFSET {(params.page - 1) * params.limit}")

    sql = " ".join(sql_parts)
    # Validate via parser
    safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
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
    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot insert rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)

    from api.database.filter_builder import construct_insert

    rows = body.rows if body.rows is not None else ([body.row] if body.row else [])
    if not rows:
        raise NexusGateException(ErrorCodes.INPUT_SCHEMA_INVALID, "No payload provided", 400)

    total_affected = 0
    # In real production, use engine's executemany. For now loop.
    try:
        for row in rows:
            sql, sql_params = construct_insert(table_name, row)
            safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
            transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)
            res = await engine.execute(transpiled_sql, sql_params)
            total_affected += (res.affected_rows or 0)

        from webhook.emitter import emit_event, WebhookTrigger
        emit_event("db", "write", db_name, table_name, "INSERT", {"affected": total_affected},
                   WebhookTrigger(api_key=auth.api_key_name, ip=request.client.host if request.client else "", request_id=getattr(request.state, "request_id", "-")))

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
    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot update rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)
    from api.database.filter_builder import construct_update

    try:
        sql, sql_params = construct_update(table_name, body.update, body.filter)
        safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
        transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

        result = await engine.execute(transpiled_sql, sql_params)

        from webhook.emitter import emit_event, WebhookTrigger
        emit_event("db", "write", db_name, table_name, "UPDATE", {"affected": result.affected_rows or 0},
                   WebhookTrigger(api_key=auth.api_key_name, ip=request.client.host if request.client else "", request_id=getattr(request.state, "request_id", "-")))

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
    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only keys cannot delete rows", 403)

    engine, db_cfg = await get_db_engine(db_name, auth)
    from api.database.filter_builder import construct_delete

    try:
        sql, sql_params = construct_delete(table_name, body.filter)
        safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
        transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

        result = await engine.execute(transpiled_sql, sql_params)

        from webhook.emitter import emit_event, WebhookTrigger
        emit_event("db", "delete", db_name, table_name, "DELETE", {"affected": result.affected_rows or 0},
                   WebhookTrigger(api_key=auth.api_key_name, ip=request.client.host if request.client else "", request_id=getattr(request.state, "request_id", "-")))

        return success_response(request, {"affected_rows": result.affected_rows})
    except Exception as e:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(e), 500)
