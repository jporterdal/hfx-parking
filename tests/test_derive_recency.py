"""Task 5.5b: the recency window as a parameter, via `mirror.derive_recency`
rather than by editing `src/hotspots.py`/`src/mirror/derive.py` (both out of
scope -- see that module's docstring for why, and for the exact arithmetic
this file checks).

Every test seeds the throwaway `clean_db` schema directly (same fixtures and
helper shapes `tests/test_derive.py` uses -- duplicated here rather than
imported, since that file belongs to a different task/agent in this change and
importing test helpers across files would couple this file to edits made
there for unrelated reasons).

The prior attempt at this task used the same shift formula this module uses
and still got the window membership wrong at the boundary. So the boundary
tests below check the *actual* inclusion/exclusion of a call placed exactly
`recency_days` old versus `recency_days + 1` old -- not just that the
computed `latest` looks plausible -- which is where that attempt's bug would
have shown up.
"""

import datetime

import pytest

import hotspots
from mirror import derive, derive_recency

pytestmark = pytest.mark.db

SQUARE_RING = [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]


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


def insert_custom_field(conn, object_id, request_id, name, value):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO custom_fields (object_id, request_id, custom_field_name, "
            "custom_field_value) VALUES (%s, %s, %s, %s)",
            (object_id, request_id, name, value),
        )
    conn.commit()


def seed_driveway_call(conn, request_id, address, when, towed="N", lat=0.5, lon=0.5,
                       district="7"):
    """One call filed under a raw label the canonical 'Blocking Driveway'
    grouping covers (`hotspots.load()`'s own substring selection matches it
    too), shaped like `tests/test_app.py`'s own `seed_driveway_call`.
    """
    insert_service_request(conn, request_id, request_id, address, when, lat=lat, lon=lon,
                           district=district)
    base = request_id * 10
    insert_custom_field(conn, base, request_id, "Alleged Violation",
                        "Blocking Driveway (DISPATCH)")
    insert_custom_field(conn, base + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, base + 2, request_id, "Vehicle Make", "FORD")
    insert_custom_field(conn, base + 3, request_id, "Vehicle Model", "F150")
    insert_custom_field(conn, base + 4, request_id, "Vehicle Colour", "BLUE")


LATEST = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)


# ------------------------------------------------------------ default is a no-op


def test_default_recency_matches_derive_derive_exactly(clean_db):
    """recency_days=365 must reproduce `derive.derive()`'s own behaviour field
    for field -- the default is not merely "close", it is `hotspots.build()`'s
    own hardcoded window, so the two must agree exactly.
    """
    seed_driveway_call(clean_db, 5001, "1 FIRST ST, HALIFAX", LATEST - datetime.timedelta(days=10))
    seed_driveway_call(clean_db, 5002, "1 FIRST ST, HALIFAX", LATEST - datetime.timedelta(days=5),
                       towed="Y")
    seed_driveway_call(clean_db, 5003, "2 SECOND ST, HALIFAX", LATEST - datetime.timedelta(days=200))

    expected = derive.derive(clean_db, violation="Driveway")
    actual = derive_recency.derive_with_recency(clean_db, violation="Driveway",
                                                 recency_days=365)

    assert actual["rows"] == expected["rows"]
    assert actual["blocks"] == expected["blocks"]
    assert actual["latest"] == expected["latest"]
    assert actual["missing_service_request_ids"] == expected["missing_service_request_ids"]
    assert actual["unmatched_census_count"] == expected["unmatched_census_count"]


def test_default_recency_constant_matches_derive_with_recency_default(clean_db):
    """Calling with no `recency_days` argument at all uses the same 365-day
    default -- proven directly, not just documented."""
    seed_driveway_call(clean_db, 5001, "1 FIRST ST, HALIFAX", LATEST)

    explicit = derive_recency.derive_with_recency(clean_db, violation="Driveway",
                                                   recency_days=365)
    implicit = derive_recency.derive_with_recency(clean_db, violation="Driveway")

    assert implicit["rows"] == explicit["rows"]
    assert derive_recency.DEFAULT_RECENCY_DAYS == 365


# --------------------------------------------------------------- the boundary


def test_a_call_exactly_at_the_recency_boundary_is_counted(clean_db):
    """`hotspots.build()`'s own comparison is `t >= recent_from` (inclusive).
    A call exactly `recency_days` before the latest call must therefore still
    count -- the precise boundary the prior, discarded attempt at this task
    got wrong.
    """
    # The anchor call: guarantees true_latest is exactly LATEST.
    seed_driveway_call(clean_db, 6001, "9 ANCHOR ST, HALIFAX", LATEST)
    # Exactly 30 days before the anchor -- the inclusive edge of a 30-day window.
    seed_driveway_call(clean_db, 6002, "1 BOUND ST, HALIFAX",
                       LATEST - datetime.timedelta(days=30))
    # 31 days before the anchor -- just outside a 30-day window.
    seed_driveway_call(clean_db, 6003, "2 OUT ST, HALIFAX",
                       LATEST - datetime.timedelta(days=31))

    result = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=1, recency_days=30,
    )

    assert result["latest"] == LATEST
    addresses = {r["address"]: r["calls_12mo"] for r in result["rows"]}
    assert addresses == {"9 ANCHOR ST": 1, "1 BOUND ST": 1}
    assert "2 OUT ST" not in addresses


def test_widening_the_window_by_one_day_pulls_in_the_boundary_call(clean_db):
    """The same three doorways as above, at recency_days=31: the previously
    excluded "2 OUT ST" doorway is now listed, and nothing else changes --
    proof the window is exact to the day, not merely "roughly right".
    """
    seed_driveway_call(clean_db, 6001, "9 ANCHOR ST, HALIFAX", LATEST)
    seed_driveway_call(clean_db, 6002, "1 BOUND ST, HALIFAX",
                       LATEST - datetime.timedelta(days=30))
    seed_driveway_call(clean_db, 6003, "2 OUT ST, HALIFAX",
                       LATEST - datetime.timedelta(days=31))

    at_30 = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=1, recency_days=30,
    )
    at_31 = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=1, recency_days=31,
    )

    assert {r["address"] for r in at_30["rows"]} == {"9 ANCHOR ST", "1 BOUND ST"}
    assert {r["address"] for r in at_31["rows"]} == {"9 ANCHOR ST", "1 BOUND ST", "2 OUT ST"}


# --------------------------------------------------- listing and ranking change


def test_recency_window_changes_which_doorways_are_listed_and_the_ranking(clean_db):
    """Task 5.5b's own verification clause: changing the recency window must
    change (a) which doorways are listed and (b) the ranking.

    "9 OLD ST" has five calls, all well inside 365 days but all older than 10
    days -- present and top-ranked once the window is wide enough to see any
    of them, entirely absent from a 10-day window. "1 NEW ST" has two calls
    inside every window tested. So a narrow window lists only NEW; a wide
    window lists OLD ranked above NEW (5 recent calls beats 2, and neither
    address ever gets towed, so tow_rate is 0 for both and `calls_12mo` alone
    orders them).
    """
    for i, days_ago in enumerate((50, 55, 60, 65, 70)):
        seed_driveway_call(clean_db, 7000 + i, "9 OLD ST, HALIFAX",
                           LATEST - datetime.timedelta(days=days_ago))
    seed_driveway_call(clean_db, 7100, "1 NEW ST, HALIFAX", LATEST)
    seed_driveway_call(clean_db, 7101, "1 NEW ST, HALIFAX",
                       LATEST - datetime.timedelta(days=5))

    narrow = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=1, recency_days=10,
    )
    wide = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=1, recency_days=200,
    )

    # Listed set changes: OLD is entirely absent from the narrow window.
    assert {r["address"] for r in narrow["rows"]} == {"1 NEW ST"}
    # Ranking changes: once OLD is visible, it outranks NEW (5 recent calls vs 2).
    assert [r["address"] for r in wide["rows"]] == ["9 OLD ST", "1 NEW ST"]
    assert wide["rows"][0]["calls_12mo"] == 5
    assert wide["rows"][1]["calls_12mo"] == 2


def test_recency_window_is_independent_of_recur_days(clean_db):
    """The recurrence window (`recur_days`) and the recency window
    (`recency_days`) govern different things (design.md M11) -- changing one
    must not move the other's figure. Two calls 40 days apart: `recur_days=365`
    counts them as a repeat either way; `recency_days` alone decides whether
    the older call is inside the listed window at all.
    """
    seed_driveway_call(clean_db, 8001, "1 PAIR ST, HALIFAX", LATEST)
    seed_driveway_call(clean_db, 8002, "1 PAIR ST, HALIFAX",
                       LATEST - datetime.timedelta(days=40))

    wide_recency = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=1, recency_days=100, recur_days=365,
    )
    narrow_recency = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=1, recency_days=30, recur_days=365,
    )

    assert wide_recency["rows"][0]["calls_12mo"] == 2
    assert wide_recency["rows"][0]["repeat_calls"] == 1
    # Narrowing recency to 30 days drops the 40-day-old call from calls_12mo,
    # but repeat_calls (driven only by recur_days) is unaffected -- it is
    # computed over every call at the address, not just the "recent" ones.
    assert narrow_recency["rows"][0]["calls_12mo"] == 1
    assert narrow_recency["rows"][0]["repeat_calls"] == 1


# --------------------------------------------------------- other filters carry

def test_min_calls_and_district_still_apply_alongside_recency(clean_db):
    seed_driveway_call(clean_db, 9001, "1 LONELY ST, HALIFAX", LATEST)
    seed_driveway_call(clean_db, 9002, "2 BUSY ST, HALIFAX", LATEST, district="9")
    seed_driveway_call(clean_db, 9003, "2 BUSY ST, HALIFAX",
                       LATEST - datetime.timedelta(days=1), district="9")

    result = derive_recency.derive_with_recency(
        clean_db, violation="Driveway", min_calls=2, district="9", recency_days=365,
    )

    assert [r["address"] for r in result["rows"]] == ["2 BUSY ST"]


# ------------------------------------------------------------------- no calls

def test_no_matching_calls_returns_empty_result_with_recency_set(clean_db):
    result = derive_recency.derive_with_recency(clean_db, violation="Driveway",
                                                 recency_days=30)

    assert result["rows"] == []
    assert result["blocks"] == []
    assert result["latest"] is None
