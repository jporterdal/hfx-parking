"""Task 9.6: two viewers in different roles triage one shared list, and each sees
the other's decisions with role attribution.

Verification only -- nothing here changes the product. Two independent
`create_app()` instances (each with its own Flask test client) over the one
throwaway schema stand in for two viewers' browsers: the only channel between
them is the database, exactly as for two real browsers hitting one server.

Scope, and what it leans on: `tests/test_app.py` already proves (a) a decision
recorded by one viewer is read back by a second (6.1), (b) decisions survive a
second `create_app()` (6.2), (c) an upsert does not duplicate, (d) types and
scopes do not mix, (e) role is stored when supplied and defaulted when not,
(f) the page source carries the role prompt, the "does not log you in" sentence
and the "Marked by / Last changed" line. This file adds only what those do not:
the end-to-end two-role scenario in both directions and for both item kinds,
attribution following the last writer with a later changed-time compared as
parsed instants, the exact set of roles the server accepts and rejects, "no
different access by role", a failed write reported as a failure, and the page's
own JavaScript (run in node with stubs -- there is no browser here) applied to
the server's real JSON.

What is NOT verified (no browser): that the role overlay renders and blocks the
click, that `toLocaleString()` shows the changed-time as a viewer reads it, and
that the banner is visible. The JS below is the page's own source, executed
against stubs, so it proves logic, not layout.
"""

import datetime
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from app import server as app_server
from mirror import triage as mirror_triage

pytestmark = pytest.mark.db

REPO = pathlib.Path(__file__).resolve().parents[1]
PAGE = (REPO / "web" / "app" / "index.html").read_text()
SERVER_SRC = (REPO / "src" / "app" / "server.py").read_text()
TRIAGE_SRC = (REPO / "src" / "mirror" / "triage.py").read_text()

COORDINATOR = "HRM coordinator"                # the page's ROLE_OPTIONS[0]
OFFICER = "Parking enforcement officer"        # the page's ROLE_OPTIONS[1]
X, Y = "blocking-driveway", "no-parking-sign"  # two different violation types

SQUARE_A = [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]
SQUARE_B = [[[10, 10], [10, 11], [11, 11], [11, 10], [10, 10]]]
LATEST = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)


# --------------------------------------------------------------------- seeding
# (copies of the tiny helpers in tests/test_app.py: that file is owned by
# another worker, and a rename there must not break this one)


def _census(conn, dauid, rings, object_id):
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO census_areas (object_id, dauid, population, dwellings, rings, "
            "min_lon, min_lat, max_lon, max_lat) VALUES (%s,%s,400,200,%s,%s,%s,%s,%s)",
            (object_id, dauid, json.dumps(rings), min(xs), min(ys), max(xs), max(ys)))
    conn.commit()


def _call(conn, rid, address, when, label, lat, lon, towed="N"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_requests (object_id, request_id, date_initiated, address, "
            "community, district, latitude, longitude, initiated_by) "
            "VALUES (%s,%s,%s,%s,'HALIFAX','7',%s,%s,'INTERNAL')",
            (rid, rid, when, address, lat, lon))
        for offset, (name, value) in enumerate([
                ("Alleged Violation", label), ("Vehicle Was Towed", towed),
                ("Vehicle Make", "FORD"), ("Vehicle Model", "F150"),
                ("Vehicle Colour", "BLUE")]):
            cur.execute(
                "INSERT INTO custom_fields (object_id, request_id, custom_field_name, "
                "custom_field_value) VALUES (%s,%s,%s,%s)",
                (rid * 10 + offset, rid, name, value))
    conn.commit()


@pytest.fixture
def shared_list(clean_db):
    """Type X (Blocking Driveway): two blocks of two doorways each, so there are
    two doorways and two blocks that different viewers can triage. Type Y (No
    Parking Sign): one block, so 'decisions on X are not returned for Y' has a
    real list to be absent from."""
    _census(clean_db, "12090999", SQUARE_A, 1)
    _census(clean_db, "12091111", SQUARE_B, 2)
    day = datetime.timedelta(days=1)
    seq = iter(range(8000, 8100))
    for address, lat, lon, ago in [
            ("1 FIRST ST, HALIFAX", 0.5, 0.5, (10, 5)), ("2 SECOND ST, HALIFAX", 0.5, 0.5, (8, 3)),
            ("9 NINTH AVE, HALIFAX", 10.5, 10.5, (12, 6)),
            ("10 TENTH AVE, HALIFAX", 10.5, 10.5, (9, 2))]:
        for d in ago:
            _call(clean_db, next(seq), address, LATEST - d * day,
                  "Blocking Driveway (DISPATCH)", lat, lon)
    for address, ago in [("7 SEVENTH AVE, HALIFAX", (12, 6)), ("8 EIGHTH AVE, HALIFAX", (9, 2))]:
        for d in ago:
            _call(clean_db, next(seq), address, LATEST - d * day, "No Parking Sign", 10.5, 10.5)
    return clean_db


def _viewer():
    """One viewer: an independent app instance and client, no state shared with
    any other viewer except through the database."""
    app = app_server.create_app()
    return app.test_client()


@pytest.fixture
def viewer_a():
    return _viewer()


@pytest.fixture
def viewer_b():
    return _viewer()


def _slug_like_the_page(text):
    """`web/app/index.html`'s `slug()`: lower-case, runs of non-alphanumerics to
    '-', trimmed."""
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", text.lower()))


def _items(client, slug=X):
    """The listed doorways and blocks as the page keys them (`"d:"+slug(row.a)`,
    `"b:"+block.bk` -- checked against the page source below), so a decision is
    recorded against a real listed item, not an invented key."""
    doorways = client.get(f"/api/types/{slug}/doorways").get_json()["rows"]
    blocks = client.get(f"/api/types/{slug}/blocks").get_json()["blocks"]
    return ({r["a"]: _slug_like_the_page(r["a"]) for r in doorways},
            {b["bk"]: b["bk"] for b in blocks})


def _post(client, slug, scope, item_key, decision, note="", role=COORDINATOR, **extra):
    body = {"scope": scope, "item_key": item_key, "decision": decision, "note": note, **extra}
    if role is not None:
        body["role"] = role
    return client.post(f"/api/types/{slug}/decisions", json=body)


def _decisions(client, slug=X):
    return {(d["scope"], d["item_key"]): d
            for d in client.get(f"/api/types/{slug}/decisions").get_json()["decisions"]}


def _instant(text):
    """A parsed, timezone-aware instant. The server serializes `updated_at` in the
    database session's timezone (offset -03:00/-04:00, not +00:00), so two strings
    naming one instant can differ; only parsed datetimes compare correctly."""
    parsed = datetime.datetime.fromisoformat(text)
    assert parsed.tzinfo is not None, f"{text!r} is not a timezone-aware instant"
    return parsed


def test_the_page_keys_decisions_the_way_these_tests_do():
    """The item keys above are the page's own: pinned so the scenario is about
    the items a viewer actually triages."""
    assert 'ROWS.forEach(r => r.key = "d:" + slug(r.a));' in PAGE
    assert 'BLOCKS.forEach(b => b.key = "b:" + b.bk);' in PAGE
    assert 'const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"");' in PAGE


# ------------------------------------------- 1 & 2: the two-viewer scenario


def test_two_viewers_in_different_roles_triage_one_list_and_see_each_other(
        shared_list, viewer_a, viewer_b):
    doors, blocks = _items(viewer_a)
    assert len(doors) == 4 and len(blocks) == 2, "the seeded list is what the scenario assumes"
    a_door, b_door = doors["1 First St"], doors["9 Ninth Ave"]
    a_block, b_block = "12090999", "12091111"
    assert (doors, blocks) == _items(viewer_b), "both viewers list the same items"

    before = datetime.datetime.now(datetime.UTC)

    # -- A (coordinator) records a decision and a note on a doorway and on a block.
    ra1 = _post(viewer_a, X, "doorway", a_door, "visit", "Sign missing at the drive", COORDINATOR)
    ra2 = _post(viewer_a, X, "block", a_block, "study", "Whole block needs a study", COORDINATOR)
    assert (ra1.status_code, ra2.status_code) == (200, 200)

    # -- B (officer), a different client, immediately reads and sees A's work.
    seen_by_b = _decisions(viewer_b)
    assert set(seen_by_b) == {("doorway", a_door), ("block", a_block)}
    door_a, block_a = seen_by_b[("doorway", a_door)], seen_by_b[("block", a_block)]
    assert (door_a["decision"], door_a["note"], door_a["role"]) == (
        "visit", "Sign missing at the drive", COORDINATOR)
    assert (block_a["decision"], block_a["note"], block_a["role"]) == (
        "study", "Whole block needs a study", COORDINATOR)
    after = datetime.datetime.now(datetime.UTC)
    for row, posted in ((door_a, ra1), (block_a, ra2)):
        changed = _instant(row["updated_at"])
        assert before <= changed <= after, "last-changed time is when A recorded it"
        assert changed == _instant(posted.get_json()["updated_at"]), \
            "what B reads is what the server confirmed to A"

    # -- B records a decision on a different doorway and a different block.
    rb1 = _post(viewer_b, X, "doorway", b_door, "noact", "Already fixed", OFFICER)
    rb2 = _post(viewer_b, X, "block", b_block, "sched", "Bollards booked", OFFICER)
    assert (rb1.status_code, rb2.status_code) == (200, 200)

    # -- A sees B's, with B's role; and A's own rows still say A.
    seen_by_a = _decisions(viewer_a)
    assert set(seen_by_a) == {("doorway", a_door), ("block", a_block),
                              ("doorway", b_door), ("block", b_block)}
    assert (seen_by_a[("doorway", b_door)]["decision"], seen_by_a[("doorway", b_door)]["note"],
            seen_by_a[("doorway", b_door)]["role"]) == ("noact", "Already fixed", OFFICER)
    assert (seen_by_a[("block", b_block)]["decision"], seen_by_a[("block", b_block)]["note"],
            seen_by_a[("block", b_block)]["role"]) == ("sched", "Bollards booked", OFFICER)
    assert seen_by_a[("doorway", a_door)]["role"] == seen_by_a[("block", a_block)]["role"] == COORDINATOR
    for row in seen_by_a.values():
        assert _instant(row["updated_at"]) >= before
    # B's writes came after A's: an instant later, compared as instants.
    assert _instant(seen_by_a[("doorway", b_door)]["updated_at"]) > _instant(
        seen_by_a[("block", a_block)]["updated_at"])
    # Both viewers now hold identical lists.
    assert seen_by_a == _decisions(viewer_b)


# ------------------------------------ 2: attribution follows the last writer


def test_attribution_follows_the_last_writer_with_a_later_instant(
        shared_list, viewer_a, viewer_b):
    doors, _ = _items(viewer_a)
    key = doors["1 First St"]
    other = doors["2 Second St"]

    first = _post(viewer_a, X, "doorway", key, "visit", "coordinator's note", COORDINATOR).get_json()
    untouched = _post(viewer_a, X, "doorway", other, "sched", "left alone", COORDINATOR).get_json()
    a_changed = _instant(first["updated_at"])

    second = _post(viewer_b, X, "doorway", key, "noact", "officer's note", OFFICER)
    assert second.status_code == 200

    for reader in (viewer_a, viewer_b):
        rows = _decisions(reader)
        row = rows[("doorway", key)]
        assert len(rows) == 2, "B's change replaced A's row, it did not add one"
        assert (row["decision"], row["note"], row["role"]) == ("noact", "officer's note", OFFICER)
        assert _instant(row["updated_at"]) > a_changed, \
            "the changed-time is a later instant than A's, compared as parsed datetimes"
        # ... and only that row changed hands.
        assert (rows[("doorway", other)]["role"], rows[("doorway", other)]["note"]) == (
            COORDINATOR, "left alone")
        assert _instant(rows[("doorway", other)]["updated_at"]) == _instant(untouched["updated_at"])

    # And back again: attribution flips with every writer.
    back = _post(viewer_a, X, "doorway", key, "visit", "coordinator again", COORDINATOR)
    row = _decisions(viewer_b)[("doorway", key)]
    assert (row["role"], row["decision"]) == (COORDINATOR, "visit")
    assert _instant(row["updated_at"]) == _instant(back.get_json()["updated_at"])
    assert _instant(row["updated_at"]) > _instant(second.get_json()["updated_at"])


def test_a_block_decision_changes_hands_the_same_way(shared_list, viewer_a, viewer_b):
    _post(viewer_a, X, "block", "12090999", "study", "a", COORDINATOR)
    a_changed = _instant(_decisions(viewer_a)[("block", "12090999")]["updated_at"])
    _post(viewer_b, X, "block", "12090999", "noact", "b", OFFICER)
    row = _decisions(viewer_a)[("block", "12090999")]
    assert (row["role"], row["decision"], row["note"]) == (OFFICER, "noact", "b")
    assert _instant(row["updated_at"]) > a_changed


# -------------------------------------------------- 3: isolation between types


def test_decisions_on_one_type_are_not_returned_for_another_types_list(
        shared_list, viewer_a, viewer_b):
    doors_x, _ = _items(viewer_a, X)
    doors_y, blocks_y = _items(viewer_a, Y)
    assert set(doors_y) == {"7 Seventh Ave", "8 Eighth Ave"}
    _post(viewer_a, X, "doorway", doors_x["1 First St"], "visit", "on X", COORDINATOR)
    _post(viewer_a, X, "block", "12090999", "study", "on X", COORDINATOR)

    assert _decisions(viewer_b, Y) == {}, "type Y's list carries none of X's decisions"

    # An item_key that exists in both lists is two rows, not one.
    _post(viewer_b, Y, "block", "12090999", "sched", "on Y", OFFICER)
    assert _decisions(viewer_a, X)[("block", "12090999")]["note"] == "on X"
    assert _decisions(viewer_a, X)[("block", "12090999")]["role"] == COORDINATOR
    only_y = _decisions(viewer_a, Y)
    assert set(only_y) == {("block", "12090999")} and only_y[("block", "12090999")]["role"] == OFFICER


# ----------------------------------------------------------------- 4: roles


# (what a request carries as `role`, status the server answers, role the row then
# names). The spec names two roles -- coordinator and parking enforcement officer;
# the page offers "HRM coordinator", "Parking enforcement officer" and "Other".
# The server enforces none of them.
_ROLE_CASES = [
    ("the page's coordinator option", {"role": COORDINATOR}, 200, COORDINATOR),
    ("the page's officer option", {"role": OFFICER}, 200, OFFICER),
    ("the page's 'Other' option", {"role": "Other"}, 200, "Other"),
    ("the spec's own word", {"role": "coordinator"}, 200, "coordinator"),
    ("an unknown role", {"role": "Mayor of Atlantis"}, 200, "Mayor of Atlantis"),
    ("no role key at all", {}, 200, mirror_triage.PLACEHOLDER_ROLE),
    ("role null", {"role": None}, 200, mirror_triage.PLACEHOLDER_ROLE),
    ("role empty string", {"role": ""}, 200, mirror_triage.PLACEHOLDER_ROLE),
]


@pytest.mark.parametrize("label,role_field,status,stored", _ROLE_CASES, ids=[c[0] for c in _ROLE_CASES])
def test_what_the_server_accepts_as_a_role(shared_list, viewer_a, viewer_b,
                                           label, role_field, status, stored):
    """Exactly what the server does with each kind of role: it accepts any string
    verbatim, and a missing/empty one is written as the placeholder
    'unspecified' -- it never rejects a write for its role."""
    key = _items(viewer_a)[0]["1 First St"]
    resp = viewer_a.post(f"/api/types/{X}/decisions",
                         json={"scope": "doorway", "item_key": key, "decision": "visit", **role_field})
    assert resp.status_code == status
    assert resp.get_json()["role"] == stored
    assert _decisions(viewer_b)[("doorway", key)]["role"] == stored


def test_reading_needs_no_role_and_writing_is_not_refused_without_one(
        shared_list, viewer_a, viewer_b):
    """Reading: a bare GET (no role, no header, no cookie) returns everything.
    Writing: the *server* does not require a role either -- a write with none is
    recorded, attributed to the placeholder 'unspecified'. The requirement that a
    viewer be asked for a role before recording lives only in the page
    (`ensureRole()`), which is a gap between spec and server, reported not fixed."""
    key = _items(viewer_a)[0]["1 First St"]
    assert viewer_b.get(f"/api/types/{X}/decisions").status_code == 200
    for path in ("doorways", "blocks", "figures", "export.csv"):
        assert viewer_b.get(f"/api/types/{X}/{path}").status_code == 200, path
    assert viewer_b.get(f"/types/{X}").status_code == 200
    assert viewer_b.get(f"/types/{X}/export").status_code == 200

    assert _post(viewer_a, X, "doorway", key, "visit", role=None).status_code == 200
    assert _decisions(viewer_b)[("doorway", key)]["role"] == "unspecified"


def test_the_page_asks_for_a_role_only_when_a_decision_is_recorded_and_offers_three():
    """Code-level: `ensureRole()` is called from `save()` (recording), not from
    boot/render/filters/map/export; the options are the two the spec names plus
    'Other'."""
    assert re.search(r'const ROLE_OPTIONS = \["HRM coordinator", "Parking enforcement officer", "Other"\];', PAGE)
    code = [ln for ln in PAGE.splitlines() if not ln.lstrip().startswith(("//", "*", "/*"))]
    callers = [ln.strip() for ln in code if "ensureRole()" in ln]
    assert callers == ["async function ensureRole(){", "const role = await ensureRole();"], callers
    assert "const role = await ensureRole();" in _function_source("save")


# ------------------------------------------ 5 (6.7): identify, not authenticate


def test_no_role_changes_what_a_request_may_read_or_write(shared_list, viewer_a, viewer_b):
    """The same operations, as coordinator, officer, an unknown role and no role at
    all: each reads the same list and each can write to every item -- including
    overwriting another role's decision. No 401/403, no challenge header, no
    cookie is issued."""
    doors, _ = _items(viewer_a)
    key = doors["1 First St"]
    roles = [COORDINATOR, OFFICER, "Other", "Mayor of Atlantis", None]
    for step, role in enumerate(roles):
        writer = viewer_a if step % 2 == 0 else viewer_b
        resp = _post(writer, X, "doorway", key, "visit" if step % 2 == 0 else "sched", f"by {role}", role)
        assert resp.status_code == 200, role
        assert "WWW-Authenticate" not in resp.headers and "Set-Cookie" not in resp.headers
        read = writer.get(f"/api/types/{X}/decisions")
        assert read.status_code == 200 and "Set-Cookie" not in read.headers
        row = _decisions(writer)[("doorway", key)]
        assert row["note"] == f"by {role}", "each role can overwrite the previous role's decision"
        assert row["role"] == (role or mirror_triage.PLACEHOLDER_ROLE)
    # the same list came back to every role, however a request announces its role
    assert _decisions(viewer_a) == _decisions(viewer_b)
    plain = viewer_b.get(f"/api/types/{X}/decisions").get_json()["decisions"]
    assert len(plain) == 1
    for role in roles:
        hinted = viewer_b.get(f"/api/types/{X}/decisions", query_string={"role": role or ""},
                              headers={"X-Role": role or ""})
        assert hinted.status_code == 200 and hinted.get_json()["decisions"] == plain, role


def _role_used_in_a_condition(src):
    """Line numbers where a variable or key named `role` takes part in a comparison
    or an `if` test (defaulting with `role or PLACEHOLDER` is not a condition)."""
    import ast
    tree, hits = ast.parse(src), []

    def mentions_role(node):
        return any((isinstance(n, ast.Name) and "role" in n.id.lower())
                   or (isinstance(n, ast.Constant) and n.value == "role")
                   or (isinstance(n, ast.Attribute) and "role" in n.attr.lower())
                   for n in ast.walk(node))

    for node in ast.walk(tree):
        test = node.test if isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)) else (
            node if isinstance(node, ast.Compare) else None)
        if test is not None and mentions_role(test):
            hits.append(node.lineno)
    return hits


def test_no_server_code_branches_on_the_role_value():
    """Code-level: `role` is read once, from the request body, and handed to
    `triage.record`, which stores it. Nothing compares it to anything, and the
    server never answers 401/403 or aborts."""
    import ast
    for name, src in (("server.py", SERVER_SRC), ("triage.py", TRIAGE_SRC)):
        assert _role_used_in_a_condition(src) == [], name
    assert len(re.findall(r'body\.get\("role"\)', SERVER_SRC)) == 1
    tree = ast.parse(SERVER_SRC)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in (401, 403)]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and getattr(n.func, "id", getattr(n.func, "attr", "")) == "abort"]


def test_the_page_states_roles_identify_and_grant_nothing_and_claims_no_access_control():
    """Code-level (no browser): the role prompt's own words, and the absence of
    any access-control vocabulary anywhere in the served page."""
    card = PAGE[PAGE.index("function askRole()") : PAGE.index("async function ensureRole()")]
    text = re.sub(r"`\s*\+\s*`", "", card)
    assert "This labels the decision so other viewers can see who made it." in text
    assert "it does not log you in" in text
    assert "changes nothing about what you can see or do on this page" in text
    visible = re.sub(r"<script.*?</script>", "", PAGE, flags=re.S)
    script = "".join(re.findall(r"<script.*?</script>", PAGE, flags=re.S))
    strings_in_script = " ".join(re.findall(r'`[^`]*`|"[^"\n]*"', script))
    for word in ("access control", "authorised", "authorized", "permission", "restricted to",
                 "only coordinators", "only enforcement", "sign in", "log in to", "password",
                 "secure"):
        assert word not in visible.lower(), word
        assert word not in strings_in_script.lower(), word


def test_gap_the_page_never_says_anyone_with_the_link_can_change_the_list():
    """Reported, not asserted as a defect: the spec (hosted-triage-app, 'Identify a
    viewer by role') adds that anyone who can reach the URL can record a decision.
    The page says a role 'changes nothing about what you can see or do', which is
    the 'no differing access' half; the 'anyone can record' half appears nowhere
    in the served page. Pinned so a change to that is noticed, not judged."""
    assert not re.search(r"anyone (who|with)[^.]{0,40}(url|link)", PAGE, flags=re.I)


# ------------------------------------------------ 6 (6.4): a failed write is reported


class _CommitFails:
    """A real connection whose commit fails, as when the link drops between the
    upsert and the commit. Everything else is the real connection."""

    def __init__(self, conn):
        self._conn = conn

    def commit(self):
        self._conn.rollback()
        import psycopg
        raise psycopg.OperationalError("connection lost at commit")

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _app_that_cannot_persist(kind, monkeypatch):
    if kind == "record_raises":
        def boom(*a, **k):
            import psycopg
            raise psycopg.OperationalError("storage unavailable")
        monkeypatch.setattr(app_server.triage, "record", boom)
        return app_server.create_app()
    if kind == "commit_fails":
        from mirror import db
        return app_server.create_app(conn_factory=lambda: _CommitFails(db.connect()))
    def unreachable():
        import psycopg
        raise psycopg.OperationalError("could not connect to server")
    return app_server.create_app(conn_factory=unreachable)


@pytest.mark.parametrize("kind", ["record_raises", "commit_fails", "database_unreachable"])
def test_a_failed_write_is_a_failure_response_and_nothing_is_recorded(
        shared_list, viewer_b, monkeypatch, kind):
    key = _items(viewer_b)[0]["1 First St"]
    with monkeypatch.context() as patched:      # the failure applies to this write only
        app = _app_that_cannot_persist(kind, patched)
        app.testing = False                     # a real 500, not a propagated exception
        resp = _post(app.test_client(), X, "doorway", key, "visit", "will not be saved", COORDINATOR)

    assert resp.status_code >= 500, "a failed write must not answer 2xx"
    assert not (200 <= resp.status_code < 300)
    if kind == "record_raises":
        assert resp.get_json()["error"] == "persist_failed"
    assert _decisions(viewer_b) == {}, "the other viewer sees nothing: it was not recorded"


def test_a_malformed_write_is_rejected_with_a_reason_and_nothing_is_recorded(
        shared_list, viewer_a, viewer_b):
    key = _items(viewer_a)[0]["1 First St"]
    for body in ({"scope": "roof", "item_key": key, "decision": "visit"},
                 {"scope": "doorway", "decision": "visit"},
                 {"scope": "doorway", "item_key": key}):
        resp = viewer_a.post(f"/api/types/{X}/decisions", json={**body, "role": COORDINATOR})
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid"
    assert _decisions(viewer_b) == {}


# ------------------------------- the page's own JS, run in node against the real API

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _function_source(name, prefix="async function "):
    """The source of one top-level function of the served page, by brace matching
    from its declaration (its template literals' `${...}` balance, so a plain
    count is enough; a mismatch is a JS syntax error in the run below)."""
    start = PAGE.index(f"{prefix}{name}(")
    depth, i = 0, PAGE.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(PAGE[i], 0)
        if depth == 0:
            return PAGE[start : i + 1]
        i += 1


_NODE_HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const banner = {textContent: "", hidden: true};
const calls = [];
const sandbox = {
  SLUG: input.slug, DEC: input.dec || {}, mode: input.mode, banner, calls, JSON, Promise, Object, Date,
  BLOCKS: input.blocks || [], ROWS: input.rows || [],
  el: id => (id === "dbstate" ? banner : {}),
  render: () => calls.push("render"), draw: () => calls.push("draw"), chips: () => calls.push("chips"),
  st: key => (sandbox.DEC[key] ? sandbox.DEC[key].status : "none_"),
  ensureRole: async () => input.role,
  console: {error() {}, log() {}},
  fetch: async (url, opts) => {
    calls.push({url, method: (opts && opts.method) || "GET", body: opts && opts.body && JSON.parse(opts.body)});
    const r = input.responses.shift();
    if (r === "throw") throw new Error("network down");
    return {ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.body};
  },
};
vm.createContext(sandbox);
(async () => {
  vm.runInContext(input.code, sandbox);
  const out = await vm.runInContext(input.call, sandbox);
  process.stdout.write(JSON.stringify({out, DEC: sandbox.DEC, banner, calls}));
})().catch(e => { console.error(e); process.exit(1); });
"""


def _run_page_js(functions, call, **inputs):
    code = "\n".join(functions)
    result = subprocess.run(
        [NODE, "-e", _NODE_HARNESS], input=json.dumps({"code": code, "call": call, **inputs}),
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


NOTE2 = 'const note2 = msg => { el("dbstate").textContent = msg; el("dbstate").hidden = !msg; };'


@needs_node
def test_page_js_loads_the_other_viewers_decisions_with_their_role(shared_list, viewer_a, viewer_b):
    """B's page boot (`loadDecisions()`, the page's own source) fed the JSON the
    server really returns after A recorded a doorway and a block decision: the
    page's decision map carries A's status, note, role and changed-time under the
    keys the rows use."""
    doors, _ = _items(viewer_a)
    _post(viewer_a, X, "doorway", doors["1 First St"], "visit", "from A", COORDINATOR)
    _post(viewer_a, X, "block", "12090999", "study", "block from A", COORDINATOR)
    payload = viewer_b.get(f"/api/types/{X}/decisions").get_json()

    ran = _run_page_js(
        [NOTE2, _function_source("loadDecisions")], "loadDecisions()", slug=X,
        mode="door", role=OFFICER, dec={},
        blocks=[{"key": "b:12090999"}, {"key": "b:12091111"}],
        rows=[{"key": "d:" + doors[a]} for a in doors],
        responses=[{"status": 200, "body": payload}])

    dec = ran["DEC"]
    door, block = dec["d:" + doors["1 First St"]], dec["b:12090999"]
    assert (door["status"], door["note"], door["role"]) == ("visit", "from A", COORDINATOR)
    assert (block["status"], block["note"], block["role"]) == ("study", "block from A", COORDINATOR)
    stored = {(d["scope"], d["item_key"]): d for d in payload["decisions"]}
    assert _instant(door["updatedAt"]) == _instant(stored[("doorway", doors["1 First St"])]["updated_at"])
    assert ran["calls"][0]["url"] == f"/api/types/{X}/decisions"
    assert "1 of 2 blocks and 1 of 4 doorways triaged" in ran["banner"]["textContent"]


@needs_node
def test_page_js_a_failed_write_rolls_back_and_says_so_and_a_good_write_takes_the_servers_row(
        shared_list, viewer_a):
    """`save()`/`saveDecision()` (the page's own source): a 500 leaves the pre-write
    state, re-renders, and puts a plain 'not been recorded' sentence in the
    banner; a 200 replaces the optimistic row with exactly the row the server
    returned (its role and its changed-time, not the client's clock)."""
    functions = [NOTE2, _function_source("saveDecision"), _function_source("save")]
    row = {"key": "d:1-first-st", "a": "1 First St", "s": "x", "d": "7"}
    previous = {"status": "sched", "note": "old", "role": OFFICER, "updatedAt": "2026-01-01T00:00:00Z"}

    failed = _run_page_js(functions, f'save({json.dumps(row)}, "visit", "new note")',
        slug=X, mode="door", role=COORDINATOR,
        dec={"d:1-first-st": previous}, responses=[{"status": 500, "body": {"error": "persist_failed"}}])
    assert failed["DEC"]["d:1-first-st"] == previous, "the unsaved decision is not left standing"
    assert "has not been recorded" in failed["banner"]["textContent"]
    assert failed["banner"]["hidden"] is False
    sent = [c for c in failed["calls"] if isinstance(c, dict)]
    assert len(sent) == 1 and sent[0]["method"] == "POST" and sent[0]["body"]["role"] == COORDINATOR
    assert failed["calls"].count("render") == 2, "shown, then re-rendered after the rollback"

    lost = _run_page_js(functions, f"save({json.dumps(row)}, 'visit', 'n')", slug=X, mode="door",
                        role=COORDINATOR, dec={}, responses=["throw"])
    assert "d:1-first-st" not in lost["DEC"] and "has not been recorded" in lost["banner"]["textContent"]

    key = _items(viewer_a)[0]["1 First St"]
    server_row = _post(viewer_a, X, "doorway", key, "visit", "n", OFFICER).get_json()
    ok = _run_page_js(functions, f"save({json.dumps(row)}, 'visit', 'n')", slug=X, mode="door",
                      role=OFFICER, dec={}, responses=[{"status": 200, "body": server_row}])
    kept = ok["DEC"]["d:1-first-st"]
    assert (kept["status"], kept["role"], kept["updatedAt"]) == ("visit", OFFICER, server_row["updated_at"])


# --------------------------------------- a defect the scenario turned up (last-writer time)


def _second_writer_connected_first_app():
    """B's request has already opened its connection (so its transaction has
    begun) when A's whole request runs; B then writes. B is still the last writer."""
    from mirror import db
    early = db.connect()
    return early, app_server.create_app(conn_factory=lambda: early)


@pytest.mark.xfail(strict=True, reason=(
    "updated_at is Postgres now() = the START of the request's transaction (begun by "
    "mirror.db.connect()'s SET search_path), not the moment of the write: a later "
    "writer whose connection was opened before an earlier writer's whole request "
    "is stamped with an earlier changed-time than the row it overwrote"))
def test_the_last_writers_changed_time_is_later_even_if_its_connection_opened_first(
        shared_list, viewer_a):
    key = _items(viewer_a)[0]["1 First St"]
    early, app_b = _second_writer_connected_first_app()   # B's transaction begins here
    a_row = _post(viewer_a, X, "doorway", key, "visit", "A", COORDINATOR).get_json()   # A completes
    b_row = _post(app_b.test_client(), X, "doorway", key, "noact", "B", OFFICER).get_json()  # B writes last
    current = _decisions(viewer_a)[("doorway", key)]
    assert (current["role"], current["decision"]) == (OFFICER, "noact"), "B is the last writer"
    assert _instant(b_row["updated_at"]) > _instant(a_row["updated_at"])
