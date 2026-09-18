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
import re

import pytest

from app import server as app_server
from mirror import derive as mirror_derive
from mirror import triage as mirror_triage
from mirror import type_figures as mirror_type_figures

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


def seed_call(conn, request_id, address, when, raw_label, towed="N", lat=0.5, lon=0.5,
             district="7"):
    """One call filed under `raw_label` -- any raw label
    `violation_types.RAW_LABEL_TO_CANONICAL` knows, so this seeds a call for
    whichever canonical type that label belongs to. `object_id` is just
    `request_id`; the two id spaces never need to differ in a test.
    """
    insert_service_request(conn, request_id, request_id, address, when, lat=lat, lon=lon,
                           district=district)
    base = request_id * 10
    insert_custom_field(conn, base, request_id, "Alleged Violation", raw_label)
    insert_custom_field(conn, base + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, base + 2, request_id, "Vehicle Make", "FORD")
    insert_custom_field(conn, base + 3, request_id, "Vehicle Model", "F150")
    insert_custom_field(conn, base + 4, request_id, "Vehicle Colour", "BLUE")


def seed_driveway_call(conn, request_id, address, when, towed="N", lat=0.5, lon=0.5,
                       district="7"):
    """One call filed under a raw label the canonical 'Blocking Driveway' grouping
    covers exactly (`violation_types.raw_labels_for("Blocking Driveway")`) -- the
    type the app's default route serves.
    """
    seed_call(conn, request_id, address, when, "Blocking Driveway (DISPATCH)",
              towed=towed, lat=lat, lon=lon, district=district)


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


def test_tow_thesis_reads_stored_figures_not_a_frozen_number(client):
    """Task 5.8 removes both 5.2's stopgap (which blanked the tow-thesis
    sentence for every non-default type rather than let it silently show
    Blocking Driveway's own number under another type's view) and the frozen
    number itself. The served markup now fetches
    `/api/types/<slug>/figures` (`mirror.type_figures`, task 5.7's store) and
    fills the sentence from whatever is stored for SLUG -- reporting "not
    yet available" with the server's own stated reason when nothing is
    stored, never a frozen number and never a per-request computation. A
    full render-and-read check would need a running browser, which this
    environment does not have; this pins that the frozen number and the
    SLUG-based blanking guard are both gone, and that the fetch and the
    fallback text are present in what ships.
    """
    body = client.get("/").get_data(as_text=True)

    assert 'id="tow-thesis"' in body
    assert "44.7 per cent against 44.6 per cent" not in body
    assert "SLUG !== DEFAULT_SLUG" not in body
    assert "/api/types/${SLUG}/figures" in body
    assert "not yet available" in body


def test_tow_thesis_conclusion_varies_by_stored_conclusion_key(client):
    """Task 7.5's judgment call: 5.8 left the tow-thesis sentence a fixed
    template ("A tow does not lower the chance a doorway calls again") with
    only the two recurrence percentages substituted per type, even though
    `mirror.per_type` stores a `tow.conclusion_key` (tow_lower/tow_higher/
    no_difference/sample_too_small) that does not always match that framing --
    a type whose interval actually rules "higher" would have been described as
    "does not lower" regardless. This is now read from the stored
    `tow.conclusion` fragment itself (already phrased correctly per key by
    `mirror.per_type._tow_conclusion`), not rebuilt from a template here.

    No browser exists in this environment to execute the script and observe
    the rendered text for each key (that would need a running mirror with
    types seeded to each of the four conclusions); this pins what does not
    need one -- that the old fixed leading clause is gone, that the script
    reads the structured fields a varying conclusion requires, and that the
    caveat these fields carry (`tow.caveat`, task 4.12) is rendered alongside
    a substantive conclusion but not a `sample_too_small` one, matching
    `mirror.per_type._type_conclusion`'s own choice.
    """
    body = client.get("/").get_data(as_text=True)

    assert "A tow does not lower the chance a doorway calls again" not in body
    assert "tow.conclusion_key" in body
    assert "tow.conclusion" in body
    assert "tow.caveat" in body
    assert '"sample_too_small"' in body


def test_interpretation_limits_are_footnoted_at_every_figure_they_qualify(client):
    """Task 7.5: the six interpretation limits (intake clock, tow as the only
    recorded outcome, vehicle identity as a floor, text-address reduction,
    derived block labels, block-wide dwelling rate) survive being served, but
    5.8/design.md M8 only ever stated them generically in the footer -- a
    viewer who never scrolls that far never sees them, even though they
    qualify the headline stats and thesis sentence shown above the fold under
    every one of the 30 routed types (`ORCHESTRATION-HANDOFF.md`'s "wider
    Driveway narrative" concern).

    Each limit now has a real, visible, keyboard-and-touch-reachable `<a
    class="lim" href="#lim-...">` next to the figure it bears on -- not just a
    hover-only `title` (task 7.6's own note flags hover-only text as invisible
    on touch and to keyboard users) -- landing on a matching `id="lim-...""`
    in the footer's "Read these numbers carefully" section. This checks both
    ends of each link exist, for every one of the six limits, and that each
    footnoted figure/sentence this task names is covered: the doorways-still-
    calling and blocks-with-2+-calling stats, the tow and vehicle-uniqueness
    stats, and the tow-thesis and block-doorways-calling sentences.
    """
    body = client.get("/").get_data(as_text=True)

    lim_ids = [
        "lim-intake", "lim-tow", "lim-vehicle", "lim-address",
        "lim-block-label", "lim-dwelling",
    ]
    for lim_id in lim_ids:
        href_count = body.count(f'href="#{lim_id}"')
        assert href_count >= 1, f"no footnote link points at #{lim_id}"
        assert f'id="{lim_id}"' in body, f"footer has no target for #{lim_id}"

    # The stats a viewer sees without expanding "Why blocks? Read the
    # analysis" each carry at least one footnote of their own.
    assert 'id="n-doorways-stat"' in body
    assert re.search(
        r'id="n-doorways-stat">[^<]*</span><span class="k">doorways still calling'
        r'</span>(<a class="lim"[^>]*>\[[^\]]+\]</a>){2}',
        body,
    ), "doorways-still-calling stat carries no footnote link"
    assert re.search(
        r'id="n-blocks-stat">[^<]*</span><span class="k">blocks with 2\+ calling'
        r'</span>(<a class="lim"[^>]*>\[[^\]]+\]</a>){2}',
        body,
    ), "blocks-with-2+-calling stat carries no footnote link"
    assert re.search(
        r'id="tow-pct-stat">[^<]*</span><span class="k">ended in a tow'
        r'</span><a class="lim"[^>]*href="#lim-tow"',
        body,
    ), "tow-rate stat carries no footnote link to the tow-as-only-outcome limit"
    assert re.search(
        r'id="unique-pct-stat">[^<]*</span><span class="k">of vehicles unique'
        r'</span><a class="lim"[^>]*href="#lim-vehicle"',
        body,
    ), "uniqueness stat carries no footnote link to the vehicle-floor limit"

    # The thesis sentence's tow clause and its "distinct cars"/"block where a
    # neighbour is also still calling" clauses each carry their own footnote,
    # rather than the page relying on the footer alone.
    assert re.search(r'id="tow-thesis"[^<]*</span><a class="lim"[^>]*href="#lim-tow"', body)
    assert re.search(
        r'recorded vehicles were different cars</b><a class="lim"[^>]*href="#lim-vehicle"',
        body,
    )
    assert re.search(
        r'also still calling</b>(<a class="lim"[^>]*>\[[^\]]+\]</a>){2}',
        body,
    ), "the block-doorways-calling sentence carries no footnote link"

    # Each footer entry states the limit in terms a reader does not need the
    # rest of the page to understand.
    assert "logged the call" in body            # lim-intake
    assert "no recorded outcome" in body         # lim-tow (already present pre-7.5)
    assert "undercount" in body                  # lim-vehicle
    assert "different ways by HRM" in body        # lim-address
    assert "not a boundary HRM" in body          # lim-block-label
    assert "whole census block" in body          # lim-dwelling


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
    # "rp" (repeat_calls) is one addition beyond that shape, task 5.5c's own:
    # the one figure the recurrence window governs, exposed so a viewer (and
    # a test, over real HTTP) can observe recur_days changing it without
    # changing the listed set.
    assert set(data["rows"][0]) == {
        "i", "a", "d", "c", "st", "bk", "nb", "m", "t", "w", "vd", "vs", "g", "l",
        "o", "lat", "lon", "rp",
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


# ---------------------------------------------- filter query parameters (5.5a, 5.5b)


def seed_two_tier_doorways(conn):
    """One census block, one doorway with five calls all older than 10 days
    but inside 365 ("9 OLD ST"), one doorway with two calls inside every
    window tested ("1 NEW ST") -- the same fixture shape
    `tests/test_derive_recency.py`'s ranking test uses, seeded here to prove
    the *route*, not just `derive_with_recency`, answers a widened
    `recency_days` with a different listed set and a different top rank.
    """
    insert_census_area(conn, "12090999", SQUARE_RING)
    for i, days_ago in enumerate((50, 55, 60, 65, 70)):
        seed_driveway_call(conn, 6000 + i, "9 OLD ST, HALIFAX",
                           LATEST - datetime.timedelta(days=days_ago))
    seed_driveway_call(conn, 6100, "1 NEW ST, HALIFAX", LATEST)
    seed_driveway_call(conn, 6101, "1 NEW ST, HALIFAX", LATEST - datetime.timedelta(days=5))


def test_doorways_api_default_recency_omits_the_older_of_two_doorways(client, clean_db):
    """Baseline for the next test: at the default (no query string), both
    doorways are within 365 days, so both are listed and "9 OLD ST" (5 calls)
    outranks "1 NEW ST" (2 calls).
    """
    seed_two_tier_doorways(clean_db)

    resp = client.get("/api/types/blocking-driveway/doorways?min_calls=1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert [r["a"] for r in data["rows"]] == ["9 Old St", "1 New St"]
    assert data["filters"] == {
        "district": None, "min_calls": 1, "min_doorways": 2,
        "recur_days": 365, "recency_days": 365,
    }


def test_recency_days_query_param_changes_the_listed_set_and_ranking_over_http(client, clean_db):
    """Task 5.5b's own verification clause, proven over the real HTTP surface
    (not by calling `derive()`/`derive_with_recency` directly, which is the
    gap 5.5's own note flagged): narrowing `recency_days` to 10 over the wire
    drops "9 OLD ST" from the doorway list entirely, changing both which
    doorways are listed and, since it was the top-ranked row a moment ago,
    the ranking.
    """
    seed_two_tier_doorways(clean_db)

    resp = client.get(
        "/api/types/blocking-driveway/doorways?min_calls=1&recency_days=10"
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert [r["a"] for r in data["rows"]] == ["1 New St"]
    assert data["filters"]["recency_days"] == 10


def test_min_calls_query_param_changes_the_doorway_list_over_http(client, clean_db):
    seed_two_doorway_block(clean_db)  # two doorways, two calls each

    default_resp = client.get("/api/types/blocking-driveway/doorways")
    widened_resp = client.get("/api/types/blocking-driveway/doorways?min_calls=1")

    assert default_resp.get_json()["count"] == 2
    assert widened_resp.get_json()["count"] == 2  # both already clear min_calls=2
    # A threshold above what either doorway reaches drops both.
    narrowed_resp = client.get("/api/types/blocking-driveway/doorways?min_calls=3")
    assert narrowed_resp.get_json()["rows"] == []
    assert narrowed_resp.get_json()["filters"]["min_calls"] == 3


def test_district_query_param_narrows_the_doorway_list_over_http(client, clean_db):
    seed_driveway_call(clean_db, 6201, "1 A ST, HALIFAX", LATEST, district="7")
    seed_driveway_call(clean_db, 6202, "1 A ST, HALIFAX",
                       LATEST - datetime.timedelta(days=1), district="7")
    seed_driveway_call(clean_db, 6203, "2 B ST, HALIFAX", LATEST, district="9")
    seed_driveway_call(clean_db, 6204, "2 B ST, HALIFAX",
                       LATEST - datetime.timedelta(days=1), district="9")

    resp = client.get("/api/types/blocking-driveway/doorways?district=9")

    assert resp.status_code == 200
    data = resp.get_json()
    assert [r["d"] for r in data["rows"]] == ["9"]
    assert data["filters"]["district"] == "9"


def test_min_doorways_query_param_changes_the_block_list_over_http(client, clean_db):
    seed_two_doorway_block(clean_db)  # one block, two still-calling doorways

    default_resp = client.get("/api/types/blocking-driveway/blocks")
    assert default_resp.get_json()["count"] == 1  # min_doorways defaults to 2

    narrowed_resp = client.get("/api/types/blocking-driveway/blocks?min_doorways=3")
    assert narrowed_resp.get_json()["count"] == 0
    assert narrowed_resp.get_json()["filters"]["min_doorways"] == 3


def test_recur_days_query_param_is_distinct_from_recency_days_over_http(client, clean_db):
    """design.md M11: the recurrence window only changes the `repeat_calls`
    column; it must never change which doorways are listed. Two calls at the
    same address, 40 days apart -- narrowing `recur_days` below 40 must drop
    `repeat_calls` to 0 while the doorway itself stays listed (its
    `calls_12mo` is governed by `recency_days`, untouched here).
    """
    seed_driveway_call(clean_db, 6301, "1 PAIR ST, HALIFAX", LATEST)
    seed_driveway_call(clean_db, 6302, "1 PAIR ST, HALIFAX",
                       LATEST - datetime.timedelta(days=40))

    wide = client.get(
        "/api/types/blocking-driveway/doorways?min_calls=1&recur_days=365"
    ).get_json()
    narrow = client.get(
        "/api/types/blocking-driveway/doorways?min_calls=1&recur_days=10"
    ).get_json()

    assert wide["count"] == narrow["count"] == 1
    assert wide["rows"][0]["m"] == narrow["rows"][0]["m"]  # calls_12mo unaffected
    assert wide["rows"][0]["g"] is not None  # median_gap_days: repeat visible either way
    assert narrow["filters"]["recur_days"] == 10


def seed_recency_vs_recurrence_fixture(conn):
    """Task 5.5c's own fixture shape: two doorways, "9 Far St" with calls
    only 50-55 days back and "1 Near St" with calls only 0-5 days back --
    "one with calls only within a narrow recency window and one with calls
    further back," in the task's own words.
    """
    insert_census_area(conn, "12090999", SQUARE_RING)
    seed_driveway_call(conn, 7001, "9 FAR ST, HALIFAX", LATEST - datetime.timedelta(days=50))
    seed_driveway_call(conn, 7002, "9 FAR ST, HALIFAX", LATEST - datetime.timedelta(days=55))
    seed_driveway_call(conn, 7003, "1 NEAR ST, HALIFAX", LATEST)
    seed_driveway_call(conn, 7004, "1 NEAR ST, HALIFAX", LATEST - datetime.timedelta(days=5))


def test_recency_vs_recurrence_windows_govern_different_things_over_http(client, clean_db):
    """Task 5.5c: the toolbar's two windows share a 365-day default and read
    as near-synonyms at a glance, but govern different things (design.md
    M11) -- proven here over real HTTP (not by calling
    `derive_with_recency()`/`hotspots.build()` directly), not assumed from
    the algebra:

    - narrowing `recency_days` below 50 drops "9 Far St" from the listed set
      entirely -- it changes *which* doorways are listed;
    - narrowing `recur_days` below the 5-day gap between "1 Near St"'s two
      calls changes only that doorway's own repeat figure (`rp`,
      `hotspots.build()`'s `repeat_calls`, exposed on the doorway payload for
      exactly this) from 1 to 0 -- the listed set (both doorways, same two
      addresses) and "1 Near St"'s own `calls_12mo` (`m`, recency's figure)
      are unchanged.
    """
    seed_recency_vs_recurrence_fixture(clean_db)

    default_resp = client.get("/api/types/blocking-driveway/doorways?min_calls=1").get_json()
    assert sorted(r["a"] for r in default_resp["rows"]) == ["1 Near St", "9 Far St"]

    narrow_recency = client.get(
        "/api/types/blocking-driveway/doorways?min_calls=1&recency_days=20"
    ).get_json()
    assert [r["a"] for r in narrow_recency["rows"]] == ["1 Near St"]

    wide_recur = client.get(
        "/api/types/blocking-driveway/doorways?min_calls=1&recur_days=365"
    ).get_json()
    narrow_recur = client.get(
        "/api/types/blocking-driveway/doorways?min_calls=1&recur_days=1"
    ).get_json()

    # recur_days never changes the listed set.
    assert sorted(r["a"] for r in wide_recur["rows"]) == ["1 Near St", "9 Far St"]
    assert sorted(r["a"] for r in narrow_recur["rows"]) == ["1 Near St", "9 Far St"]

    near_wide = next(r for r in wide_recur["rows"] if r["a"] == "1 Near St")
    near_narrow = next(r for r in narrow_recur["rows"] if r["a"] == "1 Near St")
    assert near_wide["rp"] == 1  # the two calls are 5 days apart, within a 365-day window
    assert near_narrow["rp"] == 0  # ... but not within a 1-day recurrence window
    assert near_wide["m"] == near_narrow["m"]  # calls_12mo (recency's own figure) unaffected


def test_filters_combination_that_excludes_everything_returns_200_with_empty_rows_not_an_error(
    client, clean_db
):
    """Task 5.5e, the server half of "distinguishable from a failure": a
    filter combination that matches nothing is a normal, successful response
    -- 200, an empty `rows` list, the `filters` actually in effect -- never a
    4xx/5xx that a network failure or a bug would also produce.
    `web/app/index.html`'s render()/filterSummary() reads exactly this shape
    to name the values that excluded everything (see the page-shape test
    alongside this one).
    """
    seed_two_doorway_block(clean_db)  # both doorways are in district "7"

    resp = client.get("/api/types/blocking-driveway/doorways?district=9&min_calls=1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["rows"] == []
    assert data["count"] == 0
    assert data["filters"] == {
        "district": "9", "min_calls": 1, "min_doorways": 2,
        "recur_days": 365, "recency_days": 365,
    }


def test_a_malformed_filter_value_falls_back_to_the_default_rather_than_500ing(client, clean_db):
    seed_two_doorway_block(clean_db)

    resp = client.get("/api/types/blocking-driveway/doorways?min_calls=not-a-number")

    assert resp.status_code == 200
    assert resp.get_json()["filters"]["min_calls"] == 2  # derive()'s own default


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


def test_untracked_type_page_states_a_reason_rather_than_a_bare_404(client):
    """Task 5.3: an unknown slug used to get Flask's generic "Not Found" page
    (`abort(404)`); it now gets a rendered explanation naming the slug and
    linking every tracked type, still at a 404 status -- the resource really
    does not exist, only the body now says why instead of leaving a viewer to
    guess whether it was a typo, a stale link, or a server problem.
    """
    resp = client.get("/types/not-a-real-type")

    assert resp.status_code == 404
    assert "text/html" in resp.content_type
    body = resp.get_data(as_text=True)
    assert "not-a-real-type" in body
    assert "not a tracked type" in body
    # Every tracked type is offered as a way forward, not just asserted absent.
    assert 'href="/types/no-parking-sign"' in body
    assert 'href="/"' in body


def test_untracked_type_page_escapes_the_slug(client):
    """The slug is an untrusted path segment echoed back into HTML -- Jinja
    auto-escaping (`render_template_string`) must neutralize it rather than
    have it land in the page unescaped.
    """
    resp = client.get("/types/%3Cscript%3Ealert(1)%3C/script%3E")

    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "<script>alert(1)" not in body


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


def test_decisions_survive_an_application_restart(client, clean_db):
    """Task 6.2. Nothing about a decision lives in this process: `record()`
    and `list_for()` (`src/mirror/triage.py`) open a connection, write or
    read `triage_decisions`, and hold no module-level cache across calls --
    `create_app()` builds a fresh Flask app with no state carried over
    either. So "the application restarts" is exactly `independent_client()`
    (a wholly new `create_app()`, sharing nothing in-process with `client`)
    called *after* the write, standing in for the process that made the
    write having already exited. The only thing that could make a decision
    survive that is the Postgres row itself -- which this proves is what
    actually carries it, not some server-side cache that would vanish on a
    real restart along with the process that built it.
    """
    posted = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "sched",
              "note": "Sign ordered, install pending.", "role": "HRM coordinator"},
    )
    assert posted.status_code == 200

    # Stand in for the process restarting: a brand new app, a brand new
    # client, sharing no Python object with the one that made the write --
    # only the same underlying Postgres schema.
    restarted = independent_client()

    seen = restarted.get("/api/types/blocking-driveway/decisions").get_json()

    assert len(seen["decisions"]) == 1
    entry = seen["decisions"][0]
    assert entry["decision"] == "sched"
    assert entry["note"] == "Sign ordered, install pending."
    assert entry["role"] == "HRM coordinator"
    assert entry["updated_at"] is not None


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


def test_decision_role_defaults_to_a_placeholder_when_none_is_supplied(client, clean_db):
    """Task 6.1's scope note: the `role` column is NOT NULL, so something
    sane must be written even for a request that carries no `role` at all --
    an older client, or a bare API call made by hand. Task 6.6 is what asks
    the served page's own viewers for a real role before they ever reach
    this route; this just pins the fallback for everyone else, so a change
    to the placeholder is a deliberate edit, not an accident.
    """
    resp = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "visit"},
    )

    assert resp.get_json()["role"] == "unspecified" == mirror_triage.PLACEHOLDER_ROLE


def test_decision_role_is_persisted_when_the_request_supplies_one(client, clean_db):
    """Task 6.6: `web/app/index.html`'s `ensureRole()` asks a viewer to pick a
    role before it ever calls `saveDecision()`, and that role rides along in
    the POST body. This is the server-side half of that path: a `role` the
    request actually supplies is written and read back verbatim, not
    overridden by `PLACEHOLDER_ROLE` (which `record()` only reaches for when
    the field is missing or blank -- see the previous test).
    """
    resp = client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "visit",
              "role": "Parking enforcement officer"},
    )

    assert resp.get_json()["role"] == "Parking enforcement officer"

    seen = client.get("/api/types/blocking-driveway/decisions").get_json()
    assert seen["decisions"][0]["role"] == "Parking enforcement officer"


def test_role_grants_no_differing_access_to_the_shared_list(client, clean_db):
    """Task 6.7: "the application makes no access-control claim and grants no
    differing access by role." There is no code path anywhere in this route
    that reads a `role` to decide what a request may see or do -- proven
    here by two decisions recorded under two different, even made-up, role
    strings (nothing restricts `role` to a fixed enum, because it identifies
    rather than authenticates) both landing in the same list, readable by a
    request that supplies no role of its own at all.
    """
    client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "doorway", "item_key": "1-first-st", "decision": "visit",
              "role": "HRM coordinator"},
    )
    client.post(
        "/api/types/blocking-driveway/decisions",
        json={"scope": "block", "item_key": "12090999", "decision": "study",
              "role": "someone who typed anything at all"},
    )

    # No auth header, no cookie, no role of its own -- a bare GET still sees
    # both roles' decisions, in full.
    seen = client.get("/api/types/blocking-driveway/decisions").get_json()
    roles_seen = {d["role"] for d in seen["decisions"]}

    assert len(seen["decisions"]) == 2
    assert roles_seen == {"HRM coordinator", "someone who typed anything at all"}


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
    script must call the shared-storage endpoints from its boot and save
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


def test_index_page_has_no_local_storage_fallback_left(client):
    """Task 6.3: the pre-6.1 `localStorage` fallback and the
    `window.claude.use("db")` binding it fell back through are gone from the
    served page entirely -- not merely dead and unreachable, as 6.1 left
    them, but removed, so no code path in this file can leave a viewer
    believing a local-only decision was shared. `useCap` itself is not
    asserted against here: it also backs the unrelated "Ask Claude" advice
    sampler (`window.claude.use("sample")`), which this task does not touch.
    """
    body = client.get("/").get_data(as_text=True)

    # The functions and the storage calls themselves are gone -- checked as
    # code, not as bare substrings, so a comment that still narrates this
    # history by name (as the surrounding code does) cannot make this test
    # pass by accident.
    assert "function loadLocal(" not in body
    assert "function saveLocal(" not in body
    assert "localStorage.getItem" not in body
    assert "localStorage.setItem" not in body
    assert 'useCap("db")' not in body
    assert "onSnapshot" not in body  # the removed live-update block's own call


def test_index_page_asks_for_a_role_before_recording_a_decision(client):
    """Task 6.6: a role is asked for -- `ensureRole()`/`askRole()` gate
    `save()`, the one function every decision-recording control (`data-s`
    buttons, the note textarea) calls -- before a decision is ever sent to
    the server. Nothing in the row list, the filters, the search box or the
    map calls `ensureRole()`, so those stay readable with no role picked.
    """
    body = client.get("/").get_data(as_text=True)

    assert "ensureRole" in body
    assert "askRole" in body
    assert "const role = await ensureRole();" in body


def test_index_page_states_role_identifies_not_authenticates(client):
    """Task 6.7: design.md M7's own language -- "the application says
    plainly that roles identify rather than authenticate" -- has to actually
    appear somewhere a viewer reads it, not just in this repository's design
    notes. The role prompt is where a viewer chooses a role, so it is where
    that statement has to live.
    """
    body = client.get("/").get_data(as_text=True)

    assert "does not log you in" in body


def test_index_page_shows_role_and_last_changed_time_on_a_decision(client):
    """Task 6.5: a triaged doorway or block names who marked it and when.
    `detail()` is the one function that renders that for both a doorway and
    a block (isBlock only changes which evidence/labels it reads, not which
    function builds the meta line), so one string check here covers both."""
    body = client.get("/").get_data(as_text=True)

    assert '"Marked by "' in body
    assert "Last changed" in body


def test_index_page_rolls_back_an_optimistic_decision_on_a_failed_save(client):
    """Task 6.4: `save()` used to update `DEC` (and thus the rendered row)
    before `saveDecision()`'s `fetch()` had resolved, and never undid that
    if the POST failed -- a viewer could see a decision as recorded that the
    server never got. `save()` now keeps `previous` and, when
    `saveDecision()` returns `false`, restores it and re-renders, so the row
    stops claiming a decision that was not actually persisted. This is a
    page-shape check, not a browser test (no JS harness exists in this
    repository -- see the module docstring's description of what this file
    covers): it pins the specific rollback and the specific "not recorded"
    wording, so a future edit that quietly drops either is a visible diff
    here, not a silent regression.
    """
    body = client.get("/").get_data(as_text=True)

    assert "const previous = DEC[it.key] ? {...DEC[it.key]} : null;" in body
    assert "if (previous) DEC[it.key] = previous; else delete DEC[it.key];" in body
    assert "has not been recorded" in body
    # saveDecision() must report success/failure for save() to act on --
    # bare fire-and-forget would leave nothing to roll back on.
    assert "return true;" in body
    assert "return false;" in body


# ------------------------------------------------- filter controls (5.5c/5.5e/7.4)


def test_index_page_labels_recency_and_recurrence_windows_distinctly(client):
    """Task 5.5c: the bare labels alone ("Recency window (days)" /
    "Recurrence window (days)") read as near-synonyms at a glance and share
    a 365-day default (design.md M11) -- a `title` hover hint on each states,
    in different words, exactly what that control alone governs, so a
    viewer is not left to guess which one changes the list and which one
    only changes a count. The behavioural half of this task is proven over
    HTTP by test_app.py's
    test_recency_vs_recurrence_windows_govern_different_things_over_http,
    not by this page-shape check.
    """
    body = client.get("/").get_data(as_text=True)

    assert 'for="recency-days" title="Which doorways are listed at all' in body
    assert 'for="recur-days" title="How a repeat call is counted' in body


def test_index_page_names_the_filters_in_effect_when_a_combination_lists_nothing(client):
    """Task 5.5e: an empty filter result says so plainly and names the
    values that excluded everything (`district X`, `min. recent calls >=
    N`, ...) via `filterSummary()`, reading `FILTERS` -- the same `filters`
    key the doorways/blocks routes echo back (see
    test_filters_combination_that_excludes_everything_returns_200_with_empty_rows_not_an_error
    for the server side of this). That wording must not collide with the
    two genuine-failure messages boot() already shows for a dropped
    connection or a non-2xx response -- three distinct situations get three
    distinct sentences, so an empty list from a narrow filter is never
    mistaken for either kind of failure.
    """
    body = client.get("/").get_data(as_text=True)

    assert "function filterSummary(){" in body
    assert "No ${noun} match ${filterSummary()}." in body
    assert "Widen a filter above and Apply filters again." in body
    assert "Could not reach the server. Check your connection and reload." in body
    assert "The server could not produce this list just now. Reload to try again." in body


def test_index_page_states_the_recency_windows_anchor_date_and_length(client):
    """Task 7.4: every filtered list states the recency window's length
    (`FILTERS.recency_days`) and the date it is measured from -- the
    mirror's true latest call date (`doorPayload.latest`, not "now" and not
    the shifted value `mirror.derive_recency` feeds internally to
    `hotspots.build()`) -- wired to a dedicated `#recency-note` element next
    to `#showing` so it appears on every list, not only an empty one.
    """
    body = client.get("/").get_data(as_text=True)

    assert 'id="recency-note"' in body
    assert (
        "Showing calls from the last ${FILTERS.recency_days} days, "
        "since ${doorPayload.latest}." in body
    )


# --------------------------------------------------------- type figures (5.7/5.8)


def test_figures_route_reports_unavailable_when_nothing_is_stored(client):
    """Task 5.8: a canonical type with no `type_figures` row for the mirror's
    current version answers `available: false` with a stated reason -- read
    straight through from `mirror.type_figures.figures_for` -- never a bare
    `null` and never a number computed here as a fallback (`server.py` calls
    `figures_for` only; it never touches `mirror.per_type`/`mirror.figures`).
    """
    resp = client.get("/api/types/blocking-driveway/figures")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["type"] == "Blocking Driveway"
    assert data["slug"] == "blocking-driveway"
    assert data["available"] is False
    assert isinstance(data["reason"], str) and data["reason"]
    assert "figures" not in data


def test_figures_route_404s_for_an_untracked_slug(client):
    resp = client.get("/api/types/not-a-real-type/figures")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "untracked"


def test_figures_route_serves_what_type_figures_stored(client, clean_db):
    """Once `mirror.type_figures.compute_and_store` (task 5.7) has stored a
    row for a type, this route serves exactly that row -- the tow comparison
    included -- rather than deriving anything itself. This is what
    `web/app/index.html`'s tow-thesis sentence reads.
    """
    seed_two_doorway_block(clean_db)

    # Default parameters, deliberately not overridden: `figures_for` (what the
    # route calls) reads back under `type_figures.DEFAULT_PARAMETERS` unless a
    # caller asks otherwise, and this route asks for nothing else -- a
    # `bootstrap_iterations` override here would store a row under a
    # different `parameters` key than the one the route reads, and the route
    # would then correctly (if confusingly, for a test) report `available:
    # false`. Only 4 calls are seeded, well under `per_type.MIN_TOW_GROUP_SAMPLE`
    # (30), so the cluster bootstrap never actually runs regardless of the
    # iteration count -- there is no speed cost to leaving it at the default.
    outcomes = mirror_type_figures.compute_and_store(
        clean_db, types=[app_server.DEFAULT_CANONICAL_TYPE], log=lambda m: None,
    )
    assert outcomes and outcomes[0]["ok"]

    resp = client.get("/api/types/blocking-driveway/figures")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is True
    assert data["type"] == "Blocking Driveway"
    assert data["computed_at"]
    figures = data["figures"]
    assert set(figures) >= {"tow", "vehicles", "overall_conclusion"}
    assert figures["overall_conclusion"].startswith("For Blocking Driveway")
    assert "recurrence_pct" in figures["tow"]["towed"]
    assert "recurrence_pct" in figures["tow"]["not_towed"]


# --------------------------------------------------- freshness (7.1, 7.3, 7.4a)


def set_layer_state(conn, layer, **fields):
    """Write one `layer_state` row directly. `test_sync.py`/`test_status.py`
    already prove `sync.py` writes these columns correctly from a real sync;
    this file only needs *some* known row in place so
    `mirror.status.mirror_freshness` has clocks to report through
    `GET /api/freshness` -- running an entire fake sync for that would test
    `sync.py` a second time, not this route.
    """
    cols = ["layer", *fields.keys()]
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields) or "layer = EXCLUDED.layer"
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO layer_state ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT (layer) DO UPDATE SET {updates}",
            (layer, *fields.values()),
        )
    conn.commit()


def test_freshness_route_reports_the_four_clocks(client, clean_db):
    """Task 7.1: last successful sync and next-due are both present, and are
    read off `mirror.status.mirror_freshness` (task 3.3), not recomputed."""
    success = datetime.datetime(2026, 9, 10, 4, 0, tzinfo=datetime.UTC)
    due = datetime.datetime(2026, 9, 18, 4, 0, tzinfo=datetime.UTC)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=success, last_attempt_at=success,
                        last_attempt_ok=True, last_success_at=success, next_due_at=due)
    seed_driveway_call(clean_db, 9001, "1 FIRST ST, HALIFAX", success - datetime.timedelta(days=2))

    resp = client.get("/api/freshness")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert set(payload) == {
        "checked_at", "most_recent_call_date", "last_success_at",
        "last_attempt_at", "next_due_at", "behind_source", "behind_reason",
        "stale_call_warning",
    }
    # Parsed back to the same instant regardless of the offset the string
    # carries -- this is the check that would have caught the discarded
    # attempt's bug (comparing raw .isoformat() strings across a session
    # timezone that is not UTC).
    assert datetime.datetime.fromisoformat(payload["last_success_at"]) == success
    assert datetime.datetime.fromisoformat(payload["next_due_at"]) == due
    assert payload["behind_source"] is False


def test_freshness_timestamps_are_normalized_to_a_utc_offset(client, clean_db):
    """The specific defect the discarded attempt hit: this codebase's Postgres
    session timezone is `America/Halifax`, not UTC, so a naive `.isoformat()`
    on what psycopg hands back prints `-03:00`/`-04:00` even for an instant
    that is identical to a UTC-constructed timestamp. `_iso_utc` must
    normalize before serializing, or two payloads describing the same instant
    would not even be the same *string* -- this pins the string shape itself,
    not just the parsed instant `test_freshness_route_reports_the_four_clocks`
    checks.
    """
    success = datetime.datetime(2026, 9, 10, 4, 0, tzinfo=datetime.UTC)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=success, last_attempt_at=success,
                        last_attempt_ok=True, last_success_at=success)

    payload = client.get("/api/freshness").get_json()

    assert payload["last_success_at"].endswith("+00:00")


def test_freshness_route_with_no_sync_history_does_not_crash(client, clean_db):
    """A store nobody has synced yet (design.md M2a's "not yet answerable"
    pattern) is a state of knowledge, not a 500."""
    resp = client.get("/api/freshness")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["last_success_at"] is None
    assert payload["next_due_at"] is None
    assert payload["most_recent_call_date"] is None
    assert payload["stale_call_warning"] == {
        "known": False, "stale": False, "days_old": None,
        "threshold_days": app_server.STALE_CALL_WARNING_DAYS,
        "reason": "the mirror holds no calls yet",
    }


def test_freshness_warns_when_the_newest_call_is_materially_stale(client, clean_db):
    """Task 7.4a: a deliberately stale input -- the newest call the mirror
    holds is 40 days old, past the 21-day threshold -- fires the warning."""
    now = datetime.datetime.now(datetime.UTC)
    seed_driveway_call(clean_db, 9002, "1 FIRST ST, HALIFAX", now - datetime.timedelta(days=40))

    payload = client.get("/api/freshness").get_json()

    warning = payload["stale_call_warning"]
    assert warning["known"] is True
    assert warning["stale"] is True
    assert warning["days_old"] == pytest.approx(40, abs=1)
    assert warning["threshold_days"] == 21
    assert "21-day" in warning["reason"]


def test_freshness_does_not_warn_when_the_newest_call_is_recent(client, clean_db):
    now = datetime.datetime.now(datetime.UTC)
    seed_driveway_call(clean_db, 9003, "1 FIRST ST, HALIFAX", now - datetime.timedelta(days=1))

    payload = client.get("/api/freshness").get_json()

    warning = payload["stale_call_warning"]
    assert warning["known"] is True
    assert warning["stale"] is False
    assert warning["reason"] is None


# --------------------------------------------------------- 7.2 lag attribution


def insert_pulled_edit(conn, layer, source_last_edit, finished_at=None, ok=True):
    """Write one `sync_runs` row directly, so `mirror.status.last_pulled_source_edit`
    reports a *pull* that saw an older source edit than `layer_state`'s -- the
    shape a poll-saw-an-advance-but-the-reload-hasn't-landed-yet sync leaves
    behind. Mirrors `test_derive.py`'s own `insert_sync_run` helper rather than
    running a real `sync.py` sync (this file's `set_layer_state` already
    prefers writing the state directly for the same reason)."""
    finished_at = finished_at or source_last_edit
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sync_runs (kind, layer, started_at, finished_at, ok, "
            "source_last_edit) VALUES (%s, %s, %s, %s, %s, %s)",
            ("reload", layer, finished_at, finished_at, ok, source_last_edit),
        )
    conn.commit()


def test_lag_attributed_to_hrm_when_sync_is_healthy_but_source_has_gone_quiet(
        client, clean_db):
    """The scenario 7.2 names first: a sync that is polling on schedule and
    succeeding every time, against a source that simply stopped publishing
    weeks ago. `behind_source` must read False (this system is not at fault)
    while the wording attributes the resulting gap to HRM's own publishing
    schedule, not to a defect in this sync -- easy to get backwards, so both
    the boolean and the attributed reason are checked, not just one."""
    now = datetime.datetime.now(datetime.UTC)
    quiet_edit = now - datetime.timedelta(days=30)
    recent_poll = now - datetime.timedelta(hours=1)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=quiet_edit,
                         last_attempt_at=recent_poll, last_attempt_ok=True,
                         last_success_at=recent_poll)
    seed_driveway_call(clean_db, 9004, "1 FIRST ST, HALIFAX", quiet_edit)

    payload = client.get("/api/freshness").get_json()

    assert payload["behind_source"] is False
    assert payload["stale_call_warning"]["stale"] is True
    assert "HRM's publishing schedule" in payload["behind_reason"]
    assert "not to this sync" in payload["behind_reason"]


def test_lag_attributed_to_this_system_when_sync_has_fallen_behind_a_publish(
        client, clean_db):
    """The other scenario 7.2 names: HRM has published more recently than this
    mirror has pulled -- the poll noticed the advance (`layer_state.source_last_edit`
    moved) but the last successful *pull* (`sync_runs`) is still against the
    older edit. This is this system's own problem, not HRM's, and the wording
    must say so rather than reusing the HRM-schedule phrasing from the other
    scenario."""
    now = datetime.datetime.now(datetime.UTC)
    old_edit = now - datetime.timedelta(days=2)
    new_edit = now - datetime.timedelta(hours=1)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=old_edit,
                         last_attempt_at=old_edit, last_attempt_ok=True,
                         last_success_at=old_edit)
        insert_pulled_edit(clean_db, layer, old_edit)
    # The poll's most recent observation moved past what was ever pulled --
    # only service_requests needs to advance for the aggregate to be behind.
    set_layer_state(clean_db, "service_requests", source_last_edit=new_edit,
                     last_attempt_at=new_edit, last_attempt_ok=True,
                     last_success_at=old_edit)
    seed_driveway_call(clean_db, 9005, "1 FIRST ST, HALIFAX", now - datetime.timedelta(hours=2))

    payload = client.get("/api/freshness").get_json()

    assert payload["behind_source"] is True
    assert payload["stale_call_warning"]["stale"] is False
    assert "published" in payload["behind_reason"]
    assert "HRM's publishing schedule" not in payload["behind_reason"]


def test_served_page_attributes_lag_to_the_owning_system_in_both_directions(client):
    """Task 7.2's wording lives in `web/app/index.html`'s boot() fetch, which
    Python tests cannot execute -- so this pins the two attributed sentences
    as static text in the served script, the same way
    `test_served_page_fetches_freshness_and_no_longer_asserts_liveness` pins
    7.3's markers. Both phrasings must be present and distinct: one blames
    HRM's own publishing schedule, the other blames this mirror, and neither
    should be interchangeable with the other."""
    body = client.get("/").get_data(as_text=True)

    assert "of HRM's own publishing schedule, not a problem with this mirror" in body
    assert "This mirror has fallen behind HRM" in body
    assert "not to HRM's publishing schedule" in body


def test_freshness_route_is_reachable_with_no_type_scoping(client):
    """Unlike every other API route here, freshness names no `<slug>` -- the
    mirror has one set of clocks, not one per violation type."""
    resp = client.get("/api/freshness")
    assert resp.status_code == 200


def test_served_page_fetches_freshness_and_no_longer_asserts_liveness(client):
    """Task 7.3: the undated "Regenerated from the live service, not from a
    stored export" claim is gone from the served page (it was also false --
    the app is served from a mirror, design.md M1 -- not a live per-request
    query), and the page fetches dated freshness instead. Task 7.1: the
    fetch's result is wired to a visible element, not merely present in the
    script unreached by any markup.
    """
    body = client.get("/").get_data(as_text=True)

    assert "Regenerated from the live service" not in body
    assert "/api/freshness" in body
    assert 'id="freshness"' in body
