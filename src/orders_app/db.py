"""Read-only query execution against PostgreSQL (always with the least-privilege role)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import psycopg
from psycopg import errors

from orders_app.config import readonly_conninfo


class QueryExecutionError(Exception):
    """The database could not run the query. `code` and `status_code` are used by the API."""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]


class QueryExecutor(Protocol):
    def run(self, sql: str) -> QueryResult: ...

    def data_range(self) -> tuple[date, date]: ...


class PostgresExecutor:
    """Opens a short-lived connection as `app_readonly` for each query, inside a READ ONLY transaction."""

    def __init__(self, conninfo: str | None = None):
        self._conninfo = conninfo or readonly_conninfo()

    def _connect(self) -> psycopg.Connection:
        try:
            return psycopg.connect(self._conninfo, connect_timeout=5)
        except psycopg.OperationalError as exc:
            raise QueryExecutionError("database_unavailable", "The database is not available.", 503) from exc

    def run(self, sql: str) -> QueryResult:
        with self._connect() as conn:
            conn.read_only = True
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    columns = [c.name for c in cur.description or []]
                    rows = [list(r) for r in cur.fetchall()]
            except errors.QueryCanceled as exc:
                raise QueryExecutionError("query_timeout", "The query took too long and was cancelled.", 504) from exc
            except psycopg.OperationalError as exc:
                raise QueryExecutionError("database_unavailable", "The database is not available.", 503) from exc
            except psycopg.Error as exc:
                # e.g. the LLM used a column that does not exist: the generated query is not valid.
                detail = exc.diag.message_primary or "the query failed"
                raise QueryExecutionError("query_failed", f"The generated query could not be executed: {detail}.", 422) from exc
        return QueryResult(columns=columns, rows=rows)

    def data_range(self) -> tuple[date, date]:
        result = self.run("SELECT min(order_date), max(order_date) FROM orders")
        return result.rows[0][0], result.rows[0][1]
