from typing import Dict, Any, List, Tuple

def build_where_clause(filter_json: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Parses a JSON filter object and builds a SQL WHERE clause fragment and params dictionary.
    Supports: $eq, $ne, $gt, $gte, $lt, $lte, $in, $nin, $like, $ilike, $null, $not_null
    Returns: (where_clause_string, params_dict)
    """
    if not filter_json:
        return "", {}
        
    where_parts = []
    params = {}
    
    # Very basic validation against SQL injection, in reality we map against known columns
    # We will assume column names are sane for now, or validated upstream.
    
    param_idx = 0
    for col, criteria in filter_json.items():
        if isinstance(criteria, dict):
            for op, val in criteria.items():
                pname = f"__p_{param_idx}"
                param_idx += 1
                
                if op == "$eq":
                    where_parts.append(f"{col} = :{pname}")
                    params[pname] = val
                elif op == "$ne":
                    where_parts.append(f"{col} != :{pname}")
                    params[pname] = val
                elif op in ("$gt", "$gte", "$lt", "$lte"):
                    sql_op = {
                        "$gt": ">", "$gte": ">=", 
                        "$lt": "<", "$lte": "<="
                    }[op]
                    where_parts.append(f"{col} {sql_op} :{pname}")
                    params[pname] = val
                elif op in ("$in", "$nin"):
                    if not isinstance(val, (list, tuple)):
                        raise ValueError(f"{op} requires a list value")
                    sql_op = "IN" if op == "$in" else "NOT IN"
                    in_placeholders = []
                    for i, item in enumerate(val):
                        in_pname = f"{pname}_{i}"
                        in_placeholders.append(f":{in_pname}")
                        params[in_pname] = item
                    placeholders_str = ", ".join(in_placeholders)
                    where_parts.append(f"{col} {sql_op} ({placeholders_str})")
                elif op == "$like":
                    where_parts.append(f"{col} LIKE :{pname}")
                    params[pname] = val
                elif op == "$ilike":
                    where_parts.append(f"LOWER({col}) LIKE LOWER(:{pname})") # Generic approach
                    params[pname] = val
                elif op == "$null":
                    if val is True:
                        where_parts.append(f"{col} IS NULL")
                    elif val is False:
                        where_parts.append(f"{col} IS NOT NULL")
                    param_idx -= 1 # didn't use param
                elif op == "$not_null": # Same as $null: false
                    if val is True:
                        where_parts.append(f"{col} IS NOT NULL")
                    elif val is False:
                        where_parts.append(f"{col} IS NULL")
                    param_idx -= 1
                elif op == "$between":
                    if not isinstance(val, list) or len(val) != 2:
                        raise ValueError("$between requires a list of 2 values")
                    pname_start = f"{pname}_start"
                    pname_end = f"{pname}_end"
                    where_parts.append(f"{col} BETWEEN :{pname_start} AND :{pname_end}")
                    params[pname_start] = val[0]
                    params[pname_end] = val[1]
                else:
                    raise ValueError(f"Unsupported operator: {op}")
        else:
            # implicit equal
            pname = f"__p_{param_idx}"
            param_idx += 1
            where_parts.append(f"{col} = :{pname}")
            params[pname] = criteria
            
    where_clause = " AND ".join(where_parts)
    return where_clause, params

def construct_insert(table: str, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    cols = list(data.keys())
    placeholders = [f":p_{i}" for i in range(len(cols))]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
    
    params = {f"p_{i}": val for i, val in enumerate(data.values())}
    return sql, params
    
def construct_update(table: str, update_data: Dict[str, Any], filter_json: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    set_parts = []
    params = {}
    
    # Update clause
    for i, (k, v) in enumerate(update_data.items()):
        pname = f"up_p_{i}"
        set_parts.append(f"{k} = :{pname}")
        params[pname] = v
        
    set_clause = ", ".join(set_parts)
    
    # Where clause
    where_clause, filter_params = build_where_clause(filter_json)
    if not where_clause:
        raise ValueError("Update filter cannot be empty.")
        
    params.update(filter_params)
    
    sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
    return sql, params
    
def construct_delete(table: str, filter_json: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    where_clause, params = build_where_clause(filter_json)
    if not where_clause:
        raise ValueError("Delete filter cannot be empty.")
        
    sql = f"DELETE FROM {table} WHERE {where_clause}"
    return sql, params
