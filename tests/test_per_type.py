"""Task 4.17: recurrence, the tow comparison and vehicle uniqueness computed
independently per canonical type, each stating its own conclusion rather than
inheriting driveway's -- and, since this task was reopened, the tow comparison is
judged against a cluster-aware 95% interval rather than a fixed point-estimate
threshold. `src/mirror/per_type.py` computes these from `derive.derive_all()`'s
one-pass result, so these tests build calls through the same mirror tables
`tests/test_derive.py`/`tests/test_figures.py` seed.

Every test here runs under the suite's autouse `no_network` fixture. The
bootstrap tests below reuse `figures.DEFAULT_BOOTSTRAP_SEED` (via
`per_type`'s own default, which is that same constant) so results are
deterministic without passing a seed explicitly.
"""

import datetime

import pytest

from mirror import figures, per_type, violation_types

pytestmark = pytest.mark.db

BASE = datetime.datetime(2024, 1, 1, 12, tzinfo=datetime.UTC)


def insert_service_request(conn, object_id, request_id, address, date_initiated,
                           date_closed=None, district="7", community="HALIFAX",
                           lat=44.65, lon=-63.57):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_requests (object_id, request_id, date_initiated, "
            "date_closed, address, community, district, latitude, longitude, "
            "initiated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (object_id, request_id, date_initiated, date_closed, address, community,
             district, lat, lon, "INTERNAL"),
        )
    conn.commit()


def insert_custom_field(conn, object_id, request_id, name, value):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO custom_fields (object_id, request_id, custom_field_id, "
            "custom_field_name, custom_field_value) VALUES (%s, %s, NULL, %s, %s)",
            (object_id, request_id, name, value),
        )
    conn.commit()


def seed_call(conn, request_id, object_id, address, when, label, towed="N",
             make="FORD", model="F150", colour="BLUE"):
    insert_service_request(conn, object_id, request_id, address, when)
    insert_custom_field(conn, object_id * 10, request_id, "Alleged Violation", label)
    insert_custom_field(conn, object_id * 10 + 1, request_id, "Vehicle Was Towed", towed)
    insert_custom_field(conn, object_id * 10 + 2, request_id, "Vehicle Make", make)
    insert_custom_field(conn, object_id * 10 + 3, request_id, "Vehicle Model", model)
    insert_custom_field(conn, object_id * 10 + 4, request_id, "Vehicle Colour", colour)


def seed_driveway_pattern(conn, canonical_label, object_id_start, n_towed=30,
                          n_not_towed=30, address_prefix="DRIVEWAY"):
    """Driveway's own shape: towed and not-towed recur at nearly the same rate
    (every doorway gets a second call 10 days later, regardless of tow status),
    and every call carries a distinct vehicle -- `n_towed + n_not_towed` doorways,
    each with its own unique make/model/colour triple, so nothing repeats.
    """
    oid = object_id_start
    for i in range(n_towed):
        addr = f"{i} {address_prefix} TOWED ST, HALIFAX"
        seed_call(conn, oid * 10 + 1, oid, addr, BASE, canonical_label, towed="Y",
                 make=f"MAKE{oid}A", model="M1", colour="RED")
        seed_call(conn, oid * 10 + 2, oid + 1, addr, BASE + datetime.timedelta(days=10),
                 canonical_label, towed="Y", make=f"MAKE{oid}B", model="M2", colour="BLUE")
        oid += 2
    for i in range(n_not_towed):
        addr = f"{i} {address_prefix} QUIET ST, HALIFAX"
        seed_call(conn, oid * 10 + 1, oid, addr, BASE, canonical_label, towed="N",
                 make=f"MAKEQ{oid}A", model="M1", colour="RED")
        seed_call(conn, oid * 10 + 2, oid + 1, addr, BASE + datetime.timedelta(days=10),
                 canonical_label, towed="N", make=f"MAKEQ{oid}B", model="M2", colour="BLUE")
        oid += 2
    return oid


def seed_opposite_pattern(conn, canonical_label, object_id_start, n_towed=30,
                          n_not_towed=30, address_prefix="OPPOSITE"):
    """The opposite of driveway's shape: towed calls recur far more than
    not-towed calls (a large, consistent gap across every doorway, not
    driveway's ~0-point gap), and the *same* one vehicle shows up at every
    doorway (repeat, not distinct) -- deliberately the pattern task 4.17
    requires a type must not paper over with driveway's conclusion.
    """
    oid = object_id_start
    for i in range(n_towed):
        addr = f"{i} {address_prefix} TOWED ST, HALIFAX"
        seed_call(conn, oid * 10 + 1, oid, addr, BASE, canonical_label, towed="Y",
                 make="REPEATMAKE", model="REPEATMODEL", colour="BLACK")
        seed_call(conn, oid * 10 + 2, oid + 1, addr, BASE + datetime.timedelta(days=10),
                 canonical_label, towed="Y", make="REPEATMAKE", model="REPEATMODEL",
                 colour="BLACK")
        oid += 2
    # not-towed doorways never get a second call -- zero recurrence, versus
    # towed doorways which all recur: a large, doorway-consistent gap in the
    # opposite direction from a "towed calls change nothing" finding.
    for i in range(n_not_towed):
        addr = f"{i} {address_prefix} QUIET ST, HALIFAX"
        seed_call(conn, oid * 10 + 1, oid, addr, BASE, canonical_label, towed="N",
                 make="REPEATMAKE", model="REPEATMODEL", colour="BLACK")
        oid += 1
    return oid


def _seed_doorway(conn, canonical_label, address, oid, day_offsets, towed):
    """One doorway's calls, each needing its own `object_id` (service_requests'
    primary key, and the multiplier `seed_call` uses for its custom-field rows)
    -- unlike the driveway/opposite patterns above, a doorway here can carry
    more than two calls, so the id has to advance once per call rather than
    once per doorway.
    """
    for offset in day_offsets:
        seed_call(conn, oid * 10 + 1, oid, address, BASE + datetime.timedelta(days=offset),
                 canonical_label, towed=towed)
        oid += 1
    return oid


def seed_clustered_gap_pattern(conn, canonical_label, object_id_start):
    """A gap driven almost entirely by one clustered doorway, exactly the
    "82 towed calls, possibly clustered at few addresses" concern defect 1 was
    reopened over. Six matched towed doorways and six matched not-towed
    doorways (5 calls each, spaced 400 days apart so none of them recur) cancel
    out perfectly; one extra towed-only doorway ("the outlier") has 5 calls
    spaced a day apart, so 4 of its 5 recur. That single doorway is enough to
    push the *point estimate* to towed=35 calls/4 recurring (11.4%) versus
    not_towed=30 calls/0 recurring (0%) -- an 11.4-point gap, comfortably past
    where a fixed-threshold rule would call it a finding, on totals that each
    clear `MIN_TOW_GROUP_SAMPLE`. But only one of thirteen doorways carries any
    signal at all: resampled doorways, that one address is excluded from a
    meaningful fraction of replicates and over/under-represented in the rest,
    which is exactly what a cluster-aware interval is for.
    """
    oid = object_id_start
    matched_offsets = [c * 400 for c in range(5)]
    for i in range(6):
        addr = f"{i} {canonical_label} MATCHED TOWED ST, HALIFAX"
        oid = _seed_doorway(conn, canonical_label, addr, oid, matched_offsets, "Y")
    for i in range(6):
        addr = f"{i} {canonical_label} MATCHED QUIET ST, HALIFAX"
        oid = _seed_doorway(conn, canonical_label, addr, oid, matched_offsets, "N")
    outlier_offsets = list(range(5))
    addr = f"{canonical_label} OUTLIER ST, HALIFAX"
    oid = _seed_doorway(conn, canonical_label, addr, oid, outlier_offsets, "Y")
    return oid


# --------------------------------------------------------- minimum-sample rule


def test_small_sample_reports_sample_too_small_for_both_pieces(clean_db):
    seed_call(clean_db, 1001, 1, "1 TINY ST, HALIFAX", BASE, "DRIVEWAY", towed="Y")
    seed_call(clean_db, 1002, 2, "1 TINY ST, HALIFAX",
             BASE + datetime.timedelta(days=5), "DRIVEWAY", towed="N")

    figs = per_type.compute_all(clean_db, types=["Blocking Driveway"], min_calls=1)
    fig = figs["Blocking Driveway"]

    assert fig["tow"]["conclusion_key"] == "sample_too_small"
    assert fig["tow"]["sample_sufficient"] is False
    assert fig["tow"]["effect_bound"] is None
    assert fig["vehicles"]["conclusion_key"] == "sample_too_small"
    assert fig["vehicles"]["sample_sufficient"] is False
    assert "too small" in fig["overall_conclusion"]


def test_zero_calls_reports_sample_too_small_rather_than_erroring(clean_db):
    figs = per_type.compute_all(clean_db, types=["Blocking Driveway"])
    fig = figs["Blocking Driveway"]

    assert fig["population"] == 0
    assert fig["tow"]["conclusion_key"] == "sample_too_small"
    assert fig["vehicles"]["conclusion_key"] == "sample_too_small"


# --------------------------------------------------- driveway-shaped type (own evidence)


def test_driveway_shaped_type_states_no_detectable_tow_difference(clean_db):
    seed_driveway_pattern(clean_db, "DRIVEWAY", object_id_start=1)

    figs = per_type.compute_all(clean_db, types=["Blocking Driveway"], min_calls=1)
    fig = figs["Blocking Driveway"]

    assert fig["tow"]["conclusion_key"] == "no_difference"
    assert fig["tow"]["effect_bound"]["comparable"] is True
    lo, hi = fig["tow"]["effect_bound"]["cluster_bootstrap"]["ci_95_pct_points"]
    assert lo <= 0 <= hi
    assert fig["vehicles"]["conclusion_key"] == "mostly_distinct"
    assert "Blocking Driveway" in fig["overall_conclusion"]


# ------------------------------------------------- opposite-shaped type (own evidence)


def test_opposite_shaped_type_reaches_its_own_opposite_conclusion(clean_db):
    seed_opposite_pattern(clean_db, "No Parking Sign", object_id_start=1)

    figs = per_type.compute_all(clean_db, types=["No Parking Sign"], min_calls=1)
    fig = figs["No Parking Sign"]

    assert fig["tow"]["conclusion_key"] == "tow_higher"
    lo, hi = fig["tow"]["effect_bound"]["cluster_bootstrap"]["ci_95_pct_points"]
    assert lo > 0  # interval excludes 0 below -- towed calls recur MORE often
    assert fig["vehicles"]["conclusion_key"] == "substantial_repeat"
    assert "No Parking Sign" in fig["overall_conclusion"]


def test_two_types_with_opposite_patterns_each_state_their_own_conclusion(clean_db):
    """The exact scenario task 4.17 requires be tested: two canonical types
    derived in the same `compute_all()` (one-pass) call, one shaped like
    driveway and one shaped the opposite way, must not contaminate each other's
    conclusion -- the driveway-shaped type must not be dragged down by the other
    type's poor figures, and the opposite-shaped type must not be handed a
    favourable conclusion just because they were computed together. Neither
    type's sentence may name the other, or driveway, or reuse the other's text.
    """
    next_oid = seed_driveway_pattern(clean_db, "DRIVEWAY", object_id_start=1)
    seed_opposite_pattern(clean_db, "No Parking Sign", object_id_start=next_oid)

    figs = per_type.compute_all(
        clean_db, types=["Blocking Driveway", "No Parking Sign"], min_calls=1
    )

    driveway = figs["Blocking Driveway"]
    signs = figs["No Parking Sign"]

    assert driveway["tow"]["conclusion_key"] == "no_difference"
    assert signs["tow"]["conclusion_key"] == "tow_higher"
    assert driveway["tow"]["conclusion_key"] != signs["tow"]["conclusion_key"]
    assert driveway["vehicles"]["conclusion_key"] != signs["vehicles"]["conclusion_key"]

    # Each type's sentence is self-contained: it names itself and never the
    # other tracked type, and the two sentences are not textual copies.
    assert "No Parking Sign" not in driveway["overall_conclusion"]
    assert "Blocking Driveway" not in signs["overall_conclusion"]
    assert driveway["overall_conclusion"] != signs["overall_conclusion"]

    # No doorway/call leakage between the two types' figures (4.16's invariant,
    # restated here at the figure level): the populations are disjoint sizes
    # matching what was seeded for each, not merged.
    assert driveway["population"] == 120  # 60 doorways * 2 calls each
    assert signs["population"] == 90  # 30 towed doorways * 2 + 30 not-towed * 1


def test_no_conclusion_names_another_tracked_type(clean_db):
    """Broader sweep of the same requirement across every pair this test module
    computes together: scan every type's `overall_conclusion` for every other
    requested type's name.
    """
    next_oid = seed_driveway_pattern(clean_db, "DRIVEWAY", object_id_start=1)
    seed_opposite_pattern(clean_db, "No Parking Sign", object_id_start=next_oid)

    types = ["Blocking Driveway", "No Parking Sign"]
    figs = per_type.compute_all(clean_db, types=types, min_calls=1)

    for canonical, fig in figs.items():
        for other in types:
            if other == canonical:
                continue
            assert other not in fig["overall_conclusion"], (
                f"{canonical}'s conclusion names {other!r}"
            )


# ----------------------------------------------- cluster-aware interval (defect 1)


def test_clustered_gap_is_not_over_claimed_as_a_finding(clean_db):
    """A gap that would clear the old fixed 3.0-point threshold (11.4 points, on
    totals of 35 and 30 calls -- both above `MIN_TOW_GROUP_SAMPLE`) is not
    reported as a finding when almost the entire gap is carried by one
    clustered doorway out of thirteen: the cluster-aware interval has to reach
    zero for this construction, precisely because resampling doorways
    sometimes excludes or over-represents the one doorway doing all the work.
    """
    seed_clustered_gap_pattern(clean_db, "No Parking Sign", object_id_start=1)

    figs = per_type.compute_all(clean_db, types=["No Parking Sign"], min_calls=1)
    fig = figs["No Parking Sign"]

    towed, not_towed = fig["tow"]["towed"], fig["tow"]["not_towed"]
    assert towed["calls"] == 35 and towed["recurring"] == 4
    assert not_towed["calls"] == 30 and not_towed["recurring"] == 0
    # 11.4 points -- comfortably past the old fixed 3.0-point threshold this
    # module used to judge a gap by, before defect 1 replaced it with the
    # interval below.
    assert towed["recurrence_pct"] - not_towed["recurrence_pct"] > 3.0

    eb = fig["tow"]["effect_bound"]
    naive_lo, _naive_hi = eb["naive"]["ci_95_pct_points"]
    assert naive_lo > 0  # the naive, per-call interval would call this a finding

    cluster_lo, cluster_hi = eb["cluster_bootstrap"]["ci_95_pct_points"]
    assert cluster_lo <= 0 <= cluster_hi  # the cluster-aware interval does not
    assert fig["tow"]["conclusion_key"] == "no_difference"
    assert "no detectable difference" in fig["tow"]["conclusion"]
    # Stated together with the reduction the interval rules out, not as a bare
    # absence of a claim.
    assert "rules out a reduction larger than" in fig["tow"]["conclusion"]


def test_small_sample_with_wide_interval_is_no_detectable_difference(clean_db):
    """Same construction, read from the small-sample angle the task names
    directly: a gap measured on a sample this thin, once its clustering is
    accounted for, is reported as no detectable difference rather than a
    finding.
    """
    seed_clustered_gap_pattern(clean_db, "No Parking Sign", object_id_start=1)

    figs = per_type.compute_all(clean_db, types=["No Parking Sign"], min_calls=1)
    fig = figs["No Parking Sign"]

    assert fig["tow"]["conclusion_key"] == "no_difference"
    assert fig["tow"]["sample_sufficient"] is True


def test_effect_bound_is_deterministic_for_a_fixed_seed(clean_db):
    seed_clustered_gap_pattern(clean_db, "No Parking Sign", object_id_start=1)

    first = per_type.compute_all(clean_db, types=["No Parking Sign"], min_calls=1)
    second = per_type.compute_all(clean_db, types=["No Parking Sign"], min_calls=1)

    assert (
        first["No Parking Sign"]["tow"]["effect_bound"]["cluster_bootstrap"]["ci_95_pct_points"]
        == second["No Parking Sign"]["tow"]["effect_bound"]["cluster_bootstrap"]["ci_95_pct_points"]
    )


def test_bootstrap_reuses_figures_effect_bound_not_a_reimplementation(clean_db, monkeypatch):
    """The tow comparison's interval must come from `figures._effect_bound`
    (task instruction: reuse, do not reimplement the bootstrap). Patch it to a
    sentinel and confirm `per_type` actually calls through to it rather than
    computing an interval some other way.
    """
    calls = []
    real = figures._effect_bound

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(per_type.figures, "_effect_bound", spy)
    seed_driveway_pattern(clean_db, "DRIVEWAY", object_id_start=1)

    per_type.compute_all(clean_db, types=["Blocking Driveway"], min_calls=1)

    assert calls == [1]


# ------------------------------------------------------- tow caveat (4.12, per type)


def test_caveat_present_on_every_tow_statement_including_insufficient_sample(clean_db):
    seed_call(clean_db, 1001, 1, "1 TINY ST, HALIFAX", BASE, "DRIVEWAY", towed="Y")
    figs_small = per_type.compute_all(clean_db, types=["Blocking Driveway"], min_calls=1)
    assert figs_small["Blocking Driveway"]["tow"]["caveat"] == per_type.TOW_CAVEAT


def test_caveat_present_alongside_a_substantive_tow_conclusion(clean_db):
    seed_driveway_pattern(clean_db, "DRIVEWAY", object_id_start=1)
    figs = per_type.compute_all(clean_db, types=["Blocking Driveway"], min_calls=1)
    fig = figs["Blocking Driveway"]

    assert fig["tow"]["caveat"] == per_type.TOW_CAVEAT
    assert "observational" in fig["overall_conclusion"]


# ------------------------------------------------------------- thresholds are explicit


def test_thresholds_are_positive_explicit_constants():
    assert per_type.MIN_TOW_GROUP_SAMPLE > 0
    assert per_type.MIN_VEHICLE_SAMPLE > 0
    assert 0 < per_type.SUBSTANTIAL_REPEAT_SHARE < per_type.MOSTLY_DISTINCT_SHARE <= 1


# ---------------------------------------------------- one derive_all pass (4.17/4.16)


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


def test_compute_all_query_count_is_independent_of_type_count(clean_db):
    """`per_type.compute_all()` adds no query of its own beyond `derive_all()`'s
    one pass -- computing figures for two types costs exactly what computing them
    for five types costs, matching `test_derive.py`'s own claim for `derive_all()`
    itself. This is what makes calling `compute_all()` for all 30 types different
    from calling `figures.recurrence_by_tow()` 30 times (see this module's
    docstring and the implementation findings for the measured gap). The
    bootstrap runs in Python over already-fetched rows, so it adds no query
    either, however many iterations it runs.
    """
    seed_call(clean_db, 3001, 1, "1 A ST, HALIFAX", BASE, "DRIVEWAY")
    seed_call(clean_db, 3002, 2, "2 B ST, HALIFAX", BASE, "No Parking Sign")

    two_types = ["Blocking Driveway", "No Parking Sign"]
    five_types = list(violation_types.CANONICAL_TYPES)[:5]
    if "Blocking Driveway" not in five_types:
        five_types[0] = "Blocking Driveway"

    count_two = _count_cursor_calls(
        clean_db, lambda: per_type.compute_all(clean_db, types=two_types, min_calls=1)
    )
    count_five = _count_cursor_calls(
        clean_db, lambda: per_type.compute_all(clean_db, types=five_types, min_calls=1)
    )

    assert count_two == count_five


def test_compute_all_never_calls_figures_recurrence_by_tow(clean_db, monkeypatch):
    """The one-pass claim, checked directly: `compute_all()` must not call
    `figures.recurrence_by_tow`, which would re-query the mirror per type.
    """
    called = []
    monkeypatch.setattr(
        per_type.figures, "recurrence_by_tow",
        lambda *a, **k: called.append(1) or {},
    )
    seed_call(clean_db, 4001, 1, "1 A ST, HALIFAX", BASE, "DRIVEWAY")

    per_type.compute_all(clean_db, types=["Blocking Driveway"], min_calls=1)

    assert called == []


# ----------------------------------------------------------------------- CLI


def test_main_table_output_lists_the_requested_type(clean_db, monkeypatch, capsys):
    seed_call(clean_db, 5001, 1, "1 A ST, HALIFAX", BASE, "DRIVEWAY")
    monkeypatch.setattr(
        "sys.argv", ["per_type.py", "--canonical-type", "Blocking Driveway",
                    "--min-calls", "1", "--bootstrap-iterations", "50"],
    )
    monkeypatch.setattr(per_type.db, "connect", lambda *a, **k: clean_db)

    exit_code = per_type.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Blocking Driveway" in out


def test_main_json_output_is_valid_json(clean_db, monkeypatch, capsys):
    seed_call(clean_db, 6001, 1, "1 A ST, HALIFAX", BASE, "DRIVEWAY")
    monkeypatch.setattr(
        "sys.argv", ["per_type.py", "--canonical-type", "Blocking Driveway",
                    "--min-calls", "1", "--json", "--bootstrap-iterations", "50"],
    )
    monkeypatch.setattr(per_type.db, "connect", lambda *a, **k: clean_db)

    exit_code = per_type.main()

    import json
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert "Blocking Driveway" in out


def test_main_accepts_bootstrap_iterations_flag(clean_db, monkeypatch, capsys):
    seed_driveway_pattern(clean_db, "DRIVEWAY", object_id_start=1)
    monkeypatch.setattr(
        "sys.argv", ["per_type.py", "--canonical-type", "Blocking Driveway",
                    "--min-calls", "1", "--json", "--bootstrap-iterations", "25"],
    )
    monkeypatch.setattr(per_type.db, "connect", lambda *a, **k: clean_db)

    exit_code = per_type.main()

    import json
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    eb = out["Blocking Driveway"]["tow"]["effect_bound"]
    assert eb["cluster_bootstrap"]["iterations"] == 25
