"""How the database connection is configured (change move-db-config-to-env).

Everything here but the last test runs with no database: the environment and the
`.env` path are replaced for the length of each test, and the driver is faked where a
connection would otherwise be made.
"""

import os

import psycopg
import pytest

from app import server as app_server
from mirror import db

PG_VARS = db.REQUIRED + db.OPTIONAL


@pytest.fixture
def bare_env(monkeypatch, tmp_path):
    """No PG* variable set, no `.env` loaded yet, and `.env` pointing at a temp file.

    `load_env()` writes into the real environment, which monkeypatch does not
    undo for a variable that was absent. Setting each one first makes it record
    the original state, so whatever the test loads is removed afterwards.
    """
    for name in PG_VARS:
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    monkeypatch.setattr(db, "_env_loaded", False)
    monkeypatch.setattr(db, "ENV_PATH", tmp_path / ".env")
    return tmp_path / ".env"


def write_env(path, **values):
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


def set_required(monkeypatch, **overrides):
    values = {"PGHOST": "h", "PGUSER": "u", "PGDATABASE": "d", **overrides}
    for name, value in values.items():
        monkeypatch.setenv(name, value)


# --- the .env file -----------------------------------------------------------


def test_a_value_in_the_env_file_is_used_when_the_variable_is_unset(bare_env):
    write_env(bare_env, PGHOST="from-file", PGUSER="u", PGDATABASE="d")
    db.check_configured()
    assert os.environ["PGHOST"] == "from-file"


def test_a_variable_already_set_wins_over_the_env_file(bare_env, monkeypatch):
    write_env(bare_env, PGHOST="from-file", PGUSER="u", PGDATABASE="d")
    monkeypatch.setenv("PGHOST", "from-shell")
    db.check_configured()
    assert os.environ["PGHOST"] == "from-shell"
    assert os.environ["PGUSER"] == "u"  # the file still fills in what is unset


def test_a_missing_env_file_is_not_an_error_by_itself(bare_env, monkeypatch):
    assert not bare_env.exists()
    set_required(monkeypatch)
    db.check_configured()


def test_the_env_file_is_read_once_per_process(bare_env, monkeypatch):
    write_env(bare_env, PGHOST="first")
    db.load_env()
    write_env(bare_env, PGUSER="added-later")
    db.load_env()
    assert os.environ["PGHOST"] == "first"
    assert "PGUSER" not in os.environ


def test_the_env_file_is_found_from_the_repository_root_not_the_working_directory():
    assert db.ENV_PATH.name == ".env"
    assert (db.ENV_PATH.parent / "src" / "mirror" / "db.py").is_file()


# --- the configuration check -------------------------------------------------


@pytest.mark.parametrize("missing", db.REQUIRED)
def test_one_missing_variable_is_named(bare_env, monkeypatch, missing):
    set_required(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(db.ConfigurationError) as raised:
        db.check_configured()
    message = str(raised.value)
    assert missing in message
    assert ".env.example" in message
    assert all(name not in message for name in db.REQUIRED if name != missing)


def test_several_missing_variables_are_all_named_in_one_error(bare_env, monkeypatch):
    monkeypatch.setenv("PGHOST", "h")
    with pytest.raises(db.ConfigurationError) as raised:
        db.check_configured()
    message = str(raised.value)
    assert "PGUSER" in message and "PGDATABASE" in message
    assert "PGHOST" not in message


def test_all_missing_are_named(bare_env):
    with pytest.raises(db.ConfigurationError) as raised:
        db.check_configured()
    assert all(name in str(raised.value) for name in db.REQUIRED)


def test_an_empty_value_counts_as_missing(bare_env, monkeypatch):
    set_required(monkeypatch, PGUSER="")
    with pytest.raises(db.ConfigurationError, match="PGUSER"):
        db.check_configured()


def test_an_empty_value_in_the_env_file_counts_as_missing(bare_env):
    write_env(bare_env, PGHOST="h", PGUSER="", PGDATABASE="d")
    with pytest.raises(db.ConfigurationError, match="PGUSER"):
        db.check_configured()


def test_the_password_and_port_are_not_required(bare_env, monkeypatch):
    set_required(monkeypatch)
    assert "PGPASSWORD" not in os.environ and "PGPORT" not in os.environ
    db.check_configured()


# --- connect() ---------------------------------------------------------------


def test_connect_checks_before_it_attempts_a_connection(bare_env, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("a connection was attempted while unconfigured")

    monkeypatch.setattr(db.psycopg, "connect", fail)
    with pytest.raises(db.ConfigurationError):
        db.connect()


def test_with_only_the_required_variables_connect_reaches_the_driver(
    bare_env, monkeypatch
):
    set_required(monkeypatch)
    calls = []

    class Reached(Exception):
        pass

    def fake_connect(*args, **kwargs):
        calls.append((args, kwargs))
        raise Reached

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)
    with pytest.raises(Reached):
        db.connect()
    # No connection string: libpq reads the PG* variables itself.
    assert calls == [((), {"autocommit": False})]


def test_a_connection_failure_is_raised_not_swallowed(bare_env, monkeypatch):
    set_required(monkeypatch, PGHOST="127.0.0.1", PGPORT="1")  # nothing listens on 1
    with pytest.raises(psycopg.OperationalError):
        db.connect()


# --- the served application --------------------------------------------------


def test_a_bare_create_app_raises_the_configuration_error_when_unconfigured(bare_env):
    with pytest.raises(db.ConfigurationError, match="PGHOST, PGUSER, PGDATABASE"):
        app_server.create_app()


def test_create_app_with_a_conn_factory_does_not_check(bare_env):
    def factory():
        raise AssertionError("no request was made")

    app_server.create_app(conn_factory=factory)


def test_create_app_starts_with_the_required_variables_and_makes_no_connection(
    bare_env, monkeypatch
):
    set_required(monkeypatch)

    def fail(*args, **kwargs):
        raise AssertionError("start-up must not connect")

    monkeypatch.setattr(db.psycopg, "connect", fail)
    app_server.create_app()


# --- against the real database -----------------------------------------------


@pytest.mark.db
def test_connect_reaches_the_configured_database_and_schema(db):
    """The `db` fixture connects through `mirror.db.connect()` with the PG* variables
    in force, and `HFX_MIRROR_SCHEMA` still selects the schema."""
    row = db.execute("SELECT current_database(), current_schema()").fetchone()
    assert row[0] == os.environ["PGDATABASE"]
    assert row[1] == os.environ["HFX_MIRROR_SCHEMA"]
