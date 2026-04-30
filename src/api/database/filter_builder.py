from typing import Any, Dict, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# JSON Operator Translators
# ─────────────────────────────────────────────────────────────────────────────


def _process_array_operator(
    col: str, op: str, val: Any, pname: str, where_parts: list, params: dict
) -> None:
    """Matches JSON collection arrays explicitly against standard inclusion statements."""
    if not isinstance(val, (list, tuple)):
        raise ValueError(f"{op} operator requires a list or tuple structure array")

    sql_op = "IN" if op == "$in" else "NOT IN"
    in_placeholders = []

    for array_index, item in enumerate(val):
        in_pname = f"{pname}_{array_index}"
        in_placeholders.append(f":{in_pname}")
        params[in_pname] = item

    placeholders_str = ", ".join(in_placeholders)
    where_parts.append(f"{col} {sql_op} ({placeholders_str})")


def _process_null_operator(col: str, op: str, val: Any, where_parts: list) -> None:
    """Translates implicit JS definitions mapping dynamically to IS parameters."""
    if val is True:
        where_parts.append(f"{col} IS NULL" if op == "$null" else f"{col} IS NOT NULL")
    elif val is False:
        where_parts.append(f"{col} IS NOT NULL" if op == "$null" else f"{col} IS NULL")


def _process_standard_operator(
    col: str, op: str, val: Any, pname: str, where_parts: list, params: dict
) -> None:
    """Ingests basic operational tokens seamlessly."""
    math_ops = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}

    if op == "$eq":
        where_parts.append(f"{col} = :{pname}")
    elif op == "$ne":
        where_parts.append(f"{col} != :{pname}")
    elif op in math_ops:
        where_parts.append(f"{col} {math_ops[op]} :{pname}")
    elif op == "$like":
        where_parts.append(f"{col} LIKE :{pname}")
    elif op == "$ilike":
        where_parts.append(f"LOWER({col}) LIKE LOWER(:{pname})")
    elif op == "$between":
        if not isinstance(val, list) or len(val) != 2:
            raise ValueError("$between requires a strictly bound list of 2 values")
        where_parts.append(f"{col} BETWEEN :{pname}_start AND :{pname}_end")
        params[f"{pname}_start"], params[f"{pname}_end"] = val[0], val[1]
        return
    else:
        raise ValueError(f"Operator node unsupported natively: {op}")

    params[pname] = val


def _route_filter_criteria(
    col: str, criteria: dict, param_idx: int, where_parts: list, params: dict
) -> int:
    """Isolates traversal logic bounding JSON nested trees directly."""
    for op, val in criteria.items():
        pname = f"__p_{param_idx}"
        param_idx += 1

        if op in ("$in", "$nin"):
            _process_array_operator(col, op, val, pname, where_parts, params)
        elif op in ("$null", "$not_null"):
            _process_null_operator(col, op, val, where_parts)
            param_idx -= 1
        else:
            _process_standard_operator(col, op, val, pname, where_parts, params)

    return param_idx


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic SQL Generators
# ─────────────────────────────────────────────────────────────────────────────


def build_where_clause(filter_json: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Generates parameterized queries avoiding implicit manual concatenations completely."""
    if not filter_json:
        return "", {}

    where_parts = []
    params = {}
    param_idx = 0

    for col, criteria in filter_json.items():
        if isinstance(criteria, dict):
            param_idx = _route_filter_criteria(
                col, criteria, param_idx, where_parts, params
            )
        else:
            pname = f"__p_{param_idx}"
            param_idx += 1
            where_parts.append(f"{col} = :{pname}")
            params[pname] = criteria

    return " AND ".join(where_parts), params


def construct_insert(table: str, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    cols = list(data.keys())
    placeholders = [f":p_{i}" for i in range(len(cols))]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"

    return sql, {f"p_{i}": val for i, val in enumerate(data.values())}


def construct_update(
    table: str, update_data: Dict[str, Any], filter_json: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    set_parts = []
    params = {}

    for index, (col_key, col_val) in enumerate(update_data.items()):
        pname = f"up_p_{index}"
        set_parts.append(f"{col_key} = :{pname}")
        params[pname] = col_val

    where_clause, filter_params = build_where_clause(filter_json)
    if not where_clause:
        raise ValueError(
            "Update filter nodes cannot inherently be executed unconstrained."
        )

    params.update(filter_params)
    return f"UPDATE {table} SET {', '.join(set_parts)} WHERE {where_clause}", params


def construct_delete(
    table: str, filter_json: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    where_clause, params = build_where_clause(filter_json)
    if not where_clause:
        raise ValueError("Delete operations mandate structurally bound node limits.")

    return f"DELETE FROM {table} WHERE {where_clause}", params
