"""Validate SQL produced by the LLM before it ever reaches the database.

The query is parsed into an AST with sqlglot (PostgreSQL dialect) and inspected node by node.
Never with regex: keyword matching is easy to bypass (comments, casing, quoting) and also
rejects valid queries (e.g. a column called `deleted_at`).

Rules — a query is accepted only if:
  1. It parses as SQL and contains exactly ONE statement.
  2. That statement is a read query (SELECT / WITH ... SELECT / UNION ...).
  3. It contains no write, DDL or session node anywhere (INSERT, UPDATE, DELETE, DROP, CREATE,
     ALTER, COPY, SET, ..., `SELECT ... INTO` and `FOR UPDATE` locks included).
  4. Every table it reads is in the allow-list (CTE names are allowed as they are defined
     inside the same query). No other schemas: no pg_catalog, no information_schema.
  5. It calls no dangerous server functions (file access, sleeps, config, remote connections).
  6. The result is capped: a LIMIT is added (or lowered) to `max_rows`.

The validated query is re-generated from the AST (without comments), so what runs is exactly
what was checked.
This is one layer of a defense in depth: the database role is read-only as well.
"""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from orders_app.config import ALLOWED_TABLES

logging.getLogger("sqlglot").setLevel(logging.ERROR)

DEFAULT_MAX_ROWS = 1000

# Nodes that write, change the schema, change the session or lock rows.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,  # anything sqlglot cannot model (SET, GRANT, VACUUM, ...)
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Use,
    exp.Pragma,
    exp.Into,  # SELECT ... INTO new_table creates a table
    exp.Lock,  # SELECT ... FOR UPDATE
)

# Server functions that read files, sleep, change settings or reach other servers.
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "file_")
FORBIDDEN_FUNCTIONS = {
    "set_config",
    "current_setting",
    "query_to_xml",
    "query_to_xml_and_xmlschema",
    "table_to_xml",
    "cursor_to_xml",
    "copy",
    "version",
    "inet_server_addr",
    "inet_client_addr",
}

ALLOWED_SCHEMAS = {"", "public"}


class QueryValidationError(ValueError):
    """The query is not safe or not valid. The message is meant to be shown to the API user."""


def _function_name(func: exp.Func) -> str:
    return (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()


def validate_query(
    query: str,
    allowed_tables: tuple[str, ...] | set[str] = ALLOWED_TABLES,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> str:
    """Return a safe, normalized version of `query`, or raise QueryValidationError."""
    if not query or not query.strip():
        raise QueryValidationError("The query is empty.")

    # 1. Parse + exactly one statement
    try:
        statements = [s for s in sqlglot.parse(query, read="postgres") if s is not None]
    except ParseError as exc:
        raise QueryValidationError(f"The query is not valid SQL: {exc.errors[0]['description'] if exc.errors else exc}") from exc
    if len(statements) != 1:
        raise QueryValidationError(f"Exactly one SQL statement is allowed, got {len(statements)}.")
    tree = statements[0]

    # 2. Must be a read query
    if not isinstance(tree, exp.Query):
        raise QueryValidationError(f"Only SELECT queries are allowed, got {tree.key.upper()}.")

    # 3. No write / DDL / session / lock node anywhere in the tree
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise QueryValidationError(f"Forbidden operation in query: {node.key.upper()}.")

    # 4. Tables: only the allow-list (or CTEs defined in the query itself)
    allowed = {t.lower() for t in allowed_tables}
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name, schema = table.name.lower(), table.db.lower()
        if not name:
            raise QueryValidationError("Table functions are not allowed in FROM.")
        if table.catalog or schema not in ALLOWED_SCHEMAS:
            raise QueryValidationError(f"Access to schema '{table.db}' is not allowed.")
        if name in cte_names and not schema:
            continue
        if name not in allowed:
            raise QueryValidationError(
                f"Table '{table.name}' is not allowed. Allowed tables: {', '.join(sorted(allowed))}."
            )

    # 5. Dangerous functions
    for func in tree.find_all(exp.Func):
        fname = _function_name(func)
        if fname in FORBIDDEN_FUNCTIONS or fname.startswith(FORBIDDEN_FUNCTION_PREFIXES):
            raise QueryValidationError(f"Function '{fname}' is not allowed.")

    # 6. Cap the number of rows returned
    limit = tree.args.get("limit")
    limit_value = limit.expression if limit else None
    if not (isinstance(limit_value, exp.Literal) and limit_value.is_int and int(limit_value.this) <= max_rows):
        tree = tree.limit(max_rows)

    return tree.sql(dialect="postgres", comments=False)
