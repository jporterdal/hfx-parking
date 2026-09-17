"""Task 5.1: the served application (`src/app/server.py`), exercised through
Flask's test client end to end -- route -> `mirror.derive.derive` -> JSON --
against a real connection to the throwaway `mirror_test_<pid>` schema
`tests/conftest.py`'s `clean_db` fixture seeds and truncates. Nothing here
mocks the derivation or the database, so a passing test is evidence the whole
request path works, not that a handler is merely wired up.

Flask's test client calls the WSGI app in-process (no socket), which is why
this stays inside the suite's no-network rule: `tests/conftest.py`'s
`no_network` fixture blocks only `urllib.request.urlopen` -- the source's HTTP
path -- and nothing on the path exercised here (`app.server`, `mirror.derive`,
`psycopg`) calls it. The verification the task asks for beyond this file --
starting a real server process against the real local mirror and fetching it
with curl -- is not repeated here; that is a one-off manual check, not
something worth re-running on every `pytest` invocation.
"""

import datetime
import json

import pytest

from app import server as app_server
from mirror import derive as mirror_derive
from mirror import triage as mirror_triage

pytestmark = pytest.mark.db

SQUARE_RING = [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]
LATEST = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)


# --------------------------------------------------------------------- seeding


def insert_census_area(conn, dauid, rings, dwellings=200, population=400, object_id=1):
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    box = (min(xs), min(ys), max(xs), max(ys))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO census_areas (object_id, dauid, population, dwellings, "
            "rings, min_lon, min_lat, max_lon, max_lat) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (object_id, dauid, population, dwellings, json.dumps(rings), *box),
        )
    conn.commit()


def insert_custom_field(conn, object_id, request_id, name, value):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO custom_fields (object_id, request_id, custom_field_name, "
            "custom_field_value) VALUES (%s, %s, %s, %s)",
            (object_id, request_id, name, value),
        )
    conn.commit()


def insert_service_request(conn, object_id, request_id, address, date_initiated,
                           district="7", community="HALIFAX", lat=0.5, lon=0.5):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_requests (object_id, request_id, date_initiated, "
            "address, community, district, latitude, longitude, initiated_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (object_id, request_id, date_initiated, address, community, district,
             lat, lon, "INTERNAL"),
        )
    conn.commit()


def seed_call(conn, request_id, address, when, raw_label, towed="N", lat=0.5, lon=0.5):
    """One call filed under `raw_label` -- any raw label
    `violation_types.RAW_LABEL_TO_CANONICAL` knows, so this seeds a call for
    whichever canonical type that label belongs to. `object_id` is just
    `request_id`; the two id spaces never need to differ in a test.
    """
    insert_service_request(conn, request_id, request_id, address, when, lat=lat, lon=lon)
    base = request_id * 10
    insert_custom_field(conn, base, request_id, "Alleged Violation", raw_label)
    insert_custom_field(conn, base + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, base + 2, request_id, "Vehicle Make", "FORD")
    insert_custom_field(conn, base + 3, request_id, "Vehicle Model", "F150")
    insert_custom_field(conn, base + 4, request_id, "Vehicle Colour", "BLUE")


def seed_driveway_call(conn, request_id, address, when, towed="N", lat=0.5, lon=0.5):
    """One call filed under a raw label the canonical 'Blocking Driveway' grouping
    covers exactly (`violation_types.raw_labels_for("Blocking Driveway")`) -- the
    type the app's default route serves.
    """
    seed_call(conn, request_id, address, when, "Blocking Driveway (DISPATCH)",
              towed=towed, lat=lat, lon=lon)


def seed_two_doorway_block(conn):
    """Two addresses, two calls each, both inside one census block -- enough to
    clear both `derive()` defaults (min_calls=2, min_doorways=2), so both the
    doorway list and the block list come back non-empty.
    """
    insert_census_area(conn, "12090999", SQUARE_RING)
    seed_driveway_call(conn, 4001, "1 FIRST ST, HALIFAX", LATEST - datetime.timedelta(days=10))
    seed_driveway_call(conn, 4002, "1 FIRST ST, HALIFAX", LATEST - datetime.timedelta(days=5),
                       towed="Y")
    seed_driveway_call(conn, 4003, "2 SECOND ST, HALIFAX", LATEST - datetime.timedelta(days=8))
    seed_driveway_call(conn, 4004, "2 SECOND ST, HALIFAX", LATEST - datetime.timedelta(days=3))


def seed_two_types_two_doorway_blocks(conn):
    """Two *different* canonical types, each with its own two-doorway block, in
    two different census areas -- the fixture `test_switching_types_...` below
    (5.2's own "prove no mixing" verify clause) needs data that would visibly
    collide if a route or the page ever failed to scope by type: distinct
    addresses, distinct block ids, distinct raw labels grouping to distinct
    canonical types (`violation_types.raw_labels_for`).
    """
    insert_census_area(conn, "12090999", SQUARE_RING, object_id=1)
    insert_census_area(conn, "12091111",
                       [[[10, 10], [10, 11], [11, 11], [11, 10], [10, 10]]], object_id=2)
    seed_driveway_call(conn, 4001, "1 FIRST ST, HALIFAX", LATEST - datetime.timedelta(days=10))
    seed_driveway_call(conn, 4002, "1 FIRST ST, HALIFAX", LATEST - datetime.timedelta(days=5),
                       towed="Y")
    seed_driveway_call(conn, 4003, "2 SECOND ST, HALIFAX", LATEST - datetime.timedelta(days=8))
    seed_driveway_call(conn, 4004, "2 SECOND ST, HALIFAX", LATEST - datetime.timedelta(days=3))
    seed_call(conn, 5001, "9 NINTH AVE, HALIFAX", LATEST - datetime.timedelta(days=12),
              "No Parking Sign", lat=10.5, lon=10.5)
    seed_call(conn, 5002, "9 NINTH AVE, HALIFAX", LATEST - datetime.timedelta(days=6),
              "No Parking Sign", towed="Y", lat=10.5, lon=10.5)
    seed_call(conn, 5003, "10 TENTH AVE, HALIFAX", LATEST - datetime.timedelta(days=9),
              "NOPARKING", lat=10.5, lon=10.5)
    seed_call(conn, 5004, "10 TENTH AVE, HALIFAX", LATEST - datetime.timedelta(days=2),
              "NOPARKING", lat=10.5, lon=10.5)


@pytest.fixture
def client(clean_db):
    """A Flask test client whose every request opens a real connection to the
    same throwaway schema `clean_db` just truncated. `create_app()` is called
    with no override: `tests/conftest.py`'s `db` fixture has already pointed
    `HFX_MIRROR_SCHEMA` at `mirror_test_<pid>` for the whole session, and
    `mirror.db.connect` (the factory `create_app` defaults to) reads that
    environment variable on every call -- so the app's own connections land in
    the same schema `clean_db` manipulates without this fixture having to
    inject anything.
    """
    app = app_server.create_app()
    app.testing = True
    return app.test_client()


# ------------------------------------------------------------------- slug (5.2 seam)


def test_default_slug_is_the_canonical_blocking_driveway_type():
    assert app_server.DEFAULT_CANONICAL_TYPE == "Blocking Driveway"
    assert app_server.DEFAULT_SLUG == "blocking-driveway"
    assert app_server._SLUG_TO_CANONICAL[app_server.DEFAULT_SLUG] == "Blocking Driveway"


def test_slug_function_matches_derives_own_slug_function():
    """`app.server._slug` duplicates `mirror.derive._slug` rather than importing
    it (see `server.py`'s docstring on why); this is what keeps the duplication
    honest -- both must agree on every tracked canonical type's slug, not just
    the default one, or `/api/types/<slug>/...` and `derive.py --all-types`'
    output directories would disagree about what one type is called.
    """
    from mirror import violation_types
    for name in violation_types.CANONICAL_TYPES:
        assert app_server._slug(name) == mirror_derive._slug(name)


# --------------------------------------------------------------------- the page


def test_index_served_at_root_with_no_auth_and_no_build_step(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    assert "WWW-Authenticate" not in resp.headers
    body = resp.get_data(as_text=True)
    # No leftover write_board() placeholders -- this page is fed by the API,
    # not filled at generation time (see web/app/index.html's header comment).
    # (The header comment itself names these tokens in prose, so the check is
    # for the literal unfilled JS construct, not a bare substring match.)
    assert "const DATA = __DATA__" not in body
    assert "const MAP  = __MAP__" not in body
    # It fetches its data rather than requiring any install/build step. The
    # slug is a JS template-literal interpolation (`/api/types/${SLUG}/
    # doorways`), not a literal path, so this checks for the constant that
    # resolves it plus the route pattern it is spliced into. Task 5.2: SLUG
    # (read from the URL, defaulting to DEFAULT_SLUG at "/") is what every
    # fetch below uses -- not the DEFAULT_SLUG constant itself, which would
    # pin every view to Blocking Driveway regardless of the type routed to.
    assert '"blocking-driveway"' in body
    assert "/map-network.json" in body
    assert "/api/types/${SLUG}/doorways" in body
    assert "/api/types/${SLUG}/blocks" in body
    assert "/api/types/${SLUG}/decisions" in body
    assert "const SLUG" in body
    # No bundled or externally hosted script -- the only <script> is inline,
    # matching design.md M12's "no build step, no front-end framework."
    assert "<script src" not in body
    # Task 5.2: the type switcher -- a plain <select>, populated from
    # /api/types, no router or framework added to build it.
    assert 'id="type-switch"' in body
    assert 'fetch("/api/types")' in body


def test_hardcoded_tow_thesis_is_guarded_outside_the_default_type(client):
    """5.8's job is to serve each type's own stored tow-effect figure; this
    task's job is only to make sure routing to a different type does not
    silently show Blocking Driveway's hardcoded "44.7 per cent against 44.6
    per cent" sentence as if it were that other type's own number (see
    server.py and web/app/index.html's 5.2 comments on this hazard). This
    pins the minimal guard's presence: the sentence is wrapped in
    id="tow-thesis" and boot() blanks it whenever SLUG is not the default.
    A full check would require a running browser at a non-default URL, which
    this environment does not have -- see this task's report for that
    caveat.
    """
    body = client.get("/").get_data(as_text=True)

    assert 'id="tow-thesis"' in body
    assert "44.7 per cent against 44.6 per cent" in body
    assert "SLUG !== DEFAULT_SLUG" in body
    assert "not yet available" in body


def test_index_is_reachable_with_a_bare_client_no_headers(client):
    """'No install, no account, no credential' (spec.md) -- a request carrying
    nothing but a path succeeds."""
    resp = client.get("/", headers={})
    assert resp.status_code == 200


# --------------------------------------------------------------------- the map asset


def test_map_network_served_as_its_own_asset(client):
    resp = client.get("/map-network.json")

    assert resp.status_code == 200
    payload = json.loads(resp.data)
    assert set(payload) == {"q", "names", "segs", "blocks"}
    assert len(payload["segs"]) > 0


def test_map_network_is_cacheable(client):
    """Task 5.4: the 490 KB payload carries `Cache-Control` and an `ETag`, the
    two headers that let a browser skip re-downloading it -- `max-age` so it
    skips the request entirely within the window, `ETag` so a request made
    past the window can be answered with a 304 rather than the body. Neither
    is Flask's bare default: `send_from_directory` without `max_age` sends no
    `Cache-Control` at all, which is what the pre-5.4 route did (see the route's
    comment in `src/app/server.py`).
    """
    resp = client.get("/map-network.json")

    assert resp.status_code == 200
    assert "max-age=86400" in resp.headers["Cache-Control"]
    assert "public" in resp.headers["Cache-Control"]
    assert resp.headers.get("ETag")


def test_map_network_conditional_get_sends_no_body_when_unchanged(client):
    """The mechanism behind "moving between types does not re-download the
    geometry" (5.4's verify clause): a second request that already holds the
    first response's `ETag` gets a 304 with an empty body, not another 490 KB
    payload. A real client never issues this second request within
    `max-age=86400` at all (`test_map_network_is_cacheable`); this test covers
    the request a client makes once that window has passed, which is the
    other half of "does not re-download" -- revalidation, not just caching.
    """
    first = client.get("/map-network.json")
    etag = first.headers["ETag"]

    second = client.get("/map-network.json", headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.data == b""


# --------------------------------------------------------------------- doorways API


def test_doorways_api_returns_seeded_rows_for_the_default_type(client, clean_db):
    seed_two_doorway_block(clean_db)

    resp = client.get("/api/types/blocking-driveway/doorways")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == "Blocking Driveway"
    assert data["slug"] == "blocking-driveway"
    assert data["count"] == 2
    # The newest of the four seeded calls (request 4004, "2 SECOND ST", LATEST - 3
    # days), not LATEST itself -- nothing is seeded exactly at LATEST.
    assert data["latest"] == "2026-05-29"
    addresses = {r["a"] for r in data["rows"]}
    assert addresses == {"1 First St", "2 Second St"}  # hotspots.clean_address + .title()
    # Same abbreviated-key shape write_board's page_rows uses, so
    # web/app/index.html's script (copied from web/template.html) needs no change.
    assert set(data["rows"][0]) == {
        "i", "a", "d", "c", "st", "bk", "nb", "m", "t", "w", "vd", "vs", "g", "l",
        "o", "lat", "lon",
    }
    towed_row = next(r for r in data["rows"] if r["a"] == "1 First St")
    assert towed_row["w"] == 1  # one of its two calls was towed
    assert data["summary"]["distinct"] >= 0
    assert data["summary"]["tow_pct"] is not None


def test_doorways_api_with_no_seeded_calls_returns_an_empty_list_not_an_error(client, clean_db):
    resp = client.get("/api/types/blocking-driveway/doorways")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 0
    assert data["rows"] == []
    assert data["latest"] is None


# --------------------------------------------------------------------- blocks API


def test_blocks_api_returns_the_seeded_block_for_the_default_type(client, clean_db):
    seed_two_doorway_block(clean_db)

    resp = client.get("/api/types/blocking-driveway/blocks")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == "Blocking Driveway"
    assert data["count"] == 1
    block = data["blocks"][0]
    assert block["bk"] == "12090999"
    assert block["n"] == 2  # two doorways, both still calling
    assert set(block) == {
        "i", "bk", "s", "n", "m", "t", "w", "dw", "r", "d", "worst", "addrs",
    }


def test_blocks_api_with_no_seeded_calls_returns_an_empty_list(client, clean_db):
    resp = client.get("/api/types/blocking-driveway/blocks")

    assert resp.status_code == 200
    assert resp.get_json()["blocks"] == []


# ----------------------------------------------------------- routing scoped by type


def test_a_slug_naming_nothing_answers_404_not_an_empty_list(client):
    """A slug that names no tracked canonical type -- not a real HRM type
    misspelled, just nothing at all -- answers 404 rather than silently
    rendering an empty list, so 5.3 has a clear signal to build its rendered
    explanation on rather than a response that looks like a type with zero
    doorways. (Task 5.1 used to also 404 every *real* type but the default;
    task 5.2 lifts that restriction -- see
    `test_every_tracked_type_answers_not_just_the_default`, below, for the
    positive case this test used to conflate with a truly unknown slug.)
    """
    for slug in ("not-a-real-type", "blocking-driveways"):
        resp = client.get(f"/api/types/{slug}/doorways")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "untracked"

        resp = client.get(f"/api/types/{slug}/blocks")
        assert resp.status_code == 404


def test_every_tracked_type_answers_not_just_the_default(client, clean_db):
    """Task 5.2's core lift: `_canonical_for_slug` used to answer only
    `DEFAULT_SLUG`. A second, non-default real canonical type ('No Parking
    Sign') now resolves too, with no seeded data required to prove the route
    itself works (an empty list is a valid, non-404 answer -- see
    `test_doorways_api_with_no_seeded_calls_returns_an_empty_list_not_an_error`
    for the default type's version of this same guarantee).
    """
    for slug, name in (("no-parking-sign", "No Parking Sign"),
                        ("private-property", "Private Property"),
                        ("on-highway-over-24-hours", "On Highway Over 24 Hours")):
        resp = client.get(f"/api/types/{slug}/doorways")
        assert resp.status_code == 200
        assert resp.get_json()["type"] == name

        resp = client.get(f"/api/types/{slug}/blocks")
        assert resp.status_code == 200
        assert resp.get_json()["type"] == name

        resp = client.get(f"/api/types/{slug}/decisions")
        assert resp.status_code == 200
        assert resp.get_json()["type"] == name


def test_every_canonical_type_resolves_to_a_working_route(client):
    """Not just three spot-checked types: every entry in the frozen
    `violation_types.CANONICAL_TYPES` list answers a real 200, matching the
    task's "every tracked canonical type gets a working route" requirement
    literally rather than by sample.
    """
    from mirror import violation_types
    for name in violation_types.CANONICAL_TYPES:
        slug = app_server._slug(name)
        resp = client.get(f"/api/types/{slug}/doorways")
        assert resp.status_code == 200, f"{name!r} ({slug!r}) did not resolve"
        assert resp.get_json()["type"] == name


# ------------------------------------------------------------- /api/types (5.2)


def test_api_types_lists_every_tracked_type_with_its_slug(client):
    from mirror import violation_types

    resp = client.get("/api/types")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["default_slug"] == app_server.DEFAULT_SLUG
    got = {t["slug"]: t["type"] for t in data["types"]}
    expected = {app_server._slug(name): name for name in violation_types.CANONICAL_TYPES}
    assert got == expected
    # Busiest-first order, same as violation_types.CANONICAL_TYPES itself --
    # the switcher is not free to resort it.
    assert [t["type"] for t in data["types"]] == list(violation_types.CANONICAL_TYPES)


# ------------------------------------------------------------ /types/<slug> page (5.2)


def test_type_page_serves_the_same_page_for_a_tracked_type(client):
    resp = client.get("/types/no-parking-sign")

    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    assert resp.get_data(as_text=True) == client.get("/").get_data(as_text=True)


def test_type_page_404s_for_an_untracked_slug(client):
    resp = client.get("/types/not-a-real-type")
    assert resp.status_code == 404


def test_doorways_and_blocks_endpoints_agree_on_the_type_they_scope_to(client, clean_db):
    seed_two_doorway_block(clean_db)

    door = client.get("/api/types/blocking-driveway/doorways").get_json()
    block = client.get("/api/types/blocking-driveway/blocks").get_json()

    assert door["type"] == block["type"] == app_server.DEFAULT_CANONICAL_TYPE
    assert door["slug"] == block["slug"] == app_server.DEFAULT_SLUG


# ------------------------------------------- no mixing between types (5.2's verify clause)
#
# Task 5.2's own words: "verify switching types never mixes rows and that a
# view carries only its own type's data." Every test below seeds two
# canonical types at once with `seed_two_types_two_doorway_blocks` (disjoint
# addresses, disjoint census blocks, disjoint lat/lon) and checks that a
# request for one type's route -- doorways, blocks, or triage decisions --
# never returns anything that belongs to the other. This is deliberately
# proved through the HTTP routes end to end, not asserted from reading
# `_canonical_for_slug`'s source: a correct-looking lookup function is not
# evidence the route built on it actually threads the right value through to
# every query it issues (see the DEFAULT_SLUG-vs-SLUG bug this task fixed in
# web/app/index.html's saveDecision()/loadDecisions() -- exactly the kind of
# mistake that looks fine by inspection and fails only under a request for a
# non-default type).


def test_switching_types_doorway_lists_never_mix(client, clean_db):
    seed_two_types_two_doorway_blocks(clean_db)

    driveway = client.get("/api/types/blocking-driveway/doorways").get_json()
    no_parking = client.get("/api/types/no-parking-sign/doorways").get_json()

    driveway_addrs = {r["a"] for r in driveway["rows"]}
    no_parking_addrs = {r["a"] for r in no_parking["rows"]}
    assert driveway_addrs == {"1 First St", "2 Second St"}
    assert no_parking_addrs == {"9 Ninth Ave", "10 Tenth Ave"}
    assert driveway_addrs.isdisjoint(no_parking_addrs)
    assert driveway["type"] == "Blocking Driveway"
    assert no_parking["type"] == "No Parking Sign"


def test_switching_types_block_lists_never_mix(client, clean_db):
    seed_two_types_two_doorway_blocks(clean_db)

    driveway = client.get("/api/types/blocking-driveway/blocks").get_json()
    no_parking = client.get("/api/types/no-parking-sign/blocks").get_json()

    assert {b["bk"] for b in driveway["blocks"]} == {"12090999"}
    assert {b["bk"] for b in no_parking["blocks"]} == {"12091111"}


def test_switching_types_map_markers_never_mix(client, clean_db):
    """The doorway payload *is* the marker data (`web/app/index.html` plots
    `rows[].lat/lon` directly onto the canvas) -- there is no separate
    "markers" endpoint to check, so proving the doorway rows are
    type-disjoint (the test above) already proves the markers are. This test
    additionally pins the coordinates themselves apart, so a future change
    that split markers from the row list would still be caught if it
    resurfaced the mixing this task rules out.
    """
    seed_two_types_two_doorway_blocks(clean_db)

    driveway = client.get("/api/types/blocking-driveway/doorways").get_json()
    no_parking = client.get("/api/types/no-parking-sign/doorways").get_json()

    assert all(r["lat"] == 0.5 for r in driveway["rows"])
    assert all(r["lat"] == 10.5 for r in no_parking["rows"])


def test_switching_types_triage_decisions_never_mix_over_http(client, clean_db):
    """The route-level counterpart to
    `test_a_decision_under_one_violation_type_does_not_appear_under_another`
    (which exercises `mirror.triage` directly): this goes through the actual
    POST/GET routes for two different types, which is what would have caught
    web/app/index.html's saveDecision()/loadDecisions() reading DEFAULT_SLUG
    instead of SLUG -- a defect invisible at the `mirror.triage` layer
    (correctly scoped there already by task 6.1) but live at the page's
    fetch call sites until this task's edit.
    """
    posted = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "visit",
              "note": "driveway note"},
    )
    assert posted.status_code == 200

    driveway_decisions = client.get("/api/types/blocking-driveway/decisions").get_json()
    no_parking_decisions = client.get("/api/types/no-parking-sign/decisions").get_json()

    assert len(driveway_decisions["decisions"]) == 1
    assert driveway_decisions["decisions"][0]["item_key"] == "1-first-st"
    assert no_parking_decisions["decisions"] == []


def test_same_item_key_under_two_types_does_not_collide(client, clean_db):
    """Two different types can legitimately produce the same item_key text
    (an address slug or a census block id is not unique across violation
    types) -- the table's UNIQUE constraint is
    `(violation_type, scope, item_key)`, so this must stay two independent
    rows, not one type's write clobbering the other's.
    """
    client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "shared-key", "decision": "visit"},
    )
    client.post(
        "/api/types/no-parking-sign/decisions",
        json={"scope": "doorway", "item_key": "shared-key", "decision": "noact"},
    )

    driveway_decisions = client.get("/api/types/blocking-driveway/decisions").get_json()
    no_parking_decisions = client.get("/api/types/no-parking-sign/decisions").get_json()

    assert len(driveway_decisions["decisions"]) == 1
    assert driveway_decisions["decisions"][0]["decision"] == "visit"
    assert len(no_parking_decisions["decisions"]) == 1
    assert no_parking_decisions["decisions"][0]["decision"] == "noact"


# --------------------------------------------------------- decisions API (6.1)
#
# design.md M6: "Decisions move to the store behind the application. All
# viewers read and write the same rows." Task 6.1's own verify clause is that
# a decision recorded by one viewer is visible to a second viewer of the same
# type's list, so `test_a_decision_recorded_by_one_viewer_is_visible_to_a_second_viewer`
# below is the deliverable, not a supporting check: it builds two independent
# Flask apps (each its own `create_app()` call, each its own test client --
# nothing shared between them but the real Postgres schema `clean_db` points
# both at), posts a decision through one and reads it back through the other.


def independent_client():
    """A second, wholly separate Flask app and test client -- its own
    `create_app()` call, sharing no Python object with any other client
    except (through `mirror.db.connect`'s environment-variable DSN/schema)
    the same underlying Postgres schema. Standing in for "a second viewer's
    browser" the way the `client` fixture stands in for the first.
    """
    app = app_server.create_app()
    app.testing = True
    return app.test_client()


def test_decisions_endpoint_starts_empty_for_a_type_with_no_decisions(client):
    resp = client.get("/api/types/blocking-driveway/decisions")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == app_server.DEFAULT_CANONICAL_TYPE
    assert data["slug"] == app_server.DEFAULT_SLUG
    assert data["decisions"] == []


def test_a_decision_recorded_by_one_viewer_is_visible_to_a_second_viewer(client, clean_db):
    """The task's own verify clause. `client` posts a decision; a second,
    independent client -- its own app, its own test client, never handed the
    first client's response or any Python state -- reads it back through
    GET /api/types/<slug>/decisions. The only channel between them is the
    Postgres schema clean_db just truncated, which is exactly the channel
    `design.md` M6 requires ("all viewers read and write the same rows").
    """
    second_viewer = independent_client()

    posted = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "visit",
              "note": "Needs a sign -- recorded by viewer one."},
    )
    assert posted.status_code == 200

    seen = second_viewer.get("/api/types/blocking-driveway/decisions").get_json()

    assert len(seen["decisions"]) == 1
    entry = seen["decisions"][0]
    assert entry["updated_at"] is not None
    entry = {k: v for k, v in entry.items() if k != "updated_at"}
    assert entry == {
        "scope": "doorway", "item_key": "1-first-st", "decision": "visit",
        "note": "Needs a sign -- recorded by viewer one.",
        "role": mirror_triage.PLACEHOLDER_ROLE,
    }


def test_posting_the_same_item_twice_updates_in_place_rather_than_duplicating(client, clean_db):
    first = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "visit", "note": "v1"},
    ).get_json()
    second = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "sched", "note": "v2"},
    ).get_json()

    data = client.get("/api/types/blocking-driveway/decisions").get_json()

    assert len(data["decisions"]) == 1  # not two rows for the same doorway
    assert data["decisions"][0]["decision"] == "sched"
    assert data["decisions"][0]["note"] == "v2"
    # The row's own updated_at moved between the two writes.
    assert second["updated_at"] >= first["updated_at"]


def test_doorway_and_block_decisions_with_the_same_item_key_do_not_collide(client, clean_db):
    """The table's UNIQUE constraint is (violation_type, scope, item_key) --
    scope is part of the key precisely so a doorway and a block that happen
    to share key text (unlikely, but the two key spaces are unrelated
    strings -- an address slug and a census block id) are still two rows,
    not one clobbering the other.
    """
    client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "12090999", "decision": "visit"},
    )
    client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "block", "item_key": "12090999", "decision": "study"},
    )

    data = client.get("/api/types/blocking-driveway/decisions").get_json()
    by_scope = {d["scope"]: d for d in data["decisions"]}

    assert len(data["decisions"]) == 2
    assert by_scope["doorway"]["decision"] == "visit"
    assert by_scope["block"]["decision"] == "study"


def test_a_decision_under_one_violation_type_does_not_appear_under_another(clean_db):
    """`server.py` only routes one canonical type today (5.2's job to route
    the rest), so this exercises the scoping `mirror.triage` itself provides
    -- directly, at the persistence layer -- rather than through a second
    HTTP route that does not exist yet. Task 6.1's scope note: "A decision on
    one type's list must not appear on another's."
    """
    mirror_triage.record(clean_db, "Blocking Driveway", "doorway", "1-first-st", "visit")
    clean_db.commit()

    same_type = mirror_triage.list_for(clean_db, "Blocking Driveway")
    other_type = mirror_triage.list_for(clean_db, "No Parking Sign")

    assert len(same_type) == 1
    assert other_type == []


def test_decision_role_is_a_placeholder_pending_task_6_6(client, clean_db):
    """Task 6.1's scope note: the `role` column is NOT NULL, so something
    sane must be written even though role *selection* is task 6.6's, not
    this one's. This just pins today's value so a change to the placeholder
    is a deliberate edit, not an accident -- it is not a claim that
    "unspecified" is the final word on what role means.
    """
    resp = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "visit"},
    )

    assert resp.get_json()["role"] == "unspecified" == mirror_triage.PLACEHOLDER_ROLE


def test_decision_with_an_invalid_scope_is_rejected_not_silently_dropped(client, clean_db):
    resp = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "not-a-scope", "item_key": "1-first-st", "decision": "visit"},
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid"
    assert client.get("/api/types/blocking-driveway/decisions").get_json()["decisions"] == []


def test_decision_missing_required_fields_is_rejected(client, clean_db):
    for body in ({"scope": "doorway", "decision": "visit"},          # no item_key
                  {"scope": "doorway", "item_key": "1-first-st"},    # no decision
                  {}):
        resp = client.post("/api/types/blocking-driveway/decisions", json=body)
        assert resp.status_code == 400


def test_decisions_endpoints_404_for_an_untracked_slug(client):
    resp = client.get("/api/types/not-a-real-type/decisions")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "untracked"

    resp = client.post(
        "/api/types/not-a-real-type/decisions",
        json={"scope": "doorway", "item_key": "x", "decision": "visit"},
    )
    assert resp.status_code == 404


def test_index_page_persists_decisions_through_the_server_not_local_storage_only(client):
    """A page-shape check, not an end-to-end browser test: the served page's
    script must call the new shared-storage endpoints from its boot and save
    paths, so a decision recorded in one browser is not silently confined to
    that browser's localStorage (the pre-6.1 defect design.md M6 and
    proposal.md describe). `test_index_served_at_root_with_no_auth_and_no_build_step`
    already checks the page has no build step; this checks the specific
    strings that wire triage to the server.
    """
    body = client.get("/").get_data(as_text=True)

    assert "/api/types/${SLUG}/decisions" in body
    assert "loadDecisions" in body
    assert "saveDecision" in body
    # The pre-6.1 fallback is still present (6.3 removes it), just not the
    # active path any more.
    assert "loadLocal" in body
    assert "saveLocal" in body
    assert 'useCap("db")' in body
