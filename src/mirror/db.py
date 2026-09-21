"""Connecting to the mirror, and applying its schema.

The store is plain Postgres with no PostGIS. Census polygons are JSONB and
containment stays in Python, which is where the pipeline's ray cast already lives.

The connection is configured only through the standard PG* environment variables,
which libpq reads itself, so no connection string is built here and no host, user
or password is held in the repository. A local `.env` at the repository root
(copy `.env.example`) fills in any that the real environment does not set.
"""

import os
import pathlib

import psycopg
from dotenv import load_dotenv

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")
ENV_PATH = pathlib.Path(__file__).resolve().parents[2] / ".env"
DEFAULT_SCHEMA = "mirror"

REQUIRED = ("PGHOST", "PGUSER", "PGDATABASE")
# Read by libpq, not here: a missing password is legitimate (peer authentication
# over the socket, ~/.pgpass) and a wrong one fails the connection itself.
OPTIONAL = ("PGPORT", "PGPASSWORD")

_env_loaded = False


class ConfigurationError(RuntimeError):
    """A required PG* variable is missing, so no connection was attempted."""


def load_env():
    """Read `.env` into the environment, once per process, on first use.

    Never overrides: a variable already set in the real environment wins, and a
    missing file is fine, which is what a host that sets the variables needs.
    The path is fixed to the repository root, not the working directory, so the
    server, the command-line entry points and the tests all read the same file.
    """
    global _env_loaded
    if not _env_loaded:
        _env_loaded = True
        load_dotenv(ENV_PATH, override=False)


def check_configured():
    """Raise ConfigurationError naming every required variable that is unset or empty.

    libpq alone would not notice: with nothing set it connects to the local socket
    as the operating-system user, which looks like success.
    """
    load_env()
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise ConfigurationError(
            f"database not configured: {', '.join(missing)} not set. Copy "
            ".env.example to .env and fill it in, or set them in the environment."
        )


def schema():
    """The schema the mirror lives in. Tests point this at a throwaway one."""
    return os.environ.get("HFX_MIRROR_SCHEMA") or DEFAULT_SCHEMA


def connect(autocommit=False):
    check_configured()
    conn = psycopg.connect(autocommit=autocommit)
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
