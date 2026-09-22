## Why

On a fresh Railway deployment the application boots and then does nothing useful. `create_app()` only checks that the `PG*` variables are set; it never applies the schema, so every database-backed route fails until someone runs `python3 src/mirror/load.py` by hand, waits about 25 minutes for the 1,637 pages, and then arranges for `python3 src/mirror/sync.py` to run on some schedule. The repository ships the commands but no runner for them: the nightly workflow was removed and `README.md` says "scheduled work moves to the deployment that serves the application", which today means nobody.

The sync itself is already most of the answer. `sync.py` applies the schema, and with no recorded baseline it treats every layer as advanced and reloads it in full, so bootstrap and update are the same command. What is missing is a trigger that fires at deploy and then recurs, protection against two runs colliding (the reload begins with `DROP TABLE IF EXISTS` on its staging table and nothing serialises runs), and an honest state for the 25 minutes in which the mirror is empty.

## What Changes

- **Add a mirror worker**, a long-running process that applies the schema, runs one sync immediately, then sleeps until the mirror's own next-due time and syncs again. It is deployed as a second Railway service beside the web service. No Redis, Celery or Huey: the scheduling state already lives in Postgres (`layer_state.next_due_at`, `sync_runs`), and a queue would only duplicate it.
- **Serialise sync runs with a Postgres session-level advisory lock.** The worker, `sync.py`, `load.py --restart` and `sync.py --reload` all take it; a run that cannot get it stops with a message instead of touching the staging table. This covers overlapping Railway deployments, a manual reload while the worker runs, and any future second caller.
- **Apply the schema once, under that lock, outside the web workers.** The web service does not run DDL. Concurrent `CREATE TABLE IF NOT EXISTS` from several gunicorn workers can still fail; the worker (and the manual `sync.py` and `load.py` commands, which take the same lock) is the only place the schema is applied. A pre-deploy step on the web service was considered and rejected (design D4, schema applied by the worker).
- **Give the served application an explicit "mirror is not ready" state.** With the schema absent, or present but empty, or mid first load, the application says which of those it is and does not present empty lists as a finding of no doorways. A schema that does not exist yet no longer produces a 500. While not ready the page shows one message in place of the lists and map, and refreshes itself when the mirror becomes ready.
- **Fix the failure policy:** the next scheduled attempt is the retry. A failed sync is recorded, and the worker waits a fixed backoff before trying again; nothing retries in a tight loop against HRM. A layer that fails no longer silently skips the layers after it without saying so.
- **Document the Railway service setup** (worker service, start commands, restart policy, the schema step) in the README. Deployment configuration files are deliberately not committed; see the design.
- **Retire the manual first-run instruction** in `README.md` ("The mirror is loaded and kept current by `load.py` and `sync.py`") in favour of the worker.

No **BREAKING** changes: `load.py` and `sync.py` keep their flags and remain the manual tools. They gain the lock.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hrm-data-mirror`: "Run unattended" widens from a sync that can run unattended to a mirror that initialises itself, bootstraps and keeps itself current with no human step. New requirements: initialise an empty store without intervention; never run two syncs at once; retry by schedule rather than by loop; and tell the difference between a first load in progress and a sync that has stopped.
- `hosted-triage-app`: new requirement to state that the mirror is not ready (uninitialised, loading, or awaiting its first load) instead of rendering an empty result, and never to show the overdue state for a mirror that has not yet had a first load.

## Impact

- **Code:** new `src/mirror/worker.py` (the loop); `src/mirror/db.py` (the advisory lock and a schema-present check); `src/mirror/sync.py` and `src/mirror/load.py` (take the lock in `main()`; `sync()` failure handling per layer); `src/mirror/status.py` and `src/app/server.py` (a mirror state on `/api/freshness` and a non-500 answer when the schema is absent); `web/app/index.html` (the banner wording for the new states).
- **Tests:** lock exclusion (two connections, one wins), worker loop and backoff with an injected clock, bootstrap against an empty schema end to end with the source stubbed, and every route against schema-absent, schema-empty and loading.
- **Dependencies:** none added. Standard library `time`/`signal` and the existing `psycopg`.
- **Operational:** the Railway deployment is done by hand by the repository owner and is not part of this change's implementation tasks. It needs a second always-on Railway service to create and pay for (a mostly idle Python process). The Postgres plan needs headroom for roughly two copies of the data during a reload (about 375 MB each plus WAL). The first load time from a cloud host and HRM's rate limits have not been measured; the design records them as open.
- **Specs:** deltas against `hrm-data-mirror` and `hosted-triage-app`. Nothing in the archived `mirror-hrm-data-and-host-app` decisions is overturned; its M12 ("ships two commands, one to serve and one to sync, so any host that can run a web process and a scheduled command can run it") is extended, not replaced: the worker is a third command, still host-agnostic, and Railway specifics stay in documentation.
