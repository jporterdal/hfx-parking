## Context

See `proposal.md` (Why) for the motivation and `specs/hrm-data-mirror/spec.md` and `specs/hosted-triage-app/spec.md` for the required behaviour. This records how, and why these choices over the others.

What exists now, from reading the code and the Railway documentation:

- **`sync.py` already bootstraps.** `main()` calls `db.apply_schema()` first. `poll_layer` computes `advanced = baseline is None or observed > baseline`, and on an empty database `last_pulled_source_edit` finds no completed pull and no completed load, so the baseline is `None` and every layer is reloaded in full through `full_reload`. `tests/test_bootstrap.py` now drives `sync()` from a schema that does not exist; the older tests start from a seeded `mirrored` fixture.
- **The scheduling state is already in Postgres.** `layer_state.next_due_at` is set to `now + POLL_INTERVAL` on every successful poll, and `status.py`'s overdue logic, which the served page displays, is built on it. It is written only on success: a failed poll leaves the old value in the past.
- **Nothing serialises runs.** A grep of `src/` finds no advisory lock. `prepare_staging` begins `DROP TABLE IF EXISTS` on the staging table when it cannot resume, and `sync()` re-raises the first layer's exception, which skips the later layers and the two derived steps (`history.snapshot_after_sync`, `type_figures.after_sync`).
- **The web app never applies the schema.** `create_app()` calls only `db.check_configured()`. Before the schema exists a route raises from psycopg and Flask answers 500; the freshness route already tolerates "schema present, nothing synced" (`test_freshness_route_with_no_sync_history_does_not_crash`).
- **Railway facts** (docs.railway.com, read 2026-09-21):
  - A **cron service** runs its start command on a crontab schedule (UTC, minimum interval 5 minutes, start time varies by a few minutes), must exit when done, and a run is skipped if the previous one is still active. The documentation describes runs only "on the given schedule"; it does not say a cron service runs once at deploy.
  - A **pre-deploy command** runs in a separate container between build and deploy; if it fails the deployment does not proceed, and it has no time limit unless one is set (1 to 3600 seconds).
  - Deployments are zero-downtime: the previous deployment **overlaps** the new one for a configurable time before it is sent SIGTERM.
  - **Config as code** (`railway.toml`/`railway.json`) is marked deprecated in favour of Infrastructure as Code, with legacy services supported until 2026-12-01.
- **The archived design (M12, host-agnostic server shape)** ships "two commands, one to serve and one to sync" so any host with a web process and a scheduled command can run it, and keeps every host name out of the code.

## Goals / Non-Goals

**Goals:**

- A fresh Railway deployment reaches a populated, ready mirror with no operator step, and stays current.
- No two runs of the sync can interleave, whichever caller starts them.
- A viewer during the first load can tell "being filled" from "broken".
- Every host-specific detail stays in documentation; the code adds one more host-agnostic command.

**Non-Goals:**

- A job queue, broker or task framework. Redis+Huey and Redis+Celery were considered and rejected (D1).
- External alerting or paging when the worker stops. The overdue state is the existing signal; whether to add more is an open question.
- Schema versioning or destructive migrations. `schema.sql` stays additive and idempotent.
- Committing Railway configuration files (see D8).
- Changing the poll cadence, HRM request behaviour or the reload method. The reload path, its staging table and its resumability are used as they are.
- Removing the window in which a reader can see the two Cityworks layers at different versions during a reload. It exists today and is out of scope; D6 only stops derived products being built from it.

## Decisions

### D1. One long-running worker service, not cron and not a queue

The worker is a third command beside "serve" and "sync": `python3 src/mirror/worker.py`. Deployed as its own Railway service from the same repository, it applies the schema, syncs once at start, and then sleeps until the mirror's own next-due time.

The requirement that drove this is "an initial attempt at deploy". A Railway cron service is documented to fire on its schedule and does not document a run at deploy, so a daily cron would leave a fresh deployment empty for up to a day, and a frequent one would fire the reload path 24 hours a day for a mirror that changes about weekly. A worker starts when the service starts, which is both the bootstrap and every redeploy's catch-up poll.

Alternatives considered:

- **Railway cron running `sync.py`.** The obvious minimum: no new process code. Rejected as the primary because it has no defined first run. It stays a valid fallback for the recurrence: `sync.py` is unchanged and would work under cron if the worker proves troublesome, with the first run then needing a one-off trigger.
- **The worker in the web service's start command** (`python3 src/mirror/worker.py & exec gunicorn ...`). Railway runs a service's start command on every start and deploy, so this would work, and it saves one service. Rejected: the sync would live and die with the web container, so every web restart or crash would interrupt a load; each web replica would start its own worker (the lock (D3) makes that safe but not sensible); and sync failures would land in the web service's logs. Chaining with `&&` instead of backgrounding is worse: the web server would not start until the load finished, so there would be no loading page at all and a healthcheck would fail. The lock and the readiness state behave identically either way, so this stays available if a second service ever proves too much.
- **Redis + Huey.** A consumer with an embedded scheduler, a startup hook and a Redis service. Rejected: it adds a stateful service to hold scheduling state that `layer_state.next_due_at` and `sync_runs` already hold, and a crashed task is lost unless retries are configured.
- **Redis + Celery.** Worker plus beat plus Redis. Rejected for the same reason and one more: the Redis broker's default visibility timeout is one hour, and a reload that outlasts it under late acknowledgement is redelivered while still running. A first load took 25 minutes locally and has not been measured from Railway.
- **An in-process scheduler in the web process (APScheduler or a thread).** Rejected: gunicorn runs several workers, each would schedule its own run, and the web service would carry a 25-minute job.
- **Procrastinate or another Postgres-native queue.** Would work and needs no Redis. Rejected as more machinery than one recurring job needs; worth revisiting if more background work arrives.

### D2. The worker loop

```
start:   check_configured                     (the only thing that ends the process)
cycle:   open a connection -> take the sync lock (D3) -> apply_schema -> sync
         -> release -> close
delay:   after success   earliest layer_state.next_due_at - now, but at least 5 min
         after failure   FAILURE_BACKOFF (1 hour)
         lock was busy   LOCK_BUSY_RETRY (5 min), recorded as nothing
```

The schema is applied on every cycle, not only at start. It is idempotent and cheap, it is the same thing `sync.py` does on every run, and it means a schema change reaches a running worker and a table dropped by hand is recreated. The first cycle against an empty database is the initial load; there is no separate first-run path.

- **A connection per run, closed afterwards.** The lock lives as long as the connection, so it lives as long as the run. Holding one connection through a day-long sleep would meet whatever idle timeout the platform proxy applies.
- **Success sleeps until the mirror's own promise.** Because the delay is derived from `next_due_at`, the "next update" the page shows and the moment the worker actually runs are one number by construction, and the overdue grace (`NEXT_UPDATE_GRACE_PERIOD`, one day) still measures a missed schedule.
- **Failure ignores `next_due_at`.** A failed poll leaves it in the past, so reading it after a failure would put the loop in a tight retry, the very behaviour the retry requirement forbids. After a failure the worker uses the fixed backoff and nothing else. The backoff is one hour: long enough not to burst an outage, short enough that a first load which failed for a transient reason is retried the same day. It is a constant in one place, not a setting.
- **A per-cycle exception is caught, logged and turned into a failure backoff.** Only a configuration error at start exits the process. This keeps Railway's restart policy from becoming a hot crash loop.
- **No signal handling beyond the default.** On redeploy Railway sends SIGTERM and later SIGKILL. A run killed part-way is safe by construction: a reload commits per page and swaps in one transaction, so the held version stays whole and the next run resumes the staged copy (`prepare_staging`).
- **The clock and the sleep are injected**, so the loop is tested with no real waiting.

### D3. A session-level advisory lock, taken at the entry points

`db.py` gains one context manager that runs `pg_try_advisory_lock` on a two-integer key (a fixed namespace, and a number derived from the schema name, so the loaded mirror and a test schema in the same database do not contend) on the connection doing the work and releases it on exit; a second holder gets a `SyncInProgress` exception. Postgres releases a session lock when the session ends, so a killed run frees it with no cleanup, which the spec requires.

- **Taken at the entry points**, not inside `sync()` or `full_reload()`: the worker, `sync.py`'s `main()` and `load.py`'s `main()`. Tests call the library functions on many connections and should not have to contend for it. A test asserts that each entry point takes the lock.
- **Session-level, not transaction-level.** `full_reload` calls `conn.rollback()` on failure and commits per page, and neither touches a session lock, whereas a transaction-level lock would drop at the first commit.
- **Try, don't wait.** A blocked run that waited would pile up behind a 25-minute reload. The worker treats "busy" as "not this time"; a manual command exits non-zero and says a run is in progress. The lock does not record who holds it, so the message cannot name the holder.
- **Constraint:** the connection must reach Postgres directly or through a pooler in session mode. A transaction-mode pooler would break every session lock silently. Railway's Postgres service is expected to be reached directly, but that is not confirmed: the owner checks it from the connection the worker actually gets when deploying by hand (Migration Plan, checklist item 1).

Alternatives considered: a `sync_runs` row with `finished_at IS NULL` as the marker (rejected: a killed run leaves it behind and blocks every later run until someone deletes it, the exact failure the spec forbids); a lock file (rejected: the web and worker are separate containers with no shared filesystem).

### D4. The schema is applied by the worker and the manual commands, never by the web service

`apply_schema` is idempotent, but concurrent `CREATE TABLE IF NOT EXISTS` from several gunicorn workers can still fail on Postgres's catalog uniqueness, and a web worker that raced would take the app down at boot. So DDL runs in one place, under the lock: the worker's start, which is also where `sync.py` and `load.py` already call it.

A pre-deploy command on the web service was considered and rejected: it runs in a separate container, blocks the deployment on failure, and would duplicate what the worker does. Its only gain is that the schema exists before the web service starts, which D5 makes unnecessary by handling its absence.

### D5. Readiness is computed, and the lock is the truth about "running"

A `status.mirror_readiness(conn)` returns one of the four states in the spec (on the wire and in code as `uninitialised`, `awaiting_first_load`, `loading` and `ready`):

- **uninitialised**: `to_regclass` finds no `layer_state` table in the mirror schema.
- **ready**: every tracked layer, the census layer included, has `layer_state.full_load_completed_at` set.
- **loading** / **awaiting first load**: not ready, split by whether the advisory lock is currently held by another backend, read from `pg_locks` (which any role may read).

The lock, not a `sync_runs` row, decides "loading" for the reason given in D3: a record can outlive its run, a lock cannot. It also means a poll-only run in the first seconds shows as loading, which is accurate: something is running and nothing is loaded yet.

`full_load_completed_at` must be set by both paths that complete a layer (`full_reload` does; the task list includes confirming `load.py` does too) or `ready` never becomes true for a mirror loaded by hand.

### D6. What each surface does when the mirror is not ready

- **`/api/freshness`** carries the readiness state. `web/app/index.html` already fetches it for the banner, so the banner gains the not-ready wording and the page stops rendering an empty list as a result. The overdue branch is skipped for a mirror that has never completed a load.
- **Page routes** (`/`, `/types/<slug>`) serve the static page as now. They never touch the database for the shell, so they cannot fail on a missing schema.
- **Data routes** when the schema is absent answer 503 with a body naming the state, instead of a 500 from an unhandled psycopg error. When the schema exists but the mirror is not ready they keep answering 200 with what is there, as their tests assert today. The page, which reads `/api/freshness`, is what refuses to present it as a finding. This keeps the blast radius to the handler and the page; the alternative, 503 on every data route until ready, would change the behaviour and the fixtures of most of `tests/test_app.py` for a consumer (the page) that can be told the state directly.
- **Exports** state the readiness in their header when not ready, as they already state sync time.

What the page shows and how it recovers is D9.

The spec sentence "no route fails with a server error" therefore means no *unhandled* error: a 503 that names the state is a deliberate answer. The spec wording was tightened to match while writing this design.

### D7. A layer failure is isolated, and derived products need a consistent pair

`sync()` currently raises on the first layer failure, so a census outage would prevent the two Cityworks layers from being polled at all. It changes to attempt each layer in turn, collect a failure per layer in the outcome, and report the run as failed if any layer failed. `sync.py`'s `main()` exits non-zero and the worker applies the failure backoff.

The catch is that isolation makes a new state reachable: `service_requests` reloaded and `custom_fields` then failed. The archived design treats the pair as one publish event, and the retained lists and per-type figures (`history.snapshot_after_sync`, `type_figures.after_sync`) would describe a mixture. So those steps run only when no Cityworks layer failed in the run. One visible consequence: a run in which everything fails now records one failed poll per layer, three, where the first failure used to end the run at one. The mixture in the live tables is the pre-existing window (Non-Goals); the failure only makes it last until the next attempt, which the four freshness clocks already report through `behind_source`.

### D8. Railway setup is documented, not committed

The worker service and the web service are two dashboard services from the same repository with different start commands, and the same `PG*` variables shared by reference from the Postgres service. That is a section of `README.md`, not a `railway.toml`: Config as Code is marked deprecated (legacy services supported until 2026-12-01), the replacement is not evaluated here, and the archived design keeps deployment mechanics out of the code. The runbook states:

- the web start command (`gunicorn wsgi:app --bind 0.0.0.0:$PORT`) and the worker start command;
- restart policy "always" on the worker, which is safe because D2 makes it exit only on misconfiguration;
- a web healthcheck, if one is set, on `/`, never on a route that reads the database, or a deployment would block for the length of the first load;
- that both services redeploy on every push and that this is harmless: the worker restarts, polls once, and resumes any staged reload.

### D9. The not-ready view, and how it ends

The web service is up as soon as gunicorn starts, so a viewer can arrive at any point in the first load. What they see, and how the page recovers, is decided here so it is not left to the implementer of the banner.

- **In place of the lists.** While the state is not `ready`, the page shows one message panel where the doorway list, block list and map would be, and hides those, along with the filters that act on them. The freshness banner stays and carries the same state. Hiding is deliberate: an empty table under a filter bar invites the reading "no doorways", which the spec forbids.
- **Wording, by state:**
  - `uninitialised`: the mirror is being set up, and the page will fill in when it is.
  - `loading`: HRM's data is being loaded for the first time, this takes a while, and the page will refresh itself.
  - `awaiting first load`: the first load did not complete and will be tried again automatically.

  No duration is stated. About 25 minutes is a local measurement and the Railway figure is unrecorded until the first deployment (see the risks), and `tests/test_page_claims.py`-style checks exist so that the page does not assert what the mirror cannot support. No progress figure is shown either; `load_progress` holds one and could feed a later change.
- **The page recovers by itself.** While not ready it polls `/api/freshness` every 30 seconds. The moment the state is `ready` it reloads the page once. It does not try to fill the lists in place: `web/app/index.html`'s own comments say its render pipeline was never written to cope with a data swap during the page's life, and a single reload uses the ordinary boot path, so a ready page is identical however it was reached.
- **A 503 is a state, not a failure.** With the schema absent, `/api/freshness` answers 503 with the state in its body (D6). The page reads that body as `uninitialised` and keeps polling. A fetch that fails for any other reason (a network error, a non-JSON body) leaves the previous message on screen and tries again at the next tick; it neither reports the mirror as ready nor as failed.
- **The eyebrow loses its date.** The header line reads "violation type: … · open data to …". Before a load there is no date to name, and writing words into the gap ("open data to not loaded yet") read as broken grammar in the browser, so the whole "open data to" clause is hidden while not ready.
- **Polling stops when it is over.** Nothing polls once the state is `ready`, so a ready page makes no more requests than it does today.

## Risks / Trade-offs

- **[Risk, resolved by tasks 1.1 to 1.4] The bootstrap path was unproven.** The premise that `sync()` populates an empty database held: a test now runs it against a schema that does not exist. The same test family found one defect: `last_pulled_source_edit` fell back to `layer_state.source_last_edit`, which a poll writes before any reload starts, so a first load that died part-way (an HRM error, a SIGTERM on redeploy) left the poll's own observation as the baseline. The next run reported "nothing to do" against an empty mirror and never resumed the staged copy, while the poll kept recording successes. The fallback now requires a completed load. This was the exact failure the worker exists to survive, so D2's restart behaviour depends on it.
- **[Risk] Disk headroom on the Postgres plan.** A reload holds the live and the staged copies together, about 375 MB each plus WAL, and the first load through `sync()` also stages a full copy before the swap into an empty table, where `load.py` writes directly. → The owner checks the plan's storage limit before deploying, and compares it with the database size recorded during the first load (Migration Plan, checklist items 2 and 3). A bootstrap that loads directly when the live table is empty would halve the peak and is left as an open question.
- **[Risk] Load time and rate limits from Railway are unmeasured.** 25 minutes was measured on a local machine, and HRM's limits are unknown. `source.post` retries four times at 180 seconds each. → The first deployment records its own time in `sync_runs` (Migration Plan, checklist item 3); nothing in the code assumes the local figure.
- **[Risk] Web and worker deploy skew.** New web code can start before the worker has applied a new `schema.sql`, and the schema has no version table. → Changes stay additive, and D6 already answers a missing table with a state instead of an error. A change that needs a column before it exists would still fail on the routes that read it.
- **[Risk] A session lock and a pooler.** See D3. → Not confirmed by this change. The owner checks the actual Railway connection path at the first deployment (Migration Plan, checklist item 1); until then it is an assumption from how the service is usually reached.
- **[Risk] A dead worker is silent until the page says overdue.** That is two days after the last success (`next_due_at` one day out, plus a one-day grace). → The overdue state remains the signal. Alerting is an open question, not a hidden gap.
- **[Trade-off] One more always-on service.** A mostly idle Python process is a small, standing cost, accepted for a defined first run and no scheduler dependency.
- **[Trade-off] The clean-up of a failed run is "wait for the next run".** No repair is attempted between runs (see the retry requirement), so a first load that fails at HRM's end is empty for at least the backoff.

## Migration Plan

The Railway deployment is done by hand by the repository owner. It is intentionally not part of this change's implementation tasks (tasks 6.2 and 7.1 to 7.5 are retired), and nothing here has been run on Railway.

1. Merge with the worker and the lock. Existing deployments keep working: `sync.py` and `load.py` behave as before with the lock added.
2. In Railway, add the worker service from the same repository with the worker start command and the shared `PG*` variables, following the README's "Deploying on Railway" section.
3. Deploy. The web service shows the loading state while the worker loads; watch `sync_runs` and the service logs.
4. Confirm the overdue and freshness states against a stopped worker before relying on them.

**Checklist for the owner at the first deployment.** These are what the retired tasks described, kept because the risks above lean on them:

1. **The lock works over Railway's connection.** From two separate connections built from the deployed variables, take `db.sync_lock` on one and confirm the other is refused with `SyncInProgress`. Note whether the path is direct or pooled; a transaction-mode pooler would break the lock silently (D3).
2. **Storage headroom.** Note the Postgres plan's storage limit and compare it with two copies of the data (about 375 MB each) plus write-ahead log.
3. **The first load.** Record the elapsed time, the database size and the request count from `sync_runs`, and note them here: they replace the local figures the risks quote.
4. **A redeploy during the first load.** Redeploy the worker while it is loading, and confirm the new instance reports another run in progress or resumes the staged copy, never two loads interleaving, and that the load still completes.
5. **The recurrence.** After the first sync, confirm the worker's next wake time equals the `next_due_at` the page shows, and that the next sync records a poll with no reload.
6. **The runbook.** Follow the README section unaided against the fresh environment; correct it where it was wrong.

**What was verified locally in place of these.** The worker was run against the real HRM service into a throwaway schema, killed mid-load and restarted, and it resumed and finished (task 4.4); the not-ready page and its recovery were driven in headless Chromium against the real server (tasks 5.5 and 5.6); and the lock, the loop, the readiness states and the failure handling are covered by tests. None of that is a Railway figure.

Rollback: stop the worker service. The manual commands remain valid, and the lock keeps them safe if run while the worker is up.

## Open Questions

- Should something outside the served page alert when the worker stops, given that the page shows overdue only after two days? Deferred; it changes no spec here.
- Should the bootstrap load directly (`load.py`'s path) when the live table is empty, to avoid the staged copy and its peak disk? Depends on the Railway plan's headroom and the measured first-load time; the spec is unaffected.
- When Railway's Infrastructure as Code is evaluated, should the service definitions move from the README runbook into it? Revisit before 2026-12-01 if the project adopts either.
- Would a poll more often than daily be worth its cost? At about 6 requests per poll it is cheap; the spec requires only "at least daily", and the current cadence is unchanged.
