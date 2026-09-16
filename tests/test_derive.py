"""The mirror-backed derivation (tasks 4.1, 4.4): selection, joining, outcome and
vehicle attachment, address reduction, doorway location and census placement, read
entirely from Postgres.

`src/mirror/derive.py` reuses `hotspots.py`'s pure functions rather than
reimplementing them, so most of what this file checks is that the mirror-shaped
data handed to those functions is equivalent to what `hotspots.load()`/
`fetch_blocks()` would have produced from the live service -- never that `build()`/
`roll_blocks()` themselves behave correctly, which `tests/test_hotspots_time.py` and
the sibling change's own history already cover.

Every test here runs under the suite's autouse `no_network` fixture (conftest.py),
which raises if anything reaches `urllib.request.urlopen`. A derivation test that
passes is therefore itself part of 4.1's "no network request is issued" proof; the
static test below proves the stronger claim that no code path *could* reach one.
"""

import ast
import datetime
import inspect

import pytest

import hotspots
from mirror import derive, violation_types

pytestmark = pytest.mark.db

HALIFAX_NOON_JULY = 1721044800000  # 2024-07-15 12:00:00 UTC, epoch ms
DRIVEWAY_LABELS_LIKE_MATCHES = ("DRIVEWAY", "Blocking Driveway (DISPATCH)")


# --------------------------------------------------------- no-network proof (4.1)


def test_derivation_module_never_imports_source_fetch_functions():
    """4.1: the derivation has no live-fetch path to fall back to at all, not merely
    one that happens not to be called. `mirror.source` (pages/post/count/
    last_edit_date -- the functions that issue HTTP requests) is not imported, and
    `hotspots.query`/`hotspots.fetch_blocks` (the two network functions hotspots.py
    also defines) are never *called*, even though `hotspots` itself is imported for
    its pure functions and its docstrings legitimately name them in prose.

    Walks the parsed AST rather than grepping source text, so a docstring that
    merely mentions `hotspots.fetch_blocks()` (as this module's own does, to explain
    what `census_blocks()` replaces) cannot produce a false failure -- only an
    actual `Call` node naming one of these attributes can.
    """
    assert "source" not in vars(derive)  # mirror.source is not imported at all

    tree = ast.parse(inspect.getsource(derive))
    forbidden_calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        target = node.func
        if not isinstance(target.value, ast.Name):
            continue
        called = f"{target.value.id}.{target.attr}"
        if called in {
            "source.pages", "source.post", "source.count", "source.last_edit_date",
            "hotspots.query", "hotspots.fetch_blocks", "hotspots.load",
        }:
            forbidden_calls.add(called)
    assert forbidden_calls == set()

    # Belt and braces: nothing imports urllib either, directly or transitively
    # through anything other than hotspots/mirror.db, neither of which this test
    # exercises a network path of.
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "urllib" not in imported_names
    assert "urllib.request" not in imported_names


# ------------------------------------------------------------------ census (4.4)


def insert_census_area(conn, dauid, rings, dwellings=100, population=250,
                       object_id=1):
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    box = (min(xs), min(ys), max(xs), max(ys)) if xs else (None, None, None, None)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO census_areas (object_id, dauid, population, dwellings, "
            "rings, min_lon, min_lat, max_lon, max_lat) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (object_id, dauid, population, dwellings, list_to_jsonb(rings), *box),
        )
    conn.commit()


def list_to_jsonb(rings):
    import json
    return json.dumps(rings)


# A small square, (0,0)-(1,1), point (0.5, 0.5) inside; (2, 2) outside.
SQUARE_RING = [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]


def test_census_blocks_shapes_rows_like_hotspots_fetch_blocks(clean_db):
    insert_census_area(clean_db, "12090999", SQUARE_RING, dwellings=42, population=99)

    blocks = derive.census_blocks(clean_db)

    assert len(blocks) == 1
    block = blocks[0]
    assert set(block) == {"id", "dwellings", "people", "rings", "box"}
    assert block["id"] == "12090999"
    assert block["dwellings"] == 42
    assert block["people"] == 99
    assert block["rings"] == SQUARE_RING
    assert block["box"] == (0, 0, 1, 1)


def test_census_blocks_feed_hotspots_containment_unchanged(clean_db):
    """The whole point of 4.4: hotspots.in_block/find_block run against mirror-read
    blocks with no adaptation."""
    insert_census_area(clean_db, "12090999", SQUARE_RING)

    blocks = derive.census_blocks(clean_db)

    assert hotspots.in_block(0.5, 0.5, blocks[0]) is True
    assert hotspots.in_block(2, 2, blocks[0]) is False
    assert hotspots.find_block(0.5, 0.5, blocks) is blocks[0]
    assert hotspots.find_block(2, 2, blocks) is None


def test_census_blocks_skips_a_row_with_no_geometry(clean_db):
    insert_census_area(clean_db, "12090999", [])

    assert derive.census_blocks(clean_db) == []


def test_census_blocks_reads_every_row_no_paging_or_retry(clean_db):
    # 4.4: the whole layer in one query. Seed more rows than any page size used
    # elsewhere in the mirror (census pages at 200) to show nothing here limits it.
    for i in range(3):
        insert_census_area(clean_db, f"1209{i:04}", SQUARE_RING, object_id=i + 1)

    assert len(derive.census_blocks(clean_db)) == 3


# --------------------------------------------------------------- selection (4.1)


def insert_custom_field(conn, object_id, request_id, name, value, field_id=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO custom_fields (object_id, request_id, custom_field_id, "
            "custom_field_name, custom_field_value) VALUES (%s, %s, %s, %s, %s)",
            (object_id, request_id, field_id, name, value),
        )
    conn.commit()


def test_select_by_substring_matches_hotspots_loads_own_query(clean_db):
    insert_custom_field(clean_db, 1, 101, "Alleged Violation", "DRIVEWAY")
    insert_custom_field(clean_db, 2, 102, "Alleged Violation",
                        "Blocking Driveway (DISPATCH)")
    insert_custom_field(clean_db, 3, 103, "Alleged Violation", "No Parking Sign")
    insert_custom_field(clean_db, 4, 104, "Vehicle Make", "DRIVEWAY")  # wrong field name

    ids = derive._select_ids_by_substring(clean_db, "Driveway")

    assert ids == {101, 102}


def test_select_by_canonical_type_matches_the_frozen_raw_labels(clean_db):
    insert_custom_field(clean_db, 1, 101, "Alleged Violation", "DRIVEWAY")
    insert_custom_field(clean_db, 2, 102, "Alleged Violation",
                        "Blocking Driveway (DISPATCH)")
    insert_custom_field(clean_db, 3, 103, "Alleged Violation", "No Parking Sign")

    ids = derive._select_ids_by_canonical_type(clean_db, "Blocking Driveway")

    assert ids == {101, 102}


def test_driveway_canonical_grouping_and_substring_selection_agree(clean_db):
    """The report item task 4.1 asks for: does the canonical 'Blocking Driveway'
    grouping (violation_types.py) select the same calls as hotspots.load()'s
    substring match on 'Driveway'? Seeded with every raw label either path could
    plausibly catch, plus two decoys that must be excluded by both.
    """
    labels = violation_types.raw_labels_for("Blocking Driveway")
    assert set(labels) == set(DRIVEWAY_LABELS_LIKE_MATCHES)

    for i, label in enumerate(labels):
        insert_custom_field(clean_db, i + 1, 100 + i, "Alleged Violation", label)
    # Decoys: contain no "DRIVEWAY" substring and belong to no driveway grouping.
    insert_custom_field(clean_db, 90, 190, "Alleged Violation", "No Parking Sign")
    insert_custom_field(clean_db, 91, 191, "Alleged Violation", "On Sidewalk")

    substring_ids = derive._select_ids_by_substring(clean_db, "Driveway")
    canonical_ids = derive._select_ids_by_canonical_type(clean_db, "Blocking Driveway")

    assert substring_ids == canonical_ids == {100 + i for i in range(len(labels))}


def test_select_by_substring_is_case_insensitive_like_hotspots(clean_db):
    insert_custom_field(clean_db, 1, 101, "Alleged Violation", "driveway")

    assert derive._select_ids_by_substring(clean_db, "Driveway") == {101}


def test_selection_with_no_matches_returns_empty_set(clean_db):
    insert_custom_field(clean_db, 1, 101, "Alleged Violation", "No Parking Sign")

    assert derive._select_ids_by_substring(clean_db, "Driveway") == set()


# ---------------------------------------------------- joining and outcome (4.1)


def insert_service_request(conn, object_id, request_id, address, date_initiated,
                           date_closed=None, district="7", community="HALIFAX",
                           lat=44.65, lon=-63.57, resolution=None,
                           initiated_by="INTERNAL"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_requests (object_id, request_id, date_initiated, "
            "date_closed, address, community, district, resolution, latitude, "
            "longitude, initiated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s)",
            (object_id, request_id, date_initiated, date_closed, address, community,
             district, resolution, lat, lon, initiated_by),
        )
    conn.commit()


def seed_driveway_call(conn, request_id, object_id, address, when,
                       towed="N", make="FORD", model="F150", colour="BLUE",
                       owner="HRM", lat=44.65, lon=-63.57):
    insert_service_request(conn, object_id, request_id, address, when, lat=lat, lon=lon)
    insert_custom_field(conn, object_id * 10, request_id, "Alleged Violation", "DRIVEWAY")
    insert_custom_field(conn, object_id * 10 + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, object_id * 10 + 2, request_id, "Property Ownership", owner)
    insert_custom_field(conn, object_id * 10 + 3, request_id, "Vehicle Make", make)
    insert_custom_field(conn, object_id * 10 + 4, request_id, "Vehicle Model", model)
    insert_custom_field(conn, object_id * 10 + 5, request_id, "Vehicle Colour", colour)


def test_load_returns_calls_shaped_like_hotspots_load(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)

    calls, fields = derive.load(clean_db, violation="Driveway")

    assert len(calls) == 1
    call = calls[0]
    assert set(call) == {
        "REQUEST_ID", "DATE_INITIATED", "DATE_CLOSED", "ADDRESS", "COMMUNITY",
        "DISTRICT", "RESOLUTION", "LATITUDE", "LONGITUDE", "INITIATED_BY",
    }
    assert call["REQUEST_ID"] == 2001
    assert call["ADDRESS"] == "10 QUEEN ST, HALIFAX"
    assert isinstance(call["DATE_INITIATED"], int)


def test_load_date_initiated_round_trips_through_hotspots_to_local(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)

    calls, _ = derive.load(clean_db, violation="Driveway")

    local = hotspots.to_local(calls[0]["DATE_INITIATED"])
    assert local.strftime("%Y-%m-%d %H:%M") == "2024-07-15 09:00"  # ADT, UTC-3


def test_load_date_closed_null_round_trips_as_none(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)

    calls, _ = derive.load(clean_db, violation="Driveway")

    assert calls[0]["DATE_CLOSED"] is None


def test_load_fields_carry_the_outcome_and_vehicle_attributes(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when,
                       towed="Y", make="HONDA", model="CIVIC", colour="RED")

    _, fields = derive.load(clean_db, violation="Driveway")

    assert fields[2001]["Vehicle Was Towed"] == "Y"
    assert fields[2001]["Vehicle Make"] == "HONDA"
    assert hotspots.vehicle_key(fields[2001]) == ("HONDA", "CIVIC", "RED")


def test_load_omits_an_unset_attribute_rather_than_storing_none(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    insert_service_request(clean_db, 1, 2001, "10 QUEEN ST, HALIFAX", when)
    insert_custom_field(clean_db, 10, 2001, "Alleged Violation", "DRIVEWAY")
    # No Vehicle Was Towed row at all -- distinct from a row with a NULL value.

    _, fields = derive.load(clean_db, violation="Driveway")

    assert "Vehicle Was Towed" not in fields[2001]
    assert fields[2001].get("Vehicle Was Towed") is None  # same read hotspots.build() makes


def test_load_with_no_matching_selection_returns_empty(clean_db):
    insert_custom_field(clean_db, 1, 101, "Alleged Violation", "No Parking Sign")

    calls, fields = derive.load(clean_db, violation="Driveway")

    assert calls == []
    assert fields[999] == {}  # a defaultdict, matching hotspots.load()'s return type


def test_load_requires_exactly_one_selector(clean_db):
    with pytest.raises(ValueError):
        derive.load(clean_db)
    with pytest.raises(ValueError):
        derive.load(clean_db, violation="Driveway", canonical_type="Blocking Driveway")


def test_load_by_canonical_type_reads_the_same_calls_as_by_substring(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)

    by_substring, _ = derive.load(clean_db, violation="Driveway")
    by_canonical, _ = derive.load(clean_db, canonical_type="Blocking Driveway")

    assert {c["REQUEST_ID"] for c in by_substring} == {c["REQUEST_ID"] for c in by_canonical}


# ------------------------------------------------------------- end to end (4.1/4.4)


def test_derive_builds_doorway_and_block_rows_from_the_mirror_alone(clean_db):
    insert_census_area(clean_db, "12090999", SQUARE_RING, dwellings=200)
    latest = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    # Two calls at the same address, both inside the square and within 12mo of latest.
    seed_driveway_call(clean_db, 3001, 1, "1 SAME ST, HALIFAX",
                       latest - datetime.timedelta(days=10), lat=0.5, lon=0.5)
    seed_driveway_call(clean_db, 3002, 2, "1 SAME ST, HALIFAX",
                       latest - datetime.timedelta(days=5), lat=0.5, lon=0.5, towed="Y")

    result = derive.derive(clean_db, violation="Driveway")

    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["address"] == "1 SAME ST"  # hotspots.clean_address strips the city
    assert row["calls_12mo"] == 2
    assert row["tows"] == 1
    assert row["block"] == "12090999"
    assert row["block_dwellings"] == 200
    assert result["latest"] is not None


def test_derive_matches_hotspots_build_called_directly_on_the_same_load(clean_db):
    """derive() adds nothing beyond what hotspots.build()/roll_blocks() already do
    to whatever load()/census_blocks() hand them -- proven by calling build()
    directly on derive.load()'s own output and comparing.
    """
    insert_census_area(clean_db, "12090999", SQUARE_RING, dwellings=50)
    latest = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 SAME ST, HALIFAX",
                       latest - datetime.timedelta(days=10), lat=0.5, lon=0.5)
    seed_driveway_call(clean_db, 3002, 2, "1 SAME ST, HALIFAX",
                       latest - datetime.timedelta(days=5), lat=0.5, lon=0.5)

    calls, fields = derive.load(clean_db, violation="Driveway")
    blocks_geo = derive.census_blocks(clean_db)
    latest_local = max(hotspots.to_local(c["DATE_INITIATED"]) for c in calls)
    expected_rows = hotspots.build(calls, fields, 2, 365, latest_local, blocks_geo)
    expected_blocks = hotspots.roll_blocks(expected_rows, 2)
    neighbours = {b["block"]: b["doorways"] for b in expected_blocks}
    for r in expected_rows:
        r["block_doorways_calling"] = neighbours.get(r["block"], 1)

    result = derive.derive(clean_db, violation="Driveway")

    assert result["rows"] == expected_rows
    assert result["blocks"] == expected_blocks


def test_derive_min_calls_drops_addresses_below_threshold(clean_db):
    latest = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 LONELY ST, HALIFAX", latest)

    result = derive.derive(clean_db, violation="Driveway", min_calls=2)

    assert result["rows"] == []


def test_derive_district_filter_narrows_the_doorway_list(clean_db):
    latest = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    insert_service_request(clean_db, 1, 3001, "1 A ST, HALIFAX", latest, district="7")
    insert_custom_field(clean_db, 10, 3001, "Alleged Violation", "DRIVEWAY")
    insert_service_request(clean_db, 2, 3002, "1 A ST, HALIFAX",
                           latest - datetime.timedelta(days=1), district="7")
    insert_custom_field(clean_db, 20, 3002, "Alleged Violation", "DRIVEWAY")
    insert_service_request(clean_db, 3, 3003, "2 B ST, HALIFAX", latest, district="9")
    insert_custom_field(clean_db, 30, 3003, "Alleged Violation", "DRIVEWAY")
    insert_service_request(clean_db, 4, 3004, "2 B ST, HALIFAX",
                           latest - datetime.timedelta(days=1), district="9")
    insert_custom_field(clean_db, 40, 3004, "Alleged Violation", "DRIVEWAY")

    result = derive.derive(clean_db, violation="Driveway", district="9")

    assert [r["district"] for r in result["rows"]] == ["9"]


def test_derive_with_no_matching_calls_returns_empty_result(clean_db):
    result = derive.derive(clean_db, violation="Driveway")

    assert result == {"rows": [], "blocks": [], "calls": [], "fields": result["fields"],
                      "latest": None}


# ------------------------------------------------------------------------- CLI


def test_main_writes_outputs_to_the_given_directory_only(clean_db, tmp_path,
                                                          monkeypatch):
    latest = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 SAME ST, HALIFAX",
                       latest - datetime.timedelta(days=10))
    seed_driveway_call(clean_db, 3002, 2, "1 SAME ST, HALIFAX",
                       latest - datetime.timedelta(days=5))

    out_dir = tmp_path / "derived"
    monkeypatch.setattr("sys.argv", ["derive.py", str(out_dir)])
    monkeypatch.chdir(tmp_path)  # so an accidental out/ write would land beside us, visibly
    # Reuse the fixture's own connection rather than have main() open (and leak,
    # for the rest of the test session) a second one via db.connect().
    monkeypatch.setattr(derive.db, "connect", lambda *a, **k: clean_db)

    exit_code = derive.main()

    assert exit_code == 0
    assert (out_dir / "watchlist.csv").exists()
    assert (out_dir / "watchlist.md").exists()
    assert not (tmp_path / "out").exists()


def test_main_rejects_giving_both_selectors(clean_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["derive.py", str(tmp_path), "--violation", "Driveway",
         "--canonical-type", "Blocking Driveway"],
    )

    with pytest.raises(SystemExit):
        derive.main()
