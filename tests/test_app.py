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

import csv
import datetime
import html
import html.parser
import io
import json
import re
import shutil
import subprocess
import zoneinfo

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
    # No leftover write_board() placeholders (the retired generator's
    # __DATA__/__MAP__ tokens) -- this page is fed by the API, not filled at
    # generation time (see web/app/index.html's header comment).
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


def test_block_label_and_dwelling_rate_footnotes_reach_the_list_and_detail_panel(client):
    """Follow-up to task 7.5: 7.5 linked the derived block label and the
    block-wide dwelling rate to their footnotes from the header stats only,
    while the same two figures also sit, unlinked, in the block list and the
    detail panel's evidence sentence (`evidenceBlock()`). They now carry the
    identical links there -- same markup, same `#lim-block-label` /
    `#lim-dwelling` targets.

    No browser exists in this environment, so this pins the markup and the
    wiring rather than a click: the anchor text is taken *from the header* and
    required verbatim in the detail-panel renderer, and the structural facts
    that keep a link click from being swallowed by, or mis-triggering, row
    selection are checked in the script: no anchor inside a row `<button>`
    (whose `onclick` selects the row, and where a nested link is invalid
    HTML), the detail panel a sibling of that button, and no click handler on
    the panel itself.
    """
    body = client.get("/").get_data(as_text=True)
    script = body.split("<script>", 1)[1]
    markup = body.split("<script>", 1)[0]

    # The two anchors exactly as the header stats write them ...
    label_link = re.search(
        r'<a class="lim" href="#lim-block-label" title="[^"]*">\[block label\]</a>', markup).group(0)
    rate_link = re.search(
        r'<a class="lim" href="#lim-dwelling" title="[^"]*">\[dwelling rate\]</a>', markup).group(0)
    # ... are, character for character, what the detail panel's evidence sentence uses.
    assert f"const LIM_BLOCK_LABEL = `{label_link}`;" in script
    assert f"const LIM_DWELLING = `{rate_link}`;" in script
    evidence_block = script.split("function evidenceBlock(b){", 1)[1].split("\n}\n", 1)[0]
    assert "${LIM_BLOCK_LABEL}" in evidence_block   # beside the derived label (b.s)
    assert "${LIM_DWELLING}" in evidence_block      # beside the dwelling rate (b.r / b.dw)

    # The block list carries both links too, above its column headings, and
    # only in block mode (the doorway list shows neither figure).
    list_limits = re.search(r'<p class="list-limits" id="list-limits">(.*?)</p>', markup, re.S).group(1)
    assert label_link in list_limits and rate_link in list_limits
    assert 'listLimits.hidden = mode !== "block"' in script

    # Links cannot fight row selection: none inside the row button's template,
    # the panel is appended beside the button (not into it), and only the row
    # button and the panel's own status/address/ask buttons have click handlers.
    row_template = script.split('b.innerHTML = mode === "block"', 1)[1].split("b.onclick = () => {", 1)[0]
    assert "<a " not in row_template
    assert "frag.appendChild(b);\n    if (openKey === it.key) frag.appendChild(detail(it, s, V));" in script
    assert "d.onclick" not in script
    assert re.search(r'querySelectorAll\("\[data-s\]"\)\.forEach\(btn => btn\.onclick = e => \{\s*e\.stopPropagation', script)


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
    # Same abbreviated-key shape the retired write_board's page_rows used, so
    # web/app/index.html's script (copied from the retired web/template.html,
    # last present at b04731c) needs no change.
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


def test_index_page_states_which_filters_are_off_default_on_every_list(client):
    """Task 5.5d: unlike #recency-note (7.4, states the recency window
    unconditionally) and unlike filterSummary() (5.5e, only fires when a
    combination excludes every row), a *non-empty*, *non-default* view --
    e.g. min_calls=5 back with rows still listed -- said nothing
    distinguishing it from the unfiltered default until this task.
    `activeFilterBits()`/`activeFilterSummary()` name only the filters that
    differ from `FILTER_DEFAULTS`, wired to `#filters-note` and filled from
    render(); a future export (task 7.7) can reuse `activeFilterSummary()`
    (or read `FILTERS`/`FILTER_DEFAULTS` itself) to state the same
    "filtered by ..." line without re-deriving the phrasing.
    """
    body = client.get("/").get_data(as_text=True)

    assert 'id="filters-note"' in body
    assert "function activeFilterBits(){" in body
    assert "function activeFilterSummary(){" in body
    assert 'bits.length ? `Filtered by ${bits.join(", ")}.` : ""' in body

    # The client-side defaults compared against must match the server's own
    # `_FILTER_DEFAULTS` (src/app/server.py) exactly, or a value the server
    # falls back to as "default" could still read as "filtered" client-side.
    assert app_server._FILTER_DEFAULTS == {
        "min_calls": 2, "min_doorways": 2, "recur_days": 365, "recency_days": 365,
    }
    assert (
        "const FILTER_DEFAULTS = "
        "{min_calls: 2, min_doorways: 2, recency_days: 365, recur_days: 365};"
    ) in body


def test_default_and_widened_requests_carry_distinguishable_filters(client, clean_db):
    """Task 5.5d's own verification clause: a filtered, non-empty view must
    not be mistaken for the default list. This proves the server-side half
    over real HTTP -- the same `filters` key `activeFilterBits()` reads
    client-side to decide whether to say anything at all. A default request
    echoes back exactly `FILTER_DEFAULTS` (so `activeFilterBits()` yields no
    bits, and `#filters-note` stays empty); a request with `min_calls=5`
    echoes back a `filters.min_calls` that differs from the default even
    though it lists doorways rather than excluding all of them (contrast
    with test_min_calls_query_param_changes_the_doorway_list_over_http and
    5.5e's empty-result path, which only names filters once nothing is
    left).
    """
    seed_two_doorway_block(clean_db)  # two doorways, two calls each, min_calls=2 clears both

    default_resp = client.get("/api/types/blocking-driveway/doorways").get_json()
    widened_resp = client.get(
        "/api/types/blocking-driveway/doorways?min_calls=1"
    ).get_json()

    assert default_resp["filters"] == app_server._FILTER_DEFAULTS | {"district": None}
    assert len(default_resp["rows"]) == 2

    assert widened_resp["filters"]["min_calls"] == 1
    assert widened_resp["filters"] != default_resp["filters"]
    assert len(widened_resp["rows"]) == 2  # non-empty: the case 5.5e's naming does not cover


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


def mark_loaded(conn, when=None):
    """Say every layer has completed a load, which is what makes the mirror `ready`.
    The freshness tests below seed clocks by hand; without this they describe a mirror
    that has never finished its first load, where (correctly) nothing is overdue."""
    when = when or datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
    for layer in ("service_requests", "custom_fields", "census_areas"):
        set_layer_state(conn, layer, full_load_completed_at=when)


def test_freshness_route_reports_the_four_clocks(client, clean_db):
    """Task 7.1: last successful sync and next-due are both present, and are
    read off `mirror.status.mirror_freshness` (task 3.3), not recomputed."""
    success = datetime.datetime(2026, 9, 10, 4, 0, tzinfo=datetime.UTC)
    due = datetime.datetime(2026, 9, 18, 4, 0, tzinfo=datetime.UTC)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=success, last_attempt_at=success,
                        last_attempt_ok=True, last_success_at=success, next_due_at=due)
    seed_driveway_call(clean_db, 9001, "1 FIRST ST, HALIFAX", success - datetime.timedelta(days=2))
    mark_loaded(clean_db)

    resp = client.get("/api/freshness")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["readiness"] == "ready"
    assert set(payload) == {
        "readiness", "checked_at", "most_recent_call_date", "last_success_at",
        "last_attempt_at", "next_due_at", "behind_source", "behind_reason",
        "stale_call_warning", "next_update", "next_source_estimate",
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
    assert payload["next_update"]["known"] is False
    assert payload["next_update"]["overdue"] is False
    assert payload["next_source_estimate"]["known"] is False


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


# ------------------------------------------------- 7.2a next-update overdue state


def test_next_update_reads_overdue_once_the_grace_period_has_passed_with_no_success(
        client, clean_db):
    """The scenario 7.2a exists for: a scheduler that has died. `next_due_at`
    sits well in the past (5 days ago) and no sync has succeeded since --
    not even at the due time itself -- so this must read as `overdue`, not
    keep showing a future-dated promise that never arrived."""
    now = datetime.datetime.now(datetime.UTC)
    due = now - datetime.timedelta(days=5)
    last_success = due - datetime.timedelta(days=1)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=last_success,
                         last_attempt_at=last_success, last_attempt_ok=True,
                         last_success_at=last_success, next_due_at=due)
    mark_loaded(clean_db)

    payload = client.get("/api/freshness").get_json()

    next_update = payload["next_update"]
    assert next_update["known"] is True
    assert next_update["overdue"] is True
    assert next_update["days_overdue"] > 0
    assert "grace period" in next_update["reason"]


def test_next_update_is_healthy_when_next_due_at_is_in_the_future(client, clean_db):
    """The ordinary case: the sync is on schedule, `next_due_at` is tomorrow,
    and the last success is recent. Must not read as overdue."""
    now = datetime.datetime.now(datetime.UTC)
    due = now + datetime.timedelta(days=1)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=now,
                         last_attempt_at=now, last_attempt_ok=True,
                         last_success_at=now, next_due_at=due)
    mark_loaded(clean_db)

    payload = client.get("/api/freshness").get_json()

    next_update = payload["next_update"]
    assert next_update["known"] is True
    assert next_update["overdue"] is False
    assert next_update["days_overdue"] is None
    assert next_update["reason"] is None


def test_next_update_is_healthy_when_past_due_but_still_within_the_grace_period(
        client, clean_db):
    """`next_due_at` is 6 hours in the past -- late, but well inside the
    one-day (`sync.POLL_INTERVAL`) grace period `_next_update_state` grounds
    itself in -- so this must still read as healthy, not overdue. Without
    this case, a state machine that only ever fires "overdue" the instant
    the due time passes would flag a poll that is merely a few hours behind
    schedule, which is exactly the ordinary jitter the grace period exists
    to absorb."""
    now = datetime.datetime.now(datetime.UTC)
    due = now - datetime.timedelta(hours=6)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=due,
                         last_attempt_at=due, last_attempt_ok=True,
                         last_success_at=due, next_due_at=due)

    payload = client.get("/api/freshness").get_json()

    assert payload["next_update"]["overdue"] is False


def test_next_update_known_is_false_with_no_recorded_due_time(client, clean_db):
    """A store no sync has ever touched knows no due time at all -- reported
    as a state of knowledge (`known: False`), not as overdue or healthy by
    a default that would be a guess."""
    payload = client.get("/api/freshness").get_json()

    next_update = payload["next_update"]
    assert next_update["known"] is False
    assert next_update["overdue"] is False
    assert next_update["reason"] == "no sync has ever recorded a due time"


# The boundary itself, called directly against `_next_update_state` with an
# explicit `now` rather than through the route's real wall clock -- the same
# reason `test_derive_recency.py`'s boundary tests call `derive_with_recency`
# directly with constructed instants instead of trusting the arithmetic from
# a distance. `_call_staleness`'s `>` (not `>=`) convention is mirrored here:
# exactly at the grace deadline is not yet overdue, one second past it is.
def _fresh(next_due_at, last_success_at):
    return {"next_due_at": next_due_at, "last_success_at": last_success_at}


def test_next_update_boundary_exactly_at_the_grace_deadline_is_not_yet_overdue():
    due = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
    last_success = due - datetime.timedelta(days=1)
    overdue_at = due + app_server.NEXT_UPDATE_GRACE_PERIOD

    result = app_server._next_update_state(_fresh(due, last_success), now=overdue_at)

    assert result["overdue"] is False


def test_next_update_boundary_one_second_past_the_grace_deadline_is_overdue():
    due = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
    last_success = due - datetime.timedelta(days=1)
    overdue_at = due + app_server.NEXT_UPDATE_GRACE_PERIOD

    result = app_server._next_update_state(
        _fresh(due, last_success), now=overdue_at + datetime.timedelta(seconds=1))

    assert result["overdue"] is True


def test_next_update_a_success_recorded_at_the_due_time_counts_as_since():
    """`succeeded_since_due` uses `>=`, not `>`: a success landing exactly at
    the due time counts as "since" -- so even long after the grace deadline
    has passed by the wall clock, this must not read as overdue, because the
    sync did in fact succeed at (or after) the moment it was due."""
    due = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
    far_future = due + app_server.NEXT_UPDATE_GRACE_PERIOD + datetime.timedelta(days=30)

    result = app_server._next_update_state(_fresh(due, due), now=far_future)

    assert result["overdue"] is False


def test_next_update_a_success_one_second_before_the_due_time_does_not_count():
    """The other side of the same boundary: a success one second *before*
    the due time is not "since" -- this is the ordinary shape of an overdue
    sync (its last success predates the due time it then missed), and must
    still read as overdue past the grace deadline."""
    due = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
    last_success = due - datetime.timedelta(seconds=1)
    far_future = due + app_server.NEXT_UPDATE_GRACE_PERIOD + datetime.timedelta(days=30)

    result = app_server._next_update_state(_fresh(due, last_success), now=far_future)

    assert result["overdue"] is True


# ------------------------------------------- 7.2b next-source-update estimate


def test_next_source_estimate_known_with_exactly_two_observed_intervals(client, clean_db):
    """Task 7.2b's chosen threshold, exercised through the route: three
    recorded publishes (two intervals) is exactly enough to compute a spread
    -- must read as known, with an estimate built from the median gap, not
    "not yet known" and not a crash."""
    first = datetime.datetime(2026, 8, 1, 4, 0, tzinfo=datetime.UTC)
    second = first + datetime.timedelta(days=7)
    third = second + datetime.timedelta(days=9)
    for edit in (first, second, third):
        insert_pulled_edit(clean_db, "service_requests", edit)

    payload = client.get("/api/freshness").get_json()

    estimate = payload["next_source_estimate"]
    assert estimate["known"] is True
    assert estimate["observed_intervals"] == 2
    assert estimate["median_interval_days"] == pytest.approx(8.0)
    assert (datetime.datetime.fromisoformat(estimate["estimated_next"])
            == third + datetime.timedelta(days=8))


def test_next_source_estimate_not_known_with_only_one_observed_interval(client, clean_db):
    """One below 7.2b's threshold: two recorded publishes give a single
    interval. design.md M3 states plainly that a single gap "is not evidence
    of any particular" cadence, so this must read as not known rather than
    guess a schedule from it."""
    first = datetime.datetime(2026, 8, 1, 4, 0, tzinfo=datetime.UTC)
    second = first + datetime.timedelta(days=7)
    for edit in (first, second):
        insert_pulled_edit(clean_db, "service_requests", edit)

    payload = client.get("/api/freshness").get_json()

    estimate = payload["next_source_estimate"]
    assert estimate["known"] is False
    assert estimate["observed_intervals"] == 1
    assert estimate["estimated_next"] is None
    assert "not enough history" in estimate["reason"]


def test_next_source_estimate_not_known_with_no_publish_history_at_all(client, clean_db):
    """A store nobody has synced yet: the same "not yet answerable" state as
    `test_freshness_route_with_no_sync_history_does_not_crash`, not a 500."""
    payload = client.get("/api/freshness").get_json()

    estimate = payload["next_source_estimate"]
    assert estimate["known"] is False
    assert estimate["observed_intervals"] == 0
    assert estimate["estimated_next"] is None


# The boundary itself, called directly against `_next_source_estimate` with a
# constructed history rather than through the route's real database -- the
# same reason `_next_update_state`'s boundary tests above call it directly.
# `_history_of` builds the same shape `sync.source_edit_history` returns
# (a list of dicts with "source_last_edit" and "interval", ascending, first
# entry's interval always None) without touching the database.
def _history_of(*edits):
    history, previous = [], None
    for edited in edits:
        history.append({
            "source_last_edit": edited,
            "interval": (edited - previous) if previous else None,
        })
        previous = edited
    return history


def test_next_source_estimate_boundary_exactly_two_intervals_is_known():
    edits = [
        datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC),
        datetime.datetime(2026, 8, 8, tzinfo=datetime.UTC),
        datetime.datetime(2026, 8, 17, tzinfo=datetime.UTC),
    ]

    result = app_server._next_source_estimate(_history_of(*edits))

    assert result["known"] is True
    assert result["observed_intervals"] == 2
    assert result["reason"] is None


def test_next_source_estimate_boundary_one_interval_is_one_below_threshold_and_not_known():
    edits = [
        datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC),
        datetime.datetime(2026, 8, 8, tzinfo=datetime.UTC),
    ]

    result = app_server._next_source_estimate(_history_of(*edits))

    assert result["known"] is False
    assert result["observed_intervals"] == 1
    assert result["estimated_next"] is None
    assert "not enough history" in result["reason"]


def test_median_timedelta_odd_count_returns_the_middle_value():
    deltas = [datetime.timedelta(days=5), datetime.timedelta(days=1), datetime.timedelta(days=9)]

    assert app_server._median_timedelta(deltas) == datetime.timedelta(days=5)


def test_median_timedelta_even_count_averages_the_two_middle_values():
    deltas = [datetime.timedelta(days=4), datetime.timedelta(days=10)]

    assert app_server._median_timedelta(deltas) == datetime.timedelta(days=7)


def test_served_page_shows_the_not_yet_known_and_estimate_wording_for_next_source_update(client):
    """Task 7.2b's client-side wiring lives in `web/app/index.html`'s boot()
    fetch, which Python tests cannot execute -- so this pins both branches as
    static text in the served script, the same way the neighbouring 7.2/7.2a
    tests pin their own wording. Must read `f.next_source_estimate.known`
    (the server's own decision, not a client-side re-derivation of the
    interval-count threshold), and the known branch must say "estimated",
    never "due"/"expected", so it cannot be mistaken for `nextUpdate` above
    it."""
    body = client.get("/").get_data(as_text=True)

    assert "next_source_estimate" in body
    assert "sourceEstimate.known" in body
    assert "It is not yet known when HRM itself is likely to publish next" in body
    assert "HRM itself is estimated to next publish around" in body
    assert "estimate, not a promise" in body


def test_served_page_shows_overdue_state_instead_of_a_stale_future_promise(client):
    """Task 7.2a's client-side wiring lives in `web/app/index.html`'s boot()
    fetch, which Python tests cannot execute -- so this pins the overdue
    branch as static text/behaviour in the served script, the same way
    `test_served_page_attributes_lag_to_the_owning_system_in_both_directions`
    pins 7.2's wording. Must read `f.next_update.overdue` (the server's
    state, not a client-side re-derivation of the due-time arithmetic) and
    must apply a distinct `overdue` class, styled like the existing
    `.stale`/`.behind` alert treatment."""
    body = client.get("/").get_data(as_text=True)

    assert "next_update" in body
    assert "nextUpdate.overdue" in body
    assert "Next update is overdue" in body
    assert 'classList.add("overdue")' in body
    assert ".freshness.overdue" in body or ",.freshness.overdue" in body


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


# ------------------------------------------------------------------- export (7.7)


def _csv_metadata(text):
    """Every `key,value` metadata row `_export_csv_text` writes before the
    blank line + "DOORWAYS" marker, as a dict -- the preamble this task's
    export puts ahead of the two real data tables (see that function's own
    docstring for why a preamble, not a second file, carries the type,
    clocks, filters and limits). Stops at the first row that is not exactly
    two cells, which is either a deliberate blank separator row or the
    "DOORWAYS"/"BLOCKS" section marker -- both signal the preamble is over.
    """
    meta = {}
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue  # a blank separator row between preamble groups
        if len(row) == 1:
            break  # the "DOORWAYS"/"BLOCKS" section marker -- preamble is over
        if row[0] not in meta:
            meta[row[0]] = row[1]
    return meta


def _csv_section(text, marker):
    """The rows of one lettered CSV data table (`"DOORWAYS"` or `"BLOCKS"`):
    the header row (readable column names) plus every data row that follows,
    up to the next blank row or the end of the file."""
    rows = list(csv.reader(io.StringIO(text)))
    start = next(i for i, row in enumerate(rows) if row == [marker]) + 1
    header = rows[start]
    data = []
    for row in rows[start + 1:]:
        if not row:
            break
        data.append(dict(zip(header, row)))
    return header, data


def test_export_csv_carries_every_listed_row(client, clean_db):
    """Requirement 1 of task 7.7's verify clause: the export must carry every
    row the view lists for the same type/filter combination -- checked
    against the live `/api/types/<slug>/doorways`/`/blocks` routes, not
    trusted from the CSV alone, so a discrepancy between the two would fail
    this test rather than go unnoticed.
    """
    seed_two_doorway_block(clean_db)

    doorways_json = client.get("/api/types/blocking-driveway/doorways").get_json()
    blocks_json = client.get("/api/types/blocking-driveway/blocks").get_json()
    resp = client.get("/api/types/blocking-driveway/export.csv")

    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    _, doorway_rows = _csv_section(text, "DOORWAYS")
    _, block_rows = _csv_section(text, "BLOCKS")

    assert len(doorway_rows) == len(doorways_json["rows"]) == 2
    assert len(block_rows) == len(blocks_json["blocks"]) == 1
    exported_addresses = {r["Address"] for r in doorway_rows}
    listed_addresses = {r["a"] for r in doorways_json["rows"]}
    assert exported_addresses == listed_addresses == {"1 First St", "2 Second St"}
    assert block_rows[0]["Block"] == blocks_json["blocks"][0]["bk"]


def test_export_csv_states_the_violation_type_plainly(client, clean_db):
    """Requirement 2: the canonical type name, unambiguously, as data inside
    the file -- not left for a reader to infer from the URL slug or a
    filename. (Task 7.7a, not this one, owns naming it *without* even
    cross-referencing a filename; this only checks it is stated at all.)
    """
    seed_two_doorway_block(clean_db)

    text = client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True)

    meta = _csv_metadata(text)
    assert meta["violation_type"] == "Blocking Driveway"
    assert meta["slug"] == "blocking-driveway"


def test_export_csv_404s_for_an_untracked_slug(client):
    resp = client.get("/api/types/not-a-real-type/export.csv")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "untracked"


def test_export_csv_is_served_as_a_downloadable_csv_file(client, clean_db):
    seed_two_doorway_block(clean_db)

    resp = client.get("/api/types/blocking-driveway/export.csv")

    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert 'attachment; filename="blocking-driveway-export.csv"' in resp.headers[
        "Content-Disposition"
    ]


def test_export_csv_carries_the_same_clocks_freshness_already_serves(client, clean_db):
    """Requirement 3: the clocks -- read from the identical
    `mirror.status.mirror_freshness`/`_freshness_payload` path
    `GET /api/freshness` uses, so this pins the export's clocks against that
    route's own live answer rather than against a hand-picked expectation
    that could drift from it.
    """
    success = datetime.datetime(2026, 9, 10, 4, 0, tzinfo=datetime.UTC)
    due = datetime.datetime(2026, 9, 18, 4, 0, tzinfo=datetime.UTC)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=success, last_attempt_at=success,
                        last_attempt_ok=True, last_success_at=success, next_due_at=due)
    seed_two_doorway_block(clean_db)

    freshness = client.get("/api/freshness").get_json()
    text = client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True)

    meta = _csv_metadata(text)
    assert meta["last_success_at"] == freshness["last_success_at"]
    assert meta["next_due_at"] == freshness["next_due_at"]
    assert meta["behind_source"] == str(freshness["behind_source"])
    assert meta["behind_reason"] == (freshness["behind_reason"] or "")


def test_export_csv_states_the_filter_values_in_effect(client, clean_db):
    """Requirement 4: the filter values in effect, both as raw values (every
    one of the five `_filters_from_request()` reads) and as the same "which
    filters differ from default" phrasing 5.5d introduced client-side
    (`activeFilterBits()`) -- `_active_filter_bits` is this task's Python
    equivalent, checked here for the exact same wording.
    """
    seed_two_doorway_block(clean_db)

    default_text = client.get(
        "/api/types/blocking-driveway/export.csv"
    ).get_data(as_text=True)
    filtered_text = client.get(
        "/api/types/blocking-driveway/export.csv?min_calls=1&district=7"
    ).get_data(as_text=True)

    default_meta = _csv_metadata(default_text)
    assert default_meta["filter: min_calls"] == "2"
    assert default_meta["filter: district"] == ""
    assert default_meta["filters_in_effect"] == "Default filters -- none active."

    filtered_meta = _csv_metadata(filtered_text)
    assert filtered_meta["filter: min_calls"] == "1"
    assert filtered_meta["filter: district"] == "7"
    assert filtered_meta["filters_in_effect"] == (
        "Filtered by district 7, min. recent calls ≥ 1."
    )


def test_export_csv_carries_the_same_six_limits_as_the_view(client, clean_db):
    """Requirement 5: the same six interpretation limits 7.5 put in
    `web/app/index.html`'s footer -- checked against `app_server.
    _INTERPRETATION_LIMITS` (this task's own hand-kept copy of that wording,
    see its docstring on the duplication) so a wording edit to one is
    automatically what this test compares against, not a third, independent
    copy of the six sentences pasted into this test file.
    """
    seed_two_doorway_block(clean_db)

    text = client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True)
    meta = _csv_metadata(text)

    assert len(app_server._INTERPRETATION_LIMITS) == 6
    for limit in app_server._INTERPRETATION_LIMITS:
        key = f"limit: {limit['id']}"
        assert key in meta, f"{key} missing from export"
        assert limit["title"] in meta[key]
        assert limit["text"] in meta[key]


def test_export_csv_carries_the_tow_effect_sentence_when_available(client, clean_db):
    """Requirement 6: a readable brief/table should carry the same tow-effect
    sentence the view's `#tow-thesis` shows, built from
    `mirror.type_figures.figures_for` -- not just the raw row table. Checked
    against the same stored `tow.conclusion`/`tow.caveat` fragments the
    `/api/types/<slug>/figures` route serves, via `app_server.
    _tow_thesis_sentence` applied to that exact payload, so a discrepancy
    between the export's sentence and the figures route's own data would
    fail this test.
    """
    seed_two_doorway_block(clean_db)
    outcomes = mirror_type_figures.compute_and_store(
        clean_db, types=[app_server.DEFAULT_CANONICAL_TYPE], log=lambda m: None,
    )
    assert outcomes and outcomes[0]["ok"]

    figures_payload = client.get("/api/types/blocking-driveway/figures").get_json()
    text = client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True)

    meta = _csv_metadata(text)
    expected = app_server._tow_thesis_sentence(figures_payload)
    assert meta["tow_effect"] == expected
    assert expected != "Tow-effect comparison for this type is not yet available."


def test_export_csv_states_tow_effect_not_yet_available_when_nothing_is_stored(
        client, clean_db):
    seed_two_doorway_block(clean_db)

    text = client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True)

    meta = _csv_metadata(text)
    assert meta["tow_effect"].startswith("Tow-effect comparison for this type is not yet available")


# --------------------------------------------------------- export brief (7.7)


def test_export_brief_carries_every_listed_row(client, clean_db):
    """Requirement 1, for the HTML brief this time: every doorway and block
    row appears in the rendered tables, not just a summary count."""
    seed_two_doorway_block(clean_db)

    doorways_json = client.get("/api/types/blocking-driveway/doorways").get_json()
    body = client.get("/types/blocking-driveway/export").get_data(as_text=True)

    for row in doorways_json["rows"]:
        assert row["a"] in body
    assert ">2<" in body or "Doorways (2)" in body  # doorway count heading


def test_export_brief_states_the_violation_type_plainly(client, clean_db):
    seed_two_doorway_block(clean_db)

    body = client.get("/types/blocking-driveway/export").get_data(as_text=True)

    assert "Blocking Driveway" in body
    assert "blocking-driveway" in body


def test_export_brief_404s_with_a_rendered_reason_for_an_untracked_slug(client):
    """Same convention as `/types/<slug>` (task 5.3): a rendered explanation,
    not Flask's bare default 404 page, since this is a `/types/...` page
    route rather than an `/api/...` one."""
    resp = client.get("/types/not-a-real-type/export")

    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "not-a-real-type" in body
    assert "is not a tracked type" in body


def test_export_brief_carries_the_same_clocks_freshness_already_serves(client, clean_db):
    success = datetime.datetime(2026, 9, 10, 4, 0, tzinfo=datetime.UTC)
    due = datetime.datetime(2026, 9, 18, 4, 0, tzinfo=datetime.UTC)
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(clean_db, layer, source_last_edit=success, last_attempt_at=success,
                        last_attempt_ok=True, last_success_at=success, next_due_at=due)
    seed_two_doorway_block(clean_db)

    freshness = client.get("/api/freshness").get_json()
    body = client.get("/types/blocking-driveway/export").get_data(as_text=True)

    assert freshness["last_success_at"] in body
    assert freshness["next_due_at"] in body


def test_export_brief_states_the_filter_values_in_effect(client, clean_db):
    seed_two_doorway_block(clean_db)

    body = client.get(
        "/types/blocking-driveway/export?min_calls=1&district=7"
    ).get_data(as_text=True)

    assert "Filtered by district 7, min. recent calls ≥ 1." in body


def test_export_brief_carries_the_same_six_limits_as_the_view(client, clean_db):
    seed_two_doorway_block(clean_db)

    # html.unescape: Jinja auto-escapes the apostrophes two of the six limits'
    # titles carry ("A block's street label...", "A block's dwelling
    # count..."), which is the right, safe default for text this module does
    # not control the exact characters of -- so this compares against what a
    # browser would display, not against the raw (escaped) markup.
    body = html.unescape(client.get("/types/blocking-driveway/export").get_data(as_text=True))

    for limit in app_server._INTERPRETATION_LIMITS:
        assert f'id="{limit["id"]}"' in body
        assert limit["title"] in body


def test_export_brief_carries_the_tow_effect_sentence(client, clean_db):
    seed_two_doorway_block(clean_db)
    outcomes = mirror_type_figures.compute_and_store(
        clean_db, types=[app_server.DEFAULT_CANONICAL_TYPE], log=lambda m: None,
    )
    assert outcomes and outcomes[0]["ok"]

    figures_payload = client.get("/api/types/blocking-driveway/figures").get_json()
    # html.unescape: the stored tow fragment (from too small a sample here)
    # includes a literal "need >= 30 each", which Jinja escapes to "&gt;=" in
    # the rendered page -- correctly, since this is computed text, not a
    # literal this module controls. Compare against what a browser displays.
    body = html.unescape(client.get("/types/blocking-driveway/export").get_data(as_text=True))

    expected = app_server._tow_thesis_sentence(figures_payload)
    assert expected in body


def test_export_csv_and_brief_agree_on_row_counts_with_the_view(client, clean_db):
    """A light version of task 7.8's own concern ("a view and an export
    taken together agree on every count") -- not that task's full scope, but
    a defensible sanity check that this task's two export formats do not
    silently disagree with each other or with the live JSON routes about how
    many rows a given filter combination lists.
    """
    seed_two_doorway_block(clean_db)

    doorways_json = client.get("/api/types/blocking-driveway/doorways").get_json()
    csv_text = client.get("/api/types/blocking-driveway/export.csv").get_data(as_text=True)
    brief_body = client.get("/types/blocking-driveway/export").get_data(as_text=True)

    _, csv_rows = _csv_section(csv_text, "DOORWAYS")
    assert len(csv_rows) == doorways_json["count"] == 2
    assert f"Doorways ({doorways_json['count']})" in brief_body


# ------------------------------------------ naming the type on views and exports (7.7a)
#
# Task 7.7a: "verify a reader can tell which type they are looking at without
# cross-referencing a filename." The CSV is downloaded as `{slug}-export.csv`,
# so a reader holding only the file, a pasted table, a screenshot or a printed
# brief has the *content* and nothing else. Every test below therefore reads
# response bodies only -- never `Content-Disposition`, never the request URL,
# never the slug the test itself asked for -- and each would fail if the type
# name were removed from the surface under test. (7.7's own
# `test_export_*_states_the_violation_type_plainly` tests check the type is
# stated at all, in one type, in one place; these check where, on both
# exports, for two different types at once, and on the served view.)

_TWO_TYPES = [("blocking-driveway", "Blocking Driveway"), ("no-parking-sign", "No Parking Sign")]


class _BriefParser(html.parser.HTMLParser):
    """The visible structure of `export_brief`'s HTML, as data: title, h1, h2s,
    list items, paragraphs, and each table's caption, header and rows -- so a
    test compares what a reader *sees* (entities decoded, markup stripped)
    rather than substrings of markup."""

    _TRACKED = ("title", "h1", "h2", "li", "p", "caption", "td", "th")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.h1, self.h2, self.li, self.p = "", [], [], [], []
        self.tables, self.text = [], ""
        self._tag = self._buf = self._table = self._row = None
        self._row_has_th = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = {"caption": "", "header": None, "rows": []}
        elif tag == "tr":
            self._row, self._row_has_th = [], False
        if tag in self._TRACKED:
            self._tag, self._buf = tag, []
            if tag == "th":
                self._row_has_th = True

    def handle_data(self, data):
        self.text += data
        if self._buf is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == self._tag and self._buf is not None:
            text = " ".join("".join(self._buf).split())
            if tag == "title":
                self.title = text
            elif tag == "h1":
                self.h1.append(text)
            elif tag == "h2":
                self.h2.append(text)
            elif tag == "li":
                self.li.append(text)
            elif tag == "p":
                self.p.append(text)
            elif tag == "caption":
                self._table["caption"] = text
            elif tag in ("td", "th") and self._row is not None:
                self._row.append(text)
            self._tag = self._buf = None
        elif tag == "tr" and self._row is not None:
            if self._row_has_th:
                self._table["header"] = self._row
            else:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def table_for(self, noun):
        """`(header, [row dict, ...])` of the table captioned for `noun`
        ("doorways" or "blocks"), or `(None, [])` when the brief printed a
        "No <noun> match ..." sentence instead of a table."""
        for t in self.tables:
            if t["caption"].endswith(noun):
                return t["header"], [dict(zip(t["header"], r)) for r in t["rows"]]
        return None, []

    def list_items(self):
        """`li` texts as `{label: value}` for every "Label: value" item (the
        Filters and Freshness lists) -- the limits list's items happen to
        contain colons too, which is harmless: nothing looks those up."""
        return dict(t.split(": ", 1) for t in self.li if ": " in t)


def _parse_brief(body):
    parsed = _BriefParser()
    parsed.feed(body)
    return parsed


@pytest.mark.parametrize("slug,name", _TWO_TYPES)
def test_export_csv_names_its_type_on_top_and_on_every_row_not_only_in_the_filename(
        client, clean_db, slug, name):
    """Task 7.7a, CSV: the file's own first row names the canonical type, and
    every doorway row and every block row carries it too -- a table pasted out
    of the file into a spreadsheet that already holds another type's rows
    (the very mix-up the type-scoped routes exist to prevent) still says which
    rows are which. The other type's name appears nowhere in the file."""
    seed_two_types_two_doorway_blocks(clean_db)
    other = next(n for s, n in _TWO_TYPES if s != slug)

    text = client.get(f"/api/types/{slug}/export.csv").get_data(as_text=True)

    assert next(csv.reader(io.StringIO(text))) == ["violation_type", name]
    for marker in ("DOORWAYS", "BLOCKS"):
        header, rows = _csv_section(text, marker)
        assert header[-1] == "Violation Type"
        assert rows, f"seed lists at least one {marker.lower()} row for {name}"
        assert {r["Violation Type"] for r in rows} == {name}
    assert other not in text


@pytest.mark.parametrize("slug,name", _TWO_TYPES)
def test_export_brief_names_its_type_in_title_heading_lede_and_both_tables(
        client, clean_db, slug, name):
    """Task 7.7a, brief: the document title (a browser tab, a printed
    header), the h1, the introductory line and a caption on each table -- so a
    table lifted out of the brief on its own still says which type it lists --
    all state the canonical type. The other type's name is nowhere in the
    visible text."""
    seed_two_types_two_doorway_blocks(clean_db)
    other = next(n for s, n in _TWO_TYPES if s != slug)

    parsed = _parse_brief(client.get(f"/types/{slug}/export").get_data(as_text=True))

    assert name in parsed.title
    assert parsed.h1 == [name]
    lede = next(p for p in parsed.p if p.startswith("Doorway and block list export"))
    assert f"for the violation type {name}" in lede
    assert [t["caption"] for t in parsed.tables] == [f"{name} - doorways", f"{name} - blocks"]
    assert other not in parsed.text


def test_served_view_names_its_type_from_the_payload_not_a_hard_coded_default(client):
    """Task 7.7a, the served page: `web/app/index.html` is one static file for
    all 30 types, so a type named in its markup is named wrongly on 29 of
    them -- the eyebrow used to say "blocked driveway service requests"
    unconditionally. It now carries an empty placeholder the boot script fills
    from `doorPayload.type` (what the server says these rows are rows of),
    alongside the document title and a heading on the list itself. No
    browser here: this pins the markup and the script's wiring; the payload
    half (that `type` is the canonical name for every route) is the next test.
    """
    body = client.get("/types/no-parking-sign").get_data(as_text=True)
    markup = body.split("<script>")[0]

    assert "blocked driveway service requests" not in markup
    assert 'violation type: <span id="type-name">' in markup
    assert 'id="list-type"' in markup
    # Task 9.8: the same value is what the Ask-Claude prompt names, so it has
    # one definition (`pageTypeName`) that both the header fill and the prompt
    # read -- not a second copy of `doorPayload.type || ...` beside it.
    assert 'const pageTypeName = () => doorPayload.type || blockPayload.type || "";' in body
    assert body.count("doorPayload.type || blockPayload.type") == 1
    assert 'const typeName = pageTypeName();' in body
    assert 'set("type-name", typeName || "unknown");' in body
    assert 'set("list-type", typeName ? `Violation type: ${typeName}` : "");' in body
    assert "document.title = `${typeName} · ${document.title}`" in body


def test_every_tracked_types_payloads_carry_that_types_canonical_name(client):
    """The other half of the served view's naming: the value the page writes
    into its eyebrow, list heading and title is `type` off the doorway and
    block payloads, so every tracked slug must answer with its own canonical
    name -- an empty store included (nothing seeded here), since a type with
    no rows is exactly where a page would otherwise have nothing to name."""
    for slug, name in app_server._SLUG_TO_CANONICAL.items():
        for route in ("doorways", "blocks"):
            payload = client.get(f"/api/types/{slug}/{route}").get_json()
            assert payload["type"] == name, (slug, route)


def test_untracked_type_page_and_brief_say_they_cover_no_type(client):
    """Task 7.7a, the untracked-type page: it lists tracked types as links, so
    without a plain statement a reader could take it for a page about one of
    them. It says, in its own content, that it covers no violation type --
    for `/types/<slug>` and for `/types/<slug>/export`, which share it."""
    for path in ("/types/not-a-real-type", "/types/not-a-real-type/export"):
        resp = client.get(path)
        assert resp.status_code == 404
        text = " ".join(_parse_brief(resp.get_data(as_text=True)).text.split())
        assert "covers no violation type" in text, path
        assert "not-a-real-type" in text
        assert "is not a tracked type" in text


# ----------------------------------- view and exports agree, one mirror state (7.8)
#
# Task 7.8: "verify a view and an export taken together agree on every count and
# name the same mirror state." One seeded store, three surfaces read over real
# HTTP -- the view (`/api/types/<slug>/doorways` + `/blocks` + `/api/freshness`,
# the three fetches the page's script renders from), the CSV and the brief --
# and every count, every cell, every filter value and every mirror-state
# identifier each one states is compared. Expected counts are also written out
# by hand from the seed (not only "the three match"), so three surfaces
# agreeing on the same wrong answer still fail.

T1 = datetime.datetime(2026, 9, 10, 4, 0, tzinfo=datetime.UTC)   # first sync
T2 = datetime.datetime(2026, 9, 17, 4, 0, tzinfo=datetime.UTC)   # second sync
RING_B = [[[10, 10], [10, 11], [11, 11], [11, 10], [10, 10]]]


def _days_before_latest(days):
    return LATEST - datetime.timedelta(days=days)


def _record_sync(conn, finished_at, source_edit):
    """One completed sync of both currency layers: `layer_state` moves to it
    (that is where `mirror_freshness` reads the clocks) and a `sync_runs` row
    is left behind (that is where a reader looking for "some earlier sync"
    would find one). A completed sync has completed a load of every layer, which is
    what makes the mirror ready; without that the views would (correctly) say nothing
    has been loaded rather than "no doorways match"."""
    for layer in ("service_requests", "custom_fields"):
        set_layer_state(conn, layer, source_last_edit=source_edit,
                        last_attempt_at=finished_at, last_attempt_ok=True,
                        last_success_at=finished_at,
                        next_due_at=finished_at + datetime.timedelta(days=1),
                        full_load_completed_at=finished_at)
        insert_pulled_edit(conn, layer, source_edit, finished_at=finished_at)
    set_layer_state(conn, "census_areas", full_load_completed_at=finished_at)


def _seed_first_sync_state(conn):
    """What the mirror held after the first sync: block A (district 7, 200
    dwellings) with two doorways of two calls each. Newest call: LATEST - 3d."""
    insert_census_area(conn, "12090999", SQUARE_RING, dwellings=200, object_id=1)
    insert_census_area(conn, "12091111", RING_B, dwellings=0, object_id=2)
    seed_driveway_call(conn, 7001, "1 FIRST ST, HALIFAX", _days_before_latest(10))
    seed_driveway_call(conn, 7002, "1 FIRST ST, HALIFAX", _days_before_latest(6), towed="Y")
    seed_driveway_call(conn, 7003, "2 SECOND ST, HALIFAX", _days_before_latest(8))
    seed_driveway_call(conn, 7004, "2 SECOND ST, HALIFAX", _days_before_latest(3))
    _record_sync(conn, T1, T1 - datetime.timedelta(days=2))


def _seed_second_sync_state(conn):
    """What the second sync added, on top of the first. Boundaries planted on
    purpose: FIRST ST goes from two calls to three (crossing `min_calls=3`);
    FOURTH ST has a call exactly 30 days before the newest (the inclusive edge
    of `recency_days=30`); THIRD ST has one call (listed only at
    `min_calls=1`, with no median gap); NINTH/TENTH are a second block, in
    district 8 with 0 dwellings (a `None` calls-per-1k rate). Newest call: LATEST.

    Default list afterwards: FIRST(3), SECOND(2), FOURTH(2), NINTH(2),
    TENTH(2) = 5 doorways in 2 blocks.
    """
    seed_driveway_call(conn, 7005, "1 FIRST ST, HALIFAX", _days_before_latest(2))
    seed_driveway_call(conn, 7006, "4 FOURTH ST, HALIFAX", _days_before_latest(30))
    seed_driveway_call(conn, 7007, "4 FOURTH ST, HALIFAX", _days_before_latest(1))
    seed_driveway_call(conn, 7008, "3 THIRD ST, HALIFAX", _days_before_latest(4))
    seed_driveway_call(conn, 7009, "5 NINTH AVE, HALIFAX", _days_before_latest(12),
                       lat=10.5, lon=10.5, district="8")
    seed_driveway_call(conn, 7010, "5 NINTH AVE, HALIFAX", _days_before_latest(5),
                       lat=10.5, lon=10.5, district="8")
    seed_driveway_call(conn, 7011, "6 TENTH AVE, HALIFAX", _days_before_latest(9),
                       lat=10.5, lon=10.5, district="8")
    seed_driveway_call(conn, 7012, "6 TENTH AVE, HALIFAX", _days_before_latest(0),
                       lat=10.5, lon=10.5, district="8")
    _record_sync(conn, T2, T2 - datetime.timedelta(days=1))


def _csv_cell(value, key):
    """How `_write_csv_table` writes one payload value: `None` is an empty
    cell, an address list is `; `-joined."""
    return "; ".join(value) if key == "addrs" else ("" if value is None else str(value))


def _brief_cell(value, key):
    """How the brief's table shows one payload value: the page's own en dash
    for a value that does not exist (`index.html` shows the same for a missing
    dwelling rate or vehicle count), never the word "None"."""
    return "; ".join(value) if key == "addrs" else ("–" if value is None else str(value))


def _gather(client, slug, query=""):
    """Everything the view, the CSV and the brief state for one type and one
    filter query, fetched over HTTP and parsed."""
    qs = f"?{query}" if query else ""
    csv_text = client.get(f"/api/types/{slug}/export.csv{qs}").get_data(as_text=True)
    brief = _parse_brief(client.get(f"/types/{slug}/export{qs}").get_data(as_text=True))
    return {
        "doors": client.get(f"/api/types/{slug}/doorways{qs}").get_json(),
        "blocks": client.get(f"/api/types/{slug}/blocks{qs}").get_json(),
        "fresh": client.get("/api/freshness").get_json(),
        "csv_text": csv_text,
        "csv_meta": _csv_metadata(csv_text),
        "csv_doors": _csv_section(csv_text, "DOORWAYS"),
        "csv_blocks": _csv_section(csv_text, "BLOCKS"),
        "brief": brief,
        "brief_doors": brief.table_for("doorways"),
        "brief_blocks": brief.table_for("blocks"),
    }


def _assert_view_and_exports_agree(g):
    """Every count, cell, filter value and mirror-state identifier the three
    surfaces state, compared. Each `assert` carries a label naming what
    disagreed, so a deliberately broken surface (see the canary tests below)
    is shown to fail *for the intended reason*, not just to fail."""
    doors, blocks, fresh = g["doors"], g["blocks"], g["fresh"]
    meta, brief = g["csv_meta"], g["brief"]
    csv_door_header, csv_doors = g["csv_doors"]
    csv_block_header, csv_blocks = g["csv_blocks"]
    brief_door_header, brief_doors = g["brief_doors"]
    brief_block_header, brief_blocks = g["brief_blocks"]
    door_cols, block_cols = app_server._DOORWAY_CSV_COLUMNS, app_server._BLOCK_CSV_COLUMNS

    # -- which type: the same canonical name on all three.
    assert doors["type"] == blocks["type"], "type: doorway vs block payload"
    assert meta["violation_type"] == doors["type"], "type: csv vs view"
    assert brief.h1 == [doors["type"]], "type: brief vs view"

    # -- every count each surface states about the lists.
    assert doors["count"] == len(doors["rows"]), "view: doorway count vs its own rows"
    assert blocks["count"] == len(blocks["blocks"]), "view: block count vs its own rows"
    assert int(meta["doorway_count"]) == doors["count"], "doorway count: csv stated vs view"
    assert int(meta["block_count"]) == blocks["count"], "block count: csv stated vs view"
    assert len(csv_doors) == doors["count"], "doorway count: csv rows vs view"
    assert len(csv_blocks) == blocks["count"], "block count: csv rows vs view"
    headings = {m.group(1): int(m.group(2)) for h in brief.h2
                if (m := re.fullmatch(r"(Doorways|Blocks) \((\d+)\)", h))}
    assert headings == {"Doorways": doors["count"], "Blocks": blocks["count"]}, \
        "counts: brief headings vs view"
    assert len(brief_doors) == doors["count"], "doorway count: brief rows vs view"
    assert len(brief_blocks) == blocks["count"], "block count: brief rows vs view"
    for noun, count in (("doorways", doors["count"]), ("blocks", blocks["count"])):
        if count == 0:   # an empty list is stated, not silently a missing table
            assert any(p.startswith(f"No {noun} match") for p in brief.p), \
                f"brief: empty {noun} list not stated"

    # -- every cell of every row, in the same order.
    # (the brief prints no table at all for an empty list -- checked above)
    labels = [label for _, label in door_cols]
    assert csv_door_header[:-1] == labels, "doorway columns: csv"
    assert brief_door_header == (labels if doors["count"] else None), "doorway columns: brief"
    labels = [label for _, label in block_cols]
    assert csv_block_header[:-1] == labels, "block columns: csv"
    assert brief_block_header == (labels if blocks["count"] else None), "block columns: brief"
    for i, row in enumerate(doors["rows"]):
        for key, label in door_cols:
            assert csv_doors[i][label] == _csv_cell(row[key], key), f"doorway {i} {label}: csv"
            assert brief_doors[i][label] == _brief_cell(row[key], key), f"doorway {i} {label}: brief"
    for i, row in enumerate(blocks["blocks"]):
        for key, label in block_cols:
            assert csv_blocks[i][label] == _csv_cell(row[key], key), f"block {i} {label}: csv"
            assert brief_blocks[i][label] == _brief_cell(row[key], key), f"block {i} {label}: brief"

    # -- the view's header figures, re-derived from the exported rows.
    summary = doors["summary"]
    for name, rows in (("csv", csv_doors), ("brief", brief_doors)):
        assert sum(int(r["Vehicles Distinct"]) for r in rows) == summary["distinct"], name
        assert sum(int(r["Vehicles Seen"]) for r in rows) == summary["seen"], name
        assert sum(1 for r in rows if int(r["Doorways Calling On Block"]) >= 2) \
            == summary["with_neighbour"], name

    # -- each exported block's figures against the exported doorways it rolls up.
    for name, drows, brows in (("csv", csv_doors, csv_blocks), ("brief", brief_doors, brief_blocks)):
        for b in brows:
            members = [d for d in drows if d["Block"] == b["Block"]]
            assert int(b["Doorways"]) == len(members), f"{name} block {b['Block']} doorways"
            for block_label, doorway_label in (("Calls (12mo)", "Calls (12mo)"),
                                                ("Calls (Total)", "Calls (Total)"),
                                                ("Tows", "Tows")):
                assert int(b[block_label]) == sum(int(d[doorway_label]) for d in members), \
                    f"{name} block {b['Block']} {block_label}"
            assert set(b["Addresses"].split("; ")) == {d["Address"] for d in members}, \
                f"{name} block {b['Block']} addresses"

    # -- filter values and the "filters in effect" statement.
    f = doors["filters"]
    assert blocks["filters"] == f, "filters: doorway vs block payload"
    assert meta["filter: district"] == (f["district"] or ""), "filter: csv district"
    for k in ("min_calls", "min_doorways", "recur_days", "recency_days"):
        assert meta[f"filter: {k}"] == str(f[k]), f"filter: csv {k}"
    items = brief.list_items()
    assert items["District"] == (f["district"] or "every district"), "filter: brief district"
    assert items["Minimum recent calls per doorway"] == str(f["min_calls"])
    assert items["Minimum still-calling doorways per block"] == str(f["min_doorways"])
    assert items["Recurrence window"] == f"{f['recur_days']} days"
    assert items["Recency window"] == f"{f['recency_days']} days"
    bits = app_server._active_filter_bits(f)
    in_effect = f"Filtered by {', '.join(bits)}." if bits else "Default filters -- none active."
    assert meta["filters_in_effect"] == in_effect, "filters in effect: csv"
    assert in_effect in brief.p, "filters in effect: brief"

    # -- the data date and the recency note, worded as the page words them.
    assert meta["latest_call_date"] == (doors["latest"] or ""), "latest call date: csv vs view"
    note = (f"Showing calls from the last {f['recency_days']} days, since {doors['latest']}."
            if doors["latest"] else "")
    assert meta["recency_note"] == note, "recency note: csv"
    assert (note in brief.p) if note else True, "recency note: brief"

    # -- the mirror state: what `/api/freshness` (the page's banner and footer)
    # says the mirror last did, named identically by both exports.
    assert meta["last_success_at"] == fresh["last_success_at"], "mirror state: csv last_success_at"
    assert meta["last_attempt_at"] == fresh["last_attempt_at"], "mirror state: csv last_attempt_at"
    assert meta["next_due_at"] == fresh["next_due_at"], "mirror state: csv next_due_at"
    assert meta["most_recent_call_date"] == fresh["most_recent_call_date"], \
        "mirror state: csv most_recent_call_date"
    assert items["Last successful sync"] == fresh["last_success_at"], \
        "mirror state: brief last_success_at"
    assert items["Next update due"] == fresh["next_due_at"], "mirror state: brief next_due_at"
    assert items["Most recent call in the data"] == fresh["most_recent_call_date"], \
        "mirror state: brief most_recent_call_date"


def test_view_and_exports_agree_and_all_name_the_latest_of_two_syncs(client, clean_db):
    """Task 7.8: the same store read after the first sync and again after the
    second. After each, every surface agrees with every other on every count
    (hand-written expectations below, so agreeing on a wrong count still
    fails) and all three name that sync -- after the second, the *second*,
    with the first sync's timestamp present nowhere in either export even
    though its `sync_runs` rows are still in the store."""
    slug = "blocking-driveway"

    _seed_first_sync_state(clean_db)
    first = _gather(client, slug)
    _assert_view_and_exports_agree(first)
    assert (first["doors"]["count"], first["blocks"]["count"]) == (2, 1)
    assert first["fresh"]["last_success_at"] == T1.isoformat()
    assert first["doors"]["latest"] == "2026-05-29"

    _seed_second_sync_state(clean_db)
    second = _gather(client, slug)
    _assert_view_and_exports_agree(second)
    assert (second["doors"]["count"], second["blocks"]["count"]) == (5, 2)
    assert {r["a"]: r["m"] for r in second["doors"]["rows"]} == {
        "1 First St": 3, "2 Second St": 2, "4 Fourth St": 2,
        "5 Ninth Ave": 2, "6 Tenth Ave": 2,
    }

    # Two syncs are in the store; the state named is the latest one.
    with clean_db.cursor() as cur:
        cur.execute("SELECT count(DISTINCT finished_at) FROM sync_runs WHERE layer = 'service_requests'")
        assert cur.fetchone()[0] == 2
    assert second["fresh"]["last_success_at"] == T2.isoformat() != first["fresh"]["last_success_at"]
    assert second["csv_meta"]["last_success_at"] == T2.isoformat()
    assert second["brief"].list_items()["Last successful sync"] == T2.isoformat()
    assert T1.isoformat() not in second["csv_text"]
    assert T1.isoformat() not in second["brief"].text
    # ... and the data date moved with it, on all three.
    assert second["doors"]["latest"] == second["csv_meta"]["latest_call_date"] == "2026-06-01"
    assert second["fresh"]["most_recent_call_date"].startswith("2026-06-01")


# (filter query, doorways listed, blocks listed) -- each count worked out from
# `_seed_second_sync_state`'s docstring, not read back from the app.
_FILTER_CASES = [
    # no filter: FIRST, SECOND, FOURTH, NINTH, TENTH; blocks A (3 doorways) and B (2)
    ("", 5, 2),
    # THIRD's single call is now enough; district 8's block B is out: A has 4 doorways
    ("min_calls=1&district=7", 4, 1),
    # only FIRST ST has 3 calls, and one doorway cannot make a two-doorway block
    ("min_calls=3", 1, 0),
    # the inclusive edge: FOURTH's call exactly 30 days back is still in a 30-day window ...
    ("recency_days=30", 5, 2),
    # ... and one day narrower it is out, leaving FOURTH one call (below min_calls=2)
    ("recency_days=29", 4, 2),
    # every one of the five filters off its default at once
    ("min_calls=1&district=7&min_doorways=3&recency_days=30&recur_days=100", 4, 1),
]


@pytest.mark.parametrize("query,doorway_count,block_count", _FILTER_CASES)
def test_view_and_exports_agree_under_the_same_filter(
        client, clean_db, query, doorway_count, block_count):
    """Task 7.8, the filtered cases: one filter query applied identically to
    the view's routes and both exports -- including the two boundary cases
    (a call exactly at the recency edge, a doorway exactly at `min_calls`) and
    an empty block list -- with every count, cell and filter statement
    agreeing, and every surface naming the second sync."""
    _seed_first_sync_state(clean_db)
    _seed_second_sync_state(clean_db)

    g = _gather(client, "blocking-driveway", query)

    _assert_view_and_exports_agree(g)
    assert (g["doors"]["count"], g["blocks"]["count"]) == (doorway_count, block_count)
    assert g["csv_meta"]["last_success_at"] == T2.isoformat()
    assert g["brief"].list_items()["Last successful sync"] == T2.isoformat()
    if query:
        # A filtered list cannot be mistaken for the default one, on either export.
        assert g["csv_meta"]["filters_in_effect"].startswith("Filtered by ")
    fourth = [r for r in g["doors"]["rows"] if r["a"] == "4 Fourth St"]
    if "recency_days=30" in query:
        assert [r["m"] for r in fourth] == [2]
    if "recency_days=29" in query:
        assert fourth == []


def test_all_five_filters_are_named_in_the_same_words_on_both_exports(client, clean_db):
    _seed_first_sync_state(clean_db)
    _seed_second_sync_state(clean_db)
    query = "min_calls=1&district=7&min_doorways=3&recency_days=30&recur_days=100"

    g = _gather(client, "blocking-driveway", query)

    expected = ("Filtered by district 7, min. recent calls ≥ 1, min. calling doorways/block ≥ 3, "
                "recency window ≤ 30 days, recurrence window ≤ 100 days.")
    assert g["csv_meta"]["filters_in_effect"] == expected
    assert expected in g["brief"].p


def test_a_doorway_with_no_median_gap_reads_the_same_in_both_exports_as_in_the_view(
        client, clean_db):
    """`None` values are where two renderings of one payload most easily
    diverge (a CSV writes an empty cell; a Jinja `{{ None }}` prints the word
    "None"). THIRD ST (one call, so no gap between calls) and block B (0
    dwellings, so no calls-per-1k rate) are the cases: the view says `null`,
    the CSV an empty cell, the brief an en dash -- never the string "None"."""
    _seed_first_sync_state(clean_db)
    _seed_second_sync_state(clean_db)

    g = _gather(client, "blocking-driveway", "min_calls=1")

    third = next(r for r in g["doors"]["rows"] if r["a"] == "3 Third St")
    assert third["g"] is None
    block_b = next(r for r in g["blocks"]["blocks"] if r["dw"] == 0)
    assert block_b["r"] is None
    assert "None" not in g["brief"].text
    _assert_view_and_exports_agree(g)


# The check above is only worth having if it can fail. Each test below breaks
# one surface on purpose -- the kinds of drift 7.8 exists to catch -- and shows
# `_assert_view_and_exports_agree` reject it *for the intended reason* (the
# label in the message names what disagreed).


@pytest.fixture
def two_syncs_seeded(clean_db):
    _seed_first_sync_state(clean_db)
    _seed_second_sync_state(clean_db)


def test_the_agreement_check_rejects_an_export_naming_the_earlier_sync(
        client, two_syncs_seeded, monkeypatch):
    """A stale-sync mix-up: rows from the second sync, clocks from the first."""
    real = app_server._freshness_for_export

    def names_the_first_sync(conn):
        payload = real(conn)
        payload["last_success_at"] = T1.isoformat()
        return payload

    monkeypatch.setattr(app_server, "_freshness_for_export", names_the_first_sync)

    with pytest.raises(AssertionError, match="mirror state: csv last_success_at"):
        _assert_view_and_exports_agree(_gather(client, "blocking-driveway"))


def test_the_agreement_check_rejects_a_stated_count_one_too_high(
        client, two_syncs_seeded, monkeypatch):
    """An off-by-one in the CSV's stated doorway count (the rows are right)."""
    real = app_server._export_csv_text

    def off_by_one(*args, **kwargs):
        return real(*args, **kwargs).replace("doorway_count,5\r\n", "doorway_count,6\r\n")

    monkeypatch.setattr(app_server, "_export_csv_text", off_by_one)

    with pytest.raises(AssertionError, match="doorway count: csv stated vs view"):
        _assert_view_and_exports_agree(_gather(client, "blocking-driveway"))


def test_the_agreement_check_rejects_a_csv_missing_its_last_block_row(
        client, two_syncs_seeded, monkeypatch):
    """A dropped row: the CSV's stated count and its block table disagree."""
    real = app_server._export_csv_text

    def drops_last_row(*args, **kwargs):
        return real(*args, **kwargs).rstrip("\r\n").rsplit("\r\n", 1)[0] + "\r\n"

    monkeypatch.setattr(app_server, "_export_csv_text", drops_last_row)

    with pytest.raises(AssertionError, match="block count: csv rows vs view"):
        _assert_view_and_exports_agree(_gather(client, "blocking-driveway"))


def test_the_agreement_check_rejects_a_brief_heading_one_too_high(
        client, two_syncs_seeded, monkeypatch):
    real = app_server.render_template_string

    def off_by_one(*args, **kwargs):
        return re.sub(r"Blocks \((\d+)\)", lambda m: f"Blocks ({int(m.group(1)) + 1})",
                      real(*args, **kwargs))

    monkeypatch.setattr(app_server, "render_template_string", off_by_one)

    with pytest.raises(AssertionError, match="counts: brief headings vs view"):
        _assert_view_and_exports_agree(_gather(client, "blocking-driveway"))


# ------------------------- an export names one mirror state (7.8's race caveat)
#
# Both export routes read a type's rows and the mirror's clocks (last sync, newest
# call) in separate queries. On Postgres' default READ COMMITTED each query sees
# whatever is committed at that instant, so a sync committing between the two
# reads gave an export whose rows came from one mirror state and whose named
# clocks came from the next. `_pin_snapshot` reads both from one REPEATABLE READ
# snapshot. The test lands a second connection's sync between the two reads, on
# purpose, rather than hoping a real request hits the window.

HALIFAX = zoneinfo.ZoneInfo("America/Halifax")
# 02:30 UTC on 30 May is 23:30 ADT on 29 May: this call's UTC date is not its
# Halifax date, so comparing the two as strings (or as dates without converting)
# gets the answer wrong. `derive` states the newest call as a Halifax date, the
# clocks state it as a UTC instant.
LATE_EVENING_CALL = datetime.datetime(2026, 5, 30, 2, 30, tzinfo=datetime.UTC)

# Per sync: (doorways listed, newest call as a Halifax date). Worked out from
# `_seed_first_sync_state` / `_seed_second_sync_state`, not read back from the app.
_STATE_OF_SYNC = {T1: (2, "2026-05-29"), T2: (5, "2026-06-01")}


def _seed_first_sync_ending_in_a_late_evening_call(conn):
    _seed_first_sync_state(conn)
    seed_driveway_call(conn, 7101, "2 SECOND ST, HALIFAX", LATE_EVENING_CALL)


def _state_named_by_csv(text):
    meta = _csv_metadata(text)
    _, doors = _csv_section(text, "DOORWAYS")
    return {"doorways": len(doors), "latest_call_date": meta["latest_call_date"],
            "last_success_at": meta["last_success_at"],
            "most_recent_call_date": meta["most_recent_call_date"]}


def _state_named_by_brief(body):
    brief = _parse_brief(body)
    items = brief.list_items()
    _, doors = brief.table_for("doorways")
    since = [m.group(1) for p in brief.p if (m := re.search(r"since (\d{4}-\d\d-\d\d)\.", p))]
    assert len(since) == 1, "the brief states its data date exactly once"
    return {"doorways": len(doors), "latest_call_date": since[0],
            "last_success_at": items["Last successful sync"],
            "most_recent_call_date": items["Most recent call in the data"]}


def _assert_rows_and_clocks_are_one_mirror_state(named):
    """The rows an export lists and the clocks it names must belong to the same
    sync. Instants are parsed, never compared as strings."""
    last_success = datetime.datetime.fromisoformat(named["last_success_at"])
    newest_call = datetime.datetime.fromisoformat(named["most_recent_call_date"])
    assert _STATE_OF_SYNC[last_success] == (named["doorways"], named["latest_call_date"]), (
        f"rows ({named['doorways']} doorways, newest call {named['latest_call_date']}) "
        f"are not the rows of the sync the export names ({named['last_success_at']})")
    assert newest_call.astimezone(HALIFAX).date().isoformat() == named["latest_call_date"], (
        "the newest call the clocks name is not the newest call the rows state")


_EXPORT_ROUTES = [
    ("csv", "/api/types/blocking-driveway/export.csv", _state_named_by_csv),
    ("brief", "/types/blocking-driveway/export", _state_named_by_brief),
]


@pytest.mark.parametrize("label,route,state_named_by", _EXPORT_ROUTES,
                         ids=[r[0] for r in _EXPORT_ROUTES])
def test_an_export_names_one_mirror_state_when_a_sync_lands_between_its_reads(
        client, clean_db, monkeypatch, label, route, state_named_by):
    _seed_first_sync_ending_in_a_late_evening_call(clean_db)
    # The boundary the check below has to get right: the first state's newest
    # call has a UTC date one day after its Halifax date.
    assert LATE_EVENING_CALL.date() != LATE_EVENING_CALL.astimezone(HALIFAX).date()

    real = app_server.derive_recency.derive_with_recency
    landed = []

    def rows_read_then_a_sync_lands(*args, **kwargs):
        result = real(*args, **kwargs)          # the rows are read from the first sync's state
        if not landed:
            _seed_second_sync_state(clean_db)   # a second connection commits the second sync
            landed.append(True)
        return result                           # the clocks are read after it

    monkeypatch.setattr(app_server.derive_recency, "derive_with_recency",
                        rows_read_then_a_sync_lands)

    raced = state_named_by(client.get(route).get_data(as_text=True))

    assert landed, "the second sync was never committed between the two reads"
    _assert_rows_and_clocks_are_one_mirror_state(raced)

    # The sync did land: the next request reads it, whole.
    after = state_named_by(client.get(route).get_data(as_text=True))
    _assert_rows_and_clocks_are_one_mirror_state(after)
    assert datetime.datetime.fromisoformat(after["last_success_at"]) == T2
    assert after["doorways"] == 5


# ------------------------- the export link on the served page (follow-up to 7.7)
#
# Task 7.7 landed `GET /api/types/<slug>/export.csv` and `GET /types/<slug>/
# export`, but nothing on the served page pointed at them. `web/app/index.html`
# now carries `#export-csv` and `#export-brief`, whose hrefs its script sets
# from SLUG and the filter values (`syncExportLinks()`, through the same
# `filterQueryFrom()` the data fetches use). These tests fail if the anchors go
# away, if the hrefs stop carrying the filters, or if the parameter names the
# page sends drift from the ones the routes read. No browser is involved: the
# markup is parsed from the served page, and the page's own JS functions are
# lifted out of it and run in node with stubs (skipped where node is absent).


class _AnchorParser(html.parser.HTMLParser):
    """Every `<a>` in a page: its attributes, its text, and whether it sits
    inside a `<button>` (invalid HTML, and not reliably clickable)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors, self._open, self._button_depth = [], None, 0
        self.notes = {}
        self._note_id = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "button":
            self._button_depth += 1
        elif tag == "a":
            self._open = {"attrs": a, "text": "", "in_button": self._button_depth > 0}
            self.anchors.append(self._open)
        elif tag == "span" and "export-note" in (a.get("class") or ""):
            self._note_id = "export-note"
            self.notes[self._note_id] = ""

    def handle_data(self, data):
        if self._open is not None:
            self._open["text"] += data
        if self._note_id is not None:
            self.notes[self._note_id] += data

    def handle_endtag(self, tag):
        if tag == "button":
            self._button_depth -= 1
        elif tag == "a":
            self._open = None
        elif tag == "span":
            self._note_id = None


def _page_export_anchors(client):
    parser = _AnchorParser()
    parser.feed(client.get("/").get_data(as_text=True))
    by_id = {a["attrs"].get("id"): a for a in parser.anchors}
    return by_id, parser.notes


def test_index_page_offers_both_exports_as_real_anchors_saying_what_you_get(client):
    by_id, notes = _page_export_anchors(client)

    csv_link, brief_link = by_id.get("export-csv"), by_id.get("export-brief")
    assert csv_link and brief_link, "the served page carries no export links"
    assert " ".join(csv_link["text"].split()) == "Download this list (CSV)"
    assert " ".join(brief_link["text"].split()) == "Read this list as a brief"
    # The CSV is a download; neither anchor is nested in a row-style <button>.
    assert "download" in csv_link["attrs"]
    assert not csv_link["in_button"] and not brief_link["in_button"]
    # A filtered view yields a filtered file, and the page says so beside them.
    assert "currently filtered" in notes["export-note"]


def test_index_page_export_links_are_not_left_pointing_nowhere(client):
    """The anchors carry no href in the markup (the script owns it, from SLUG
    and the filters), so they are hidden until the script has set both -- a
    failed load must not leave a focusable link with no target."""
    body = client.get("/").get_data(as_text=True)

    assert re.search(r'<p class="list-exports" id="list-exports" hidden>', body)
    assert 'id="export-csv" href=' not in body and 'id="export-brief" href=' not in body
    # ... and the script un-hides them only alongside setting the hrefs.
    fn = _js_function(body, "syncExportLinks")
    assert fn.index("el(\"export-csv\").href =") < fn.index("box.hidden = false")
    assert fn.index("el(\"export-brief\").href =") < fn.index("box.hidden = false")


def _js_function(body, name):
    m = re.search(rf"^function {name}\([^)]*\)\{{.*?^\}}", body, re.M | re.S)
    assert m, f"function {name}() not found in the served page"
    return m.group(0)


def test_index_page_export_hrefs_are_built_by_the_data_fetches_own_query_builder(client):
    body = client.get("/").get_data(as_text=True)

    # One query-string builder: the data fetches and the export links share it.
    assert "return filterQueryFrom(name => src.get(name));" in body
    assert "const q = filterQueryFrom(get);" in _js_function(body, "exportHrefs")
    assert "exportHrefs(SLUG, " in _js_function(body, "syncExportLinks")
    # Re-pointed on every render(), which is what District/"Clear filters" call.
    render_head = _js_function(body, "render").split("\n", 2)[1]
    assert render_head.strip() == "syncExportLinks();"


def test_index_page_filter_parameter_names_are_the_ones_the_routes_read(client):
    """The page's `FILTER_PARAM_NAMES` (which the export hrefs are built from)
    must be exactly the five keys `_filters_from_request` reads, or an export
    would silently ignore a filter the list on screen honours."""
    body = client.get("/").get_data(as_text=True)
    names = re.search(r"^const FILTER_PARAM_NAMES = (\[.*?\]);$", body, re.M)
    assert names, "FILTER_PARAM_NAMES not found"

    with app_server.create_app().test_request_context("/"):
        route_names = set(app_server._filters_from_request())

    assert set(json.loads(names.group(1))) == route_names == {
        "district", "min_calls", "min_doorways", "recur_days", "recency_days",
    }


_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run_page_export_script(body, slug, search, districts):
    """Run the served page's own `FILTER_PARAM_NAMES`, `filterQueryFrom`,
    `exportHrefs` and `syncExportLinks` in node against stubs for what a
    browser supplies (`location`, `el`, the view state `f`), calling
    `syncExportLinks()` once per District value in `districts` -- the way
    `render()` does after each pick. Returns each call's `[csv_href,
    brief_href, hidden]`. No browser was involved."""
    source = "\n".join([
        re.search(r"^const FILTER_PARAM_NAMES = .*?;$", body, re.M).group(0),
        _js_function(body, "filterQueryFrom"),
        _js_function(body, "exportHrefs"),
        _js_function(body, "syncExportLinks"),
    ])
    harness = f"""
const location = {{search: {json.dumps(search)}}};
const SLUG = {json.dumps(slug)};
const f = {{district: null}};
const nodes = {{"list-exports": {{hidden: true}}, "export-csv": {{}}, "export-brief": {{}}}};
const el = id => nodes[id];
{source}
const out = [];
for (const d of {json.dumps(districts)}){{
  f.district = d;
  syncExportLinks();
  out.push([nodes["export-csv"].href, nodes["export-brief"].href, nodes["list-exports"].hidden]);
}}
console.log(JSON.stringify(out));
"""
    done = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@_NEEDS_NODE
def test_index_page_script_builds_export_hrefs_from_slug_and_current_filters(client):
    body = client.get("/").get_data(as_text=True)

    # Unfiltered: bare routes for the slug the page is on, and shown.
    assert _run_page_export_script(body, "no-parking-sign", "", [None]) == [[
        "/api/types/no-parking-sign/export.csv", "/types/no-parking-sign/export", False,
    ]]

    # All five filters on the loaded query string travel to both routes, under
    # the names the routes read; unknown parameters (a shared link may carry
    # any) and blank ones do not.
    search = "?min_calls=1&min_doorways=3&recency_days=30&recur_days=100&district=7&utm=x&bogus="
    [(csv_href, brief_href, hidden)] = _run_page_export_script(
        body, "blocking-driveway", search, [None])
    assert not hidden
    assert csv_href.startswith("/api/types/blocking-driveway/export.csv?")
    assert brief_href.startswith("/types/blocking-driveway/export?")
    for href in (csv_href, brief_href):
        qs = dict(p.split("=") for p in href.split("?", 1)[1].split("&"))
        assert qs == {"district": "7", "min_calls": "1", "min_doorways": "3",
                      "recur_days": "100", "recency_days": "30"}


@_NEEDS_NODE
def test_index_page_export_hrefs_follow_a_district_pick_and_clear_without_a_reload(client):
    """The District select filters the list on screen at once (no reload), so
    the links must follow it: a pick wins over the loaded district, and
    "Clear filters" (`f.district = null`) falls back to the loaded query."""
    body = client.get("/").get_data(as_text=True)

    picks = _run_page_export_script(body, "blocking-driveway", "?min_calls=1", [None, "5", None])

    base = "/api/types/blocking-driveway/export.csv"
    assert [p[0] for p in picks] == [
        f"{base}?min_calls=1", f"{base}?district=5&min_calls=1", f"{base}?min_calls=1",
    ]
    assert picks[1][1] == "/types/blocking-driveway/export?district=5&min_calls=1"


@_NEEDS_NODE
def test_the_urls_the_page_produces_are_served_by_both_export_routes(client, clean_db):
    """End to end: take the hrefs the page's own script produces for a tracked
    slug under non-default filters and request exactly those URLs."""
    seed_two_doorway_block(clean_db)
    body = client.get("/").get_data(as_text=True)
    search = "?min_calls=1&district=7&min_doorways=2&recur_days=100&recency_days=30"

    for slug in ("blocking-driveway", "no-parking-sign"):
        [(csv_href, brief_href, _)] = _run_page_export_script(body, slug, search, [None])

        csv_resp = client.get(csv_href)
        assert csv_resp.status_code == 200, csv_href
        assert csv_resp.content_type.startswith("text/csv")
        meta = _csv_metadata(csv_resp.get_data(as_text=True))
        # The file is the filtered one, not the default: every value arrived.
        assert (meta["filter: min_calls"], meta["filter: district"]) == ("1", "7")
        assert meta["filter: recur_days"] == "100" and meta["filter: recency_days"] == "30"
        assert meta["filter: min_doorways"] == "2"

        brief_resp = client.get(brief_href)
        assert brief_resp.status_code == 200, brief_href
        assert brief_resp.content_type.startswith("text/html")
        assert "district 7, min. recent calls ≥ 1" in brief_resp.get_data(as_text=True)


def test_untracked_type_page_shows_no_export_link(client):
    """`_untracked_type_page` is a separate template, not `index.html`: no
    lists, no slug, so no export link (and no dead `#export-*` anchor)."""
    for path in ("/types/not-a-real-type", "/types/not-a-real-type/export"):
        resp = client.get(path)
        assert resp.status_code == 404
        body = resp.get_data(as_text=True)
        assert "export-csv" not in body and "export-brief" not in body, path
        assert "Download this list" not in body and "as a brief" not in body
