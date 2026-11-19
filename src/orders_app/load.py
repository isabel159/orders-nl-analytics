"""Pipeline: read the raw CSV -> clean it -> load it into PostgreSQL -> build views -> grant
read-only access.

Everything runs in a single transaction: if any step fails, the database is left untouched.

Usage:
    uv run python -m orders_app.load [--csv data/orders_raw.csv]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql

from orders_app.cleaning import COLUMNS, clean_orders
from orders_app.config import ALLOWED_TABLES, RAW_CSV, SQL_DIR, admin_conninfo, readonly_credentials


def read_raw_csv(path: Path) -> pd.DataFrame:
    """Read every column as text, without letting pandas guess types or turn 'N/A' into NaN."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


def _copy_rows(cur: psycopg.Cursor, table: str, df: pd.DataFrame) -> None:
    cols = ", ".join(df.columns)
    with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
        for row in df.itertuples(index=False, name=None):
            copy.write_row(row)


def apply_readonly_role(cur: psycopg.Cursor, user: str, password: str) -> None:
    """Create/update the API role with the minimum privileges it needs.

    - LOGIN only: no superuser, no CREATE, no ownership.
    - Every transaction of this role is read-only by default, with a statement timeout.
    - SELECT only on ALLOWED_TABLES; nothing on `rejected_orders` or anything else.
    Identifiers and the password are composed with psycopg.sql (properly quoted), never
    with string formatting.
    """
    role = sql.Identifier(user)
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(
        sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            role, sql.Literal(password)
        )
    )
    cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role))
    cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '5s'").format(role))

    cur.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role))
    cur.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}, PUBLIC").format(role))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    cur.execute(
        sql.SQL("GRANT SELECT ON {} TO {}").format(
            sql.SQL(", ").join(sql.Identifier(t) for t in ALLOWED_TABLES), role
        )
    )


def load(csv_path: Path) -> dict[str, int]:
    result = clean_orders(read_raw_csv(csv_path))

    with psycopg.connect(admin_conninfo()) as conn, conn.cursor() as cur:
        cur.execute((SQL_DIR / "01_schema.sql").read_text(encoding="utf-8"))
        _copy_rows(cur, "orders", result.clean[COLUMNS])
        _copy_rows(cur, "rejected_orders", result.rejected)
        cur.execute((SQL_DIR / "02_views.sql").read_text(encoding="utf-8"))
        apply_readonly_role(cur, *readonly_credentials())
        # Leaving the `with` block commits the transaction (or rolls back on error).

    return result.stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=RAW_CSV)
    args = parser.parse_args()

    stats = load(args.csv)
    print(f"Loaded {args.csv} into PostgreSQL")
    for name, value in stats.items():
        print(f"  {name:<32}: {value}")
    print("Views created: v_sales_by_month_country, v_top_customers")
    print(f"Read-only role '{readonly_credentials()[0]}' can SELECT on: {', '.join(ALLOWED_TABLES)}")


if __name__ == "__main__":
    main()
