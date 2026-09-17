"""Task 5.1: the served application (`src/app/server.py`), exercised through
Flask's test client end to end -- route -> `mirror.derive.derive` -> JSON --
against a real connection to the throwaway `mirror_test` schema
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


def seed_driveway_call(conn, request_id, address, when, towed="N", lat=0.5, lon=0.5):
    """One call filed under a raw label the canonical 'Blocking Driveway' grouping
    covers exactly (`violation_types.raw_labels_for("Blocking Driveway")`) -- the
    type the app's default route serves. `object_id` is just `request_id`; the
    two id spaces never need to differ in a test.
    """
    insert_service_request(conn, request_id, request_id, address, when, lat=lat, lon=lon)
    base = request_id * 10
    insert_custom_field(conn, base, request_id, "Alleged Violation",
                        "Blocking Driveway (DISPATCH)")
    insert_custom_field(conn, base + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, base + 2, request_id, "Vehicle Make", "FORD")
    insert_custom_field(conn, base + 3, request_id, "Vehicle Model", "F150")
    insert_custom_field(conn, base + 4, request_id, "Vehicle Colour", "BLUE")


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


@pytest.fixture
def client(clean_db):
    """A Flask test client whose every request opens a real connection to the
    same throwaway schema `clean_db` just truncated. `create_app()` is called
    with no override: `tests/conftest.py`'s `db` fixture has already pointed
    `HFX_MIRROR_SCHEMA` at `mirror_test` for the whole session, and
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
    # slug is a JS template-literal interpolation (`/api/types/${DEFAULT_SLUG}/
    # doorways`), not a literal path, so this checks for the constant that
    # resolves it plus the route pattern it is spliced into.
    assert '"blocking-driveway"' in body
    assert "/map-network.json" in body
    assert "/api/types/${DEFAULT_SLUG}/doorways" in body
    assert "/api/types/${DEFAULT_SLUG}/blocks" in body
    # No bundled or externally hosted script -- the only <script> is inline,
    # matching design.md M12's "no build step, no front-end framework."
    assert "<script src" not in body


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


def test_an_untracked_or_unrecognised_slug_answers_404_not_an_empty_list(client):
    """Task 5.1 deliberately does not build type switching: only the default
    slug resolves. A different real canonical type's slug ('No Parking Sign')
    and a slug naming nothing at all are treated identically today -- both
    answer 404 rather than silently rendering an empty list, so 5.2/5.3 have a
    clear signal to build on rather than a response that looks like a type
    with zero doorways.
    """
    for slug in ("no-parking-sign", "not-a-real-type"):
        resp = client.get(f"/api/types/{slug}/doorways")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "untracked"

        resp = client.get(f"/api/types/{slug}/blocks")
        assert resp.status_code == 404


def test_doorways_and_blocks_endpoints_agree_on_the_type_they_scope_to(client, clean_db):
    seed_two_doorway_block(clean_db)

    door = client.get("/api/types/blocking-driveway/doorways").get_json()
    block = client.get("/api/types/blocking-driveway/blocks").get_json()

    assert door["type"] == block["type"] == app_server.DEFAULT_CANONICAL_TYPE
    assert door["slug"] == block["slug"] == app_server.DEFAULT_SLUG
