"""Unit tests for the dynamic SQL filter builder."""
import pytest
from api.database.filter_builder import (
    build_where_clause,
    construct_insert,
    construct_update,
    construct_delete,
)

# ─────────────────────────────────────────────────────────────────────────────
# Where Clause Transformation Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_where_equality_comparison():
    """Verify standard field=value equality mapping."""
    sql, params = build_where_clause({"name": "Alice"})
    assert "name = :__p_0" in sql
    assert params["__p_0"] == "Alice"

def test_where_composite_conditions():
    """Verify AND-combined conditions with mixed operators."""
    sql, params = build_where_clause({"active": True, "age": {"$gte": 18}})
    assert "active = " in sql
    assert "age >=" in sql
    assert len(params) == 2

def test_where_membership_operators():
    """Verify $in and $nin array membership mapping."""
    # Test IN
    sql_in, params_in = build_where_clause({"status": {"$in": ["active", "pending"]}})
    assert "IN" in sql_in
    assert "active" in params_in.values()
    
    # Test NOT IN
    sql_nin, _ = build_where_clause({"role": {"$nin": ["banned", "deleted"]}})
    assert "NOT IN" in sql_nin

def test_where_string_pattern_matching():
    """Verify $like pattern matching mapping."""
    sql, params = build_where_clause({"email": {"$like": "%@example.com"}})
    assert "LIKE" in sql
    # Extract the value to verify parameter binding
    binding_key = list(params.keys())[0]
    assert params[binding_key] == "%@example.com"

def test_where_nullability_checks():
    """Verify $null operator mapping for both True and False."""
    # Test IS NULL
    sql_null, params_null = build_where_clause({"deleted_at": {"$null": True}})
    assert "IS NULL" in sql_null
    assert not params_null

    # Test IS NOT NULL
    sql_not_null, _ = build_where_clause({"verified_at": {"$null": False}})
    assert "IS NOT NULL" in sql_not_null

def test_where_range_comparison():
    """Verify $between range operator mapping."""
    sql, params = build_where_clause({"age": {"$between": [18, 65]}})
    assert "BETWEEN" in sql
    assert 18 in params.values()
    assert 65 in params.values()

def test_where_empty_filter_safety():
    """Ensure empty filters produce empty strings and no parameters."""
    sql, params = build_where_clause({})
    assert sql == ""
    assert not params

# ─────────────────────────────────────────────────────────────────────────────
# Statement Construction Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_sql_insert_generation():
    """Verify generation of valid INSERT statement with correct column mapping."""
    sql, params = construct_insert("users", {"name": "Bob", "active": True})
    assert "INSERT INTO users" in sql
    assert "name" in sql and "active" in sql
    assert "Bob" in params.values()

def test_sql_update_generation():
    """Verify generation of valid UPDATE statement with explicit WHERE scoping."""
    sql, params = construct_update("users", {"active": False}, {"id": 42})
    assert "UPDATE users SET" in sql
    assert "WHERE" in sql
    assert False in params.values()
    assert 42 in params.values()

def test_sql_delete_generation():
    """Verify generation of valid DELETE statement with explicit WHERE scoping."""
    sql, params = construct_delete("users", {"id": 99})
    assert "DELETE FROM users WHERE" in sql
    assert 99 in params.values()

# ─────────────────────────────────────────────────────────────────────────────
# Constraint & Safety Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_unscoped_mutation_rejection():
    """Ensure that mutations without filters are rejected to prevent mass-wipes."""
    # Unscoped Delete
    with pytest.raises(ValueError, match="filter cannot be empty"):
        construct_delete("users", {})

    # Unscoped Update
    with pytest.raises(ValueError, match="filter cannot be empty"):
        construct_update("users", {"name": "x"}, {})
