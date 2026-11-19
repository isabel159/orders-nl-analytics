"""Integration tests: the read-only role can read the allowed models and nothing else.

They need the PostgreSQL container running and the data loaded
(`docker compose up -d` + `uv run python -m orders_app.load`); otherwise they are skipped.
"""

import psycopg
import pytest

from orders_app.config import readonly_conninfo


@pytest.fixture(scope="module")
def ro_conn():
    try:
        conn = psycopg.connect(readonly_conninfo(), connect_timeout=3, autocommit=True)
    except (psycopg.OperationalError, KeyError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    yield conn
    conn.close()


def test_readonly_role_can_read_views(ro_conn):
    n_months = ro_conn.execute("SELECT count(DISTINCT month) FROM v_sales_by_month_country").fetchone()[0]
    top = ro_conn.execute("SELECT ranking, total_spent FROM v_top_customers ORDER BY ranking").fetchall()

    assert n_months == 24  # 2024-01 .. 2025-12
    assert len(top) == 10
    assert [t[1] for t in top] == sorted((t[1] for t in top), reverse=True)


def test_views_only_count_completed_sales(ro_conn):
    view_total, table_total = ro_conn.execute(
        """
        SELECT (SELECT sum(total_sales) FROM v_sales_by_month_country),
               (SELECT sum(amount) FROM orders WHERE status = 'completado')
        """
    ).fetchone()
    assert view_total == table_total


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM rejected_orders",  # table not in the allow-list
        "DELETE FROM orders",  # write
        "DROP TABLE orders",  # DDL
        "CREATE TABLE hack (id int)",  # DDL
    ],
)
def test_readonly_role_cannot_escape(ro_conn, statement):
    with pytest.raises(psycopg.Error):
        ro_conn.execute(statement)
