import sqlglot

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_target_dialect(to_dialect: str) -> str:
    """Normalizes generic engine names to literal parser definitions expected by SQLGlot."""
    if to_dialect == "mssql":
        return "tsql"
    return to_dialect

def _execute_ast_conversion(sql: str, to_dialect: str, from_dialect: str = None) -> str:
    """Applies profound Abstract Syntax Tree conversions mutating the raw queries securely."""
    try:
        # sqlglot.transpile handles AST conversion under the hood. Returns a list.
        result = sqlglot.transpile(sql, read=from_dialect, write=to_dialect)
        
        # Validation strictly enforces single-statement ingestion prior to this method
        return result[0]
        
    except sqlglot.errors.ParseError as ast_error:
        from api.errors import NexusGateException, ErrorCodes
        raise NexusGateException(
            code=ErrorCodes.DB_QUERY_INVALID,
            message="Failed to parse SQL query.",
            details=str(ast_error),
            status_code=400
        )

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint Component
# ─────────────────────────────────────────────────────────────────────────────

def transpile_sql(sql: str, to_dialect: str, from_dialect: str = None) -> str:
    """
    Consumes a generalized SQL dialect statement and generates a natively
    compliant statement identical in capability formatted for the given target.
    
    Supported targets correspond to generic driver architectures (e.g., 'postgres', 'mysql').
    """
    mapped_target = _resolve_target_dialect(to_dialect)
    return _execute_ast_conversion(sql, mapped_target, from_dialect)
