"""Coverage for `hotspots.py`'s pure functions (task 4.8): address reduction
(`clean_address`), street extraction (`street_of`), vehicle identity
(`vehicle_key`), and the recurrence-counting half of `build()`.

Point-in-polygon coverage, including interior holes, lives in
`tests/test_census_containment.py` (task 4.5) rather than being duplicated here --
the task notes call that out explicitly as shareable.

Nothing here touches the network or the database; the whole file runs under the
suite's autouse `no_network` fixture with no `pytest.mark.db`, and is written to
also pass under `unshare -rn` with outbound networking removed entirely.
"""

import datetime

import hotspots


# ------------------------------------------------------------- clean_address


def test_clean_address_strips_city_and_postal_code_after_first_comma():
    assert hotspots.clean_address("123 Main St, Halifax, NS B3H 1A1") == "123 MAIN ST"


def test_clean_address_uppercases():
    assert hotspots.clean_address("55 spring garden rd") == "55 SPRING GARDEN RD"


def test_clean_address_collapses_internal_whitespace():
    assert hotspots.clean_address("55   Spring    Garden  Rd") == "55 SPRING GARDEN RD"


def test_clean_address_with_no_comma_keeps_the_whole_string():
    assert hotspots.clean_address("5285 Sackville St") == "5285 SACKVILLE ST"


def test_clean_address_none_input_returns_none():
    assert hotspots.clean_address(None) is None


def test_clean_address_empty_string_returns_none():
    assert hotspots.clean_address("") is None


def test_clean_address_blank_before_comma_returns_none():
    # Everything meaningful was after the first comma; the head is empty.
    assert hotspots.clean_address(", Halifax, NS") is None


def test_clean_address_leading_and_trailing_whitespace_stripped():
    assert hotspots.clean_address("   12 Oak St   , Halifax") == "12 OAK ST"


# ---------------------------------------------------------------- street_of


def test_street_of_strips_a_leading_street_number():
    assert hotspots.street_of("5285 SACKVILLE ST") == "SACKVILLE ST"


def test_street_of_strips_a_leading_number_with_a_letter_suffix():
    assert hotspots.street_of("123A MAIN ST") == "MAIN ST"


def test_street_of_takes_the_first_street_of_an_intersection():
    assert hotspots.street_of("TULIP ST & MAPLE ST") == "TULIP ST"


def test_street_of_strips_number_then_takes_the_first_street_of_an_intersection():
    assert hotspots.street_of("123 TULIP ST & MAPLE ST") == "TULIP ST"


def test_street_of_with_no_leading_number_is_unchanged():
    assert hotspots.street_of("MAIN ST") == "MAIN ST"


def test_street_of_empty_after_stripping_returns_none():
    # The leading-number pattern requires trailing whitespace to match at all
    # ("123" alone, with nothing after it, does not match and comes back
    # unchanged) -- so what is left empty is a number followed by nothing but
    # whitespace.
    assert hotspots.street_of("123 ") is None


# -------------------------------------------------------------- vehicle_key


def test_vehicle_key_all_three_fields_present():
    key = hotspots.vehicle_key(
        {"Vehicle Make": "Toyota", "Vehicle Model": "Corolla", "Vehicle Colour": "Blue"}
    )
    assert key == ("TOYOTA", "COROLLA", "BLUE")


def test_vehicle_key_is_case_and_whitespace_insensitive():
    a = hotspots.vehicle_key(
        {"Vehicle Make": "toyota", "Vehicle Model": " corolla ", "Vehicle Colour": "BLUE"}
    )
    b = hotspots.vehicle_key(
        {"Vehicle Make": "TOYOTA", "Vehicle Model": "Corolla", "Vehicle Colour": "blue"}
    )
    assert a == b == ("TOYOTA", "COROLLA", "BLUE")


def test_vehicle_key_missing_fields_default_to_empty():
    key = hotspots.vehicle_key({"Vehicle Make": "Honda"})
    assert key == ("HONDA", "", "")


def test_vehicle_key_nothing_recorded_returns_none():
    assert hotspots.vehicle_key({}) is None
    assert hotspots.vehicle_key({"Vehicle Make": "", "Vehicle Colour": "  "}) is None


def test_vehicle_key_ignores_unrelated_fields():
    key = hotspots.vehicle_key(
        {"Vehicle Was Towed": "Y", "Property Ownership": "PRIVATE", "Vehicle Make": "Ford"}
    )
    assert key == ("FORD", "", "")


# --------------------------------------------------- build(): recurrence counting


def epoch_ms(year, month, day, hour=12):
    """A UTC instant, in the epoch-millisecond shape ArcGIS (and the mirror) use.
    Noon UTC keeps `to_local` well clear of any date-boundary shift under either
    Halifax offset, since only the day-to-day gaps below matter for this file.
    """
    dt = datetime.datetime(year, month, day, hour, tzinfo=datetime.UTC)
    return int(dt.timestamp() * 1000)


def make_call(request_id, address, year, month, day):
    """The minimal `hotspots.build()` input: no LATITUDE/LONGITUDE, so `build()`
    skips census placement entirely (`if (lat and lon)` is false) and every row's
    `block` comes back `None` -- exactly what an isolated recurrence-counting test
    wants, since it keeps this file's coverage independent of any block fixture.
    """
    return {
        "REQUEST_ID": request_id,
        "DATE_INITIATED": epoch_ms(year, month, day),
        "DATE_CLOSED": None,
        "ADDRESS": address,
        "COMMUNITY": "HALIFAX",
        "DISTRICT": "7",
        "RESOLUTION": None,
        "LATITUDE": None,
        "LONGITUDE": None,
        "INITIATED_BY": "311 Online",
    }


def build_one_address(calls, recur_days=365, min_calls=0):
    """Runs `hotspots.build()` over a single synthetic address's calls, with no
    census blocks and `latest` pinned to the last call, and returns that address's
    one row. `min_calls=0` so a row is never dropped by the recency-count floor,
    which this file is not testing.
    """
    latest = max(hotspots.to_local(c["DATE_INITIATED"]) for c in calls)
    fields = {c["REQUEST_ID"]: {} for c in calls}
    rows = hotspots.build(calls, fields, min_calls, recur_days, latest, blocks=[])
    assert len(rows) == 1, "all synthetic calls share one address; expected one row"
    return rows[0]


def test_recurrence_counts_a_pair_within_the_window():
    # Two calls, 10 days apart, well inside a 365-day window: the earlier one
    # counts as a repeat (it has a follower inside the window), the later one does
    # not (nothing follows it).
    calls = [
        make_call("1", "1 MAIN ST", 2024, 1, 1),
        make_call("2", "1 MAIN ST", 2024, 1, 11),
    ]
    row = build_one_address(calls, recur_days=365)
    assert row["repeat_calls"] == 1


def test_recurrence_does_not_count_a_pair_outside_the_window():
    calls = [
        make_call("1", "1 MAIN ST", 2023, 1, 1),
        make_call("2", "1 MAIN ST", 2024, 6, 1),  # ~518 days later
    ]
    row = build_one_address(calls, recur_days=365)
    assert row["repeat_calls"] == 0


def test_recurrence_boundary_inclusive_at_exactly_recur_days():
    calls = [
        make_call("1", "1 MAIN ST", 2024, 1, 1),
        make_call("2", "1 MAIN ST", 2025, 1, 1),  # exactly 366 days (2024 is a leap year)
    ]
    row = build_one_address(calls, recur_days=366)
    assert row["repeat_calls"] == 1  # <=, so equal to the window counts


def test_recurrence_boundary_exclusive_one_day_past_recur_days():
    calls = [
        make_call("1", "1 MAIN ST", 2024, 1, 1),
        make_call("2", "1 MAIN ST", 2025, 1, 1),  # 366 days later
    ]
    row = build_one_address(calls, recur_days=365)  # one day short of the gap
    assert row["repeat_calls"] == 0


def test_recurrence_does_not_double_count_a_chain_beyond_the_window():
    # day 0, day 10, day 400 with a 365-day window: (0, 10) is inside the window
    # and (10, 400) is not, so only the first call counts as a repeat -- a call
    # is counted once per follower it has within the window, and the far call
    # having no follower at all means it never counts.
    calls = [
        make_call("1", "1 MAIN ST", 2024, 1, 1),
        make_call("2", "1 MAIN ST", 2024, 1, 11),
        make_call("3", "1 MAIN ST", 2025, 2, 4),  # 400 days after the first
    ]
    row = build_one_address(calls, recur_days=365)
    assert row["repeat_calls"] == 1
    assert row["calls_total"] == 3


def test_recurrence_counts_each_call_in_a_close_chain():
    # Three calls each 100 days apart, all consecutive pairs within a 365-day
    # window: the first two each have a follower inside the window, the last does
    # not.
    calls = [
        make_call("1", "1 MAIN ST", 2024, 1, 1),
        make_call("2", "1 MAIN ST", 2024, 4, 10),   # +100 days
        make_call("3", "1 MAIN ST", 2024, 7, 19),   # +100 days
    ]
    row = build_one_address(calls, recur_days=365)
    assert row["repeat_calls"] == 2


def test_recurrence_single_call_never_repeats():
    calls = [make_call("1", "1 MAIN ST", 2024, 1, 1)]
    row = build_one_address(calls, recur_days=365)
    assert row["repeat_calls"] == 0
    assert row["calls_total"] == 1
