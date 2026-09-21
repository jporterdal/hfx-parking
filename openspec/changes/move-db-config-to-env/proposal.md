## Why

`src/mirror/db.py` falls back to a default connection string with a username and password written into it (`DEFAULT_DSN`) whenever `HFX_MIRROR_DSN` is unset. The credential is committed, has been in history since `5c244ba`, and the remote is on GitHub. It also means a checkout with no configuration at all connects quietly to whatever answers on `127.0.0.1:5432`, and a database that is unconfigured looks the same as one that is configured correctly.

The test suite hides a second consequence. `tests/conftest.py`'s `db` fixture turns *any* connection failure into a skip, so a run with no reachable database reports green while every `db`-marked test, which is most of them, went unexercised. Removing the default without changing that fixture would make the hole wider, not smaller.

## What Changes

- **Remove the hard-coded credential.** `DEFAULT_DSN`, `dsn()` and the `HFX_MIRROR_DSN` variable are deleted. **BREAKING** for anything that sets `HFX_MIRROR_DSN` today (a deployment, a shell profile, the documented no-network test command); it moves to the `PG*` variables below.
- **Configure the connection from discrete `PG*` environment variables.** `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` and `PGDATABASE`, the names libpq already reads, so `psycopg.connect()` takes them with no connection string built in this codebase and the same file also drives `psql` and `pg_isready`.
- **Load them from a local, uncommitted `.env` file** through `python-dotenv`, added to `requirements.txt`. Real environment variables take precedence over the file, so a host, a CI job or a command-line prefix still wins. `.env` is added to `.gitignore`; a `.env.example` with placeholder values is committed.
- **Fail loudly when the database is not configured.** `PGHOST`, `PGUSER` and `PGDATABASE` are required; `PGPASSWORD` and `PGPORT` are optional, so passwordless unix-socket authentication and `~/.pgpass` keep working. A missing required variable raises an error that names it and points at `.env.example`, before any connection is attempted.
- **Fail loudly when the database is unreachable.** No fallback, and no skip. A connection that fails, with or without the optional variables set, is an error.
- **Fail the served application at startup**, not on the first request, when the database is not configured. Tests that inject `conn_factory` into `create_app` are exempt.
- **Make the `db` tests error instead of skip** when the database is unconfigured or unreachable. Tests that need no database still run, so a contributor without Postgres can still run those.
- **Update what described the old behaviour:** `README.md`, the `wsgi.py` and `tests/conftest.py` docstrings, `server.py`'s module docstring, the `db` marker text in `pyproject.toml`, and the guard test in `tests/test_sync.py` that pins the exact set of environment variables `db.py` reads.

## Capabilities

### New Capabilities

- `db-connection-config`: how the system finds and authenticates to its Postgres database. Covers the environment-only configuration, the required and optional variables, the local `.env` file and its precedence, no credential in the repository, and loud failure for a database that is unconfigured or unreachable.

### Modified Capabilities

None. The one requirement this brushes against, "The sync SHALL run with no credentials" in `hrm-data-mirror`, is in the unarchived change `mirror-hrm-data-and-host-app` and not in `openspec/specs/`, so there is nothing to modify here. It means credentials for the *source*; the new capability states that scoping explicitly, and `design.md` records the wording to correct when that change is archived.

## Impact

- **Code:** `src/mirror/db.py` (the whole change is centred here; every consumer goes through `db.connect()`), `src/app/server.py` (startup check in `create_app`), `wsgi.py` (docstring only).
- **Tests:** `tests/conftest.py` (`db` fixture, docstring), `tests/test_sync.py` (guard test on the environment surface), `pyproject.toml` (`db` marker text), new tests for the missing-variable error and the `.env` precedence.
- **Dependencies:** adds `python-dotenv` to `requirements.txt`, pinned like its neighbours. `src/hotspots.py` stays stdlib-only.
- **Repository files:** `.gitignore`, a new `.env.example`, `README.md`.
- **Operational:** anything that sets `HFX_MIRROR_DSN` must move to `PG*` (whether a deployment does is not visible from the repository); every existing checkout needs a `.env` before the `db` tests or the server will run.
