import sqlglot

def transpile_sql(sql: str, to_dialect: str, from_dialect: str = None) -> str:
    """
    Parse SQL and transpile it to the target dialect.
    `to_dialect` must map to a sqlglot dialect (e.g. 'postgres', 'mysql', 'sqlite', 'tsql').
    """
    if to_dialect == "mssql":
        to_dialect = "tsql"
        
    try:
        # sqlglot.transpile handles AST conversion under the hood.
        # it returns a list of transpiled statements.
        result = sqlglot.transpile(sql, read=from_dialect, write=to_dialect)
        
        # We enforce single statement in validation, so we can just grab the first
        return result[0]
    except sqlglot.errors.ParseError as e:
        from api.errors import NexusGateException, ErrorCodes
        raise NexusGateException(
            code=ErrorCodes.DB_QUERY_INVALID,
            message="Failed to parse SQL query.",
            details=str(e),
            status_code=400
        )
