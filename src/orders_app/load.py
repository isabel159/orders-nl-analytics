"""Pipeline: read the raw CSV -> clean it -> load it into PostgreSQL.

Everything runs in a single transaction: if any step fails, the database is left untouched.

Usage:
    uv run python -m orders_app.load [--csv data/orders_raw.csv]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import psycopg

from orders_app.cleaning import COLUMNS, clean_orders
from orders_app.config import RAW_CSV, SQL_DIR, admin_conninfo


def read_raw_csv(path: Path) -> pd.DataFrame:
    """Read every column as text, without letting pandas guess types or turn 'N/A' into NaN."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


def _copy_rows(cur: psycopg.Cursor, table: str, df: pd.DataFrame) -> None:
    cols = ", ".join(df.columns)
    with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
        for row in df.itertuples(index=False, name=None):
            copy.write_row(row)


def load(csv_path: Path) -> dict[str, int]:
    result = clean_orders(read_raw_csv(csv_path))

    with psycopg.connect(admin_conninfo()) as conn, conn.cursor() as cur:
        cur.execute((SQL_DIR / "01_schema.sql").read_text(encoding="utf-8"))
        _copy_rows(cur, "orders", result.clean[COLUMNS])
        _copy_rows(cur, "rejected_orders", result.rejected)
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


if __name__ == "__main__":
    main()
