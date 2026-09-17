"""Shared fixtures.

Two things every test here depends on. The source is a fixture, never the live
service: outbound HTTP is blocked for the whole suite, so a source outage and a
code regression cannot look alike. And the database tests run against a throwaway
schema, so running the suite never touches the loaded mirror.

    venv/bin/pytest

To prove the first claim rather than trust it, run the suite with no network at all.
Postgres is reached over its unix socket, which a network namespace does not block:

    unshare -rn sh -c 'ip link set lo up; \
        HFX_MIRROR_DSN="postgresql:///hfx_parking?host=/var/run/postgresql&user=ross" \
        venv/bin/pytest'
"""

import json
import os
import pathlib
import re
import urllib.request

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# A fixed schema name would let two pytest sessions running at once against the
# same local Postgres drop each other's tables mid-run (each session's teardown,
# or even its startup DROP, would hit the other's live schema). The OS pid is
# unique per process, and an xdist worker is itself a separate process, so
# keying off it keeps both concurrent sessions and workers within one session
# apart. Do not simplify this back to a constant.
TEST_SCHEMA = f"mirror_test_{os.getpid()}"
_STALE_SCHEMA_RE = re.compile(r"^mirror_test_(\d+)$")


def _pid_is_alive(pid):
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just owned by someone else
    return True


def _drop_stale_schemas(conn):
    """Clean up schemas orphaned by a session that crashed before its teardown ran.

    Only names matching mirror_test_<pid> are ever considered, and only when
    that pid is no longer a running process -- so this can never touch a live
    concurrent session's schema, and never touches `public` or `mirror` (the
    real mirror) since neither matches the pattern.
    """
    rows = conn.execute(
        "SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname LIKE 'mirror_test_%'"
    ).fetchall()
    for (name,) in rows:
        match = _STALE_SCHEMA_RE.match(name)
        if match and not _pid_is_alive(int(match.group(1))):
            conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    conn.commit()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in the suite may reach the network."""

    def blocked(*args, **kwargs):
        raise AssertionError(
            "the test suite must not reach the network; use a fixture instead"
        )

    monkeypatch.setattr(urllib.request, "urlopen", blocked)


def load_fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def features(name):
    return load_fixture(name)["features"]


@pytest.fixture
def service_request_features():
    return features("service_requests_page")


@pytest.fixture
def custom_field_features():
    return features("custom_fields_page")


@pytest.fixture
def census_features():
    return features("census_areas_page")


@pytest.fixture
def parking_features():
    return features("parking_custom_fields")


@pytest.fixture(scope="session")
def db():
    """A connection to a throwaway schema, or a skip when Postgres is not there."""
    os.environ["HFX_MIRROR_SCHEMA"] = TEST_SCHEMA
    from mirror import db as mirror_db

    try:
        conn = mirror_db.connect()
    except Exception as exc:  # no database, no db tests
        pytest.skip(f"mirror database unavailable: {exc}")
    _drop_stale_schemas(conn)
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.commit()
    mirror_db.apply_schema(conn)
    yield conn
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.commit()
    conn.close()


@pytest.fixture
def clean_db(db):
    """The throwaway schema, emptied before each test that writes to it."""
    db.execute(
        "TRUNCATE service_requests, custom_fields, census_areas, load_progress, "
        "layer_state, sync_runs, sync_anomalies, layer_versions, "
        "layer_version_samples, list_snapshots, doorway_list_history, "
        "block_list_history, triage_decisions RESTART IDENTITY CASCADE"
    )
    for table in ("service_requests", "custom_fields", "census_areas"):
        db.execute(f"DROP TABLE IF EXISTS {table}__staging")
    db.commit()
    return db
