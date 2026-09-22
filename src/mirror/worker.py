"""The mirror worker: initialise the store, populate it, and keep it current.

One long-running process, run as its own service beside the web service:

    python3 src/mirror/worker.py

Everything it does is `sync.sync()`. That function already treats a layer with no
completed load as advanced, so the first cycle against an empty database *is* the
initial load, and every later cycle is the ordinary poll. The worker only decides
when to run it and what to do when it cannot.

Each cycle, under the mirror's sync lock (`db.sync_lock`):

    apply the schema  ->  sync  ->  work out how long to sleep

and the sleep is one of

    after a success   until the earliest `layer_state.next_due_at`, so the "next
                      update" the page shows and the moment the worker runs are one
                      number; never less than MIN_DELAY, never more than POLL_INTERVAL
    after a failure   FAILURE_BACKOFF, whatever `next_due_at` says. A failed poll
                      leaves it in the past, and reading it would retry in a tight
                      loop against a source that is already having a bad day
    lock was busy     LOCK_BUSY_RETRY. Someone else is syncing; nothing is recorded as
                      a failure, because nothing failed

The next scheduled attempt is the retry: nothing here retries in a loop.

A connection is opened for the cycle and closed at the end of it. The lock lives as
long as the connection, so it lives as long as the run, and a connection held through
a day-long sleep would meet whatever idle timeout the platform puts in the way.

An exception inside a cycle is caught, logged and turned into a failure backoff. The
only thing that ends the process is a missing `PG*` variable at start, before the
loop begins, so a platform restart policy cannot turn a transient error into a hot
crash loop. A process killed mid-reload (a redeploy sends SIGTERM) is safe: a reload
commits per page and swaps in one transaction, so the held version stays whole and the
next cycle resumes the staged copy.

Host-agnostic: it reads the same `PG*` variables as everything else and names no host.
"""

import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror import db, sync  # noqa: E402

# The shortest the worker ever sleeps. Also the floor under any retry: nothing here
# starts a second attempt sooner than this after the first.
MIN_DELAY = datetime.timedelta(minutes=5)

# After a failed run. Long enough not to meet an outage or a rate limit with a burst,
# short enough that a first load that failed for a transient reason is tried again the
# same day.
FAILURE_BACKOFF = datetime.timedelta(hours=1)

# After finding another run in progress.
LOCK_BUSY_RETRY = datetime.timedelta(minutes=5)

# What the process exits with when the database is not configured (sysexits' EX_CONFIG).
EXIT_NOT_CONFIGURED = 78


def log(message):
    stamp = sync.utcnow().isoformat(timespec="seconds")
    print(f"{stamp} worker: {message}", file=sys.stderr, flush=True)


def delay_after_success(conn, now):
    """Sleep until the mirror's own promise of when it is next due."""
    due = conn.execute("SELECT min(next_due_at) FROM layer_state").fetchone()[0]
    if due is None:
        return sync.POLL_INTERVAL
    return max(MIN_DELAY, min(sync.POLL_INTERVAL, due - now))


def cycle(connect, run_sync, now, log=log):
    """One pass. Returns how long to sleep before the next, and never raises an
    `Exception`."""
    conn = None
    try:
        conn = connect()
        with db.sync_lock(conn):
            db.apply_schema(conn)
            outcome = run_sync(conn, log=log)
            if outcome["ok"]:
                return delay_after_success(conn, now())
        for layer, error in sorted(outcome["errors"].items()):
            log(f"{layer} failed: {error}")
        return FAILURE_BACKOFF
    except db.SyncInProgress:
        log("another sync, load or reload is in progress; looking again later")
        return LOCK_BUSY_RETRY
    except Exception as exc:
        log(f"cycle failed: {type(exc).__name__}: {exc}")
        return FAILURE_BACKOFF
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def run(connect=None, run_sync=None, now=None, sleep=None, log=log, max_cycles=None):
    """Cycle, sleep, repeat. Forever unless `max_cycles` says otherwise (tests)."""
    connect = connect or db.connect
    run_sync = run_sync or (lambda conn, log: sync.sync(conn, log=log))
    now = now or sync.utcnow
    sleep = sleep or time.sleep

    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        delay = cycle(connect, run_sync, now, log)
        cycles += 1
        log(f"next attempt in {delay}")
        sleep(delay.total_seconds())


def main():
    try:
        db.check_configured()
    except db.ConfigurationError as exc:
        print(f"worker: {exc}", file=sys.stderr)
        return EXIT_NOT_CONFIGURED
    log("starting")
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
