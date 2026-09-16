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
    assert set(out) == {"recurrence_by_tow", "response_time", "call_denominators"}
    assert out["response_time"]["median_elapsed_minutes"] == 41.0


def test_main_rejects_giving_both_selectors(clean_db, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["figures.py", "--violation", "Driveway", "--canonical-type", "Blocking Driveway"],
    )

    with pytest.raises(SystemExit):
        figures.main()


# ------------------------------------------------------------- effect bound (4.11)


def seed_cluster(conn, base_id, address, when, n_calls, spacing_days, towed):
    """`n_calls` at one doorway, `spacing_days` apart, all with the same tow
    status -- a controlled doorway 'cluster' for the effect-bound tests below.
    """
    for i in range(n_calls):
        seed_driveway_call(
            conn, base_id + i, base_id + i, address,
            when + datetime.timedelta(days=i * spacing_days), towed=towed,
        )


def test_effect_bound_reports_naive_and_cluster_aware_intervals_and_a_headline(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 1001, 1, "1 TOWED ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 1002, 2, "1 TOWED ST, HALIFAX",
                       base + datetime.timedelta(days=10), towed="Y")
    seed_driveway_call(clean_db, 1003, 3, "2 QUIET ST, HALIFAX", base, towed="N")

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")
    eb = result["effect_bound"]

    assert eb["comparable"] is True
    assert eb["headline"] == "cluster_bootstrap"
    assert len(eb["naive"]["ci_95_pct_points"]) == 2
    assert eb["naive"]["rules_out_reduction_larger_than_pct_points"] >= 0
    assert "independent" in eb["naive"]["assumption"].lower()
    assert eb["cluster_bootstrap"]["resample_unit"] == "doorway (clean address)"
    assert eb["cluster_bootstrap"]["seed"] == figures.DEFAULT_BOOTSTRAP_SEED


def test_effect_bound_not_comparable_when_a_group_is_empty(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    # Every call not-towed: the towed group is empty.
    seed_driveway_call(clean_db, 2001, 1, "1 A ST, HALIFAX", base, towed="N")

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")

    assert result["effect_bound"] == {
        "comparable": False,
        "reason": "the towed or not-towed group has no calls in this selection",
    }


def test_effect_bound_is_deterministic_for_a_fixed_seed(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_cluster(clean_db, 3000, "1 TOWED ST, HALIFAX", base, 6, 10, towed="Y")
    seed_cluster(clean_db, 3100, "2 QUIET ST, HALIFAX", base, 6, 10, towed="N")

    first = figures.recurrence_by_tow(clean_db, violation="Driveway")
    second = figures.recurrence_by_tow(clean_db, violation="Driveway")

    assert (
        first["effect_bound"]["cluster_bootstrap"]["ci_95_pct_points"]
        == second["effect_bound"]["cluster_bootstrap"]["ci_95_pct_points"]
    )


def test_effect_bound_cluster_aware_interval_is_wider_when_calls_cluster_by_doorway(clean_db):
    """The whole point of 4.11's cluster bootstrap: when a few doorways each
    contribute many calls with very different recurrence behaviour, the naive
    per-call interval is far too confident, because it counts 40 calls as 40
    independent trials when there are really only 2 doorways per group behaving
    consistently. The cluster-aware interval, which resamples doorways, reports
    that honestly as a much wider interval.
    """
    base = datetime.datetime(2024, 1, 1, 12, tzinfo=HALIFAX_TZ)
    # Towed: one doorway that almost always recurs, one that never does.
    seed_cluster(clean_db, 4000, "1 TOWED RECUR ST, HALIFAX", base, 20, 10, towed="Y")
    seed_cluster(clean_db, 4100, "2 TOWED QUIET ST, HALIFAX", base, 20, 400, towed="Y")
    # Not towed: same pattern, so the headline difference is close to zero.
    seed_cluster(clean_db, 4200, "3 FREE RECUR ST, HALIFAX", base, 20, 10, towed="N")
    seed_cluster(clean_db, 4300, "4 FREE QUIET ST, HALIFAX", base, 20, 400, towed="N")

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")
    eb = result["effect_bound"]

    naive_lo, naive_hi = eb["naive"]["ci_95_pct_points"]
    cluster_lo, cluster_hi = eb["cluster_bootstrap"]["ci_95_pct_points"]

    assert (cluster_hi - cluster_lo) > 2 * (naive_hi - naive_lo)


def test_effect_bound_rules_out_reduction_reads_off_the_lower_ci_bound(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_cluster(clean_db, 5000, "1 TOWED ST, HALIFAX", base, 10, 10, towed="Y")
    seed_cluster(clean_db, 5100, "2 QUIET ST, HALIFAX", base, 10, 10, towed="N")

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")
    eb = result["effect_bound"]

    naive_lo, _naive_hi = eb["naive"]["ci_95_pct_points"]
    expected = round(max(0.0, -naive_lo), 1)
    assert eb["naive"]["rules_out_reduction_larger_than_pct_points"] == expected


# ---------------------------------------------------- caveat travels with it (4.12)


def test_result_dict_places_the_caveat_immediately_after_the_effect_bound(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 6001, 1, "1 TOWED ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 6002, 2, "2 QUIET ST, HALIFAX", base, towed="N")

    result = figures.recurrence_by_tow(clean_db, violation="Driveway")
    keys = list(result.keys())

    assert keys.index("caveat") == keys.index("effect_bound") + 1


def test_main_json_output_carries_the_caveat_adjacent_to_the_effect_bound(
    clean_db, monkeypatch, capsys,
):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 7001, 1, "1 TOWED ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 7002, 2, "2 QUIET ST, HALIFAX", base, towed="N")

    monkeypatch.setattr("sys.argv", ["figures.py", "--violation", "Driveway"])
    monkeypatch.setattr(figures.db, "connect", lambda *a, **k: clean_db)

    figures.main()

    import json
    out = json.loads(capsys.readouterr().out)
    recurrence_keys = list(out["recurrence_by_tow"].keys())

    assert "effect_bound" in out["recurrence_by_tow"]
    assert recurrence_keys.index("caveat") == recurrence_keys.index("effect_bound") + 1
    assert "observational" in out["recurrence_by_tow"]["caveat"].lower()


def test_main_readable_output_carries_the_caveat_right_after_the_effect_bound_lines(
    clean_db, monkeypatch, capsys,
):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 7101, 1, "1 TOWED ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 7102, 2, "2 QUIET ST, HALIFAX", base, towed="N")

    monkeypatch.setattr("sys.argv", ["figures.py", "--violation", "Driveway"])
    monkeypatch.setattr(figures.db, "connect", lambda *a, **k: clean_db)

    figures.main()

    err_lines = capsys.readouterr().err.splitlines()
    # The last line naming a bound (naive, then cluster-aware) precedes the caveat.
    last_bound_line = max(
        i for i, l in enumerate(err_lines) if "rules out a reduction" in l
    )
    caveat_line = next(i for i, l in enumerate(err_lines) if "observational" in l.lower())

    # The caveat is the line immediately following the effect-bound lines -- it
    # is not printed as a separate, detachable fact somewhere else in the report.
    assert caveat_line == last_bound_line + 1
    assert err_lines[caveat_line].strip().startswith(
        "Observational, not a randomized comparison"
    )


def test_no_output_path_prints_the_recurrence_comparison_without_the_caveat(
    clean_db, monkeypatch, capsys,
):
    """4.12: every place the towed-vs-not-towed comparison is emitted -- the
    returned dict, the JSON on stdout, and the readable report on stderr --
    carries the observational caveat. None of the three can show the percentages
    without it.
    """
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 7201, 1, "1 TOWED ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 7202, 2, "2 QUIET ST, HALIFAX", base, towed="N")

    # 1. The dict recurrence_by_tow returns directly.
    result = figures.recurrence_by_tow(clean_db, violation="Driveway")
    assert "caveat" in result and "observational" in result["caveat"].lower()

    # 2. The JSON main() prints to stdout.
    monkeypatch.setattr("sys.argv", ["figures.py", "--violation", "Driveway"])
    monkeypatch.setattr(figures.db, "connect", lambda *a, **k: clean_db)
    figures.main()
    captured = capsys.readouterr()
    import json
    out = json.loads(captured.out)
    assert "observational" in out["recurrence_by_tow"]["caveat"].lower()

    # 3. The readable report main() prints to stderr, wherever the towed/not_towed
    # percentages appear.
    assert "towed:" in captured.err
    assert "not_towed:" in captured.err
    assert "observational" in captured.err.lower()


# --------------------------------------------------------- call denominators (4.14)


def seed_labeled_call(conn, object_id, request_id, address, when, raw_label, towed="N"):
    """Like `seed_driveway_call`, but with a caller-chosen raw `Alleged Violation`
    label rather than the fixed `"DRIVEWAY"` -- for 4.14's raw-label-breakdown
    tests, which need more than one raw label in the selection.
    """
    insert_service_request(conn, object_id, request_id, address, when)
    insert_custom_field(conn, object_id * 10, request_id, "Alleged Violation", raw_label)
    insert_custom_field(conn, object_id * 10 + 1, request_id, "Vehicle Was Towed", towed)


def test_call_denominators_breaks_the_raw_selection_down_by_raw_label(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_labeled_call(clean_db, 1, 8001, "1 A ST, HALIFAX", base,
                      "Blocking Driveway (DISPATCH)")
    seed_labeled_call(clean_db, 2, 8002, "2 B ST, HALIFAX", base,
                      "Blocking Driveway (DISPATCH)")
    seed_labeled_call(clean_db, 3, 8003, "3 C ST, HALIFAX", base, "DRIVEWAY")

    result = figures.call_denominators(clean_db, violation="Driveway")
    pops = result["populations"]

    assert pops["raw_label:Blocking Driveway (DISPATCH)"]["count"] == 2
    assert pops["raw_label:DRIVEWAY"]["count"] == 1
    assert pops["raw_selection_total"]["count"] == 3
    # The label-alone count plus the legacy code reconciles to the total (the
    # known 9,770 + 64 = 9,834 lead this task carries).
    assert (
        pops["raw_label:Blocking Driveway (DISPATCH)"]["count"]
        + pops["raw_label:DRIVEWAY"]["count"]
        == pops["raw_selection_total"]["count"]
    )


def test_call_denominators_tow_comparison_cohort_matches_recurrence_by_tow(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 9001, 1, "1 TOWED ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 9002, 2, "2 QUIET ST, HALIFAX", base, towed="N")
    seed_driveway_call(clean_db, 9003, 3, "3 MYSTERY ST, HALIFAX", base, set_towed=False)

    denominators = figures.call_denominators(clean_db, violation="Driveway")
    recurrence = figures.recurrence_by_tow(clean_db, violation="Driveway")

    pops = denominators["populations"]
    assert pops["tow_comparison_population"]["count"] == recurrence["population"]["count"]
    assert pops["tow_comparison_known_status"]["count"] == (
        recurrence["groups"]["towed"]["calls"] + recurrence["groups"]["not_towed"]["calls"]
    )
    assert pops["excluded_no_usable_address_or_date"]["count"] == (
        recurrence["population"]["excluded_no_usable_address_or_date"]
    )


def test_call_denominators_as_of_adds_the_earlier_snapshot_population(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 9101, 1, "1 EARLY ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 9102, 2, "2 LATE ST, HALIFAX",
                       base + datetime.timedelta(days=5), towed="N")

    cutoff = base + datetime.timedelta(days=1)
    result = figures.call_denominators(clean_db, violation="Driveway", as_of=cutoff)

    assert "raw_selection_as_of" in result["populations"]
    assert result["populations"]["raw_selection_as_of"]["count"] == 1
    assert result["as_of"] == "2024-07-01"


def test_call_denominators_omits_as_of_population_when_no_cutoff_given(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 9201, 1, "1 A ST, HALIFAX", base)

    result = figures.call_denominators(clean_db, violation="Driveway")

    assert "raw_selection_as_of" not in result["populations"]
    assert result["as_of"] is None


def test_every_population_carries_a_count_and_a_plain_english_definition(clean_db):
    """4.14: verify every figure names which population it is drawn from -- not
    just the denominators report itself, but recurrence_by_tow and response_time,
    whose `population` dicts this report reconciles against.
    """
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 9301, 1, "1 A ST, HALIFAX", base,
                       date_closed=base + datetime.timedelta(minutes=10))

    recurrence = figures.recurrence_by_tow(clean_db, violation="Driveway")
    response = figures.response_time(clean_db, violation="Driveway")
    denominators = figures.call_denominators(clean_db, violation="Driveway")

    for population in (recurrence["population"], response["population"]):
        assert population["count"] >= 0
        assert isinstance(population["definition"], str) and population["definition"]

    assert denominators["populations"], "denominators report has no populations"
    for name, pop in denominators["populations"].items():
        assert isinstance(pop["count"], int)
        assert isinstance(pop["definition"], str) and pop["definition"], name


def test_readable_denominators_lists_every_population_with_its_count(clean_db):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 9401, 1, "1 A ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 9402, 2, "2 B ST, HALIFAX", base, towed="N")

    denominators = figures.call_denominators(clean_db, violation="Driveway")
    readable = figures._readable_denominators(denominators)

    for name, pop in denominators["populations"].items():
        assert name in readable
        assert f"{pop['count']:,}" in readable


def test_main_json_and_readable_output_both_carry_call_denominators(
    clean_db, monkeypatch, capsys,
):
    base = datetime.datetime(2024, 7, 1, 12, tzinfo=HALIFAX_TZ)
    seed_driveway_call(clean_db, 9501, 1, "1 A ST, HALIFAX", base, towed="Y")
    seed_driveway_call(clean_db, 9502, 2, "2 B ST, HALIFAX", base, towed="N")

    monkeypatch.setattr("sys.argv", ["figures.py", "--violation", "Driveway"])
    monkeypatch.setattr(figures.db, "connect", lambda *a, **k: clean_db)

    figures.main()

    captured = capsys.readouterr()
    import json
    out = json.loads(captured.out)

    assert "call_denominators" in out
    assert out["call_denominators"]["populations"]["raw_selection_total"]["count"] == 2
    assert "Call denominators" in captured.err
