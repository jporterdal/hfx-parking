"""DST-aware conversion of source timestamps to Halifax local time.

Task 4.6: the fixed -3 offset that used to live at `to_local` was ADT (daylight
time) applied year-round, so every record outside daylight saving was an hour
out. `HALIFAX` (zoneinfo) carries the real transition dates instead.
"""

import datetime

import hotspots


def epoch_ms(year, month, day, hour, minute=0):
    """UTC wall clock -> epoch milliseconds, the shape ArcGIS publishes."""
    dt = datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.UTC)
    return int(dt.timestamp() * 1000)


def test_same_local_clock_time_in_july_and_january_reads_the_same():
    # Noon UTC in July (ADT, UTC-3) and noon UTC in January (AST, UTC-4) land on
    # different local clock times under a fixed offset. They should not: a call
    # logged at 09:00 local in July and one logged at 08:00 local in January are
    # each simply "morning," and the conversion should say so consistently -
    # concretely, an instant chosen to land on the same Halifax clock time in
    # both seasons must produce that same clock time in both.
    july = hotspots.to_local(epoch_ms(2024, 7, 15, 12, 0))   # noon UTC = 09:00 ADT
    january = hotspots.to_local(epoch_ms(2024, 1, 15, 13, 0))  # 13:00 UTC = 09:00 AST
    assert july.strftime("%H:%M") == "09:00"
    assert january.strftime("%H:%M") == "09:00"


def test_july_is_three_hours_behind_utc():
    dt = hotspots.to_local(epoch_ms(2024, 7, 15, 12, 0))
    assert dt.utcoffset() == datetime.timedelta(hours=-3)
    assert dt.strftime("%H:%M") == "09:00"


def test_january_is_four_hours_behind_utc():
    dt = hotspots.to_local(epoch_ms(2024, 1, 15, 12, 0))
    assert dt.utcoffset() == datetime.timedelta(hours=-4)
    assert dt.strftime("%H:%M") == "08:00"


def test_the_old_fixed_offset_was_wrong_for_january():
    # A regression check against the bug this task closes: under the retired
    # fixed -3 (ADT) offset, a January timestamp would have read 09:00, not the
    # correct 08:00 AST.
    dt = hotspots.to_local(epoch_ms(2024, 1, 15, 12, 0))
    assert dt.strftime("%H:%M") != "09:00"


def test_none_and_empty_pass_through():
    assert hotspots.to_local(None) is None
    assert hotspots.to_local("") is None


def test_across_2020_to_2026_every_january_instant_is_ast():
    # design.md: "across a 2020-2026 range every record outside daylight saving
    # is currently an hour out." Spot-check a January instant each year.
    for year in range(2020, 2027):
        dt = hotspots.to_local(epoch_ms(year, 1, 15, 12, 0))
        assert dt.utcoffset() == datetime.timedelta(hours=-4), year


def test_dst_transition_boundary_is_respected():
    # 2024-03-10 07:00 UTC is just before the 2024 spring-forward (02:00 AST ->
    # 03:00 ADT at 06:00 UTC); 2024-03-10 08:00 UTC is just after.
    before = hotspots.to_local(epoch_ms(2024, 3, 10, 5, 59))
    after = hotspots.to_local(epoch_ms(2024, 3, 10, 6, 1))
    assert before.utcoffset() == datetime.timedelta(hours=-4)
    assert after.utcoffset() == datetime.timedelta(hours=-3)
