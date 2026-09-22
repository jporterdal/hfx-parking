"""The sync lock (change automate-mirror-bootstrap-and-sync, tasks 2.1 and 2.2).

At most one sync, load or reload may run against a mirror at a time. These tests use
real second connections, because the property is about sessions: what a lock does when
its holder commits, rolls back, fails or is killed cannot be shown with a stub.
"""

import contextlib
import time

import pytest

from conftest import TEST_SCHEMA
from mirror import db as mirror_db
from mirror.db import SyncInProgress, sync_lock, sync_lock_held


@pytest.fixture
def conns(db):
    """Two fresh connections to the throwaway schema, closed afterwards."""
    opened = [mirror_db.connect(), mirror_db.connect()]
    yield opened
    for c in opened:
        if not c.closed:
            c.close()


def wait_until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_with_two_connections_one_wins_and_the_other_is_refused(conns):
    first, second = conns
    with sync_lock(first):
        with pytest.raises(SyncInProgress) as refused:
            with sync_lock(second):
                pytest.fail("the second run must not enter the block")
        assert "already running" in str(refused.value)
        assert "nothing was changed" in str(refused.value)


def test_the_lock_is_free_again_once_the_holder_leaves_the_block(conns):
    first, second = conns
    with sync_lock(first):
        pass

    with sync_lock(second):
        assert sync_lock_held(first)


def test_a_refused_run_does_not_wait(conns):
    first, second = conns
    with sync_lock(first):
        started = time.monotonic()
        with pytest.raises(SyncInProgress):
            with sync_lock(second):
                pass
        assert time.monotonic() - started < 1.0


def test_commit_and_rollback_on_the_holder_do_not_release_the_lock(conns):
    """A reload commits per page and rolls back on failure. A transaction-level lock
    would drop at the first of those; this one must not."""
    first, second = conns
    with sync_lock(first):
        first.execute("SELECT 1")
        first.commit()
        assert sync_lock_held(second)
        first.execute("SELECT 1")
        first.rollback()
        assert sync_lock_held(second)
        with pytest.raises(SyncInProgress):
            with sync_lock(second):
                pass


def test_killing_the_holder_frees_the_lock_with_no_cleanup(db, conns):
    """A dead run must not block every later one (spec: the exclusion disappears
    when the run holding it ends for any reason)."""
    holder, other = conns
    pid = holder.info.backend_pid
    stack = contextlib.ExitStack()
    stack.enter_context(sync_lock(holder))
    assert sync_lock_held(other)

    db.execute("SELECT pg_terminate_backend(%s)", (pid,))
    db.commit()

    assert wait_until(lambda: not sync_lock_held(other))
    with sync_lock(other):
        pass
    stack.close()      # the holder's own release finds its session gone, and is quiet


def test_a_connection_that_dies_inside_the_block_does_not_hide_the_real_error(db, conns):
    """The caller is handling some error; the release must not replace it with
    "terminating connection due to administrator command"."""
    holder, other = conns
    pid = holder.info.backend_pid

    with pytest.raises(RuntimeError, match="the real error"):
        with sync_lock(holder):
            db.execute("SELECT pg_terminate_backend(%s)", (pid,))
            db.commit()
            assert wait_until(lambda: not sync_lock_held(other))
            raise RuntimeError("the real error")


def test_an_exception_in_the_block_releases_the_lock(conns):
    first, second = conns
    with pytest.raises(RuntimeError, match="boom"):
        with sync_lock(first):
            raise RuntimeError("boom")

    assert not sync_lock_held(second)


def test_a_failed_statement_in_the_block_does_not_stop_the_lock_being_released(conns):
    """After a database error the transaction is aborted and refuses every statement,
    including the unlock. The release must roll back first."""
    import psycopg

    first, second = conns
    with pytest.raises(psycopg.errors.UndefinedTable):
        with sync_lock(first):
            first.execute("SELECT * FROM a_table_that_does_not_exist")

    assert not sync_lock_held(second)


def test_leaving_the_block_never_commits_what_the_block_left_half_done(db, conns):
    """`sync_lock` commits when it acquires, so that `connect()`'s SET is permanent. On
    the way out it must not commit anything the run itself left pending."""
    first, second = conns
    db.execute("CREATE TABLE lock_probe (x int)")
    db.commit()
    try:
        with pytest.raises(RuntimeError):
            with sync_lock(first):
                first.execute("INSERT INTO lock_probe VALUES (1)")
                raise RuntimeError("died with a write pending")

        assert second.execute("SELECT count(*) FROM lock_probe").fetchone()[0] == 0
    finally:
        # both connections still hold locks on the table until their transactions end
        # (the holder's is the pending write the lock deliberately did not commit),
        # and the DROP would wait on them for ever
        first.rollback()
        second.rollback()
        db.execute("DROP TABLE IF EXISTS lock_probe")
        db.commit()


def test_the_lock_protects_one_mirror_not_the_whole_database(conns, monkeypatch):
    """A test schema and the loaded mirror live in one database. Running the suite must
    not be blocked by (nor block) a real sync of the real mirror."""
    first, second = conns
    with sync_lock(first):
        monkeypatch.setenv("HFX_MIRROR_SCHEMA", f"{TEST_SCHEMA}_another_mirror")
        assert not sync_lock_held(second)
        with sync_lock(second):
            pass


def test_sync_lock_held_reports_another_session_and_never_the_asker(conns):
    first, second = conns
    assert not sync_lock_held(second)

    with sync_lock(first):
        assert sync_lock_held(second)
        assert not sync_lock_held(first)     # "another backend", not "any backend"

    assert not sync_lock_held(second)


def test_sync_lock_held_is_false_after_the_holding_connection_closes(conns):
    first, second = conns
    stack = contextlib.ExitStack()
    stack.enter_context(sync_lock(first))
    assert sync_lock_held(second)

    first.close()

    assert wait_until(lambda: not sync_lock_held(second))
    stack.close()


def test_sync_lock_held_leaves_the_askers_transaction_as_it_found_it(conns):
    first, second = conns
    second.commit()
    with sync_lock(first):
        sync_lock_held(second)
        # a read only: nothing for the caller to commit or roll back is left behind
        second.rollback()
        assert second.execute("SELECT 1").fetchone()[0] == 1
