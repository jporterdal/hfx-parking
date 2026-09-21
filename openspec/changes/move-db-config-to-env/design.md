## Context

See `proposal.md` (Why) for the motivation and `specs/db-connection-config/spec.md` for the required behaviour. This records how, and why these choices over the others.

What exists now, from reading the code:

- **One chokepoint.** `src/mirror/db.py` holds `DEFAULT_DSN`, `dsn()` and `connect()`. Every entry point reaches the database through `db.connect()`: `sync.py`, `load.py`, `derive.py`, `figures.py`, `per_type.py`, `type_figures.py`, `reconcile.py`, `reconcile_figures.py`, `scripts/*` and `app/server.py`. `server.py` calls it inside each route, about ten sites, via `create_app(conn_factory=None)`, which defaults to `db.connect`.
- **A variable already exists.** `HFX_MIRROR_DSN` overrides the default. It is named in `README.md`, in the `wsgi.py` and `tests/conftest.py` docstrings, in `server.py`'s module docstring, and in the guard test at `tests/test_sync.py:1003-1010`.
- **The suite converts failure to a skip.** The session-scoped `db` fixture in `tests/conftest.py` catches every exception from `connect()` and calls `pytest.skip`. `pyproject.toml` documents the `db` marker as "skipped when it cannot be reached".
- **No loader exists and `.gitignore` is minimal** (`__pycache__/`, `*.pyc`, `.DS_Store`). `venv/` is not listed there; it is ignored by the `.gitignore` that `python -m venv` writes inside it.
- **The documented no-network run** in `conftest.py` connects over the unix socket as `ross` with no password, so passwordless authentication is an existing, supported workflow.
- **Most tests that build the app call `create_app()` with no factory**, and rely on `HFX_MIRROR_SCHEMA` having been pointed at the throwaway schema by the `db` fixture.

## Goals / Non-Goals

**Goals:**

- No credential in tracked files, and none reachable from history going forward.
- One way to configure the connection, working the same for a developer, a test run and a host.
- A database problem is always visible: at start-up, on the first connect, or as a failed test. Never a skip, never a silent default.

**Non-Goals:**

- Choosing or provisioning a host, managing secrets on one, or changing what a deployment does beyond the variable names.
- Rewriting git history to remove the old password, or rotating it. Deleting the constant does not remove it from history; the owner handles the password separately.
- Connection pooling, TLS options or a read-only role. `PGSSLMODE` and the other libpq variables already work if someone sets them, but this change neither requires nor tests them.
- Changing `HFX_MIRROR_SCHEMA`, which stays as it is: it names a schema in the database, not how to reach it.

## Decisions

### Discrete `PG*` variables, not a connection string

`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` and `PGDATABASE` are the names libpq reads itself, so `psycopg.connect()` with an empty conninfo picks them up and this codebase never assembles a connection string. That removes the URL-encoding trap (a password containing `@` or `/` breaks a `postgresql://` URL), keeps credential-shaped strings out of `db.py` entirely, and means the same `.env` drives `psql`, `pg_isready` and `pg_dump`.

*Alternatives.* A single `HFX_MIRROR_DSN` with the fallback deleted was smaller and gave a crisp "unset" test, but the user chose discrete variables. Custom `HFX_DB_*` names would let the code build the string but would duplicate names libpq already defines and would not work with `psql`.

### "Unconfigured" is an explicit check, because libpq will not detect it

With nothing set, libpq connects to the local unix socket as the operating-system user, which is a plausible-looking success on a developer machine and exactly the silent behaviour this change removes. So `db.py` checks `PGHOST`, `PGUSER` and `PGDATABASE` itself before connecting and raises one error naming everything missing, with a pointer to `.env.example`.

A variable set to the empty string counts as missing. `python-dotenv` reads `PGPASSWORD=` as an empty string, and libpq's treatment of an empty value is not something to rely on. `.env.example` therefore lists the optional variables commented out rather than blank.

`PGPASSWORD` and `PGPORT` are not checked. A missing password is legitimate (peer auth over the socket, `~/.pgpass`), and a wrong one is caught by the connection itself, which the spec requires to fail loudly.

*Alternative.* Requiring `PGPASSWORD` too was simpler to explain but breaks the passwordless socket workflow the test docs already describe, which would then need a dummy value.

### `python-dotenv`, loaded from an explicit path, never overriding

A dependency instead of a hand-written parser, per the user's decision: it handles quoting, comments, `export` prefixes and multi-line values that a hand-rolled loader gets wrong. Pinned in `requirements.txt` like its neighbours (`==`, chosen at implementation time from the version the package index offers).

The file is loaded from a path built from `db.py`'s own location (the repository root), not from the working directory and not by upward search, so gunicorn, `python3 src/mirror/sync.py`, a script run from `scripts/` and pytest all read the same file. It is loaded with `override=False`, so a shell variable or a host's real environment always wins, and a missing file is silently fine, which is what a host with real variables needs.

Loading happens on first use, not at import of `mirror.db`, so importing the module has no side effect and the tests that import it stay pure. It runs once per process.

*Alternatives.* A stdlib loader avoids the dependency but was ruled out. Sourcing `.env` in the shell (`set -a; . .env`) puts the burden on every invocation and does nothing for gunicorn or the tests.

### `HFX_MIRROR_DSN` is retired, not kept as an override

Two ways to say the same thing means one of them is stale on somebody's machine and nobody can say which is winning. Everything now reads `PG*`. This is the only breaking part of the change, and it is why the task list has an explicit step for the documented no-network command and for any host configuration.

### The served application checks at start-up, and a factory exempts it

`create_app()` runs the configuration check when no `conn_factory` is supplied. With gunicorn, `wsgi.py` calls `create_app()` at import, so an unconfigured deployment fails while the worker boots, loudly and with the missing names, instead of returning a 500 from every route (which is what would happen now, since `connect()` is called per request).

A supplied `conn_factory` bypasses the check, because the caller has taken responsibility for connections; the tests that inject a failing or fake connection depend on that. Tests that call `create_app()` bare, which is most of them, now run the check and therefore need the configuration, as they already needed the database through the `db` fixture.

### The `db` fixture errors instead of skipping, and both failures look the same

The fixture stops catching. An unconfigured database raises the configuration error, and an unreachable one raises the connection error; being session-scoped, each `db`-marked test reports it. Tests that never request the fixture are unaffected, so the pure tests still run for someone with no Postgres, which was the reason the skip was there. The `db` marker text is corrected so it no longer promises a skip.

A single up-front `pytest_sessionstart` abort was considered and rejected: it would stop the pure tests too.

### Keep the guard test, and change what it pins

`test_the_sync_reads_no_credential_from_the_environment` pins the exact set of variables `db.py` reads. That is a useful tripwire against credentials creeping into the environment surface, so it is updated to the new expected set (`PGHOST`, `PGUSER`, `PGDATABASE`, `PGPASSWORD`, `PGPORT`, `HFX_MIRROR_SCHEMA`) rather than removed. Its other half, that `sync.py` names no source credential, stays as it is.

## Risks / Trade-offs

- **The old password stays in history** → not addressed by this change (see Non-Goals). The repo's visibility isn't confirmed from here.
- **Any existing environment that sets `HFX_MIRROR_DSN` stops working** → it fails at once, with a message naming the missing `PG*` variables, rather than silently. The documented no-network command is updated in the same change. Whether a deployment sets it is unknown from the repository.
- **Every existing checkout needs a `.env` before the `db` tests run** → `.env.example` and the error message that points at it. The suite failing where it used to skip is the intent.
- **A non-database test that calls `create_app()` bare would now error** → a task audits every bare call site (`test_type_conclusions.py`, `test_verify_view_export_agreement.py` and the others) and gives any that are not database-backed a `conn_factory`.
- **The test schema is created in whatever database `.env` names, possibly the real mirror's** → unchanged from today (`mirror_test_<pid>`, dropped on teardown), but now visible in `.env.example` so a developer knows to point tests at a database they do not mind.
- **`load_dotenv` mutates the process environment** → deliberate, since libpq reads it from there; it never overwrites, so it cannot change a value a caller set.
- **The mirror spec wording** → `hrm-data-mirror`'s "The sync SHALL run with no credentials" now reads too broadly, since the sync needs a database password. It lives in the unarchived change `mirror-hrm-data-and-host-app`, which this change does not edit. When that change is archived, the sentence should read "no *source* credentials". The new capability already states the intended scope.

## Migration Plan

1. Land the code, dependency and docs together, so no revision has a required variable without a template for it.
2. Each developer copies `.env.example` to `.env` and fills in `PGHOST`, `PGUSER` and `PGDATABASE`, and `PGPASSWORD` if the server wants one. Install the new dependency with `pip install -r requirements.txt`.
3. Move any host configuration from `HFX_MIRROR_DSN` to `PG*`.

Rollback is reverting the change; nothing is stored, and the database itself is untouched.

## Open Questions

- The `python-dotenv` pin: the version the index offers when the dependency is added.
