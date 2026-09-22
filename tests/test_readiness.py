"""The mirror's readiness (change automate-mirror-bootstrap-and-sync, task 5.1).

Four states, told apart by what is actually true: whether the structure exists, whether
every layer has completed a load, and whether another session is running a sync right
now. The killed-run case is the one that matters most: a first load that died must read
as awaiting a load, not as loading for ever.
"""

import contextlib
import datetime
import time

import pytest

from mirror import db as mirror_db
from mirror import source, status, sync
from test_sync import BASE_DATE, field_feature, request_feature, service  # noqa: F401

NOW = datetime.datetime(2026, 9, 22, 12, 0, tzinfo=datetime.UTC)
LAYERS = ("service_requests", "custom_fields", "census_areas")


def complete(conn, *layers):
    for key in layers:
        sync.touch_layer(conn, key, NOW, True, full_load_completed_at=NOW,
                         last_success_at=NOW)


@pytest.fixture
def other():
    """A second session, the one whose lock the asker can see."""
    conn = mirror_db.connect()
    yield conn
    if not conn.closed:
        conn.close()


def test_with_no_structure_the_mirror_is_uninitialised(db, monkeypatch):
    monkeypatch.setenv("HFX_MIRROR_SCHEMA", "mirror_that_was_never_created")
    conn = mirror_db.connect()

    assert status.mirror_readiness(conn) == status.UNINITIALISED == "uninitialised"
    conn.close()


def test_structure_but_nothing_loaded_and_nothing_running_is_awaiting_a_first_load(
        clean_db):
    assert status.mirror_readiness(clean_db) == status.AWAITING_FIRST_LOAD


def test_structure_nothing_loaded_and_a_sync_running_is_loading(clean_db, other):
    with mirror_db.sync_lock(other):
        assert status.mirror_readiness(clean_db) == status.LOADING == "loading"


def test_every_layer_completed_is_ready(clean_db):
    complete(clean_db, *LAYERS)

    assert status.mirror_readiness(clean_db) == status.READY == "ready"


@pytest.mark.parametrize("missing", LAYERS)
def test_one_layer_short_of_complete_is_not_ready(clean_db, missing):
    """The analysis reads all three; a mirror with two is a half-filled one."""
    complete(clean_db, *[k for k in LAYERS if k != missing])

    assert status.mirror_readiness(clean_db) == status.AWAITING_FIRST_LOAD


def test_a_poll_alone_does_not_make_a_mirror_ready(clean_db, service, monkeypatch):
    """A poll writes the layer's row and a success time before anything is loaded. Were
    either read as readiness, a first load that had only got as far as polling would
    look done."""
    service.install(monkeypatch)
    for key in LAYERS:
        sync.poll_layer(clean_db, source.LAYERS[key], NOW)

    assert clean_db.execute("SELECT count(*) FROM layer_state").fetchone()[0] == 3
    assert status.mirror_readiness(clean_db) == status.AWAITING_FIRST_LOAD


def test_a_completed_mirror_stays_ready_while_a_reload_runs(clean_db, other):
    """The reload is not a state of its own: the version held stays whole and
    queryable until the swap."""
    complete(clean_db, *LAYERS)

    with mirror_db.sync_lock(other):
        assert status.mirror_readiness(clean_db) == status.READY


def test_a_killed_first_load_reads_as_awaiting_not_loading(db, clean_db):
    """The lock, not a record, is the truth about "running": kill the process doing the
    first load and the state must fall back to awaiting, not stay loading for ever."""
    holder = mirror_db.connect()
    stack = contextlib.ExitStack()
    stack.enter_context(mirror_db.sync_lock(holder))
    assert status.mirror_readiness(clean_db) == status.LOADING

    db.execute("SELECT pg_terminate_backend(%s)", (holder.info.backend_pid,))
    db.commit()

    deadline = time.monotonic() + 5
    state = status.LOADING
    while state == status.LOADING and time.monotonic() < deadline:
        clean_db.rollback()
        state = status.mirror_readiness(clean_db)
        time.sleep(0.05)
    assert state == status.AWAITING_FIRST_LOAD
    stack.close()


def test_a_run_that_left_an_unfinished_row_behind_is_not_loading(clean_db):
    """A `sync_runs` row with no finish time is what a killed run leaves. Nothing holds
    the lock, so it is not loading."""
    clean_db.execute("INSERT INTO sync_runs (kind, layer) VALUES ('reload', "
                     "'service_requests')")
    clean_db.commit()

    assert status.mirror_readiness(clean_db) == status.AWAITING_FIRST_LOAD


def test_a_mirror_loaded_through_the_sync_is_ready(clean_db, service, monkeypatch):
    service.install(monkeypatch)
    sync.sync(clean_db, now=NOW, log=lambda m: None)

    assert status.mirror_readiness(clean_db) == status.READY
