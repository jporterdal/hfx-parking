"""Connecting to the mirror, and applying its schema.

The store is plain Postgres with no PostGIS. Census polygons are JSONB and
containment stays in Python, which is where the pipeline's ray cast already lives.

The connection is configured only through the standard PG* environment variables,
which libpq reads itself, so no connection string is built here and no host, user
or password is held in the repository. A local `.env` at the repository root
(copy `.env.example`) fills in any that the real environment does not set.
"""

import contextlib
import os
import pathlib
import zlib

import psycopg
from dotenv import load_dotenv
from psycopg import pq

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


# ------------------------------------------------------------------ the sync lock

# The first half of the advisory-lock key ("hfxp"). The second half is derived from
# the schema name, so the lock protects *one mirror*: a test schema and the loaded
# mirror living in the same database do not contend for it, and neither do two test
# sessions, each in a schema of its own.
SYNC_LOCK_NAMESPACE = 0x68667870


class SyncInProgress(RuntimeError):
    """Another sync, load or reload holds the lock, so this run touched nothing."""


# What a command run by hand exits with when it is refused: sysexits' EX_TEMPFAIL,
# "try again later", distinct from 1 (the run failed) so a script can tell them apart.
EXIT_SYNC_IN_PROGRESS = 75


def _sync_lock_key():
    # Kept below 2**31 so the value Postgres reports in pg_locks (an unsigned oid) is
    # the same number as the signed one passed to the lock functions.
    return SYNC_LOCK_NAMESPACE, zlib.crc32(schema().encode()) & 0x7FFFFFFF


@contextlib.contextmanager
def sync_lock(conn):
    """Hold the mirror's sync lock for the duration of the block, or raise
    `SyncInProgress` without waiting.

    At most one sync, load or reload runs against a mirror at a time (spec: "Never run
    two syncs at once"). The reload begins by dropping its staging table, so two runs
    are not merely wasteful, they destroy each other's progress.

    A *session*-level advisory lock, on the connection doing the work, for three
    reasons. `commit()` and `rollback()` (which a reload does constantly) leave it
    alone, where a transaction-level lock would drop at the first page. The server
    releases it when the session ends, so a run that is killed frees it with no
    cleanup and cannot block every later run the way a "running" row would. And it
    needs no file, so it works between the web and worker containers that share only
    the database. Try, never wait: a caller that waited would queue behind a
    25-minute reload.

    It relies on the connection reaching Postgres directly, or through a pooler in
    session mode; a transaction-mode pooler would hand each statement to a different
    session and silently break it.

    Take it at the top of an entry point, before any work: it commits once after
    acquiring, which is what makes `connect()`'s `SET search_path` permanent as well.
    """
    key = _sync_lock_key()
    got = conn.execute("SELECT pg_try_advisory_lock(%s, %s)", key).fetchone()[0]
    conn.commit()
    if not got:
        raise SyncInProgress(
            "another sync, load or reload is already running against this mirror; "
            "nothing was changed"
        )
    try:
        yield
    finally:
        _release_sync_lock(conn, key)


def _release_sync_lock(conn, key):
    if conn.closed or conn.broken:
        return                    # the session ended, and so did the lock
    try:
        status = conn.info.transaction_status
        if status == pq.TransactionStatus.INERROR:
            conn.rollback()       # a failed statement blocks even the unlock
            status = pq.TransactionStatus.IDLE
        conn.execute("SELECT pg_advisory_unlock(%s, %s)", key)
        if status == pq.TransactionStatus.IDLE:
            conn.commit()         # never commit what the block left half-done
    except psycopg.OperationalError:
        # The connection died inside the block (the server was restarted, the backend
        # was terminated). The lock died with the session, and raising here would
        # replace the error the caller is actually handling with a lesser one.
        return


def sync_lock_held(conn):
    """Whether *another* session currently holds this mirror's sync lock.

    Read from `pg_locks`, which any role can read, so the web service can ask without
    holding or contending for the lock. The lock, not a row saying "running", is the
    truth about whether something is running: a row can outlive a run that died, and
    a lock cannot. Writes nothing and leaves the caller's transaction as it found it.
    """
    namespace, number = _sync_lock_key()
    row = conn.execute(
        "SELECT EXISTS ("
        " SELECT 1 FROM pg_locks l JOIN pg_database d ON d.oid = l.database"
        " WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 2"
        "   AND d.datname = current_database()"
        "   AND l.classid::bigint = %s AND l.objid::bigint = %s"
        "   AND l.pid <> pg_backend_pid())",
        (namespace, number),
    ).fetchone()
    return bool(row[0])


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
