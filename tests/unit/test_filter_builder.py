"""Unit tests for the dynamic filter builder."""
import pytest
from api.database.filter_builder import (
    build_where_clause,
    construct_insert,
    construct_update,
    construct_delete,
)


def test_simple_equality():
    sql, params = build_where_clause({"name": "Alice"})
    assert "name = :__p_0" in sql
    assert params["__p_0"] == "Alice"


def test_multiple_conditions():
    sql, params = build_where_clause({"active": True, "age": {"$gte": 18}})
    assert "active = " in sql
    assert "age >=" in sql
    assert len(params) == 2


def test_in_operator():
    sql, params = build_where_clause({"status": {"$in": ["active", "pending"]}})
    assert "IN" in sql
    assert "active" in params.values()
    assert "pending" in params.values()


def test_not_in_operator():
    sql, params = build_where_clause({"role": {"$nin": ["banned", "deleted"]}})
    assert "NOT IN" in sql


def test_like_operator():
    sql, params = build_where_clause({"email": {"$like": "%@example.com"}})
    assert "LIKE" in sql
    assert params[list(params.keys())[0]] == "%@example.com"


def test_null_check():
    sql, params = build_where_clause({"deleted_at": {"$null": True}})
    assert "IS NULL" in sql
    assert len(params) == 0  # No params needed for IS NULL


def test_not_null_check():
    sql, params = build_where_clause({"verified_at": {"$null": False}})
    assert "IS NOT NULL" in sql


def test_between():
    sql, params = build_where_clause({"age": {"$between": [18, 65]}})
    assert "BETWEEN" in sql
    assert 18 in params.values()
    assert 65 in params.values()


def test_empty_filter_returns_empty():
    sql, params = build_where_clause({})
    assert sql == ""
    assert params == {}


def test_construct_insert():
    sql, params = construct_insert("users", {"name": "Bob", "active": True})
    assert "INSERT INTO users" in sql
    assert "name" in sql
    assert "active" in sql
    assert "Bob" in params.values()


def test_construct_update():
    sql, params = construct_update(
        "users",
        {"active": False},
        {"id": 42},
    )
    assert "UPDATE users SET" in sql
    assert "WHERE" in sql
    assert False in params.values()


def test_construct_delete():
    sql, params = construct_delete("users", {"id": 99})
    assert "DELETE FROM users WHERE" in sql
    assert 99 in params.values()


def test_construct_delete_no_filter_raises():
    with pytest.raises(ValueError, match="filter cannot be empty"):
        construct_delete("users", {})


def test_construct_update_no_filter_raises():
    with pytest.raises(ValueError, match="filter cannot be empty"):
        construct_update("users", {"name": "x"}, {})
