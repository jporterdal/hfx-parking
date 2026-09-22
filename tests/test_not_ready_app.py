"""What the served application does while the mirror is not ready (change
automate-mirror-bootstrap-and-sync, tasks 5.2, 5.3 and 5.7; design D6).

A new deployment has a web service that is up the moment gunicorn starts, and a mirror
that is not: the structure may not exist yet, then it is empty, then it is loading for
some time. No route may answer any of that with an unhandled error, and nothing may
present an empty mirror as a finding.
"""

import datetime
import re

import pytest

from app import server as app_server
from mirror import db as mirror_db
from mirror import status, sync
from test_app import client, mark_loaded, seed_driveway_call, set_layer_state  # noqa: F401

pytestmark = pytest.mark.db

NOW = datetime.datetime(2026, 9, 22, 12, 0, tzinfo=datetime.UTC)
LONG_AGO = NOW - datetime.timedelta(days=30)


@pytest.fixture
def absent_client(db, monkeypatch):
    """An application pointed at a schema that does not exist: the state of a fresh
    deployment before the worker has run."""
    monkeypatch.setenv("HFX_MIRROR_SCHEMA", "mirror_that_does_not_exist_yet")
    app = app_server.create_app()
    app.testing = False           # see the 500 a route returns, not an exception
    return app.test_client()


# ------------------------------------------------------------- 5.2 the state on the wire


def test_freshness_names_the_state_of_an_empty_mirror(client, clean_db):
    payload = client.get("/api/freshness").get_json()

    assert payload["readiness"] == "awaiting_first_load"


def test_freshness_names_loading_while_a_sync_is_running(client, clean_db):
    holder = mirror_db.connect()
    with mirror_db.sync_lock(holder):
        payload = client.get("/api/freshness").get_json()
    holder.close()

    assert payload["readiness"] == "loading"


def test_freshness_names_a_completed_mirror_as_ready(client, clean_db):
    mark_loaded(clean_db)

    assert client.get("/api/freshness").get_json()["readiness"] == "ready"


def seed_a_due_time_long_past(conn):
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(conn, layer, source_last_edit=LONG_AGO, last_attempt_at=LONG_AGO,
                        last_attempt_ok=True, last_success_at=LONG_AGO,
                        next_due_at=LONG_AGO + datetime.timedelta(days=1))


def test_nothing_is_overdue_before_a_first_load_completes(client, clean_db):
    """A poll records a due time as soon as it runs. If that became a promise to be
    late on, every slow first load would read as a fault."""
    seed_a_due_time_long_past(clean_db)

    next_update = client.get("/api/freshness").get_json()["next_update"]

    assert next_update["overdue"] is False
    assert next_update["days_overdue"] is None
    assert next_update["seconds_overdue"] is None
    assert next_update["due_passed"] is False
    assert "first load" in next_update["reason"]


def test_the_same_clocks_are_overdue_once_a_load_has_completed(client, clean_db):
    """The contrast that makes the test above mean something."""
    seed_a_due_time_long_past(clean_db)
    mark_loaded(clean_db)

    next_update = client.get("/api/freshness").get_json()["next_update"]

    assert next_update["overdue"] is True


def test_a_reload_of_a_ready_mirror_changes_nothing_on_the_wire(client, clean_db):
    mark_loaded(clean_db)
    before = client.get("/api/freshness").get_json()
    holder = mirror_db.connect()
    with mirror_db.sync_lock(holder):
        during = client.get("/api/freshness").get_json()
    holder.close()

    assert during["readiness"] == before["readiness"] == "ready"
    assert during["next_update"] == before["next_update"]


# --------------------------------------------------------- 5.3 the structure is absent


def test_freshness_with_no_structure_is_a_503_naming_the_state(absent_client):
    resp = absent_client.get("/api/freshness")

    assert resp.status_code == 503
    assert resp.get_json() == {
        "error": "not_ready", "readiness": "uninitialised",
        "message": app_server._NOT_READY_MESSAGES["uninitialised"],
    }
    assert resp.headers["Retry-After"] == "30"


def routes_to_try():
    """Every route the application registers, read from its URL map so a route added
    later is included without anyone remembering to add it here."""
    app = app_server.create_app(conn_factory=lambda: None)
    slug = "blocking-driveway"
    assert slug in app_server._SLUG_TO_CANONICAL
    found = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = re.sub(r"<[^>]+>", slug, rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            found.append((method, path))
    return sorted(found)


def test_the_route_list_is_read_from_the_application_not_written_here():
    routes = routes_to_try()

    assert ("GET", "/api/freshness") in routes
    assert ("POST", "/api/types/blocking-driveway/decisions") in routes
    assert len(routes) >= 10


@pytest.mark.parametrize("method, path", routes_to_try(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_no_route_fails_with_an_unhandled_error_when_the_structure_is_absent(
        absent_client, method, path):
    if method == "POST":
        resp = absent_client.post(path, json={"decision": "x", "role": "coordinator"})
    else:
        resp = absent_client.get(path)

    assert resp.status_code != 500, f"{method} {path} -> 500: {resp.get_data(as_text=True)[:300]}"


DATABASE_ROUTES = [
    "/api/freshness",
    "/api/types/blocking-driveway/doorways",
    "/api/types/blocking-driveway/blocks",
    "/api/types/blocking-driveway/decisions",
    "/api/types/blocking-driveway/figures",
    "/api/types/blocking-driveway/export.csv",
]


@pytest.mark.parametrize("path", DATABASE_ROUTES)
def test_a_data_route_answers_with_the_state_when_the_structure_is_absent(absent_client,
                                                                          path):
    resp = absent_client.get(path)

    assert resp.status_code == 503, path
    body = resp.get_json()
    assert body["error"] == "not_ready" and body["readiness"] == "uninitialised"


def test_the_readable_brief_renders_an_explanation_not_json(absent_client):
    resp = absent_client.get("/types/blocking-driveway/export")

    assert resp.status_code == 503
    assert resp.mimetype == "text/html"
    text = resp.get_data(as_text=True)
    assert "has not been set up yet" in text
    assert "clears by itself" in text


@pytest.mark.parametrize("path", ["/", "/types/blocking-driveway"])
def test_the_page_shell_is_served_whatever_state_the_mirror_is_in(absent_client, path):
    """The shell reads no database, so it cannot fail on a missing schema; it is the
    page's own script that then reads `/api/freshness` and shows the state."""
    resp = absent_client.get(path)

    assert resp.status_code == 200
    assert "text/html" in resp.mimetype


def test_a_missing_table_that_is_not_a_missing_mirror_is_still_an_error(client,
                                                                        clean_db):
    """The handler must not turn a genuine bug into a comforting 503. Here the
    structure exists; a table one route reads has been dropped."""
    clean_db.execute("DROP TABLE triage_decisions")
    clean_db.commit()
    try:
        app = app_server.create_app()
        app.testing = False
        resp = app.test_client().get("/api/types/blocking-driveway/decisions")

        assert resp.status_code == 500
    finally:
        mirror_db.apply_schema(clean_db)


# ------------------------------------------------------ 5.7 the web never makes the schema


def schema_exists(conn, name):
    return conn.execute(
        "SELECT count(*) FROM pg_namespace WHERE nspname = %s", (name,)
    ).fetchone()[0] == 1


@pytest.mark.parametrize("method, path", routes_to_try(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_no_request_creates_the_schema(db, monkeypatch, method, path):
    """Concurrent `CREATE TABLE IF NOT EXISTS` from several gunicorn workers can still
    fail, so DDL runs in one place (the worker). The web app only ever reads."""
    name = "mirror_web_must_not_create_this"
    db.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    db.commit()
    monkeypatch.setenv("HFX_MIRROR_SCHEMA", name)
    app = app_server.create_app()
    app.testing = False
    client = app.test_client()

    if method == "POST":
        client.post(path, json={"decision": "x", "role": "coordinator"})
    else:
        client.get(path)

    assert not schema_exists(db, name)


def test_creating_the_application_applies_no_schema(db, monkeypatch):
    name = "mirror_web_must_not_create_this"
    db.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    db.commit()
    monkeypatch.setenv("HFX_MIRROR_SCHEMA", name)

    app_server.create_app()

    assert not schema_exists(db, name)


# ------------------------------------------------------------------ 5.4 the exports


def csv_meta(resp):
    import csv
    import io
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True))))
    return {r[0]: r[1] for r in rows if len(r) == 2}


def test_the_csv_export_of_an_unready_mirror_says_so_at_its_head(client, clean_db):
    resp = client.get("/api/types/blocking-driveway/export.csv")

    assert resp.status_code == 200
    meta = csv_meta(resp)
    assert meta["mirror_readiness"] == "awaiting_first_load"
    assert "first load has not completed" in meta["mirror_not_ready"]
    assert "nothing has been loaded, not because nothing matched" in meta["mirror_not_ready"]


def test_the_csv_export_of_a_ready_mirror_carries_no_not_ready_note(client, clean_db):
    mark_loaded(clean_db)

    meta = csv_meta(client.get("/api/types/blocking-driveway/export.csv"))

    assert meta["mirror_readiness"] == "ready"
    assert "mirror_not_ready" not in meta


def test_the_csv_names_loading_when_a_sync_is_running(client, clean_db):
    holder = mirror_db.connect()
    with mirror_db.sync_lock(holder):
        meta = csv_meta(client.get("/api/types/blocking-driveway/export.csv"))
    holder.close()

    assert meta["mirror_readiness"] == "loading"
    assert "being loaded for the first time" in meta["mirror_not_ready"]


def test_the_brief_of_an_unready_mirror_says_so_and_does_not_claim_a_finding(client,
                                                                             clean_db):
    text = client.get("/types/blocking-driveway/export").get_data(as_text=True)

    assert "<b>Not ready:</b>" in text
    assert "<li>Mirror: awaiting_first_load</li>" in text
    assert "Nothing has been loaded yet." in text
    assert "No doorways match" not in text and "No blocks match" not in text


def test_the_brief_of_a_ready_mirror_is_unchanged_apart_from_the_state(client, clean_db):
    mark_loaded(clean_db)

    text = client.get("/types/blocking-driveway/export").get_data(as_text=True)

    assert "Not ready" not in text
    assert "<li>Mirror: ready</li>" in text
    assert "No doorways match" in text          # a ready mirror can genuinely have none
