"""Configuration read from environment variables (and from a local .env file, if present)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_ROOT / "sql"
RAW_CSV = PROJECT_ROOT / "data" / "orders_raw.csv"


def _conninfo(user: str, password: str) -> str:
    return make_conninfo(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "orders"),
        user=user,
        password=password,
    )


def admin_conninfo() -> str:
    """Owner account: creates tables and loads data. Never used by the API."""
    return _conninfo(os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"])
