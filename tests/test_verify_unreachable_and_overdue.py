"""Verification 9.1 and 9.2 (change `mirror-hrm-data-and-host-app`).

9.1  the application serves correctly with the HRM service unreachable, and its
     views state the mirror's freshness.
9.2  a sync stopped past its scheduled time produces a visibly overdue application
     rather than a healthy-looking one displaying a future update that never
     arrives.

These are verification tests: they exercise what is built and change nothing in
`src/` or `web/`. Reused, not duplicated, from `test_app.py`: the `client` fixture,
`set_layer_state`, `insert_pulled_edit` and the seeding helpers; the API-level
7.2/7.2a/7.2b cases there (lag attribution in both directions, the grace-deadline
boundary against `_next_update_state`, the two-interval threshold of
`_next_source_estimate`) are relied on, and this file adds only what they do not
reach: every route with the source made to fail loudly, a sync driven through
`sync.py`'s real failure path, the same boundary through the HTTP route with
stored timestamps in a non-UTC offset near local midnight, and the served page's
banner script run in node (no browser was involved) against real `/api/freshness`
payloads.

The server clock is frozen with `freeze_server_clock` wherever a boundary is being
pinned: "now" is then a constructed instant, never the wall clock, so a test at
one second either side of the grace deadline is deterministic.

Tests marked `xfail(strict=True)` each document a real defect found while writing
this file; they are written to pass once the defect is fixed (strict, so the
fix has to remove the marker).
"""

import csv
import datetime
import io
import json
import os
import re
import shutil
import socket
import subprocess
import types
import urllib.error
import urllib.request
import zoneinfo

import pytest

from app import server as app_server
from mirror import source, status, sync

from test_app import (  # noqa: F401  (client is used as a fixture)
    client, insert_pulled_edit, seed_driveway_call, seed_two_doorway_block,
    set_layer_state,
)

pytestmark = pytest.mark.db

UTC = datetime.UTC
HALIFAX = datetime.timezone(datetime.timedelta(hours=-3))        # ADT in September
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HALIFAX_ZONE = zoneinfo.ZoneInfo("America/Halifax")
DAY = datetime.timedelta(days=1)
HOUR = datetime.timedelta(hours=1)
SECOND = datetime.timedelta(seconds=1)

# A constructed "now" that is 23:30 on 17 September in Halifax: local midnight is
# thirty minutes away, so a comparison that mixes local wall-clock text with UTC
# text lands on the wrong side of a boundary here.
NOW = datetime.datetime(2026, 9, 18, 2, 30, tzinfo=UTC)
assert NOW.astimezone(HALIFAX).strftime("%H:%M %d") == "23:30 17"

# The two Cityworks layers whose clocks the aggregate reads.
CURRENCY = ("service_requests", "custom_fields")

_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


# ---------------------------------------------------------------- helpers


def freeze_server_clock(monkeypatch, now):
    """Make `src/app/server.py`'s own `datetime.datetime.now(...)` return `now`."""

    class Frozen(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    shim = types.SimpleNamespace(
        **{k: getattr(datetime, k) for k in dir(datetime) if not k.startswith("__")}
    )
    shim.datetime = Frozen
    monkeypatch.setattr(app_server, "datetime", shim)


def seed_clocks(conn, *, success, due, attempt=None, attempt_ok=True, source_edit=None):
    """Both currency layers' `layer_state`, the way a sync leaves it."""
    attempt = attempt or success
    for layer in CURRENCY:
        set_layer_state(
            conn, layer, source_last_edit=source_edit or success,
            last_attempt_at=attempt, last_attempt_ok=attempt_ok,
            last_success_at=success, next_due_at=due,
        )


def refusal(calls, name):
    def refuse(*args, **kwargs):
        calls.append(name)
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    return refuse


@pytest.fixture
def unreachable_source(monkeypatch):
    """HRM is down, and anything that tries to reach it is recorded and refused.

    Every public function of `mirror.source`, `urllib.request.urlopen`, and the
    two socket entry points a Python HTTP client would go through. The returned
    list is the record of attempts: an empty list afterwards is the proof that
    nothing tried. (libpq reaches Postgres from C, not through these.)
    """
    calls = []
    for name in ("post", "count", "last_edit_date", "page", "pages"):
        monkeypatch.setattr(source, name, refusal(calls, f"source.{name}"))
    monkeypatch.setattr(urllib.request, "urlopen", refusal(calls, "urllib.request.urlopen"))
    monkeypatch.setattr(socket, "create_connection", refusal(calls, "socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", refusal(calls, "socket.getaddrinfo"))
    return calls


def csv_metadata(text):
    """The two-cell `key,value` rows of an export's preamble, as a dict."""
    return {r[0]: r[1] for r in csv.reader(io.StringIO(text)) if len(r) == 2}


def brief_value(body, label):
    m = re.search(rf"<li>{re.escape(label)}: (.*?)</li>", body)
    assert m, f"{label!r} not found in the brief"
    return m.group(1)


def at(iso):
    return datetime.datetime.fromisoformat(iso)


def index_body(client):
    return client.get("/").get_data(as_text=True)


def _banner_script(body):
    m = re.search(
        r'\(async \(\) => \{\n  const header = document\.getElementById\("freshness"\);'
        r".*?\n\}\)\(\);", body, re.S)
    assert m, "the freshness banner script was not found in the served page"
    return m.group(0)


def run_banner(body, payload, probes=(), tz="America/Halifax"):
    """Run the served page's own freshness-banner script in node against `payload`.

    Stubs: `document.getElementById` (the header and the footer line), `fetch`
    (answers `payload`, records the URL). `probes` are ISO instants formatted by an
    independent `toLocaleString` call, so a test can ask "does the banner name this
    instant" without hard-coding a locale's punctuation. Returns the rendered text,
    the classes added, the footer line, the URLs fetched and the probe strings. No
    browser was involved.
    """
    harness = f"""
const payload = {json.dumps(payload)};
const probes = {json.dumps(list(probes))};
const nodes = {{
  freshness: {{hidden: true, textContent: "Checking update status...", cls: [],
              classList: {{add(c){{ nodes.freshness.cls.push(c); }}}}}},
  "footer-sync-date": {{textContent: ""}},
}};
const document = {{getElementById: id => nodes[id] || null}};
const requested = [];
const fetch = async url => {{
  requested.push(url);
  return {{ok: true, status: 200, json: async () => payload}};
}};
{_banner_script(body)}
setTimeout(() => {{
  const fmt = iso => new Date(iso).toLocaleString("en-US", {{dateStyle: "medium", timeStyle: "short"}});
  console.log(JSON.stringify({{
    text: nodes.freshness.textContent, hidden: nodes.freshness.hidden,
    classes: nodes.freshness.cls, footer: nodes["footer-sync-date"].textContent,
    requested, probes: Object.fromEntries(probes.map(p => [p, fmt(p)])),
  }}));
}}, 50);
"""
    env = {**os.environ, "TZ": tz, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
    done = subprocess.run(["node", "-e", harness], capture_output=True, text=True,
                          timeout=30, env=env)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def banner_for(client, monkeypatch, *, probes=()):
    """`/api/freshness` as the route serves it (clock frozen at NOW), rendered by
    the page's own script. Returns `(payload, rendered)`."""
    payload = client.get("/api/freshness").get_json()
    return payload, run_banner(index_body(client), payload, probes)


# ==================================================== 9.1 the source is unreachable

TYPE_SCOPED_GETS = (
    "/types/{slug}",
    "/api/types/{slug}/doorways",
    "/api/types/{slug}/blocks",
    "/api/types/{slug}/figures",
    "/api/types/{slug}/decisions",
    "/api/types/{slug}/export.csv",
    "/types/{slug}/export",
)
UNSCOPED_GETS = ("/", "/api/types", "/api/freshness", "/map-network.json")
DECISIONS_TEMPLATE = "/api/types/<slug>/decisions"      # also the one POST


def test_no_route_reaches_the_network_with_the_source_unreachable(
        client, clean_db, unreachable_source):
    """9.1.1: every route the application has answers from the mirror alone, for
    every tracked type, and nothing at all tried to reach HRM while it did."""
    seed_two_doorway_block(clean_db)
    seed_clocks(clean_db, success=NOW - HOUR, due=NOW + 23 * HOUR)

    # The route set is enumerated from the application, not from this file's list,
    # so a route added later cannot escape the check by being forgotten here.
    rules = {r.rule for r in client.application.url_map.iter_rules()
             if r.endpoint != "static"}
    covered = set(UNSCOPED_GETS) | {
        t.replace("{slug}", "<slug>") for t in TYPE_SCOPED_GETS} | {DECISIONS_TEMPLATE}
    assert rules == covered, f"routes not covered: {rules ^ covered}"

    slugs = [t["slug"] for t in client.get("/api/types").get_json()["types"]]
    assert len(slugs) > 1
    served = []
    for path in UNSCOPED_GETS:
        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.status_code)
        served.append(path)
    for slug in slugs:
        for template in TYPE_SCOPED_GETS:
            path = template.format(slug=slug)
            resp = client.get(path)
            assert resp.status_code == 200, (path, resp.status_code)
            served.append(path)
    post = client.post(f"/api/types/{slugs[0]}/decisions",
                       json={"scope": "doorway", "item_key": "1 FIRST ST",
                             "decision": "watch", "role": "Analyst"})
    assert post.status_code == 200, post.get_data(as_text=True)

    assert len(served) == len(UNSCOPED_GETS) + len(slugs) * len(TYPE_SCOPED_GETS)
    assert unreachable_source == [], f"a request reached for the network: {unreachable_source}"


def test_the_source_stub_really_does_fail_loudly_when_touched(unreachable_source):
    """The proof above is only as good as the stub: touching any of it must be
    recorded, or an empty record would mean nothing."""
    with pytest.raises(urllib.error.URLError):
        source.last_edit_date(source.LAYERS["service_requests"])
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen("https://example.invalid/")
    with pytest.raises(urllib.error.URLError):
        socket.create_connection(("example.invalid", 443))
    assert unreachable_source == [
        "source.last_edit_date", "urllib.request.urlopen", "socket.create_connection",
    ]


def test_the_page_itself_fetches_only_from_its_own_origin(client):
    """9.1.1, client side: every `fetch(` in the served page targets a path on the
    application, never HRM. (The page also links a Google Fonts stylesheet and a
    Street View anchor; neither is HRM and neither is a fetch.)"""
    body = index_body(client)
    targets = re.findall(r"fetch\(\s*([`\"'])(.*?)\1", body)
    assert targets, "no fetch() calls found in the served page"
    for _, target in targets:
        assert target.startswith("/"), f"page fetches a non-local URL: {target}"
    assert "arcgis.com/" not in "".join(t for _, t in targets)
    assert not re.search(r"fetch\(\s*[A-Za-z_]", body), "a fetch() target is computed, not literal"


@pytest.fixture
def failed_sync(clean_db, monkeypatch):
    """A mirror that synced successfully at T0, then had its next sync fail because
    HRM was unreachable, driven through `sync.py`'s real failure path.

    T0 is a real `sync.poll_layer` per layer (source stubbed only for that step, so
    the state is written by the code under test, not seeded). T1's `sync.sync` runs
    with `source.post` unpatched except for its retry sleep, so the real
    `source.last_edit_date` -> `source.post` -> `urlopen` chain raises the
    connection error and `poll_layer`'s own `except` records it.
    """
    seed_two_doorway_block(clean_db)
    t0, t1 = NOW - 3 * HOUR, NOW - HOUR
    with monkeypatch.context() as m:
        m.setattr(source, "last_edit_date", lambda layer: t0 - 2 * DAY)
        for key in sync.SYNC_ORDER:
            sync.poll_layer(clean_db, source.LAYERS[key], t0)

    calls = []
    real_post = source.post
    monkeypatch.setattr(
        source, "post",
        lambda url, body, attempts=4, timeout=180, sleep=None: real_post(
            url, body, attempts, timeout, sleep=lambda s: None))
    monkeypatch.setattr(urllib.request, "urlopen", refusal(calls, "urlopen"))
    return types.SimpleNamespace(t0=t0, t1=t1, calls=calls)


def _held(client):
    return {
        "doorways": client.get("/api/types/blocking-driveway/doorways").get_json(),
        "blocks": client.get("/api/types/blocking-driveway/blocks").get_json(),
    }


def test_a_sync_that_fails_because_hrm_is_unreachable_is_recorded_and_changes_nothing_served(
        client, clean_db, failed_sync, monkeypatch):
    """9.1.2: the failure is a recorded failed attempt with its error, the loaded
    data is intact and still served, and last-successful-sync did not move."""
    freeze_server_clock(monkeypatch, NOW)
    before = _held(client)
    assert before["doorways"]["count"] == 2 and before["blocks"]["count"] == 1
    layer_before = status.layer_freshness(clean_db, "service_requests")
    assert layer_before["last_success_at"] == failed_sync.t0

    with pytest.raises(urllib.error.URLError):
        sync.sync(clean_db, now=failed_sync.t1, log=lambda m: None)
    assert failed_sync.calls, "the failure path was not actually driven through the network call"

    with clean_db.cursor() as cur:
        cur.execute("SELECT ok, error, finished_at FROM sync_runs "
                    "WHERE layer = 'service_requests' AND kind = 'poll' "
                    "ORDER BY id DESC LIMIT 1")
        ok, error, finished = cur.fetchone()
    assert ok is False and "Connection refused" in error and finished == failed_sync.t1

    layer = status.layer_freshness(clean_db, "service_requests")
    assert layer["last_attempt_ok"] is False
    assert layer["last_attempt_at"] == failed_sync.t1
    assert layer["last_success_at"] == failed_sync.t0                    # did NOT advance
    assert layer["next_due_at"] == layer_before["next_due_at"]

    assert _held(client) == before                                       # data intact, served


def test_after_a_failed_sync_the_views_state_both_the_last_attempt_and_the_last_success(
        client, clean_db, failed_sync, monkeypatch):
    """9.1.2 / design M4 "last attempt vs last success": the served picture names
    the last attempt (T1) and the last success (T0) as two different instants."""
    freeze_server_clock(monkeypatch, NOW)
    with pytest.raises(urllib.error.URLError):
        sync.sync(clean_db, now=failed_sync.t1, log=lambda m: None)
    t0, t1 = failed_sync.t0, failed_sync.t1

    api = client.get("/api/freshness").get_json()
    assert at(api["last_success_at"]) == t0
    assert at(api["last_attempt_at"]) == t1
    assert at(api["last_attempt_at"]) != at(api["last_success_at"])

    meta = csv_metadata(client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True))
    assert at(meta["last_success_at"]) == t0
    assert at(meta["last_attempt_at"]) == t1

    brief = client.get("/types/blocking-driveway/export").get_data(as_text=True)
    assert at(brief_value(brief, "Last successful sync")) == t0          # the brief names the success


@_NEEDS_NODE
def test_the_page_banner_states_the_last_success_not_the_failed_attempt(
        client, clean_db, failed_sync, monkeypatch):
    """9.1.3, page: the banner is rendered from `/api/freshness` (fetched from that
    URL) and its "Last updated" is the last success, in the viewer's local time --
    T0 is 01:00 Halifax on 18 Sep while T1 is 03:00, and only the first may appear.
    The page renders no "last attempt" line; that is available from the API and CSV."""
    freeze_server_clock(monkeypatch, NOW)
    with pytest.raises(urllib.error.URLError):
        sync.sync(clean_db, now=failed_sync.t1, log=lambda m: None)

    payload, out = banner_for(
        client, monkeypatch,
        probes=(payload_iso(failed_sync.t0), payload_iso(failed_sync.t1)))

    assert out["requested"] == ["/api/freshness"]
    assert out["hidden"] is False
    t0_text, t1_text = out["probes"][payload_iso(failed_sync.t0)], out["probes"][payload_iso(failed_sync.t1)]
    assert f"Last updated {t0_text}." in out["text"]
    assert t1_text not in out["text"]
    assert out["footer"] == f"Last synced from HRM Open Data {t0_text}."
    local = failed_sync.t0.astimezone(HALIFAX_ZONE)
    assert f"{local.hour % 12 or 12}:{local.minute:02d}" in t0_text     # Halifax wall clock, not UTC


def payload_iso(when):
    return when.astimezone(UTC).isoformat()


def test_views_name_the_last_success_and_most_recent_call_with_the_source_unreachable(
        client, clean_db, unreachable_source, monkeypatch):
    """9.1.3: the API, the CSV and the brief each name the last successful sync and
    the most recent call date, with HRM unreachable, from stored values given in
    a non-UTC offset (parsed and compared as instants, never as strings)."""
    freeze_server_clock(monkeypatch, NOW)
    seed_two_doorway_block(clean_db)
    newest_call = datetime.datetime(2026, 9, 17, 23, 45, tzinfo=HALIFAX)   # 02:45Z on the 18th
    seed_driveway_call(clean_db, 4999, "9 NINTH ST, HALIFAX", newest_call)
    success = datetime.datetime(2026, 9, 17, 22, 10, tzinfo=HALIFAX)
    # A failed attempt after the success, so an export that named the attempt where
    # it should name the success would be caught.
    seed_clocks(clean_db, success=success, due=success + DAY,
                attempt=success + HOUR, attempt_ok=False)

    api = client.get("/api/freshness").get_json()
    assert at(api["last_success_at"]) == success
    assert at(api["most_recent_call_date"]) == newest_call

    meta = csv_metadata(client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True))
    assert at(meta["last_success_at"]) == success
    assert at(meta["most_recent_call_date"]) == newest_call

    brief = client.get("/types/blocking-driveway/export").get_data(as_text=True)
    assert at(brief_value(brief, "Last successful sync")) == success
    assert at(brief_value(brief, "Most recent call in the data")) == newest_call

    assert unreachable_source == []


@_NEEDS_NODE
def test_the_page_banner_is_rendered_from_the_freshness_payload(client, clean_db, monkeypatch):
    """9.1.3, page: run with a payload the route produced, the banner names the last
    successful update and the next expected one, in Halifax local time, and is
    shown (not hidden) without being sought."""
    freeze_server_clock(monkeypatch, NOW)
    success = datetime.datetime(2026, 9, 17, 22, 10, tzinfo=HALIFAX)
    due = success + DAY
    seed_clocks(clean_db, success=success, due=due, attempt=success + HOUR, attempt_ok=False)

    payload, out = banner_for(
        client, monkeypatch,
        probes=(payload_iso(success), payload_iso(due), payload_iso(success + HOUR)))

    assert out["probes"][payload_iso(success + HOUR)] not in out["text"]
    assert payload["next_update"]["overdue"] is False
    assert out["hidden"] is False and out["classes"] == []
    assert f"Last updated {out['probes'][payload_iso(success)]}." in out["text"]
    assert f"Next update expected {out['probes'][payload_iso(due)]}." in out["text"]
    assert "overdue" not in out["text"].lower()
    assert "10:10 PM" in out["probes"][payload_iso(success)]            # local, not 01:10 UTC


# ============================================ 9.2 a stopped sync reads as overdue

GRACE = app_server.NEXT_UPDATE_GRACE_PERIOD


def test_the_grace_period_is_the_sync_poll_interval():
    """The constant as built (task 7.2a): one polling interval, one day. Design
    leaves the grace period open ("follows from the observed cadence"), so this
    pins what shipped and that it is tied to the sync's own cadence."""
    assert GRACE == sync.POLL_INTERVAL == DAY


@pytest.mark.parametrize("due_zone", [UTC, HALIFAX, IST], ids=["utc", "halifax", "ist"])
@pytest.mark.parametrize("offset,overdue,label", [
    (-DAY, False, "a: due tomorrow, healthy"),
    (-SECOND, False, "a: due in one second, healthy"),
    (6 * HOUR, False, "b: due passed, inside the grace period"),
    (GRACE - SECOND, False, "c: one second before the grace period ends"),
    (GRACE, False, "c: exactly at the end of the grace period"),
    (GRACE + SECOND, True, "c: one second after"),
    (GRACE + 6 * HOUR, True, "c: six hours after"),
    (21 * DAY, True, "d: a sync that stopped three weeks ago"),
], ids=lambda v: v if isinstance(v, str) else None)
def test_overdue_boundary_through_the_route_with_stored_offsets(
        client, clean_db, monkeypatch, due_zone, offset, overdue, label):
    """9.2.1 (a)-(d) through `GET /api/freshness`: the due time is stored with a
    non-UTC offset (and NOW is 23:30 in Halifax, so the local calendar date differs
    from UTC's). `offset` is how long after the due time "now" is. Not overdue up to
    and including exactly the end of the grace period, overdue from one second
    after: strictly-greater, implemented and pinned here."""
    due = (NOW - offset).astimezone(due_zone)
    success = due - DAY
    seed_clocks(clean_db, success=success, due=due)
    freeze_server_clock(monkeypatch, NOW)

    nu = client.get("/api/freshness").get_json()["next_update"]

    assert nu["known"] is True
    assert nu["overdue"] is overdue, label
    if overdue:
        want = round((offset - GRACE).total_seconds() / 86400, 1)
        assert nu["days_overdue"] == want
        assert nu["reason"]
    else:
        assert nu["days_overdue"] is None and nu["reason"] is None


@pytest.mark.parametrize("due_zone", [UTC, HALIFAX, IST], ids=["utc", "halifax", "ist"])
def test_overdue_is_monotonic_and_matches_epoch_arithmetic(due_zone):
    """9.2.1 (c): sweep "now" across the whole neighbourhood of the due time in
    fifteen-minute steps, plus one second either side of the deadline, with the due
    time stored in `due_zone`. Expected values come from epoch seconds, not from
    datetimes or strings; once overdue it must stay overdue as time advances."""
    due = datetime.datetime(2026, 9, 10, 23, 59, 30, tzinfo=HALIFAX).astimezone(due_zone)
    success = due - 2 * DAY
    deadline = due.timestamp() + GRACE.total_seconds()
    step = 15 * 60
    nows = [due.timestamp() - 2 * 86400 + i * step for i in range(int(8 * 86400 / step))]
    nows += [deadline - 1, deadline, deadline + 1]
    verdicts = []
    for epoch in sorted(nows):
        now = datetime.datetime.fromtimestamp(epoch, UTC)
        state = app_server._next_update_state(
            {"next_due_at": due, "last_success_at": success}, now=now)
        assert state["overdue"] is (epoch > deadline), (now, due)
        verdicts.append(state["overdue"])
    assert verdicts == sorted(verdicts)                       # False... then True..., never back
    assert verdicts[0] is False and verdicts[-1] is True


def test_a_success_at_or_after_the_due_time_means_not_overdue_whatever_the_clock_says():
    """9.2.1 (a): the healthy case with the due time long past: the sync did run at
    or after it (in a different offset from the due time)."""
    due = datetime.datetime(2026, 9, 1, 23, 30, tzinfo=HALIFAX)                 # 02:30Z on the 2nd
    success = datetime.datetime(2026, 9, 2, 2, 30, tzinfo=UTC)                  # the same instant
    state = app_server._next_update_state(
        {"next_due_at": due, "last_success_at": success}, now=due + GRACE + 30 * DAY)
    assert state["overdue"] is False


def test_failing_every_hour_is_overdue_not_healthy(client, clean_db, failed_sync, monkeypatch):
    """9.2.1 (e), design M4: "a sync failing every hour has a recent last attempt and
    is exactly as stale as one that stopped a month ago". Real failing syncs, one an
    hour for the last day, after a last success three days ago: a recent attempt
    that failed, an old success, and the state is overdue."""
    freeze_server_clock(monkeypatch, NOW)
    old_success = NOW - 3 * DAY
    with clean_db.cursor() as cur:            # rewind the fixture's T0 success to three days ago
        cur.execute("UPDATE layer_state SET last_success_at = %s, last_attempt_at = %s, "
                    "next_due_at = %s", (old_success, old_success, old_success + DAY))
    clean_db.commit()
    for hours_ago in range(24, 0, -1):
        with pytest.raises(urllib.error.URLError):
            sync.sync(clean_db, now=NOW - hours_ago * HOUR, log=lambda m: None)

    payload = client.get("/api/freshness").get_json()

    assert at(payload["last_attempt_at"]) == NOW - HOUR                 # recent...
    assert at(payload["last_success_at"]) == old_success                # ...but nothing succeeded
    assert status.layer_freshness(clean_db, "service_requests")["last_attempt_ok"] is False
    assert payload["next_update"]["overdue"] is True
    assert payload["next_update"]["days_overdue"] == 1.0

    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM sync_runs WHERE kind = 'poll' AND NOT ok")
        assert cur.fetchone()[0] == 24


@_NEEDS_NODE
def test_failing_every_hour_reads_overdue_on_the_page_and_names_the_stale_success(
        client, clean_db, failed_sync, monkeypatch):
    """9.2.2 for (e): the banner shows the old success (not the recent failed
    attempt) and the overdue state."""
    freeze_server_clock(monkeypatch, NOW)
    old_success = NOW - 3 * DAY
    with clean_db.cursor() as cur:
        cur.execute("UPDATE layer_state SET last_success_at = %s, last_attempt_at = %s, "
                    "next_due_at = %s", (old_success, old_success, old_success + DAY))
    clean_db.commit()
    for hours_ago in range(24, 0, -1):
        with pytest.raises(urllib.error.URLError):
            sync.sync(clean_db, now=NOW - hours_ago * HOUR, log=lambda m: None)

    payload, out = banner_for(client, monkeypatch, probes=(
        payload_iso(old_success), payload_iso(NOW - HOUR), payload_iso(old_success + DAY)))

    assert f"Last updated {out['probes'][payload_iso(old_success)]}." in out["text"]
    assert out["probes"][payload_iso(NOW - HOUR)] not in out["text"]
    assert re.search(r"\boverdue\b", out["text"])
    assert "Next update expected" not in out["text"]
    assert "overdue" in out["classes"]


@_NEEDS_NODE
def test_a_sync_stopped_weeks_ago_reads_overdue_and_shows_no_upcoming_update(
        client, clean_db, monkeypatch):
    """9.2.2 (d): the JSON says overdue and the page path that renders it stops
    presenting the due time as upcoming: the "Next update expected" sentence is
    gone, replaced by an overdue sentence that puts the due time in the past tense,
    and the banner carries the overdue class."""
    freeze_server_clock(monkeypatch, NOW)
    success, due = NOW - 21 * DAY, NOW - 20 * DAY
    seed_clocks(clean_db, success=success, due=due)

    payload, out = banner_for(
        client, monkeypatch, probes=(payload_iso(success), payload_iso(due)))

    assert payload["next_update"]["overdue"] is True
    assert payload["next_update"]["days_overdue"] == 19.0
    assert "Next update expected" not in out["text"]
    assert re.search(r"\boverdue\b", out["text"])
    assert f"due {out['probes'][payload_iso(due)]}" in out["text"]           # past tense, dated
    assert f"Last updated {out['probes'][payload_iso(success)]}." in out["text"]
    assert "overdue" in out["classes"]
    assert out["hidden"] is False


def test_exports_name_the_overdue_state_and_the_stale_success_rather_than_looking_healthy(
        client, clean_db, monkeypatch):
    """9.2.2, exports: with the sync stopped three weeks ago, the CSV says overdue
    (with the reason) in place of "on schedule", both exports carry the stale
    last-success instant, and the brief has its bold Overdue line."""
    freeze_server_clock(monkeypatch, NOW)
    seed_two_doorway_block(clean_db)
    success = datetime.datetime(2026, 8, 28, 23, 30, tzinfo=HALIFAX)
    seed_clocks(clean_db, success=success, due=success + DAY)

    text = client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True)
    meta = csv_metadata(text)
    assert meta["next_update"] != "on schedule"
    assert "grace period" in meta["next_update"] and "has not completed successfully" in meta["next_update"]
    assert at(meta["last_success_at"]) == success

    brief = client.get("/types/blocking-driveway/export").get_data(as_text=True)
    assert re.search(r"<b>Overdue:</b> the sync was due", brief)
    assert at(brief_value(brief, "Last successful sync")) == success


def test_exports_do_not_claim_overdue_when_the_sync_is_on_schedule(client, clean_db, monkeypatch):
    """The other side of the previous test: the states are distinguishable."""
    freeze_server_clock(monkeypatch, NOW)
    seed_two_doorway_block(clean_db)
    seed_clocks(clean_db, success=NOW - HOUR, due=NOW + 23 * HOUR)

    meta = csv_metadata(client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True))
    brief = client.get("/types/blocking-driveway/export").get_data(as_text=True)

    assert meta["next_update"] == "on schedule"
    assert "Overdue:" not in brief


# ------------------------------------------------- 9.2.1 (f): next update not known


def _publish_history(conn, layer, *edits):
    for edit in edits:
        insert_pulled_edit(conn, layer, edit)


@_NEEDS_NODE
@pytest.mark.parametrize("publishes", [1, 2], ids=["one-observation", "one-interval"])
def test_too_little_history_says_not_yet_known_and_never_invents_a_date(
        client, clean_db, monkeypatch, publishes):
    """9.2.1 (f) / 7.2b: a single observed publish (no interval), and a single
    interval (still under the two the estimate requires), give `known: false`, a
    null estimate, and banner wording that says so; no "estimated to next publish"
    sentence and no date."""
    freeze_server_clock(monkeypatch, NOW)
    edits = [NOW - 14 * DAY, NOW - 7 * DAY][:publishes]
    seed_clocks(clean_db, success=NOW - HOUR, due=NOW + 23 * HOUR, source_edit=edits[-1])
    _publish_history(clean_db, "service_requests", *edits)
    seed_driveway_call(clean_db, 9100, "1 FIRST ST, HALIFAX", NOW - 8 * DAY)

    payload, out = banner_for(client, monkeypatch)

    estimate = payload["next_source_estimate"]
    assert estimate["known"] is False
    assert estimate["estimated_next"] is None and estimate["median_interval_days"] is None
    assert estimate["observed_intervals"] == publishes - 1
    assert "not yet known when HRM itself is likely to publish next" in out["text"]
    assert "estimated to next publish" not in out["text"]


@_NEEDS_NODE
def test_enough_history_is_worded_as_an_estimate_from_the_observed_gap(
        client, clean_db, monkeypatch):
    """The counterpart of (f), so "not yet known" is shown to be a state and not
    the only thing the banner can say: three publishes a week apart give a known,
    future estimate worded as an estimate."""
    freeze_server_clock(monkeypatch, NOW)
    edits = [NOW - 20 * DAY, NOW - 13 * DAY, NOW - 6 * DAY]
    seed_clocks(clean_db, success=NOW - HOUR, due=NOW + 23 * HOUR, source_edit=edits[-1])
    _publish_history(clean_db, "service_requests", *edits)
    seed_driveway_call(clean_db, 9100, "1 FIRST ST, HALIFAX", NOW - 6 * DAY)

    payload, out = banner_for(client, monkeypatch)

    estimate = payload["next_source_estimate"]
    assert estimate["known"] is True
    assert at(estimate["estimated_next"]) == NOW + DAY
    assert "estimated to next publish around" in out["text"]
    assert "an estimate, not a promise" in out["text"]


# ------------------------------- 9.2.3: whose limit each state names (design M4)


def _seed_quiet_source_healthy_sync(conn):
    """HRM last published 30 days ago; this sync polled an hour ago, found nothing
    new, and is on schedule. The newest call is as old as the publish."""
    quiet = NOW - 30 * DAY
    seed_clocks(conn, success=NOW - HOUR, due=NOW + 23 * HOUR, source_edit=quiet)
    insert_pulled_edit(conn, "service_requests", quiet)
    insert_pulled_edit(conn, "custom_fields", quiet)
    seed_driveway_call(conn, 9200, "1 FIRST ST, HALIFAX", quiet)


@_NEEDS_NODE
def test_a_source_that_has_not_published_is_HRMs_limit_and_not_this_projects_failure(
        client, clean_db, monkeypatch):
    """9.2.3: the data is a month old because HRM has not published; the sync is
    healthy. The banner names the data source's publishing schedule as the limit,
    says the mirror has no problem, is not in the overdue state, and does not say
    this system is behind."""
    freeze_server_clock(monkeypatch, NOW)
    _seed_quiet_source_healthy_sync(clean_db)

    payload, out = banner_for(client, monkeypatch)

    assert payload["behind_source"] is False
    assert payload["stale_call_warning"]["stale"] is True
    assert payload["next_update"]["overdue"] is False
    assert "a limit of HRM's own publishing schedule, not a problem with this mirror" in out["text"]
    assert "fallen behind" not in out["text"]
    assert "belongs to this system" not in out["text"]
    assert "overdue" not in out["text"].lower()
    assert "stale" in out["classes"] and "overdue" not in out["classes"] and "behind" not in out["classes"]


@_NEEDS_NODE
def test_a_sync_behind_a_published_version_is_this_projects_limit_and_not_HRMs(
        client, clean_db, monkeypatch):
    """9.2.3, the other direction: HRM published an hour ago, this mirror has not
    pulled it. The banner says this system is behind and does not put it on HRM's
    publishing schedule."""
    freeze_server_clock(monkeypatch, NOW)
    old_edit, new_edit = NOW - 2 * DAY, NOW - HOUR
    seed_clocks(clean_db, success=NOW - 2 * HOUR, due=NOW + 22 * HOUR, source_edit=old_edit)
    set_layer_state(clean_db, "service_requests", source_last_edit=new_edit)
    insert_pulled_edit(clean_db, "service_requests", old_edit)
    insert_pulled_edit(clean_db, "custom_fields", old_edit)
    seed_driveway_call(clean_db, 9201, "1 FIRST ST, HALIFAX", NOW - 3 * HOUR)

    payload, out = banner_for(client, monkeypatch)

    assert payload["behind_source"] is True
    assert "fallen behind HRM" in out["text"]
    assert "belongs to this system, not to HRM's publishing schedule" in out["text"]
    assert "publishing schedule, not a problem with this mirror" not in out["text"]
    assert "behind" in out["classes"]


def _seed_stalled_sync_and_old_data(conn):
    """The scheduler died three weeks ago; the newest call is older still. The last
    successful poll saw HRM's version and pulled it, so `behind_source` is false:
    this system cannot know whether HRM has published since."""
    stopped = NOW - 21 * DAY
    seed_clocks(conn, success=stopped, due=stopped + DAY, source_edit=NOW - 40 * DAY)
    insert_pulled_edit(conn, "service_requests", NOW - 40 * DAY)
    insert_pulled_edit(conn, "custom_fields", NOW - 40 * DAY)
    seed_driveway_call(conn, 9300, "1 FIRST ST, HALIFAX", NOW - 40 * DAY)


@_NEEDS_NODE
def test_a_stopped_sync_reads_overdue_on_the_page_when_the_newest_call_is_old_too(
        client, clean_db, monkeypatch):
    """9.2.2 with old data as well: still overdue, still not showing an upcoming
    update. (What else the banner then says about whose limit it is: see the
    xfail below.)"""
    freeze_server_clock(monkeypatch, NOW)
    _seed_stalled_sync_and_old_data(clean_db)

    payload, out = banner_for(client, monkeypatch)

    assert payload["next_update"]["overdue"] is True
    assert re.search(r"\boverdue\b", out["text"])
    assert "Next update expected" not in out["text"]
    assert "overdue" in out["classes"]


# ------------------------------------------------------------- defects found
# Each of the following asserts what the spec/design require and fails against
# the product as built. See the report for the reproduction of each.


@_NEEDS_NODE
@pytest.mark.xfail(strict=True, reason=(
    "DEFECT D: a stalled sync (overdue) with an old newest call is labelled HRM's "
    "limit -- the banner says 'this sync is caught up with HRM ... not a problem "
    "with this mirror' beside 'overdue'; behind_source cannot know, and "
    "_behind_reason/the banner ignore next_update.overdue (violates design M4 "
    "whose-limit; hosted-triage-app 'The source is the limit' only applies while "
    "'this system is syncing successfully')"))
def test_a_stalled_sync_is_never_labelled_as_HRMs_limit(client, clean_db, monkeypatch):
    freeze_server_clock(monkeypatch, NOW)
    _seed_stalled_sync_and_old_data(clean_db)

    payload, out = banner_for(client, monkeypatch)

    assert payload["next_update"]["overdue"] is True
    assert "this sync is caught up with HRM" not in out["text"]
    assert "not a problem with this mirror" not in out["text"]


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT D (API half): /api/freshness `behind_reason` says 'a lag here belongs to "
    "HRM's publishing schedule, not to this sync' while next_update.overdue is true "
    "(and the CSV/brief print it next to the overdue line)"))
def test_the_api_does_not_attribute_the_lag_to_HRM_while_the_sync_is_overdue(
        client, clean_db, monkeypatch):
    freeze_server_clock(monkeypatch, NOW)
    _seed_stalled_sync_and_old_data(clean_db)

    payload = client.get("/api/freshness").get_json()

    assert payload["next_update"]["overdue"] is True
    assert "not to this sync" not in (payload["behind_reason"] or "")


@_NEEDS_NODE
def test_inside_the_grace_period_a_passed_due_time_is_not_shown_as_expected(
        client, clean_db, monkeypatch):
    """9.2 defect A, fixed: hosted-triage-app "SHALL NOT continue displaying a future
    update time once that time has passed". Inside the grace period (due time
    passed, not yet overdue) the banner says the sync was due then and has not
    completed yet; it is not the overdue state and it is not "expected"."""
    freeze_server_clock(monkeypatch, NOW)
    due = NOW - 6 * HOUR
    seed_clocks(clean_db, success=NOW - 30 * HOUR, due=due)

    payload, out = banner_for(client, monkeypatch, probes=(payload_iso(due),))

    assert payload["next_update"]["overdue"] is False and at(payload["next_due_at"]) < NOW
    assert payload["next_update"]["due_passed"] is True
    assert f"Next update expected {out['probes'][payload_iso(due)]}" not in out["text"]
    assert "Next update expected" not in out["text"]
    assert (f"The next sync was due {out['probes'][payload_iso(due)]} and has not completed yet."
            in out["text"])
    assert not re.search(r"\boverdue\b", out["text"])
    assert "overdue" not in out["classes"]


@_NEEDS_NODE
@pytest.mark.parametrize("due_zone", [UTC, HALIFAX, IST], ids=["utc", "halifax", "ist"])
@pytest.mark.parametrize("passed_by,expected", [
    (-HOUR, True), (-SECOND, True), (datetime.timedelta(0), True),
    (SECOND, False), (HOUR, False), (GRACE, False),
], ids=["due-in-1h", "due-in-1s", "due-exactly-now", "passed-1s", "passed-1h", "at-grace-end"])
def test_a_due_time_is_shown_as_expected_only_while_it_is_not_in_the_past(
        client, clean_db, monkeypatch, due_zone, passed_by, expected):
    """9.2 defect A, boundary: `passed_by` is how long after the due time "now" is.
    Up to and including the instant itself the time is still upcoming; one second
    after, it is never "expected" again (the due time stored in three offsets,
    now 23:30 in Halifax so the local date differs from UTC's)."""
    freeze_server_clock(monkeypatch, NOW)
    due = (NOW - passed_by).astimezone(due_zone)
    seed_clocks(clean_db, success=due - DAY, due=due)

    payload, out = banner_for(client, monkeypatch, probes=(payload_iso(due),))

    assert payload["next_update"]["overdue"] is False
    assert (at(payload["next_due_at"]) - NOW) == -passed_by
    shown = out["probes"][payload_iso(due)]
    assert (f"Next update expected {shown}." in out["text"]) is expected
    assert (f"The next sync was due {shown} and has not completed yet." in out["text"]) is (not expected)


@_NEEDS_NODE
def test_the_overdue_banner_names_how_long_it_has_been_overdue(client, clean_db, monkeypatch):
    """9.2 defect B, fixed: hosted-triage-app "the message SHALL change to an overdue
    state naming how long it has been overdue". The API's `days_overdue` (19.0) is
    counted from the end of the grace period, so the banner says so."""
    freeze_server_clock(monkeypatch, NOW)
    seed_clocks(clean_db, success=NOW - 21 * DAY, due=NOW - 20 * DAY)

    payload, out = banner_for(client, monkeypatch)

    days = payload["next_update"]["days_overdue"]                      # 19.0 from the API
    assert days == 19.0
    assert re.search(r"\b19(\.0)?\s+days?\b", out["text"]), out["text"]
    assert "Next update is overdue by 19 days (counted after a 1-day grace period): it was due" \
        in out["text"]


@_NEEDS_NODE
@pytest.mark.parametrize("overdue_for,phrase", [
    (SECOND, "less than an hour"),
    (59 * 60 * SECOND + 59 * SECOND, "less than an hour"),
    (HOUR, "1 hour"),
    (5 * HOUR + 40 * 60 * SECOND, "5 hours"),
    (DAY - SECOND, "23 hours"),
    (DAY, "1 day"),
    (DAY + 2 * HOUR, "1 day"),                            # 1.08 days, floored to one decimal
    (DAY + 3 * HOUR, "1.1 days"),                         # 1.125
    (36 * HOUR, "1.5 days"),
    (2 * DAY - SECOND, "1.9 days"),                       # never rounded up to 2
    (2 * DAY, "2 days"),
    (19 * DAY, "19 days"),
    (19 * DAY + 20 * HOUR, "19.8 days"),
], ids=lambda v: v if isinstance(v, str) else None)
def test_the_overdue_length_is_worded_by_unit_and_never_rounded_up(
        client, clean_db, monkeypatch, overdue_for, phrase):
    """9.2 defect B, units: `overdue_for` is how long after the END of the grace
    period "now" is (so the sync was due `overdue_for + 1 day` ago). Singular and
    plural, hours below a day, and floored rather than rounded."""
    freeze_server_clock(monkeypatch, NOW)
    due = NOW - GRACE - overdue_for
    seed_clocks(clean_db, success=due - DAY, due=due)

    payload, out = banner_for(client, monkeypatch)

    assert payload["next_update"]["overdue"] is True
    assert payload["next_update"]["seconds_overdue"] == int(overdue_for.total_seconds())
    assert f"Next update is overdue by {phrase} (counted after a 1-day grace period): " in out["text"]


@_NEEDS_NODE
def test_the_overdue_banner_does_not_name_a_length_it_was_not_given(client, clean_db, monkeypatch):
    """A payload without the length (an older API) still reads overdue, with no
    invented duration; and a healthy one carries neither."""
    freeze_server_clock(monkeypatch, NOW)
    seed_clocks(clean_db, success=NOW - 21 * DAY, due=NOW - 20 * DAY)
    payload = client.get("/api/freshness").get_json()
    payload["next_update"].pop("seconds_overdue")
    payload["next_update"].pop("days_overdue")

    out = run_banner(index_body(client), payload)

    assert "Next update is overdue: it was due" in out["text"]
    assert " overdue by " not in out["text"]


@_NEEDS_NODE
@pytest.mark.xfail(strict=True, reason=(
    "DEFECT C: with a stopped sync and >=3 past publishes, next_source_estimate.estimated_next "
    "(last publish + median gap) is already in the past, and the banner still renders it as "
    "'HRM itself is estimated to next publish around <past date>' (a date that has passed "
    "presented as upcoming; _next_source_estimate never compares it with now)"))
def test_a_passed_estimate_of_HRMs_next_publish_is_not_shown_as_upcoming(
        client, clean_db, monkeypatch):
    freeze_server_clock(monkeypatch, NOW)
    edits = [NOW - 60 * DAY, NOW - 53 * DAY, NOW - 46 * DAY]          # weekly; next was due 39 days ago
    stopped = NOW - 21 * DAY
    seed_clocks(clean_db, success=stopped, due=stopped + DAY, source_edit=edits[-1])
    _publish_history(clean_db, "service_requests", *edits)
    seed_driveway_call(clean_db, 9301, "1 FIRST ST, HALIFAX", edits[-1])

    payload, out = banner_for(client, monkeypatch, probes=(payload_iso(NOW - 39 * DAY),))

    estimate = payload["next_source_estimate"]
    assert estimate["known"] is True and at(estimate["estimated_next"]) == NOW - 39 * DAY
    assert payload["next_update"]["overdue"] is True
    assert f"estimated to next publish around {out['probes'][payload_iso(NOW - 39 * DAY)]}" \
        not in out["text"]
