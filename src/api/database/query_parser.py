import sqlglot
from sqlglot import exp
from typing import Optional
from api.errors import NexusGateException, ErrorCodes
from config.schema import DatabaseDefConfig

def validate_query(sql: str, db_config: DatabaseDefConfig, user_mode: str) -> str:
    """
    Parse and validate the SQL query using sqlglot AST.
    Returns parsed and reformatted SQL string, or raises exception.
    """
    try:
        # Parse into a list of expressions
        expressions = sqlglot.parse(sql)
    except sqlglot.errors.ParseError as e:
        raise NexusGateException(ErrorCodes.DB_QUERY_INVALID, f"Parse error: {str(e)}", 400)

    # 1. Block multi-statement
    if len(expressions) > 1:
        raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, "Multiple statements are not allowed.", 403)
        
    expr = expressions[0]
    if not expr:
         raise NexusGateException(ErrorCodes.DB_QUERY_INVALID, "Empty query.", 400)

    # 2. Check query type vs user mode
    is_select = isinstance(expr, (exp.Select, exp.Show, exp.Describe))
    is_write = isinstance(expr, (exp.Insert, exp.Update, exp.Delete))

    if user_mode == "readonly" and not is_select:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "API Key mode 'readonly' cannot execute write queries.", 403)
    if user_mode == "writeonly" and not is_write:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "API Key mode 'writeonly' cannot execute read queries.", 403)

    # 3. Block dangerous operations if configured
    dangerous_classes = (exp.Drop, exp.Alter, exp.Create, exp.Command) 
    
    if not db_config.dangerous_operations:
        if isinstance(expr, dangerous_classes):
            raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, "Dangerous operations (DROP/ALTER/CREATE/etc) are disabled globally on this database.", 403)
            
        # specifically check TRUNCATE which is sometimes parsed differently
        if isinstance(expr, exp.Command) and 'TRUNCATE' in expr.sql().upper():
            raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, "TRUNCATE is disabled globally on this database.", 403)

    # 4. Check blacklists/whitelists
    query_type = expr.key.upper()
    if db_config.query_blacklist and query_type in [q.upper() for q in db_config.query_blacklist]:
        raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, f"Operation '{query_type}' is blacklisted.", 403)

    if db_config.query_whitelist and query_type not in [q.upper() for q in db_config.query_whitelist]:
        raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, f"Operation '{query_type}' is not whitelisted.", 403)

    return expr.sql()

