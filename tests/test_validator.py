"""Tests for the SQL validator: legitimate analytical queries pass, anything else is blocked."""

import pytest

from orders_app.validator import QueryValidationError, validate_query


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM orders",
        "select count(*) from ORDERS",
        "SELECT * FROM public.orders",
        "SELECT sum(total_sales) FROM v_sales_by_month_country "
        "WHERE country = 'España' AND month = DATE '2025-03-01'",
        "SELECT customer, total_spent FROM v_top_customers ORDER BY ranking",
        "SELECT date_trunc('month', order_date) AS m, sum(amount) FROM orders "
        "WHERE status = 'completado' AND extract(year FROM order_date) = 2025 GROUP BY 1 ORDER BY 1",
        "WITH spain AS (SELECT * FROM orders WHERE country = 'España') SELECT count(*) FROM spain",
        "SELECT country FROM orders UNION SELECT country FROM v_top_customers",
        "SELECT * FROM orders WHERE amount > (SELECT avg(amount) FROM orders)",
        "SELECT 1;",  # a single trailing semicolon is fine
    ],
)
def test_valid_read_queries_are_accepted(query):
    assert validate_query(query)


@pytest.mark.parametrize(
    "query, reason",
    [
        # Writes and DDL
        ("DELETE FROM orders", "Only SELECT"),
        ("UPDATE orders SET amount = 0", "Only SELECT"),
        ("INSERT INTO orders (order_id) VALUES ('x')", "Only SELECT"),
        ("DROP TABLE orders", "Only SELECT"),
        ("CREATE TABLE hack AS SELECT * FROM orders", "Only SELECT"),
        ("TRUNCATE orders", "Only SELECT"),
        ("COPY orders TO '/tmp/orders.csv'", "Only SELECT"),
        ("SET ROLE orders_admin", "Only SELECT"),
        ("GRANT ALL ON orders TO public", "Only SELECT"),
        # Hidden writes inside a SELECT
        ("SELECT * INTO hack FROM orders", "Forbidden operation"),
        ("SELECT * FROM orders FOR UPDATE", "Forbidden operation"),
        ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "Forbidden operation"),
        # Stacked statements (classic injection)
        ("SELECT 1; DROP TABLE orders", "Exactly one"),
        ("SELECT * FROM orders; SELECT * FROM rejected_orders", "Exactly one"),
        # Tables outside the allow-list
        ("SELECT * FROM rejected_orders", "not allowed"),
        ("SELECT * FROM orders o JOIN rejected_orders r USING (order_id)", "not allowed"),
        ("WITH x AS (SELECT * FROM rejected_orders) SELECT * FROM x", "not allowed"),
        ("SELECT * FROM orders WHERE order_id IN (SELECT order_id FROM rejected_orders)", "not allowed"),
        ("SELECT * FROM pg_catalog.pg_user", "schema"),
        ("SELECT * FROM information_schema.tables", "schema"),
        ("SELECT * FROM pg_shadow", "not allowed"),
        ("SELECT * FROM generate_series(1, 1000000)", "Table functions"),
        # Dangerous functions
        ("SELECT pg_read_file('/etc/passwd')", "Function"),
        ("SELECT pg_sleep(60)", "Function"),
        ("SELECT set_config('statement_timeout', '0', false)", "Function"),
        ("SELECT * FROM orders WHERE dblink_exec('host=evil', 'DROP TABLE x') IS NULL", "Function"),
        # Not SQL at all
        ("", "empty"),
        ("   ", "empty"),
        ("cuánto vendimos en España", "not valid SQL"),
    ],
)
def test_unsafe_or_invalid_queries_are_rejected(query, reason):
    with pytest.raises(QueryValidationError, match=reason):
        validate_query(query)


def test_limit_is_added_when_missing():
    assert validate_query("SELECT * FROM orders", max_rows=100).endswith("LIMIT 100")


def test_limit_is_lowered_when_too_high():
    assert validate_query("SELECT * FROM orders LIMIT 999999", max_rows=100).endswith("LIMIT 100")


def test_smaller_limit_is_kept():
    assert validate_query("SELECT * FROM orders LIMIT 5", max_rows=100).endswith("LIMIT 5")


def test_comments_cannot_smuggle_a_second_statement():
    # The validated SQL is re-generated from the AST: what runs is exactly what was checked.
    safe = validate_query("SELECT count(*) FROM orders -- ; DROP TABLE orders")
    assert safe == "SELECT COUNT(*) FROM orders LIMIT 1000"
