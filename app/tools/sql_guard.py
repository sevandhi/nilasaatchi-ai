"""AST guard for model-written SQL (sqlglot, Postgres dialect).

Rules: exactly one statement; SELECT only (CTEs/UNION allowed); no DDL/DML/COPY/SET/SELECT INTO; every relation
in an allow-list; no dangerous functions; an outer LIMIT <= max_rows is injected. The query then runs as
``agent_ro`` in a READ ONLY transaction with ``statement_timeout`` (app/tools/db.py) — the guard is the first of
three layers, not the only one.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

MAX_ROWS = 5000
BLOCKED_FUNCS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "lo_get", "lo_put", "dblink", "dblink_exec", "set_config", "pg_terminate_backend",
    "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile", "query_to_xml", "query_to_xml_and_xmlschema",
    "table_to_xml", "cursor_to_xml", "pg_advisory_lock", "pg_advisory_xact_lock", "txid_current", "pg_notify",
    "current_setting", "pg_file_write", "copy",
}
FORBIDDEN_NODES = tuple(t for t in (
    getattr(exp, n, None) for n in ("Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Copy",
                                    "Merge", "TruncateTable", "Set", "Transaction", "Commit", "Rollback", "Grant",
                                    "Into", "LoadData", "Use", "Pragma", "AlterColumn", "Lock")) if t is not None)


class SqlGuardError(ValueError):
    pass


@dataclass
class GuardedSql:
    sql: str
    tables: list[str]
    limit_injected: bool


def guard_sql(sql: str, allowed: set[str], max_rows: int = MAX_ROWS) -> GuardedSql:
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SqlGuardError("empty SQL")
    try:
        stmts = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlGuardError(f"parse error: {str(e).splitlines()[0][:200]}") from None
    if len(stmts) != 1:
        raise SqlGuardError(f"exactly one statement allowed, got {len(stmts)}")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SqlGuardError(f"only SELECT is allowed (got {type(tree).__name__})")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlGuardError(f"forbidden construct: {type(node).__name__}")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in BLOCKED_FUNCS:
                raise SqlGuardError(f"function not allowed: {name}")
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = []
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if not name:
            continue                      # e.g. table functions / unnest
        schema = (t.db or "").lower()
        if schema and schema != "public":
            raise SqlGuardError(f"schema not allowed: {schema}")
        if name in cte_names:
            continue
        if name not in allowed:
            raise SqlGuardError(f"relation not allowed: {name}")
        tables.append(name)
    injected = False
    lim = tree.args.get("limit")
    cur = None
    if lim is not None:
        try:
            cur = int(lim.expression.this) if lim.expression is not None else None
        except (TypeError, ValueError, AttributeError):
            cur = None
    if cur is None or cur > max_rows:
        tree = tree.limit(max_rows, copy=False) if isinstance(tree, exp.Select) else \
            exp.select("*").from_(tree.subquery("q")).limit(max_rows)
        injected = True
    return GuardedSql(sql=tree.sql(dialect="postgres"), tables=sorted(set(tables)), limit_injected=injected)
