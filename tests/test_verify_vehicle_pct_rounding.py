"""Task 9.4 / concern 24: the vehicle-uniqueness percentage `/api/types/<slug>/figures`
serves must be the one the live brief prints.

The defect this pins: `per_type._vehicle_conclusion` rounded the ratio to 3 decimals and
then formatted it as a whole percent, so 2413/2528 = 95.45% became 0.955 and then "96%",
where `hotspots.py`'s brief prints `f"{100 * distinct / seen:.0f} per cent unique"` = 95
and `/doorways` `summary.unique_pct` (`round(100 * distinct / seen)`) says 95. Rounding
twice can tip a percentage across a whole-percent boundary in either direction; the fix
rounds once, from the unrounded ratio, written exactly as the live brief writes it.

The boundary cases are constructed and run, not argued from algebra:
  * 2413/2528, the real Driveway figures (95.45% -> 95; double rounding said 96);
  * 34/38 = 89.47% (double rounding said 90 and, at the 0.90 threshold, changes what a
    reader takes from the sentence);
  * 20/31 = 64.52% (65; double rounding tips it DOWN to 64, the other direction);
  * 1911/2000 = 95.55% and 43/45 = 95.56% (genuinely above .5: must stay 96, so the fix
    cannot be "always round down");
  * 191/200 = 95.5% exactly (a tie: follows the live brief's own `:.0f`, which rounds half
    to even, so 96);
plus a sweep of every (distinct, seen) with seen from the minimum sample to 300 against an
exact-fraction oracle (`round(Fraction(100 * d, s))`, half to even, no float involved), and
one end-to-end case through `compute_and_store` and `GET /api/types/<slug>/figures` on a
throwaway schema.
"""

import datetime
import re
from fractions import Fraction

import pytest

from app import server as app_server
from mirror import per_type
from mirror import type_figures as mirror_type_figures

SENTENCE = re.compile(r"\((\d+)% of (\d+) seen\)")


def sentence_pct(distinct, seen):
    key, fragment, sufficient = per_type._vehicle_conclusion(
        {"doorways": 1, "seen": seen, "distinct": distinct})
    m = SENTENCE.search(fragment)
    assert m, fragment
    assert int(m.group(2)) == seen
    return key, int(m.group(1))


def live_brief_pct(distinct, seen):
    """`src/hotspots.py`'s brief line, character for character."""
    return int(f"{100 * distinct / seen:.0f}")


# (distinct, seen, expected whole percent, what the double rounding used to say)
BOUNDARY = [
    (2413, 2528, 95, 96),   # the real Driveway figures, concern 24
    (34, 38, 89, 90),       # tips upward under double rounding
    (20, 31, 65, 64),       # tips downward under double rounding
    (1911, 2000, 96, 96),   # 95.55%: genuinely above .5, correct either way
    (43, 45, 96, 96),       # 95.56%
    (191, 200, 96, 96),     # 95.5% exactly: half to even, as the live brief formats it
    (2412, 2528, 95, 95),   # 95.41%, below the boundary either way
]


@pytest.mark.parametrize("distinct,seen,want,was", BOUNDARY)
def test_sentence_percent_is_rounded_once_and_equals_the_live_brief(distinct, seen, want, was):
    _, got = sentence_pct(distinct, seen)
    assert got == want
    assert got == live_brief_pct(distinct, seen)
    # what the served summary rounds to (`server._summary`'s `unique_pct`)
    assert got == round(100 * distinct / seen)
    # The old path, kept here so the case demonstrably discriminates: it said `was`.
    assert int(f"{round(distinct / seen, 3):.0%}".rstrip("%")) == was


def test_the_boundary_set_contains_cases_the_old_double_rounding_got_wrong():
    wrong = [(d, s) for d, s, want, was in BOUNDARY if want != was]
    assert (2413, 2528) in wrong
    assert len(wrong) >= 3


def test_classification_is_unchanged_by_the_percent_fix():
    assert sentence_pct(2413, 2528)[0] == "mostly_distinct"     # share 0.955 >= 0.90
    assert sentence_pct(34, 38)[0] == "mixed"                    # share 0.895
    assert sentence_pct(11, 31)[0] == "substantial_repeat"       # share 0.355 <= 0.70


def test_every_ratio_from_the_minimum_sample_to_300_matches_an_exact_oracle():
    for seen in range(per_type.MIN_VEHICLE_SAMPLE, 301):
        for distinct in range(seen + 1):
            want = round(Fraction(100 * distinct, seen))     # exact, half to even
            key, got = sentence_pct(distinct, seen)
            assert got == want, (distinct, seen, got, want)
            assert got == live_brief_pct(distinct, seen), (distinct, seen)


def test_the_cli_table_column_does_not_round_twice_either():
    """`per_type._table` shows the same percentage in its `veh_share` column."""
    fig = {
        "population": 9698,
        "tow": {"towed": {"calls": 445, "recurrence_pct": None},
                "not_towed": {"calls": 9249, "recurrence_pct": None},
                "conclusion_key": "no_difference"},
        "vehicles": {"doorways": 351, "seen": 2528, "distinct": 2413, "share": 0.955,
                     "conclusion_key": "mostly_distinct"},
        "overall_conclusion": "x",
    }
    line = per_type._table({"Blocking Driveway": fig}).splitlines()[1]
    assert " 95% " in line and " 96% " not in line
    fig["vehicles"] = {**fig["vehicles"], "seen": 0, "distinct": 0, "share": None}
    assert " n/a " in per_type._table({"Blocking Driveway": fig}).splitlines()[1]


# ---------------------------------------------------- end to end, throwaway schema

pytestmark_db = pytest.mark.db
BASE = datetime.datetime(2026, 5, 1, 12, tzinfo=datetime.UTC)
COLOURS = ["RED", "BLUE", "GREEN", "BLACK", "WHITE", "GREY", "SILVER", "YELLOW", "ORANGE",
           "BROWN", "PURPLE"]


@pytest.fixture
def client(clean_db):
    app = app_server.create_app()
    app.testing = True
    return app.test_client()


@pytestmark_db
def test_served_figures_sentence_says_35_not_36_for_11_distinct_of_31_seen(clean_db, client):
    """31 calls at one doorway carrying 11 distinct vehicles: 11/31 = 35.48%. Rounded twice
    that is 0.355 -> "36%"; rounded once it is 35, which is what the served `summary`
    (`unique_pct`) says beside it and what the live brief would print."""
    conn = clean_db
    n = 31
    for i in range(n):
        rid = 2000 + i
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO service_requests (object_id, request_id, date_initiated, address, "
                "community, district, latitude, longitude, initiated_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rid, rid, BASE + datetime.timedelta(days=i), "1 EDGE ST, HALIFAX", "HALIFAX",
                 "7", 44.65, -63.57, "INTERNAL"),
            )
            fields = [("Alleged Violation", "Blocking Driveway (DISPATCH)"),
                      ("Vehicle Was Towed", "N"), ("Vehicle Make", "FORD"),
                      ("Vehicle Model", "F150"), ("Vehicle Colour", COLOURS[i % 11])]
            for j, (name, value) in enumerate(fields):
                cur.execute(
                    "INSERT INTO custom_fields (object_id, request_id, custom_field_name, "
                    "custom_field_value) VALUES (%s,%s,%s,%s)", (rid * 10 + j, rid, name, value))
        conn.commit()

    outcomes = mirror_type_figures.compute_and_store(
        conn, types=[app_server.DEFAULT_CANONICAL_TYPE], log=lambda m: None)
    assert outcomes and outcomes[0]["ok"]

    fig = client.get("/api/types/blocking-driveway/figures").get_json()["figures"]
    assert (fig["vehicles"]["distinct"], fig["vehicles"]["seen"]) == (11, 31)
    assert "(35% of 31 seen)" in fig["vehicles"]["conclusion"]
    assert "(35% of 31 seen)" in fig["overall_conclusion"]
    assert "36%" not in fig["overall_conclusion"]

    summary = client.get("/api/types/blocking-driveway/doorways").get_json()["summary"]
    assert (summary["distinct"], summary["seen"], summary["unique_pct"]) == (11, 31, 35)
    assert live_brief_pct(11, 31) == 35
