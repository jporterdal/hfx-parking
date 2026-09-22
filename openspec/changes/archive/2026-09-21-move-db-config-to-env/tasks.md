Section 1 clears the repository of the credential and adds the template and dependency. Section 2 is the
change itself, in `db.py`. Section 3 makes the served application fail at start-up. Section 4 turns the
test suite's skip into a failure. Section 5 reconciles the documentation. Section 6 verifies, end to end,
the three states the spec cares about: configured, unconfigured and unreachable.

Sections 2 to 4 leave the suite failing until a `.env` exists (1.2), which is the intent: do 1.2 on the
machine doing the work before running anything.

## 1. Repository hygiene and dependency

- [x] 1.1 Add `.env` to `.gitignore`, and verify `git status` does not list a `.env` file created in the repository root, and that `git check-ignore -v .env` names the rule
- [x] 1.2 Add a committed `.env.example` listing `PGHOST`, `PGUSER` and `PGDATABASE` as required and `PGPORT` and `PGPASSWORD` as optional (commented out, so none is read as an empty string), each with a placeholder value and a one-line comment, then copy it to `.env` locally with real values, and verify `.env.example` contains no value that could be a real credential
- [x] 1.3 Add `python-dotenv` to `requirements.txt` at an exact pinned version, matching the file's `==` convention and with a comment saying it loads the local `.env`, and verify `venv/bin/pip install -r requirements.txt` succeeds and `venv/bin/python -c "import dotenv"` runs

## 2. Configuration in `src/mirror/db.py`

- [x] 2.1 Delete `DEFAULT_DSN` and `dsn()`, load the repository-root `.env` once per process on first use with existing environment variables taking precedence, and verify with a test that a value in a temporary `.env` is used when the variable is unset and is ignored when the variable is already set
- [x] 2.2 Add the configuration check: `PGHOST`, `PGUSER` and `PGDATABASE` required, an empty value counting as missing, raising one error that names every missing variable and points at `.env.example`, and verify with database-free tests for one missing, several missing, all missing, and an empty value
- [x] 2.3 Verify the check does not require `PGPASSWORD` or `PGPORT`: a test with only the three required variables set gets past the check to the connection attempt
- [x] 2.4 Make `connect()` run the check, then call the driver with no connection string so it reads the `PG*` variables itself, and verify a run against the local database with `.env` populated connects and that `HFX_MIRROR_SCHEMA` still selects the schema
- [x] 2.5 Verify nothing else in the repository reads a connection string or names a default host, user or database, by searching `src/`, `scripts/`, `tests/` and `wsgi.py` for `DEFAULT_DSN`, `HFX_MIRROR_DSN`, `dsn()` and `postgresql://`, and correct any reference that remains
- [x] 2.6 Verify the measurement scripts (`scripts/address_spread.py`, `derivation_timing.py`, `snapshot_storage.py`) and the command-line entry points (`sync.py`, `load.py`, `derive.py`) start against the configured database from a working directory other than the repository root

## 3. Served application refuses to start unconfigured

- [x] 3.1 Run the configuration check in `create_app()` when no `conn_factory` is supplied and skip it when one is, and verify with tests that a bare `create_app()` raises the configuration error with the variables unset, and that `create_app(conn_factory=...)` does not
- [x] 3.2 Audit every bare `create_app()` call site in `tests/` (`test_app.py`, `test_verify_served_matches_derive.py`, `test_verify_no_311_and_claims.py`, `test_verify_view_export_agreement.py`, `test_verify_shared_triage.py`, `test_verify_vehicle_pct_rounding.py`, `test_type_conclusions.py`) and confirm each is already database-backed; where one is not, pass it a `conn_factory`, and verify the tests that need no database still pass with no configuration
- [x] 3.3 Verify gunicorn fails while booting, not on the first request, when a required variable is missing: run `venv/bin/gunicorn wsgi:app` with the variables unset and confirm a non-zero exit with the error naming the missing variables, then run it configured and confirm it serves `/`

## 4. Tests fail rather than skip

- [x] 4.1 Remove the `except Exception` / `pytest.skip` from the `db` fixture in `tests/conftest.py`, so both the configuration error and a connection error propagate, and verify a run with the variables unset errors every `db`-marked test with the configuration error and does not report a pass
- [x] 4.2 Verify a run against an unreachable host (a `PGHOST` nothing answers on) errors every `db`-marked test with the connection error, and that a run with a wrong `PGPASSWORD` errors with the authentication error
- [x] 4.3 Verify the tests that need no database (for example `test_hotspots_pure.py`, `test_reconcile_figures.py`) still run and pass with no configuration
- [x] 4.4 Change the `db` marker text in `pyproject.toml` so it no longer says the tests are skipped when the database cannot be reached, and verify with `pytest --markers`
- [x] 4.5 Update `test_the_sync_reads_no_credential_from_the_environment` in `tests/test_sync.py` to pin the new set of variables `db.py` reads (`PGHOST`, `PGUSER`, `PGDATABASE`, `PGPASSWORD`, `PGPORT`, `HFX_MIRROR_SCHEMA`), keeping its check that `sync.py` names no source credential, and verify it passes and fails if a further variable is added to `db.py`

## 5. Documentation

- [x] 5.1 Update `README.md` (the run instructions that name `HFX_MIRROR_DSN`) to describe the `.env` file, the required and optional variables and `.env.example`, and verify no sentence still says the address comes from `HFX_MIRROR_DSN`
- [x] 5.2 Update the docstrings that name `HFX_MIRROR_DSN`: `wsgi.py`, `src/app/server.py` (module docstring and the `run()` docstring's environment note) and `tests/conftest.py`, including its no-network command, which becomes `PGHOST=/var/run/postgresql PGUSER=ross PGDATABASE=hfx_parking`, and verify by searching for `HFX_MIRROR_DSN` that nothing outside the archived and in-progress change directories still mentions it
- [x] 5.3 State in `README.md` that `db`-marked tests error when the database is unconfigured or unreachable, and that they create and drop a `mirror_test_<pid>` schema in whichever database `PGDATABASE` names, so a developer knows what the tests touch

## 6. Verification

- [x] 6.1 Run the full suite with `.env` configured and verify the same tests pass as before this change, with none skipped for want of a database
- [x] 6.2 Run the suite with no `.env` and no `PG*` variables and verify the `db` tests error with the message naming `PGHOST`, `PGUSER` and `PGDATABASE`, and the run does not pass
- [x] 6.3 Run the documented no-network command from `tests/conftest.py` with the `PG*` form and verify the suite passes with no `.env` and no password
- [x] 6.4 Verify precedence end to end: with `.env` naming one database and a shell `PGDATABASE` naming another, confirm the shell's value is the one connected to
- [x] 6.5 Verify no tracked file contains the old password, by a `git grep` for it (typed at the prompt, not written into any file) that returns nothing in the working tree, and `git ls-files` lists `.env.example` and not `.env`
- [x] 6.6 Record for the archive of `mirror-hrm-data-and-host-app` that its "Run unattended" requirement should read "no source credentials" (design, Risks), and confirm with the user whether any deployment sets `HFX_MIRROR_DSN` and needs moving to `PG*`
