"""Connecting to the mirror, and applying its schema.

The store is plain Postgres with no PostGIS. Census polygons are JSONB and
containment stays in Python, which is where the pipeline's ray cast already lives.
"""

import os
import pathlib

import psycopg

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")
DEFAULT_DSN = "postgresql://ross:hfx_local@127.0.0.1:5432/hfx_parking"
DEFAULT_SCHEMA = "mirror"


def dsn():
    return os.environ.get("HFX_MIRROR_DSN") or DEFAULT_DSN


def schema():
    """The schema the mirror lives in. Tests point this at a throwaway one."""
    return os.environ.get("HFX_MIRROR_SCHEMA") or DEFAULT_SCHEMA


def connect(autocommit=False):
    conn = psycopg.connect(dsn(), autocommit=autocommit)
    conn.execute(f"SET search_path TO {schema()},public")
    return conn


def apply_schema(conn):
    """Create anything missing. Safe to run against a store that already exists."""
    sql = SCHEMA_PATH.read_text()
    if schema() != DEFAULT_SCHEMA:
        sql = sql.replace(
            f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_SCHEMA};",
            f"CREATE SCHEMA IF NOT EXISTS {schema()};",
        ).replace(
            f"SET search_path TO {DEFAULT_SCHEMA}, public;",
            f"SET search_path TO {schema()}, public;",
        )
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    conn.execute(f"SET search_path TO {schema()},public")


def table_sizes(conn, tables):
    """On-disk size per table, including indexes and TOAST."""
    sizes, prefix = {}, schema()
    with conn.cursor() as cur:
        for table in tables:
            qualified = f"{prefix}.{table}"
            cur.execute(
                "SELECT pg_total_relation_size(%s), pg_relation_size(%s), "
                "pg_indexes_size(%s)",
                (qualified, qualified, qualified),
            )
            total, heap, indexes = cur.fetchone()
            sizes[table] = {"total": total, "heap": heap, "indexes": indexes}
    return sizes


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
