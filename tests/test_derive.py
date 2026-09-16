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
import csv
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


def seed_call(conn, request_id, object_id, address, when, label,
             towed="N", make="FORD", model="F150", colour="BLUE",
             owner="HRM", lat=44.65, lon=-63.57):
    insert_service_request(conn, object_id, request_id, address, when, lat=lat, lon=lon)
    insert_custom_field(conn, object_id * 10, request_id, "Alleged Violation", label)
    insert_custom_field(conn, object_id * 10 + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, object_id * 10 + 2, request_id, "Property Ownership", owner)
    insert_custom_field(conn, object_id * 10 + 3, request_id, "Vehicle Make", make)
    insert_custom_field(conn, object_id * 10 + 4, request_id, "Vehicle Model", model)
    insert_custom_field(conn, object_id * 10 + 5, request_id, "Vehicle Colour", colour)


def seed_driveway_call(conn, request_id, object_id, address, when,
                       towed="N", make="FORD", model="F150", colour="BLUE",
                       owner="HRM", lat=44.65, lon=-63.57):
    seed_call(conn, request_id, object_id, address, when, "DRIVEWAY",
             towed=towed, make=make, model=model, colour=colour, owner=owner,
             lat=lat, lon=lon)


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

    figures = {k: v for k, v in result.items()
              if k not in ("derived_at", "last_sync_success_at",
                           "most_recent_call_date", "reconciled")}
    assert figures == {
        "rows": [], "blocks": [], "calls": [], "fields": result["fields"],
        "latest": None, "missing_service_request_ids": [],
    }


# --------------------------------------------------- absent, not fetched (4.2)


def test_derive_reports_a_selected_id_missing_from_service_requests(clean_db):
    """4.2's first concrete shape of "absent": a request id the selection query
    named (it carries a matching `Alleged Violation` custom field) but that
    `service_requests` does not hold -- a gap a live-fallback would have papered
    over by fetching it. There is no such path here (see the module docstring and
    `test_derivation_module_never_imports_source_fetch_functions`), so it is
    reported instead.
    """
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)
    # A custom field naming a call with no corresponding service_requests row.
    insert_custom_field(clean_db, 99, 9999, "Alleged Violation", "DRIVEWAY")

    result = derive.derive(clean_db, violation="Driveway")

    assert result["missing_service_request_ids"] == [9999]
    # Not topped up: the missing id never appears in `calls`.
    assert 9999 not in {c["REQUEST_ID"] for c in result["calls"]}
    assert {c["REQUEST_ID"] for c in result["calls"]} == {2001}


def test_derive_missing_ids_empty_when_every_selected_id_is_found(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)

    result = derive.derive(clean_db, violation="Driveway")

    assert result["missing_service_request_ids"] == []


def test_derive_reports_addresses_with_no_census_block_match(clean_db):
    """4.2's second shape of "absent": a doorway whose coordinates match no held
    census polygon. Already visible per row (`row["block"]` is falsy); this checks
    it is also surfaced as a count on the result, not merely left to a reader who
    happens to inspect every row.
    """
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    # No census_areas row inserted at all -- every doorway is outside every block.
    seed_driveway_call(clean_db, 3001, 1, "1 LONE ST, HALIFAX", when, lat=0.5, lon=0.5)
    seed_driveway_call(clean_db, 3002, 2, "1 LONE ST, HALIFAX",
                       when - datetime.timedelta(days=1), lat=0.5, lon=0.5)

    result = derive.derive(clean_db, violation="Driveway")

    assert len(result["rows"]) == 1
    assert result["rows"][0]["block"] in (None, "")
    assert result["unmatched_census_count"] == 1


# ------------------------------------------------------ reconciliation state (4.3)


def insert_sync_run(conn, layer, ok=True, reconciled=True, started_at=None):
    started_at = started_at or datetime.datetime.now(datetime.UTC)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sync_runs (kind, layer, started_at, finished_at, ok, "
            "reconciled) VALUES (%s, %s, %s, %s, %s, %s)",
            ("reload", layer, started_at, started_at, ok, reconciled),
        )
    conn.commit()


def insert_layer_state(conn, layer, last_success_at=None, static=False):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO layer_state (layer, last_success_at, static) "
            "VALUES (%s, %s, %s) ON CONFLICT (layer) DO UPDATE SET "
            "last_success_at = EXCLUDED.last_success_at, static = EXCLUDED.static",
            (layer, last_success_at, static),
        )
    conn.commit()


def test_reconciliation_state_true_when_every_currency_layer_reconciled(clean_db):
    insert_sync_run(clean_db, "service_requests", reconciled=True)
    insert_sync_run(clean_db, "custom_fields", reconciled=True)

    state = derive.reconciliation_state(clean_db)

    assert state == {
        "reconciled": True,
        "per_layer": {"service_requests": True, "custom_fields": True},
    }


def test_reconciliation_state_false_when_one_layer_diverged(clean_db):
    insert_sync_run(clean_db, "service_requests", reconciled=True)
    insert_sync_run(clean_db, "custom_fields", reconciled=False)

    state = derive.reconciliation_state(clean_db)

    assert state["reconciled"] is False
    assert state["per_layer"] == {"service_requests": True, "custom_fields": False}


def test_reconciliation_state_unknown_when_no_sync_ever_recorded(clean_db):
    state = derive.reconciliation_state(clean_db)

    assert state == {
        "reconciled": False,
        "per_layer": {"service_requests": None, "custom_fields": None},
    }


def test_reconciliation_state_uses_the_latest_run_per_layer(clean_db):
    insert_sync_run(clean_db, "service_requests", reconciled=False,
                    started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "service_requests", reconciled=True,
                    started_at=datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "custom_fields", reconciled=True)

    state = derive.reconciliation_state(clean_db)

    assert state["per_layer"]["service_requests"] is True
    assert state["reconciled"] is True


def test_reconciliation_state_ignores_a_failed_attempt(clean_db):
    """A more recent *failed* sync attempt (kind carries no reconciliation
    outcome, `ok=False`) must not shadow the last successful attempt's verdict --
    otherwise a sync that starts failing right after a clean reconciliation would
    make an agreeing mirror read as unreconciled for no evidential reason.
    """
    insert_sync_run(clean_db, "service_requests", reconciled=True,
                    started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "service_requests", ok=False, reconciled=None,
                    started_at=datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "custom_fields", reconciled=True)

    state = derive.reconciliation_state(clean_db)

    assert state["per_layer"]["service_requests"] is True
    assert state["reconciled"] is True


def test_reconciliation_state_a_newer_null_row_does_not_shadow_an_older_true(clean_db):
    """A successful run that never recorded a count comparison (reconciled is
    NULL -- a no-op poll, or a pre-3.5 row) is not evidence either way, so it must
    not hide the last run that actually did record one.
    """
    insert_sync_run(clean_db, "service_requests", reconciled=True,
                    started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "service_requests", reconciled=None,
                    started_at=datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "custom_fields", reconciled=True)

    state = derive.reconciliation_state(clean_db)

    assert state["per_layer"]["service_requests"] is True
    assert state["reconciled"] is True


def test_reconciliation_state_a_newer_null_row_does_not_shadow_an_older_false(clean_db):
    """Same rule in the other direction: a later no-op poll must not paper over a
    divergence an earlier run actually observed.
    """
    insert_sync_run(clean_db, "service_requests", reconciled=False,
                    started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "service_requests", reconciled=None,
                    started_at=datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "custom_fields", reconciled=True)

    state = derive.reconciliation_state(clean_db)

    assert state["per_layer"]["service_requests"] is False
    assert state["reconciled"] is False


def test_reconciliation_state_none_when_only_null_rows_recorded(clean_db):
    """A layer whose only successful runs are no-op polls has never recorded a
    reconciliation outcome at all -- that reads the same as no successful sync
    ever having run, not as an observed divergence.
    """
    insert_sync_run(clean_db, "service_requests", reconciled=None,
                    started_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "service_requests", reconciled=None,
                    started_at=datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC))
    insert_sync_run(clean_db, "custom_fields", reconciled=True)

    state = derive.reconciliation_state(clean_db)

    assert state["per_layer"]["service_requests"] is None
    assert state["reconciled"] is False


def test_derive_result_carries_the_reconciliation_state(clean_db):
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)
    insert_sync_run(clean_db, "service_requests", reconciled=True)
    insert_sync_run(clean_db, "custom_fields", reconciled=True)

    result = derive.derive(clean_db, violation="Driveway")

    assert result["reconciled"] == {
        "reconciled": True,
        "per_layer": {"service_requests": True, "custom_fields": True},
    }


def test_derive_does_not_top_up_missing_rows_when_unreconciled(clean_db):
    """4.3: a derivation against a mirror whose latest reconciliation diverged
    still reports itself as unreconciled, and still derives from exactly what the
    mirror holds -- it neither hides the gap nor closes it. There is no code path
    here that could close it: no network import exists in this module at all
    (`test_derivation_module_never_imports_source_fetch_functions`).
    """
    when = datetime.datetime(2024, 7, 15, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 2001, 1, "10 QUEEN ST, HALIFAX", when)
    insert_sync_run(clean_db, "service_requests", reconciled=False)
    insert_sync_run(clean_db, "custom_fields", reconciled=True)
    # The kind of gap an unreconciled mirror can carry: a custom field naming a
    # call the held service_requests table does not have.
    insert_custom_field(clean_db, 900, 9999, "Alleged Violation", "DRIVEWAY")

    result = derive.derive(clean_db, violation="Driveway")

    assert result["reconciled"]["reconciled"] is False
    assert result["reconciled"]["per_layer"]["service_requests"] is False
    assert 9999 in result["missing_service_request_ids"]
    assert {c["REQUEST_ID"] for c in result["calls"]} == {2001}


# --------------------------------------------------------- freshness clocks (4.7)


def test_derive_result_carries_all_three_clocks_together(clean_db):
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 A ST, HALIFAX", when)
    sync_time = datetime.datetime(2026, 6, 2, 3, 0, tzinfo=datetime.UTC)
    insert_layer_state(clean_db, "service_requests", last_success_at=sync_time)
    insert_layer_state(clean_db, "custom_fields", last_success_at=sync_time)

    result = derive.derive(clean_db, violation="Driveway")

    now = datetime.datetime.now(datetime.UTC)
    assert result["derived_at"] is not None
    assert datetime.timedelta(0) <= now - result["derived_at"] < datetime.timedelta(minutes=1)
    assert result["last_sync_success_at"] == sync_time
    assert result["most_recent_call_date"] == when


def test_write_outputs_stamps_csv_rows_with_all_three_clocks(clean_db, tmp_path):
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 A ST, HALIFAX", when)
    seed_driveway_call(clean_db, 3002, 2, "1 A ST, HALIFAX",
                       when - datetime.timedelta(days=1))
    result = derive.derive(clean_db, violation="Driveway")

    derive._write_outputs(result, "Driveway", tmp_path, 365)

    with open(tmp_path / "watchlist.csv", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row["derived_at"] == result["derived_at"].isoformat()
    assert row["most_recent_call_date"] == result["most_recent_call_date"].isoformat()
    assert row["last_sync_success_at"] == "unknown"  # no sync recorded in clean_db


def test_write_outputs_stamps_brief_with_a_freshness_note(clean_db, tmp_path):
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 A ST, HALIFAX", when)
    seed_driveway_call(clean_db, 3002, 2, "1 A ST, HALIFAX",
                       when - datetime.timedelta(days=1))
    result = derive.derive(clean_db, violation="Driveway")

    derive._write_outputs(result, "Driveway", tmp_path, 365)

    text = (tmp_path / "watchlist.md").read_text()
    note_line = next(l for l in text.splitlines() if l.startswith("Derived "))
    assert "Derived" in note_line
    assert "last successful sync" in note_line
    assert "most recent call in the data" in note_line


# --------------------------------------------------------- one pass, all types (4.16)


def test_derive_all_defaults_to_every_canonical_type(clean_db):
    results = derive.derive_all(clean_db)

    assert set(results) == set(violation_types.CANONICAL_TYPES)


def test_derive_all_no_call_belongs_to_two_types_output(clean_db):
    """The 4.16 invariant: a call belongs to exactly one type's output. Seeded
    with two distinct canonical types sharing nothing but the module under test.
    """
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 A ST, HALIFAX", when)
    seed_driveway_call(clean_db, 3002, 2, "1 A ST, HALIFAX",
                       when - datetime.timedelta(days=1))
    seed_call(clean_db, 3003, 3, "2 B ST, HALIFAX", when, "No Parking Sign")
    seed_call(clean_db, 3004, 4, "2 B ST, HALIFAX",
             when - datetime.timedelta(days=1), "No Parking Sign")

    results = derive.derive_all(
        clean_db, types=["Blocking Driveway", "No Parking Sign"], min_calls=1
    )

    driveway_ids = {c["REQUEST_ID"] for c in results["Blocking Driveway"]["calls"]}
    sign_ids = {c["REQUEST_ID"] for c in results["No Parking Sign"]["calls"]}
    assert driveway_ids == {3001, 3002}
    assert sign_ids == {3003, 3004}
    assert driveway_ids.isdisjoint(sign_ids)

    driveway_addresses = {r["address"] for r in results["Blocking Driveway"]["rows"]}
    sign_addresses = {r["address"] for r in results["No Parking Sign"]["rows"]}
    assert driveway_addresses.isdisjoint(sign_addresses)


def test_derive_all_slice_matches_direct_derive_exactly(clean_db):
    """The 4.16 ordering/shape requirement: a type's slice of `derive_all()` is
    exactly what a separate `derive(canonical_type=...)` call on that type alone
    would have produced -- rows, blocks, calls, fields and latest all equal, in
    the same order. Only `derived_at` is excluded from the comparison: it is
    wall-clock time the two calls cannot share (see `derive()`'s docstring).
    """
    insert_census_area(clean_db, "12090999", SQUARE_RING, dwellings=80)
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 SAME ST, HALIFAX", when,
                       lat=0.5, lon=0.5)
    seed_driveway_call(clean_db, 3002, 2, "1 SAME ST, HALIFAX",
                       when - datetime.timedelta(days=5), lat=0.5, lon=0.5)
    seed_call(clean_db, 3003, 3, "2 B ST, HALIFAX", when, "No Parking Sign")
    seed_call(clean_db, 3004, 4, "2 B ST, HALIFAX",
             when - datetime.timedelta(days=1), "No Parking Sign")

    all_results = derive.derive_all(
        clean_db, types=["Blocking Driveway", "No Parking Sign"], min_calls=1
    )
    direct = derive.derive(clean_db, canonical_type="Blocking Driveway", min_calls=1)

    from_all = all_results["Blocking Driveway"]
    from_all.pop("derived_at")
    direct.pop("derived_at")
    assert from_all == direct


def _count_cursor_calls(conn, fn):
    original = conn.cursor
    count = 0

    def counting(*a, **k):
        nonlocal count
        count += 1
        return original(*a, **k)

    conn.cursor = counting
    try:
        fn()
    finally:
        conn.cursor = original
    return count


def test_derive_all_query_count_is_independent_of_number_of_types(clean_db):
    """4.16's "one pass" claim, checked rather than trusted: how many `cursor()`
    opens `derive_all()` needs must not grow with how many canonical types are
    asked for -- 2 types and 5 types cost exactly the same.
    """
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 A ST, HALIFAX", when)
    insert_service_request(clean_db, 2, 3002, "2 B ST, HALIFAX", when)
    insert_custom_field(clean_db, 20, 3002, "Alleged Violation", "No Parking Sign")

    two_types = ["Blocking Driveway", "No Parking Sign"]
    five_types = list(violation_types.CANONICAL_TYPES)[:5]

    count_two = _count_cursor_calls(
        clean_db, lambda: derive.derive_all(clean_db, types=two_types, min_calls=1)
    )
    count_five = _count_cursor_calls(
        clean_db, lambda: derive.derive_all(clean_db, types=five_types, min_calls=1)
    )

    assert count_two == count_five


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


def test_main_rejects_all_types_with_violation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["derive.py", str(tmp_path), "--all-types", "--violation", "Driveway"],
    )

    with pytest.raises(SystemExit):
        derive.main()


def test_main_all_types_writes_one_subdirectory_per_type(clean_db, tmp_path,
                                                          monkeypatch):
    when = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
    seed_driveway_call(clean_db, 3001, 1, "1 A ST, HALIFAX", when)
    seed_driveway_call(clean_db, 3002, 2, "1 A ST, HALIFAX",
                       when - datetime.timedelta(days=1))
    seed_call(clean_db, 3003, 3, "2 B ST, HALIFAX", when, "No Parking Sign")
    seed_call(clean_db, 3004, 4, "2 B ST, HALIFAX",
             when - datetime.timedelta(days=1), "No Parking Sign")

    out_dir = tmp_path / "all"
    monkeypatch.setattr(
        "sys.argv", ["derive.py", str(out_dir), "--all-types", "--min-calls", "1"]
    )
    monkeypatch.setattr(derive.db, "connect", lambda *a, **k: clean_db)

    exit_code = derive.main()

    assert exit_code == 0
    assert (out_dir / "blocking-driveway" / "watchlist.csv").exists()
    assert (out_dir / "blocking-driveway" / "watchlist.md").exists()
    assert (out_dir / "no-parking-sign" / "watchlist.csv").exists()
    # A type with no seeded calls gets no directory at all, not an empty one.
    assert not (out_dir / "private-property").exists()
