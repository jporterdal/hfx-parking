"""The command-line entry points take the sync lock (change
automate-mirror-bootstrap-and-sync, task 2.3, and 2.4 for the library functions).

`sync.py` and `load.py` are what an operator runs by hand, possibly while the worker is
part-way through a 25-minute reload. Refused, they must touch nothing.
"""

import sys

import pytest

from mirror import db as mirror_db
from mirror import load, source, sync
from test_sync import BASE_DATE, field_feature, request_feature, service  # noqa: F401

SYNC_WRITING_MODES = [
    [], ["--poll-only"], ["--force-pull"], ["--reload"],
    ["--reload", "--layer", "service_requests"],
]
LOAD_WRITING_MODES = [[], ["--restart"], ["--layer", "census_areas"]]


@pytest.fixture
def held(clean_db):
    """Another connection holding the lock, as a worker part-way through a run would."""
    holder = mirror_db.connect()
    lock = mirror_db.sync_lock(holder)
    lock.__enter__()
    yield holder
    lock.__exit__(None, None, None)
    holder.close()


def snapshot_of_state(conn):
    """Everything a refused run must leave alone: the run history and any staging."""
    runs = conn.execute("SELECT count(*) FROM sync_runs").fetchone()[0]
    progress = conn.execute("SELECT count(*) FROM load_progress").fetchone()[0]
    staged = [t for (t,) in conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name LIKE '%%\\_\\_staging'",
        (mirror_db.schema(),)).fetchall()]
    conn.rollback()
    return runs, progress, staged


@pytest.mark.parametrize("flags", SYNC_WRITING_MODES, ids=lambda f: " ".join(f) or "plain")
def test_sync_py_refuses_while_another_run_holds_the_lock(held, clean_db, service,
                                                          monkeypatch, capsys, flags):
    service.install(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["sync.py", *flags])
    before = snapshot_of_state(clean_db)

    status = sync.main()

    assert status == mirror_db.EXIT_SYNC_IN_PROGRESS != 0
    assert "already running" in capsys.readouterr().err
    assert snapshot_of_state(clean_db) == before
    assert service.queries == [] and service.metadata_reads == []   # not even a poll


@pytest.mark.parametrize("flags", LOAD_WRITING_MODES, ids=lambda f: " ".join(f) or "plain")
def test_load_py_refuses_while_another_run_holds_the_lock(held, clean_db, service,
                                                          monkeypatch, capsys, flags):
    service.install(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["load.py", *flags])
    before = snapshot_of_state(clean_db)

    status = load.main()

    assert status == mirror_db.EXIT_SYNC_IN_PROGRESS != 0
    assert "already running" in capsys.readouterr().err
    assert snapshot_of_state(clean_db) == before
    assert service.queries == []


def test_restart_does_not_discard_progress_when_refused(held, clean_db, service,
                                                        monkeypatch):
    """`--restart` deletes `load_progress`. Refused, that must not happen."""
    service.install(monkeypatch)
    clean_db.execute(
        "INSERT INTO load_progress (layer, page_size, next_offset) "
        "VALUES ('service_requests', 3, 6)")
    clean_db.commit()
    monkeypatch.setattr(sys, "argv", ["load.py", "--restart"])

    assert load.main() == mirror_db.EXIT_SYNC_IN_PROGRESS

    assert clean_db.execute(
        "SELECT next_offset FROM load_progress WHERE layer = 'service_requests'"
    ).fetchone()[0] == 6


@pytest.mark.parametrize("flag", ["--history", "--versions"])
def test_the_read_only_sync_modes_do_not_take_or_wait_for_the_lock(held, clean_db,
                                                                   monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["sync.py", flag])

    assert sync.main() == 0


def test_the_load_report_does_not_take_or_wait_for_the_lock(held, clean_db,
                                                            service, monkeypatch):
    service.install(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["load.py", "--report"])

    assert load.main() == 0


@pytest.mark.parametrize("entry, argv", [(sync, ["sync.py"]), (load, ["load.py"])])
def test_the_entry_points_release_the_lock_when_they_finish(clean_db, service,
                                                            monkeypatch, entry, argv):
    service.install(monkeypatch)
    monkeypatch.setattr(sys, "argv", argv)
    watcher = mirror_db.connect()

    entry.main()

    assert not mirror_db.sync_lock_held(watcher)
    watcher.close()


def test_the_library_functions_take_no_lock_of_their_own(clean_db, service, monkeypatch):
    """2.4: tests (and the worker, which takes the lock itself) call `sync()` and
    `load_layer()` freely, on many connections in one process. If either took the lock,
    a caller that already held it would be refused by its own callee."""
    service.install(monkeypatch)
    holder = mirror_db.connect()
    with mirror_db.sync_lock(holder):
        conn = mirror_db.connect()
        load.load_layer(conn, source.LAYERS["census_areas"], log=lambda m: None)
        sync.sync(conn, log=lambda m: None)
        assert mirror_db.sync_lock_held(conn)       # still the holder's, untouched
        conn.close()
    holder.close()
