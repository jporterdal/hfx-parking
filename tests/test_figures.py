"""Task 4.10 and 4.13's mirror-backed figures: recurrence split by tow status, and
median response time. `src/mirror/figures.py` computes both from `derive.load()`'s
own output, so these tests build calls through the same mirror tables
`tests/test_derive.py` seeds, rather than through fixtures for the live service.

Every test here runs under the suite's autouse `no_network` fixture, so a passing
run is itself part of the "no network call" claim the module docstring makes.
"""

import datetime

import pytest

from mirror import figures

pytestmark = pytest.mark.db

HALIFAX_TZ = datetime.timezone(datetime.timedelta(hours=-3))  # ADT, matches July fixtures below


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


def insert_custom_field(conn, object_id, request_id, name, value, field_id=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO custom_fields (object_id, request_id, custom_field_id, "
            "custom_field_name, custom_field_value) VALUES (%s, %s, %s, %s, %s)",
            (object_id, request_id, field_id, name, value),
        )
    conn.commit()


def seed_driveway_call(conn, request_id, object_id, address, when, date_closed=None,
                       towed="N", make="FORD", model="F150", colour="BLUE",
                       set_towed=True):
    insert_service_request(conn, object_id, request_id, address, when,
                           date_closed=date_closed)
    insert_custom_field(conn, object_id * 10, request_id, "Alleged Violation", "DRIVEWAY")
    if set_towed:
        insert_custom_field(conn, object_id * 10 + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, object_id * 10 + 3, request_id, "Vehicle Make", make)
    insert_custom_field(conn, object_id * 10 + 4, request_id, "Vehicle Model", model)
    insert_custom_field(conn, object_id * 10 + 5, request_id, "Vehicle Colour", colour)


# ------------------------------------------------------------- recurrence (4.10)


def test_recurrence_counts_the_earlier_call_as_recurring_when_a_later_one_follows(clean_db):
    """The archived enforcement-effectiveness spec's own scenario: a doorway with
    two calls inside the window has its earlier call counted as recurring, looking
    forward only.
    """
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 1001, 1, "1 SAME ST, HALIFAX", base)
    seed_driveway_call(clean_db, 1002, 2, "1 SAME ST, HALIFAX",
                       base + datetime.timedelta(days=30))

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")

    assert result["population"]["count"] == 2
    # Both calls are not-towed (default towed="N"): the earlier one recurs, the
    # later one has nothing after it to recur into.
    assert result["groups"]["not_towed"]["calls"] == 2
    assert result["groups"]["not_towed"]["recurring"] == 1


def test_recurrence_window_excludes_a_call_outside_the_configured_days(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 1001, 1, "1 SAME ST, HALIFAX", base)
    seed_driveway_call(clean_db, 1002, 2, "1 SAME ST, HALIFAX",
                       base + datetime.timedelta(days=400))  # outside 365-day default

    result = figures.recurrence_by_tow(clean_db, violation="Driveway", recur_days=365)

    assert result["groups"]["not_towed"]["recurring"] == 0

    result_wide = figures.recurrence_by_tow(clean_db, violation="Driveway", recur_days=400)
    assert result_wide["groups"]["not_towed"]["recurring"] == 1


def test_recurrence_split_by_tow_status_is_independent_per_group(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    # A towed doorway: two calls, 10 days apart -- recurs.
    seed_driveway_call(clean_db, 2001, 1, "1 TOWED ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 2002, 2, "1 TOWED ST, HALIFAX",
                       base + datetime.timedelta(days=10), towed="Y")
    # A not-towed doorway: one call, never recurs.
    seed_driveway_call(clean_db, 2003, 3, "2 QUIET ST, HALIFAX", base, towed="N")

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")

    assert result["groups"]["towed"] == {"calls": 2, "recurring": 1, "recurrence_pct": 50.0}
    assert result["groups"]["not_towed"] == {"calls": 1, "recurring": 0, "recurrence_pct": 0.0}


def test_recurrence_reports_missing_tow_field_as_unknown_not_not_towed(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 3001, 1, "1 UNKNOWN ST, HALIFAX", base, set_towed=False)

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")

    assert result["groups"]["not_towed"]["calls"] == 0
    assert result["groups"]["unknown_tow_status"]["calls"] == 1


def test_recurrence_population_excludes_calls_with_no_usable_address_or_date(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 4001, 1, "1 GOOD ST, HALIFAX", base)
    seed_driveway_call(clean_db, 4002, 2, "", base)  # unusable address -> clean_address None

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")

    assert result["population"]["raw_selection_count"] == 2
    assert result["population"]["count"] == 1
    assert result["population"]["excluded_no_usable_address_or_date"] == 1


def test_recurrence_does_not_use_the_recency_or_min_calls_watchlist_filters(clean_db):
    """A single old call -- one that hotspots.build() would drop from the watchlist
    for having no call in the last 12 months -- still counts in this population.
    This figure describes every call made, not the currently-listed doorways.
    """
    long_ago = datetime.datetime(2020, 1, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 5001, 1, "1 OLD ST, HALIFAX", long_ago)

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")

    assert result["population"]["count"] == 1


def test_recurrence_as_of_restricts_to_calls_initiated_before_the_cutoff(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 6001, 1, "1 EARLY ST, HALIFAX", base)
    seed_driveway_call(clean_db, 6002, 2, "2 LATE ST, HALIFAX",
                       base + datetime.timedelta(days=5))

    cutoff = base + datetime.timedelta(days=1)
    result = figures.recurrence_by_tow(clean_db, violation="Driveway", as_of=cutoff)

    assert result["population"]["count"] == 1
    assert "2024-07-01" in result["population"]["definition"]


def test_recurrence_carries_a_caveat_and_states_the_window(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 7001, 1, "1 A ST, HALIFAX", base)

    result = figures.recurrence_by_tow(clean_db, violation="Driveway", recur_days=200)

    assert result["recurrence_window_days"] == 200
    assert "observational" in result["caveat"].lower()


# ------------------------------------------------------------- response time (4.13)


def test_response_time_computes_median_minutes_between_initiated_and_closed(clean_db):
    when = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 8001, 1, "1 A ST, HALIFAX", when,
                       date_closed=when + datetime.timedelta(minutes=40))
    seed_driveway_call(clean_db, 8002, 2, "2 B ST, HALIFAX", when,
                       date_closed=when + datetime.timedelta(minutes=42))

    result = figures.response_time(clean_db, violation="Driveway")

    assert result["closed_calls"] == 2
    assert result["never_closed_calls"] == 0
    assert result["median_elapsed_minutes"] == 41.0


def test_response_time_excludes_never_closed_from_the_median_but_counts_them(clean_db):
    when = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 8101, 1, "1 A ST, HALIFAX", when,
                       date_closed=when + datetime.timedelta(minutes=10))
    seed_driveway_call(clean_db, 8102, 2, "2 B ST, HALIFAX", when, date_closed=None)

    result = figures.response_time(clean_db, violation="Driveway")

    assert result["population"]["count"] == 2
    assert result["closed_calls"] == 1
    assert result["never_closed_calls"] == 1
    assert result["median_elapsed_minutes"] == 10.0


def test_response_time_is_unaffected_by_which_side_of_a_dst_change_the_calls_fall(clean_db):
    """4.13's honesty note: elapsed time is a difference between two fixed UTC
    instants, so the daylight-saving-aware local conversion (4.6) must not change
    it. A call spanning the fall-back boundary still reports the true elapsed
    minutes, not a local-clock difference that the DST shift would distort.
    """
    # 2024-11-03 02:00 local is when Halifax falls back from ADT to AST.
    initiated = datetime.datetime(2024, 11, 3, 4, 0, tzinfo=datetime.UTC)  # just before
    closed = initiated + datetime.timedelta(minutes=90)  # spans the fall-back instant
    seed_driveway_call(clean_db, 8201, 1, "1 A ST, HALIFAX", initiated, date_closed=closed)

    result = figures.response_time(clean_db, violation="Driveway")

    assert result["median_elapsed_minutes"] == 90.0


def test_response_time_as_of_restricts_the_population(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 8301, 1, "1 A ST, HALIFAX", base,
                       date_closed=base + datetime.timedelta(minutes=20))
    seed_driveway_call(clean_db, 8302, 2, "2 B ST, HALIFAX",
                       base + datetime.timedelta(days=5),
                       date_closed=base + datetime.timedelta(days=5, minutes=60))

    cutoff = base + datetime.timedelta(days=1)
    result = figures.response_time(clean_db, violation="Driveway", as_of=cutoff)

    assert result["population"]["count"] == 1
    assert result["median_elapsed_minutes"] == 20.0


def test_response_time_with_no_calls_reports_no_median_rather_than_erroring(clean_db):
    result = figures.response_time(clean_db, violation="Driveway")

    assert result["population"]["count"] == 0
    assert result["median_elapsed_minutes"] is None


# --------------------------------------------------------------------------- CLI


def test_main_prints_json_with_both_figures(clean_db, monkeypatch, capsys):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 9001, 1, "1 A ST, HALIFAX", base,
                       date_closed=base + datetime.timedelta(minutes=41))

    monkeypatch.setattr("sys.argv", ["figures.py", "--violation", "Driveway"])
    monkeypatch.setattr(figures.db, "connect", lambda *a, **k: clean_db)

    exit_code = figures.main()

    assert exit_code == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"recurrence_by_tow", "response_time"}
    assert out["response_time"]["median_elapsed_minutes"] == 41.0


def test_main_rejects_giving_both_selectors(clean_db, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["figures.py", "--violation", "Driveway", "--canonical-type", "Blocking Driveway"],
    )

    with pytest.raises(SystemExit):
        figures.main()
