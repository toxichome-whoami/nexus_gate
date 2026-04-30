import asyncio
import base64
import json
from typing import Any

import httpx
from fastapi import Depends, Path, Request

from api.database.filter_builder import build_where_clause
from api.database.query_parser import validate_query
from api.database.schemas import (
    DeleteRequest,
    FetchRowsParams,
    InsertRequest,
    QueryRequest,
    UpdateRequest,
)
from api.errors import ErrorCodes, NexusGateException
from api.federation.proxy import proxy_request
from api.federation.sync import FederationState
from api.responses import success_response
from config.loader import ConfigManager
from db.dialect.transpiler import transpile_sql
from db.pool import DatabasePoolManager
from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode
from webhook.emitter import WebhookTrigger, emit_event

from .router import router

# ─────────────────────────────────────────────────────────────────────────────
# Core Extraction Procedures
# ─────────────────────────────────────────────────────────────────────────────


def _is_federated(alias: str) -> bool:
    """Detects implicit federated sub-node calls cleanly."""
    config = ConfigManager.get()
    if not config.features.federation or not config.federation.enabled:
        return False
    return any(
        alias.startswith(f"{srv_alias}_")
        for srv_alias in config.federation.server.keys()
    )


async def get_db_engine(db_name: str, auth: AuthContext):
    """Verifies internal pool mapping executing scope validations."""
    if "*" not in auth.db_scope and db_name not in auth.db_scope:
        raise NexusGateException(
            ErrorCodes.AUTH_SCOPE_DENIED,
            f"API key does not have access to database '{db_name}'",
            403,
        )

    engine = await DatabasePoolManager.get_engine(db_name)
    if not engine:
        raise NexusGateException(
            ErrorCodes.DB_NOT_FOUND, f"Database '{db_name}' not found", 404
        )

    return engine, ConfigManager.get().database[db_name]


def _emit_db_webhook_event(
    request: Request,
    auth: AuthContext,
    db_name: str,
    table_name: str,
    action: str,
    affected_rows: int,
):
    """Transmits real-time mutation state via isolated webhooks."""
    config = ConfigManager.get()
    if not config.features.webhook or not config.webhooks.enabled:
        return

    trigger_context = WebhookTrigger(
        api_key=auth.api_key_name,
        ip=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", "-"),
        webhook_token=request.headers.get("X-NexusGate-Webhook-Token"),
    )

    event_type = "delete" if action == "DELETE" else "write"
    if action == "SELECT":
        event_type = "read"

    emit_event(
        "db",
        event_type,
        db_name,
        table_name,
        action,
        {"affected": affected_rows},
        trigger_context,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Federation Synchronizers
# ─────────────────────────────────────────────────────────────────────────────


def _append_federated_schemas(
    alias: str, remote_payload: list, databases_map: dict
) -> None:
    """Updates the internal schema manifest targeting isolated nodes."""
    for remote_db in remote_payload:
        if remote_db.get("federated"):
            continue

        db_name = remote_db.get("name")
        databases_map[db_name] = {
            "status": databases_map.get(db_name, "up"),
            "engine": remote_db.get("engine", "unknown"),
            "mode": remote_db.get("mode", "unknown"),
            "tables_count": remote_db.get("tables_count", 0),
        }


async def _fetch_remote_databases(
    alias: str, server_state: dict, active_dbs: list, auth: AuthContext
):
    """Executes network proxy calls updating remote alias maps dynamically."""
    config = ConfigManager.get()
    if server_state.get("status") != "up" or alias not in config.federation.server:
        return

    srv_config = config.federation.server[alias]
    databases_map = server_state.get("databases", {})

    url = srv_config.url.rstrip("/")
    headers = {
        "X-Federation-Secret": base64.b64encode(
            srv_config.secret.encode("utf-8")
        ).decode("utf-8"),
        "X-Federation-Node": srv_config.node_id,
    }

    try:
        async with httpx.AsyncClient(
            verify=(srv_config.trust_mode == "verify"), timeout=5
        ) as client:
            resp = await client.get(f"{url}/api/v1/db/databases", headers=headers)
            if resp.status_code == 200:
                remote_payload = resp.json().get("data", {}).get("databases", [])
                _append_federated_schemas(alias, remote_payload, databases_map)
    except Exception:
        pass

    for db_name, info in databases_map.items():
        federated_name = f"{alias}_{db_name}"
        if "*" in auth.db_scope or federated_name in auth.db_scope:
            valid_info = info if isinstance(info, dict) else {}
            active_dbs.append(
                {
                    "name": federated_name,
                    "engine": valid_info.get("engine", "unknown"),
                    "mode": valid_info.get("mode", "unknown"),
                    "status": valid_info.get("status", info)
                    if not isinstance(info, dict)
                    else valid_info.get("status", "unknown"),
                    "tables_count": valid_info.get("tables_count", 0),
                    "federated": True,
                    "remote_server": alias,
                }
            )


# ─────────────────────────────────────────────────────────────────────────────
# Routing Generators
# ─────────────────────────────────────────────────────────────────────────────


def _construct_select_rest_payload(
    table_name: str, params: FetchRowsParams
) -> tuple[str, dict]:
    """Generates pure AST-compliant queries directly from REST schema validations."""
    sql_parts = [f"SELECT {params.fields if params.fields else '*'} FROM {table_name}"]
    sql_params = {}

    if params.filter:
        try:
            filter_json = json.loads(params.filter)
            where_sql, filter_params = build_where_clause(filter_json)
            if where_sql:
                sql_parts.append(f"WHERE {where_sql}")
                sql_params.update(filter_params)
        except Exception:
            raise NexusGateException(
                ErrorCodes.INPUT_SCHEMA_INVALID, "Invalid filter JSON structure", 400
            )

    if params.sort:
        direction = "DESC" if params.order.upper() == "DESC" else "ASC"
        sql_parts.append(f"ORDER BY {params.sort} {direction}")

    sql_parts.append(f"LIMIT {params.limit} OFFSET {(params.page - 1) * params.limit}")
    return " ".join(sql_parts), sql_params


# ─────────────────────────────────────────────────────────────────────────────
# Class-Based Execution Architecture
# ─────────────────────────────────────────────────────────────────────────────


class FederatedQueryEngine:
    """Enterprise structural layer for resolving virtual cross-node aggregations."""

    @staticmethod
    async def execute_distributed_query(
        db_name: str, path_segment: str, request: Request
    ) -> Any:
        """
        Executes true parallel map-reduce data meshes natively via scatter-gather async flows.
        """
        if "," in db_name:
            targets = [t.strip() for t in db_name.split(",")]
            tasks = [
                proxy_request(target, path_segment, request, True) for target in targets
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            merged_rows = []
            for resp in responses:
                if isinstance(resp, BaseException):
                    continue
                try:
                    body_chunks = []
                    async for chunk in resp.body_iterator:
                        if isinstance(chunk, str):
                            body_chunks.append(chunk.encode("utf-8"))
                        else:
                            body_chunks.append(chunk)
                    body_bytes = b"".join(body_chunks)
                    payload = json.loads(body_bytes.decode("utf-8"))

                    # Extract the payload optimally depending on the proxy wrapper
                    data_block = (
                        payload.get("data", payload)
                        if isinstance(payload, dict)
                        else payload
                    )
                    if isinstance(data_block, list):
                        merged_rows.extend(data_block)
                except Exception:
                    pass

            return success_response(
                request, {"mesh_nodes": len(targets), "rows": merged_rows}
            )

        return await proxy_request(db_name, path_segment, request, True)


class QueryExecutionPipeline:
    """High-level abstraction for query validation, transpilation, and execution."""

    @staticmethod
    async def run_query(
        engine, db_cfg, auth, request: Request, db_name: str, sql: str, params: dict
    ) -> dict:
        safe_sql, operations, target_table = validate_query(
            sql, db_cfg, auth.mode.value
        )
        # Skip transpilation when source matches target dialect (always true for
        # non-federated queries — db_cfg.engine == engine.dialect). Transpilation
        # is only needed when federating across different engine types.
        if db_cfg.engine.value == engine.dialect:
            transpiled_sql = safe_sql
        else:
            transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

        result = await engine.execute(transpiled_sql, params)

        # Fast-path: skip webhook entirely when feature is disabled
        config = ConfigManager.get()
        if config.features.webhook and config.webhooks.enabled:
            webhook_action = (
                "SELECT"
                if operations in ("select", "show", "describe")
                else operations.upper()
            )
            _emit_db_webhook_event(
                request,
                auth,
                db_name,
                target_table,
                webhook_action,
                result.affected_rows or 0,
            )

        return {
            "columns": result.columns,
            "rows": result.rows,
            "affected_rows": result.affected_rows,
        }

    @staticmethod
    async def run_bulk_inserts(
        engine,
        db_cfg,
        auth,
        request: Request,
        db_name: str,
        table_name: str,
        rows: list,
    ) -> int:
        from api.database.filter_builder import construct_insert

        total_affected = 0

        # Batching execution loop structurally contained
        for row in rows:
            sql, sql_params = construct_insert(table_name, row)
            safe_sql, _, _ = validate_query(sql, db_cfg, auth.mode.value)
            if db_cfg.engine.value == engine.dialect:
                transpiled_sql = safe_sql
            else:
                transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)
            result = await engine.execute(transpiled_sql, sql_params)
            total_affected += result.affected_rows or 0

        _emit_db_webhook_event(
            request, auth, db_name, table_name, "INSERT", total_affected
        )
        return total_affected


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/databases")
async def list_databases(
    request: Request, auth: AuthContext = Depends(get_auth_context)
):
    config = ConfigManager.get()
    active_dbs = []

    for name, db_cfg in config.database.items():
        if "*" not in auth.db_scope and name not in auth.db_scope:
            continue

        engine = await DatabasePoolManager.get_engine(name)
        status = (
            "connected" if engine and await engine.health_check() else "disconnected"
        )

        active_dbs.append(
            {
                "name": name,
                "engine": db_cfg.engine.value,
                "mode": db_cfg.mode.value,
                "status": status,
                "tables_count": len(await engine.list_tables())
                if (status == "connected" and engine)
                else 0,
                "federated": False,
            }
        )

    if config.features.federation and config.federation.enabled:
        state = FederationState()
        tasks = [
            _fetch_remote_databases(alias, srv_state, active_dbs, auth)
            for alias, srv_state in state.servers.items()
        ]
        if tasks:
            await asyncio.gather(*tasks)

    return success_response(request, {"databases": active_dbs})


@router.get("/{db_name}/tables")
async def list_tables(
    request: Request,
    db_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context),
):
    if _is_federated(db_name):
        return await FederatedQueryEngine.execute_distributed_query(
            db_name, "tables", request
        )

    if auth.mode == ServerMode.WRITEONLY:
        raise NexusGateException(
            ErrorCodes.AUTH_INSUFFICIENT_MODE, "Write-only keys cannot list tables", 403
        )

    engine, _ = await get_db_engine(db_name, auth)
    tables = await engine.list_tables()
    formatted_tables = []

    for table in tables:
        columns = await engine.describe_table(table.name)
        formatted_tables.append(
            {
                "name": table.name,
                "row_count_estimate": table.row_count_estimate,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "nullable": c.nullable,
                        "primary_key": c.primary_key,
                    }
                    for c in columns
                ],
            }
        )

    return success_response(request, {"database": db_name, "tables": formatted_tables})


@router.post("/{db_name}/query")
async def execute_query(
    request: Request,
    body: QueryRequest,
    db_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context),
):
    if _is_federated(db_name):
        return await FederatedQueryEngine.execute_distributed_query(
            db_name, "query", request
        )

    engine, db_cfg = await get_db_engine(db_name, auth)

    try:
        data = await QueryExecutionPipeline.run_query(
            engine, db_cfg, auth, request, db_name, body.sql, body.params or {}
        )
        return success_response(request, data)
    except Exception as exec_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(exec_error), 500)


@router.get("/{db_name}/{table_name}/rows")
async def get_rows(
    request: Request,
    db_name: str = Path(...),
    table_name: str = Path(...),
    params: FetchRowsParams = Depends(),
    auth: AuthContext = Depends(get_auth_context),
):
    if _is_federated(db_name):
        return await FederatedQueryEngine.execute_distributed_query(
            db_name, f"{table_name}/rows", request
        )

    if auth.mode == ServerMode.WRITEONLY:
        raise NexusGateException(
            ErrorCodes.AUTH_INSUFFICIENT_MODE, "Write-only limits apply", 403
        )

    engine, db_cfg = await get_db_engine(db_name, auth)
    raw_sql, sql_params = _construct_select_rest_payload(table_name, params)

    try:
        data = await QueryExecutionPipeline.run_query(
            engine, db_cfg, auth, request, db_name, raw_sql, sql_params
        )
        return success_response(request, data["rows"])
    except Exception as select_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(select_error), 500)


@router.post("/{db_name}/{table_name}/rows")
async def insert_rows(
    request: Request,
    body: InsertRequest,
    db_name: str = Path(...),
    table_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context),
):
    if _is_federated(db_name):
        return await FederatedQueryEngine.execute_distributed_query(
            db_name, f"{table_name}/rows", request
        )

    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(
            ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only limits apply", 403
        )

    engine, db_cfg = await get_db_engine(db_name, auth)

    target_rows = (
        body.rows if body.rows is not None else ([body.row] if body.row else [])
    )
    if not target_rows:
        raise NexusGateException(
            ErrorCodes.INPUT_SCHEMA_INVALID, "No payload array", 400
        )

    try:
        total_affected = await QueryExecutionPipeline.run_bulk_inserts(
            engine, db_cfg, auth, request, db_name, table_name, target_rows
        )
        return success_response(request, {"affected_rows": total_affected})
    except Exception as exec_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(exec_error), 500)


@router.put("/{db_name}/{table_name}/rows")
async def update_rows(
    request: Request,
    body: UpdateRequest,
    db_name: str = Path(...),
    table_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context),
):
    if _is_federated(db_name):
        return await FederatedQueryEngine.execute_distributed_query(
            db_name, f"{table_name}/rows", request
        )
    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(
            ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only limits apply", 403
        )

    engine, db_cfg = await get_db_engine(db_name, auth)
    from api.database.filter_builder import construct_update

    try:
        sql, sql_params = construct_update(table_name, body.update, body.filter)
        data = await QueryExecutionPipeline.run_query(
            engine, db_cfg, auth, request, db_name, sql, sql_params
        )
        return success_response(request, {"affected_rows": data["affected_rows"]})
    except Exception as update_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(update_error), 500)


@router.delete("/{db_name}/{table_name}/rows")
async def delete_rows(
    request: Request,
    body: DeleteRequest,
    db_name: str = Path(...),
    table_name: str = Path(...),
    auth: AuthContext = Depends(get_auth_context),
):
    if _is_federated(db_name):
        return await FederatedQueryEngine.execute_distributed_query(
            db_name, f"{table_name}/rows", request
        )
    if auth.mode == ServerMode.READONLY:
        raise NexusGateException(
            ErrorCodes.AUTH_INSUFFICIENT_MODE, "Read-only limits apply", 403
        )

    engine, db_cfg = await get_db_engine(db_name, auth)
    from api.database.filter_builder import construct_delete

    try:
        sql, sql_params = construct_delete(table_name, body.filter)
        data = await QueryExecutionPipeline.run_query(
            engine, db_cfg, auth, request, db_name, sql, sql_params
        )
        return success_response(request, {"affected_rows": data["affected_rows"]})
    except Exception as exec_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(exec_error), 500)
