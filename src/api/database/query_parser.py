import sqlglot
from sqlglot import exp
from typing import Optional
from api.errors import NexusGateException, ErrorCodes
from config.schema import DatabaseDefConfig

# ─────────────────────────────────────────────────────────────────────────────
# AST Policies
# ─────────────────────────────────────────────────────────────────────────────

def _enforce_user_mode(expr: exp.Expression, user_mode: str) -> None:
    """Verifies that the executed logical branch strictly follows the credential mode."""
    is_select = isinstance(expr, (exp.Select, exp.Show, exp.Describe))
    is_write = isinstance(expr, (exp.Insert, exp.Update, exp.Delete))

    if user_mode == "readonly" and not is_select:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "API Key mode 'readonly' cannot execute mutate blocks.", 403)
    if user_mode == "writeonly" and not is_write:
        raise NexusGateException(ErrorCodes.AUTH_INSUFFICIENT_MODE, "API Key mode 'writeonly' exclusively manages mutators.", 403)

def _enforce_query_policy(expr: exp.Expression, db_config: DatabaseDefConfig, query_type: str) -> None:
    """Ensures globally blocked nodes are securely rejected dynamically."""
    if not db_config.dangerous_operations:
        if isinstance(expr, (exp.Drop, exp.Alter, exp.Create, exp.Command)):
            raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, "Dangerous operations (DROP/ALTER/CREATE/etc) are disabled.", 403)

        if isinstance(expr, exp.Command) and 'TRUNCATE' in expr.sql().upper():
            raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, "TRUNCATE statements remain intrinsically blocked globally.", 403)

    if db_config.query_blacklist and query_type in [q.upper() for q in db_config.query_blacklist]:
        raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, f"Operation '{query_type}' explicitly mapped in blacklists.", 403)

    if db_config.query_whitelist and query_type not in [q.upper() for q in db_config.query_whitelist]:
        raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, f"Operation '{query_type}' is natively missing from whitelists.", 403)

def _extract_target_table(expr: exp.Expression) -> str:
    """Safely traverses AST extracting webhook dependencies dynamically linking tables."""
    try:
        tables = list(expr.find_all(exp.Table))
        if tables:
            return tables[0].name
    except Exception:
        pass
    return "*"

# ─────────────────────────────────────────────────────────────────────────────
# Primary SQL Validator Execution
# ─────────────────────────────────────────────────────────────────────────────

def validate_query(sql: str, db_config: DatabaseDefConfig, user_mode: str) -> tuple[str, str, str]:
    """Parses and strictly confines the SQL query mapping ASTs natively preventing injections."""
    try:
        expressions = sqlglot.parse(sql)
    except sqlglot.errors.ParseError as ast_error:
        raise NexusGateException(ErrorCodes.DB_QUERY_INVALID, f"Parse tree failure: {str(ast_error)}", 400)

    if len(expressions) > 1:
        raise NexusGateException(ErrorCodes.DB_QUERY_BLOCKED, "Multiple parallel blocks strictly blocked via node logic.", 403)

    expr = expressions[0]
    if not expr:
        raise NexusGateException(ErrorCodes.DB_QUERY_INVALID, "Detected implicitly empty node map.", 400)

    query_type = expr.key.upper()
    
    _enforce_user_mode(expr, user_mode)
    _enforce_query_policy(expr, db_config, query_type)

    return expr.sql(), query_type.lower(), _extract_target_table(expr)
