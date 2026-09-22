"""The mirror worker (change automate-mirror-bootstrap-and-sync, tasks 4.1 to 4.3).

The loop is driven with a fake clock and a fake sleep, so no test waits, and with a
stub in place of `sync.sync` wherever the question is about scheduling rather than
about syncing.
"""

import contextlib
import datetime
import os
import subprocess
import sys

import psycopg
import pytest

from mirror import db, sync, worker
from test_bootstrap import fresh_schema  # noqa: F401
from test_sync import BASE_DATE, field_feature, request_feature, service  # noqa: F401

T0 = datetime.datetime(2026, 9, 22, 12, 0, tzinfo=datetime.UTC)
MINUTE = datetime.timedelta(minutes=1)
HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)


class FakeClock:
    """A clock that only moves when the worker sleeps."""

    def __init__(self, start=T0):
        self.current = start
        self.slept = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.slept.append(datetime.timedelta(seconds=seconds))
        self.current += datetime.timedelta(seconds=seconds)


def ok_sync(conn, log):
    return {"ok": True, "errors": {}}


def failed_sync(conn, log):
    return {"ok": False, "errors": {"service_requests": "OSError: connection reset"}}


def set_due(conn, due, layers=("service_requests", "custom_fields", "census_areas")):
    for key in layers:
        sync.touch_layer(conn, key, T0, True, next_due_at=due)


def quiet(message):
    pass


# ------------------------------------------------------------------ the delay after


def test_after_a_success_the_worker_sleeps_until_the_mirrors_own_next_due_time(clean_db):
    set_due(clean_db, T0 + 20 * HOUR)
    clock = FakeClock()

    worker.run(run_sync=ok_sync, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=1)

    assert clock.slept == [20 * HOUR]


def test_the_earliest_due_layer_decides(clean_db):
    set_due(clean_db, T0 + 20 * HOUR)
    set_due(clean_db, T0 + 6 * HOUR, layers=("custom_fields",))
    clock = FakeClock()

    worker.run(run_sync=ok_sync, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=1)

    assert clock.slept == [6 * HOUR]


@pytest.mark.parametrize("due", [T0 - DAY, T0, T0 + 3 * MINUTE],
                         ids=["long past", "now", "three minutes away"])
def test_a_success_never_sleeps_less_than_five_minutes(clean_db, due):
    set_due(clean_db, due)
    clock = FakeClock()

    worker.run(run_sync=ok_sync, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=1)

    assert clock.slept == [worker.MIN_DELAY]


def test_a_success_never_sleeps_longer_than_the_poll_interval(clean_db):
    set_due(clean_db, T0 + 30 * DAY)              # a wildly wrong clock somewhere
    clock = FakeClock()

    worker.run(run_sync=ok_sync, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=1)

    assert clock.slept == [sync.POLL_INTERVAL]


def test_with_no_recorded_due_time_the_worker_sleeps_one_poll_interval(clean_db):
    clock = FakeClock()

    worker.run(run_sync=ok_sync, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=1)

    assert clock.slept == [sync.POLL_INTERVAL]


# ---------------------------------------------------------------- the delay after not


def test_after_a_failure_the_backoff_is_fixed_whatever_the_due_time_says(clean_db):
    """A failed poll leaves `next_due_at` in the past. Reading it after a failure would
    put the loop in a tight retry against a source that is already failing."""
    set_due(clean_db, T0 - 3 * DAY)
    clock = FakeClock()
    logged = []

    worker.run(run_sync=failed_sync, now=clock.now, sleep=clock.sleep,
               log=logged.append, max_cycles=1)

    assert clock.slept == [worker.FAILURE_BACKOFF] == [HOUR]
    assert any("service_requests failed: OSError" in line for line in logged)


def test_a_busy_lock_is_not_a_failure_and_runs_nothing(clean_db):
    holder = db.connect()
    calls = []
    clock = FakeClock()
    runs_before = clean_db.execute("SELECT count(*) FROM sync_runs").fetchone()[0]

    with db.sync_lock(holder):
        worker.run(run_sync=lambda conn, log: calls.append(1) or ok_sync(conn, log),
                   now=clock.now, sleep=clock.sleep, log=quiet, max_cycles=1)
    holder.close()

    assert clock.slept == [worker.LOCK_BUSY_RETRY] == [5 * MINUTE]
    assert calls == []
    assert clean_db.execute("SELECT count(*) FROM sync_runs").fetchone()[0] == runs_before
    assert clean_db.execute("SELECT count(*) FROM layer_state").fetchone()[0] == 0


def test_an_exception_inside_a_cycle_is_caught_logged_and_backed_off(clean_db):
    def explode(conn, log):
        raise ValueError("something nobody planned for")

    clock, logged = FakeClock(), []
    watcher = db.connect()

    worker.run(run_sync=explode, now=clock.now, sleep=clock.sleep, log=logged.append,
               max_cycles=1)

    assert clock.slept == [HOUR]
    assert any("ValueError: something nobody planned for" in line for line in logged)
    assert not db.sync_lock_held(watcher)             # the lock did not leak
    watcher.close()


def test_a_database_that_cannot_be_reached_is_a_failure_backoff_not_a_crash():
    def refuse():
        raise psycopg.OperationalError("connection refused")

    clock, logged = FakeClock(), []

    worker.run(connect=refuse, run_sync=ok_sync, now=clock.now, sleep=clock.sleep,
               log=logged.append, max_cycles=1)

    assert clock.slept == [HOUR]
    assert any("connection refused" in line for line in logged)


def test_the_loop_carries_on_after_a_cycle_raises(clean_db):
    attempts = []

    def flaky(conn, log):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("first one dies")
        return {"ok": True, "errors": {}}

    set_due(clean_db, T0 + 10 * HOUR)
    clock = FakeClock()

    worker.run(run_sync=flaky, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=3)

    assert len(attempts) == 3
    assert clock.slept[0] == HOUR


def test_the_lock_is_released_at_the_end_of_every_cycle(clean_db):
    watcher = db.connect()
    clock = FakeClock()
    seen = []

    def spy(conn, log):
        seen.append(db.sync_lock_held(watcher))       # held while the sync runs
        return {"ok": True, "errors": {}}

    worker.run(run_sync=spy, now=clock.now, sleep=clock.sleep, log=quiet, max_cycles=2)

    assert seen == [True, True]
    assert not db.sync_lock_held(watcher)
    watcher.close()


# ------------------------------------------------------------- 4.2 the first cycle


def test_the_first_cycle_locks_then_applies_the_schema_then_syncs_and_populates(
        fresh_schema, service, monkeypatch):
    """A deployment against a database that has never held the mirror: the schema does
    not exist, and by the time the worker sleeps, every layer is loaded."""
    service.install(monkeypatch)
    events = []
    real_lock, real_apply = db.sync_lock, db.apply_schema

    @contextlib.contextmanager
    def spy_lock(conn):
        events.append("lock")
        with real_lock(conn):
            yield
        events.append("unlock")

    def spy_apply(conn):
        events.append("schema")
        return real_apply(conn)

    def spy_sync(conn, log):
        events.append("sync")
        return sync.sync(conn, now=T0, log=log)

    monkeypatch.setattr(db, "sync_lock", spy_lock)
    monkeypatch.setattr(db, "apply_schema", spy_apply)
    clock = FakeClock()

    worker.run(run_sync=spy_sync, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=1)

    assert events == ["lock", "schema", "sync", "unlock"]
    probe = db.connect()
    state = dict(probe.execute(
        "SELECT layer, full_load_completed_at IS NOT NULL FROM layer_state").fetchall())
    assert state == {"service_requests": True, "custom_fields": True,
                     "census_areas": True}
    # the first poll promised the next update one interval out, and the worker sleeps
    # until exactly then
    assert clock.slept == [sync.POLL_INTERVAL]
    probe.close()


def test_a_later_cycle_against_a_populated_mirror_is_a_quiet_poll(fresh_schema, service,
                                                                  monkeypatch):
    """Bootstrap and update are the same operation: the second cycle is not told it is
    a second cycle, it just finds nothing has moved."""
    service.install(monkeypatch)
    clock = FakeClock()
    pages_per_cycle = []

    def spy(conn, log):
        before = service.pages_served
        outcome = sync.sync(conn, now=clock.now(), log=log)
        pages_per_cycle.append(service.pages_served - before)
        return outcome

    worker.run(run_sync=spy, now=clock.now, sleep=clock.sleep, log=quiet, max_cycles=2)

    assert pages_per_cycle[0] > 0            # the first cycle loaded everything
    assert pages_per_cycle[1] == 0           # the second only looked


# --------------------------------------------------------------- 4.3 the retry floor


def test_no_retry_ever_starts_sooner_than_five_minutes_after_the_last(clean_db):
    starts = []
    clock = FakeClock()

    def always_fails(conn, log):
        starts.append(clock.now())
        return {"ok": False, "errors": {"census_areas": "OSError: down"}}

    worker.run(run_sync=always_fails, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=8)

    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 8
    assert all(gap >= worker.MIN_DELAY for gap in gaps)
    assert set(gaps) == {worker.FAILURE_BACKOFF}      # in fact a fixed hour, not a burst


def test_no_retry_floor_is_undercut_by_a_busy_lock_or_an_exception(clean_db):
    holder = db.connect()
    starts = []
    clock = FakeClock()

    def noting(conn, log):
        starts.append(clock.now())
        raise OSError("down")

    with db.sync_lock(holder):
        worker.run(run_sync=noting, now=clock.now, sleep=clock.sleep, log=quiet,
                   max_cycles=3)
    holder.close()
    assert starts == []                    # the busy lock never got as far as a sync
    assert all(slept >= worker.MIN_DELAY for slept in clock.slept)

    worker.run(run_sync=noting, now=clock.now, sleep=clock.sleep, log=quiet,
               max_cycles=4)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps and all(gap >= worker.MIN_DELAY for gap in gaps)


def test_every_constant_that_paces_the_worker_is_at_least_the_floor():
    assert worker.MIN_DELAY == 5 * MINUTE
    assert worker.FAILURE_BACKOFF >= worker.MIN_DELAY
    assert worker.LOCK_BUSY_RETRY >= worker.MIN_DELAY


# ------------------------------------------------------------------ starting up


def test_an_unconfigured_database_ends_the_process_before_the_loop_starts(monkeypatch,
                                                                           capsys):
    monkeypatch.setenv("PGHOST", "")
    monkeypatch.setattr(worker, "run", lambda *a, **k: pytest.fail("loop started"))

    assert worker.main() == worker.EXIT_NOT_CONFIGURED != 0

    assert "PGHOST" in capsys.readouterr().err


def test_the_worker_runs_as_a_script_and_exits_non_zero_when_unconfigured():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PGHOST": ""}
    done = subprocess.run([sys.executable, os.path.join(root, "src/mirror/worker.py")],
                          env=env, capture_output=True, text=True, timeout=60)

    assert done.returncode == worker.EXIT_NOT_CONFIGURED
    assert "PGHOST" in done.stderr
