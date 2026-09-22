"""The mirror populates itself from nothing (change automate-mirror-bootstrap-and-sync,
tasks 1.1 to 1.4).

Everything else in that change rests on one reading of the code: that `sync.py`, run
against a database that has never held the mirror, creates the schema and loads every
layer, because with no recorded baseline the poll reports every layer as advanced.
`tests/test_sync.py` starts from a seeded `mirrored` fixture and so never exercises
that. These tests start from a schema that does not exist.
"""

import datetime
import sys

import pytest

from conftest import TEST_SCHEMA
from mirror import db, load, source, sync
from test_sync import BASE_DATE, field_feature, request_feature, service  # noqa: F401

NOW = datetime.datetime(2026, 9, 21, 12, 0, tzinfo=datetime.UTC)
LAYER_KEYS = ("service_requests", "custom_fields", "census_areas")


@pytest.fixture
def fresh_schema(db, monkeypatch):
    """A schema name that does not exist yet, dropped again afterwards.

    Not the `db` fixture's schema, which `apply_schema` has already populated: the
    point is that nothing exists when the sync starts.
    """
    name = f"{TEST_SCHEMA}_boot"
    monkeypatch.setenv("HFX_MIRROR_SCHEMA", name)
    db.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    db.commit()
    yield name
    db.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    db.commit()


def schema_has_table(conn, schema, table):
    return conn.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",)).fetchone()[0] \
        is not None


def test_sync_against_a_database_with_no_mirror_creates_the_schema_and_loads_every_layer(
        fresh_schema, service, monkeypatch):
    """1.1: the command an operator (or the worker) runs on a fresh deployment."""
    service.install(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["sync.py"])
    probe = db.connect()
    assert not schema_has_table(probe, fresh_schema, "layer_state")   # truly empty

    assert sync.main() == 0

    assert schema_has_table(probe, fresh_schema, "layer_state")
    state = {row[0]: row for row in probe.execute(
        "SELECT layer, full_load_completed_at, stored_count, source_count "
        "FROM layer_state").fetchall()}
    assert set(state) == set(LAYER_KEYS)
    for key in LAYER_KEYS:
        _, completed, stored, source_count = state[key]
        assert completed is not None, f"{key} never recorded a completed load"
        assert stored == source_count == len(service.rows[key])
    probe.close()


def test_the_first_run_on_an_empty_database_pulls_everything_and_says_why(
        fresh_schema, service, monkeypatch):
    service.install(monkeypatch)
    conn = db.connect()
    db.apply_schema(conn)

    outcome = sync.sync(conn, now=NOW, log=lambda m: None)

    assert set(outcome["pulled"]) == set(LAYER_KEYS)
    for key in LAYER_KEYS:
        assert outcome["layers"][key]["baseline"] is None
        assert outcome["layers"][key]["reason"] == "source advanced"
        assert outcome["layers"][key]["reload"]["swapped"] is True
    conn.close()


def test_the_first_run_produces_the_retained_lists_and_the_per_type_figures(
        fresh_schema, service, monkeypatch):
    """The derived steps run on a first pull too, against a mirror that has never
    held a previous version to compare with."""
    from mirror import violation_types

    service.install(monkeypatch)
    conn = db.connect()
    db.apply_schema(conn)

    outcome = sync.sync(conn, now=NOW, log=lambda m: None)

    assert len(outcome["snapshots"]) == len(violation_types.CANONICAL_TYPES)
    assert all(s["ok"] for s in outcome["snapshots"])
    assert len(outcome["type_figures"]) == len(violation_types.CANONICAL_TYPES)
    assert conn.execute("SELECT count(*) FROM list_snapshots").fetchone()[0] \
        == len(violation_types.CANONICAL_TYPES)
    conn.close()


def test_a_second_run_after_the_bootstrap_is_a_quiet_poll(fresh_schema, service,
                                                          monkeypatch):
    """Bootstrap and update are the same operation: what the mirror already holds
    decides what is pulled, not which kind of run this is."""
    service.install(monkeypatch)
    conn = db.connect()
    db.apply_schema(conn)
    sync.sync(conn, now=NOW, log=lambda m: None)
    service.queries.clear()
    service.pages_served = 0

    outcome = sync.sync(conn, now=NOW + datetime.timedelta(days=1),
                        log=lambda m: None)

    assert outcome["pulled"] == []
    assert service.pages_served == 0
    conn.close()


def test_load_py_also_records_a_completed_load(clean_db, service, monkeypatch):
    """1.2: readiness reads `full_load_completed_at`, so a mirror filled by the
    manual loader must set it as the sync's reload does."""
    service.install(monkeypatch)
    for key in LAYER_KEYS:
        load.load_layer(clean_db, source.LAYERS[key], log=lambda m: None)

    rows = dict(clean_db.execute(
        "SELECT layer, full_load_completed_at FROM layer_state").fetchall())
    assert set(rows) == set(LAYER_KEYS)
    assert all(v is not None for v in rows.values())


def test_load_py_leaves_the_column_unset_for_a_layer_that_did_not_finish(
        clean_db, service, monkeypatch):
    """A partial load is not a completed one, or readiness would report a half-filled
    mirror as ready."""
    service.install(monkeypatch)
    layer = source.LAYERS["service_requests"]

    load.load_layer(clean_db, layer, page_size=3, limit_pages=1, log=lambda m: None)

    row = clean_db.execute(
        "SELECT full_load_completed_at FROM layer_state WHERE layer = %s",
        ("service_requests",)).fetchone()
    assert row is None or row[0] is None


def test_an_interrupted_first_load_resumes_where_it_stopped(fresh_schema, service,
                                                            monkeypatch):
    """1.3: the network dies part-way through the first load; the next run continues
    the staged copy from its last completed page instead of restarting."""
    service.install(monkeypatch)
    monkeypatch.setattr(source.LAYERS["service_requests"], "page_size", 3,
                        raising=False)
    conn = db.connect()
    db.apply_schema(conn)

    service.fail_after_pages = 2
    failed = sync.sync(conn, now=NOW, log=lambda m: None)
    assert failed["ok"] is False
    assert failed["errors"]["service_requests"].startswith("OSError")
    assert conn.execute("SELECT count(*) FROM service_requests").fetchone()[0] == 0

    service.fail_after_pages = None
    logged = []
    outcome = sync.sync(conn, now=NOW + datetime.timedelta(minutes=5),
                        log=logged.append)

    assert any("resuming the staged copy" in line for line in logged)
    assert outcome["layers"]["service_requests"]["reload"]["resumed"] is True
    assert conn.execute("SELECT count(*) FROM service_requests").fetchone()[0] \
        == len(service.rows["service_requests"])
    conn.close()


def test_a_poll_alone_is_not_a_baseline(clean_db, service, monkeypatch):
    """A poll records what it observed before any reload starts. That observation is
    not a pulled version: were it the baseline, a first load that died part-way would
    look already pulled, and the next run would report "nothing to do" against an
    empty mirror."""
    service.install(monkeypatch)
    layer = source.LAYERS["service_requests"]

    polled = sync.poll_layer(clean_db, layer, NOW)

    assert polled["baseline"] is None and polled["advanced"] is True
    assert sync.last_pulled_source_edit(clean_db, "service_requests") is None


def test_a_layer_that_completed_a_load_keeps_its_baseline_without_a_reload_row(
        clean_db, service, monkeypatch):
    """The fallback exists for a mirror loaded by `load.py` before reloads recorded
    their source timestamp: it must still stop the first sync reloading the static
    census layer for no reason."""
    service.install(monkeypatch)
    for key in LAYER_KEYS:
        load.load_layer(clean_db, source.LAYERS[key], log=lambda m: None)
    clean_db.execute("DELETE FROM sync_runs")
    clean_db.commit()

    outcome = sync.sync(clean_db, now=NOW, log=lambda m: None)

    assert outcome["pulled"] == []


def test_a_first_load_killed_mid_run_is_resumed_not_mistaken_for_done(
        fresh_schema, service, monkeypatch):
    """The process is killed, so nothing records a failure: the run's row is left
    unfinished and the last successful attempt on the layer is the poll's."""
    service.install(monkeypatch)
    monkeypatch.setattr(source.LAYERS["service_requests"], "page_size", 3,
                        raising=False)
    conn = db.connect()
    db.apply_schema(conn)
    service.fail_after_pages = 2

    class Killed(BaseException):
        """Not an Exception: `full_reload` records failures for those, and a killed
        process records nothing."""

    def die(*args, **kwargs):
        raise Killed()

    monkeypatch.setattr(service, "between_pages",
                        lambda: die() if service.pages_served >= 2 else None)
    with pytest.raises(Killed):
        sync.sync(conn, now=NOW, log=lambda m: None)
    conn.rollback()
    unfinished = conn.execute(
        "SELECT count(*) FROM sync_runs WHERE kind = 'reload' AND finished_at IS NULL"
    ).fetchone()[0]
    assert unfinished == 1

    service.fail_after_pages = None
    monkeypatch.setattr(service, "between_pages", None)
    logged = []
    sync.sync(conn, now=NOW + datetime.timedelta(minutes=5), log=logged.append)

    assert any("resuming the staged copy" in line for line in logged)
    assert conn.execute("SELECT count(*) FROM service_requests").fetchone()[0] \
        == len(service.rows["service_requests"])
    conn.close()
