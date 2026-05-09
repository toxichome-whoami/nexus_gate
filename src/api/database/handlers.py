import asyncio
import hashlib
import json
import time
from typing import Any

from fastapi import Depends, Path, Query, Request

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
from api.responses import cacheable_response, success_response
from cache import CacheManager
from config.provider import get_config_dependency
from config.schema import NexusGateConfig
from db.dialect.transpiler import transpile_sql
from db.pool import DatabasePoolManager
from server.middleware.auth import get_auth_context
from utils.types import AuthContext, ServerMode
from webhook.emitter import WebhookTrigger, emit_event

from .router import router

# ─────────────────────────────────────────────────────────────────────────────
# Module-level feature flags (checked once at import, never per-request)
# ─────────────────────────────────────────────────────────────────────────────

_FEDERATION_ENABLED: bool = False
_FEDERATION_SERVERS: tuple = ()
_WEBHOOK_ENABLED: bool = False
_QUERY_CACHE_ENABLED: bool = False
_QUERY_RESULTS_TTL: int = 5

# Health check cache: {db_name: (status_str, timestamp)}
_HEALTH_CACHE: dict[str, tuple[str, float]] = {}
_HEALTH_CACHE_TTL: int = 5  # seconds


def _refresh_feature_flags():
    global \
        _FEDERATION_ENABLED, \
        _FEDERATION_SERVERS, \
        _WEBHOOK_ENABLED, \
        _QUERY_CACHE_ENABLED, \
        _QUERY_RESULTS_TTL
    from config.provider import GlobalConfigProvider

    config = GlobalConfigProvider().get_config()
    _FEDERATION_ENABLED = bool(config.features.federation and config.federation.enabled)
    _FEDERATION_SERVERS = (
        tuple(config.federation.server.keys()) if _FEDERATION_ENABLED else ()
    )
    _WEBHOOK_ENABLED = bool(config.features.webhook and config.webhooks.enabled)
    _QUERY_CACHE_ENABLED = bool(config.cache.enabled and config.cache.query_cache)
    _QUERY_RESULTS_TTL = config.cache.query_results_ttl

    if _FEDERATION_ENABLED:
        try:
            from api.federation.proxy import _build_alias_map

            _build_alias_map()
        except ImportError:
            pass


_refresh_feature_flags()

# ─────────────────────────────────────────────────────────────────────────────
# Core Extraction Procedures
# ─────────────────────────────────────────────────────────────────────────────


def _is_federated(alias: str) -> bool:
    if not _FEDERATION_ENABLED:
        return False

    from api.federation.proxy import _resolve_server

    return _resolve_server(alias) is not None


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

    from config.provider import GlobalConfigProvider

    return engine, GlobalConfigProvider().get_config().database[db_name]


async def _emit_db_webhook_event(
    request: Request,
    auth: AuthContext,
    db_name: str,
    table_name: str,
    action: str,
    affected_rows: int,
):
    """Transmits real-time mutation state via isolated webhooks."""
    if not _WEBHOOK_ENABLED:
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

    await emit_event(
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


# Removed _fetch_remote_databases since state is now local
async def _append_cached_remote_databases(
    alias: str, info_dict: dict, active_dbs: list, auth: AuthContext
):
    for db_name, info in info_dict.items():
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
# Column Validation
# ─────────────────────────────────────────────────────────────────────────────


async def _validate_select_columns(
    engine, db_name: str, table_name: str, fields: str | None, sort: str | None
) -> None:
    if not fields and not sort:
        return
    columns = await _get_cached_columns(db_name, table_name, engine)
    valid = {c.name.lower() for c in columns}

    if fields and fields != "*":
        for col in fields.split(","):
            col = col.strip().split(".")[-1].split(" ")[0].strip("`\"'")
            if col.lower() not in valid and col != "*":
                raise NexusGateException(
                    ErrorCodes.INPUT_SCHEMA_INVALID,
                    f"Column '{col}' not found in '{table_name}'",
                    400,
                )
    if sort:
        c = sort.strip().split(".")[-1].strip("`\"'")
        if c.lower() not in valid:
            raise NexusGateException(
                ErrorCodes.INPUT_SCHEMA_INVALID,
                f"Column '{c}' not found in '{table_name}'",
                400,
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
# Introspection Caching (O(N) Elimination)
# ─────────────────────────────────────────────────────────────────────────────


async def _get_cached_tables(db_name: str, engine) -> list:
    """Fetches table names with O(1) cache lookup, bypassing O(N) introspection."""
    cache_key = f"schema:tables:{db_name}"
    cached = await CacheManager.get(cache_key)
    if cached is not None:
        return cached

    tables = await engine.list_tables()
    # Cache for 60 seconds (schemas change rarely during a session)
    await CacheManager.set(cache_key, tables, ttl=60)
    return tables


async def _get_cached_columns(db_name: str, table_name: str, engine) -> list:
    """Fetches column metadata with O(1) cache lookup."""
    cache_key = f"schema:cols:{db_name}:{table_name}"
    cached = await CacheManager.get(cache_key)
    if cached is not None:
        return cached

    columns = await engine.describe_table(table_name)
    # Cache for 300 seconds (column metadata is very stable)
    await CacheManager.set(cache_key, columns, ttl=300)
    return columns


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

        is_read = operations in ("select", "show", "describe")
        cache_key = None
        if is_read and _QUERY_CACHE_ENABLED:
            cache_key = (
                "qc:"
                + hashlib.md5(
                    f"{db_name}|{safe_sql}|{json.dumps(params, sort_keys=True, default=str)}".encode()
                ).hexdigest()
            )
            cached = await CacheManager.get(cache_key)
            if cached is not None:
                return cached

        transpiled_sql = transpile_sql(safe_sql, to_dialect=engine.dialect)

        result = await engine.execute(transpiled_sql, params)

        if _WEBHOOK_ENABLED:
            webhook_action = "SELECT" if is_read else operations.upper()
            await _emit_db_webhook_event(
                request,
                auth,
                db_name,
                target_table,
                webhook_action,
                result.affected_rows or 0,
            )

        response = {
            "columns": result.columns,
            "rows": result.rows,
            "affected_rows": result.affected_rows,
        }

        if is_read and _QUERY_CACHE_ENABLED and cache_key is not None:
            await CacheManager.set(cache_key, response)

        return response

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

        await _emit_db_webhook_event(
            request, auth, db_name, table_name, "INSERT", total_affected
        )
        return total_affected


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/databases")
async def list_databases(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    config: NexusGateConfig = Depends(get_config_dependency),
):
    active_dbs = []
    now = time.monotonic()

    for name, db_cfg in config.database.items():
        if "*" not in auth.db_scope and name not in auth.db_scope:
            continue

        engine = await DatabasePoolManager.get_engine(name)

        # Cached health check — avoids O(n) pings per request
        cached_entry = _HEALTH_CACHE.get(name)
        if cached_entry and (now - cached_entry[1]) < _HEALTH_CACHE_TTL:
            status = cached_entry[0]
        else:
            status = (
                "connected"
                if engine and await engine.health_check()
                else "disconnected"
            )
            _HEALTH_CACHE[name] = (status, now)

        tables_count = 0
        if status == "connected" and engine:
            tables_count = len(await _get_cached_tables(name, engine))

        active_dbs.append(
            {
                "name": name,
                "engine": db_cfg.engine.value,
                "mode": db_cfg.mode.value,
                "status": status,
                "tables_count": tables_count,
                "federated": False,
            }
        )

    if _FEDERATION_ENABLED:
        try:
            from api.federation.state import FederationStateManager

            state_mgr = FederationStateManager()
            await state_mgr.load()

            for alias in config.federation.server:
                node_state = await state_mgr.get_state(alias)
                if node_state and node_state.status == "up":
                    await _append_cached_remote_databases(
                        alias, node_state.databases, active_dbs, auth
                    )
        except Exception:
            pass

    return cacheable_response(request, {"databases": active_dbs})


@router.get("/{db_name}/tables")
async def list_tables(
    request: Request,
    db_name: str = Path(...),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
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
    tables = await _get_cached_tables(db_name, engine)
    total = len(tables)
    page = tables[offset : offset + limit]
    formatted_tables = []

    for table in page:
        columns = await _get_cached_columns(db_name, table.name, engine)
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

    return cacheable_response(
        request,
        {
            "database": db_name,
            "tables": formatted_tables,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
            },
        },
    )


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
    except NexusGateException:
        raise
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

    await _validate_select_columns(
        engine, db_name, table_name, params.fields, params.sort
    )

    raw_sql, sql_params = _construct_select_rest_payload(table_name, params)

    try:
        data = await QueryExecutionPipeline.run_query(
            engine, db_cfg, auth, request, db_name, raw_sql, sql_params
        )

        row_count = len(data["rows"])
        has_more = row_count >= params.limit
        pagination = {"page": params.page, "limit": params.limit, "has_more": has_more}

        # Optional accurate count via ?count=1
        if request.query_params.get("count") == "1":
            count_sql = f"SELECT COUNT(*) AS cnt FROM {table_name}"
            count_params = {}
            if params.filter:
                fj = json.loads(params.filter)
                ws, fp = build_where_clause(fj)
                if ws:
                    count_sql += f" WHERE {ws}"
                    count_params = fp
            cr = await QueryExecutionPipeline.run_query(
                engine, db_cfg, auth, request, db_name, count_sql, count_params
            )
            total = cr["rows"][0]["cnt"]
            pagination["total"] = total
            pagination["has_more"] = (params.page * params.limit) < total

        return cacheable_response(
            request,
            {"rows": data["rows"], "pagination": pagination},
            max_age=_QUERY_RESULTS_TTL,
        )
    except NexusGateException:
        raise
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
    except NexusGateException:
        raise
    except Exception as exec_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(exec_error), 500)


@router.patch("/{db_name}/{table_name}/rows")
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
    except NexusGateException:
        raise
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
    except NexusGateException:
        raise
    except Exception as exec_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_FAILED, str(exec_error), 500)
