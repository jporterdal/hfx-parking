"""The frozen canonical violation-type list: task 4.15.

Pure data plus lookup logic, so nothing here touches the network or the database --
`conftest.py`'s `no_network` fixture applies (autouse) but is never exercised, which
is the point: this module has no business reaching the source or the mirror, only
recording what was observed there.
"""

from mirror import violation_types as vt


def test_driveway_groups_the_dispatch_label_and_the_legacy_code():
    """The one grouping already load-bearing elsewhere: src/hotspots.py's docstring
    names exactly these two labels for `--violation Driveway`."""
    assert vt.canonical_type("Blocking Driveway (DISPATCH)") == "Blocking Driveway"
    assert vt.canonical_type("DRIVEWAY") == "Blocking Driveway"
    assert set(vt.raw_labels_for("Blocking Driveway")) == {
        "Blocking Driveway (DISPATCH)",
        "DRIVEWAY",
    }


def test_every_canonical_type_groups_at_least_one_raw_label():
    for canonical, raw_labels in vt.CANONICAL_TYPES.items():
        assert raw_labels, f"{canonical!r} has no raw labels"


def test_no_raw_label_is_claimed_by_two_canonical_types():
    seen = {}
    for canonical, raw_labels in vt.CANONICAL_TYPES.items():
        for raw_label, _count in raw_labels:
            assert raw_label not in seen, (
                f"{raw_label!r} claimed by both {seen.get(raw_label)!r} and "
                f"{canonical!r}"
            )
            seen[raw_label] = canonical


def test_excluded_labels_are_not_in_any_canonical_group():
    excluded = {
        raw_label
        for raw_labels in vt.EXCLUDED_TYPES.values()
        for raw_label, _count in raw_labels
    }
    assert excluded == {"Other", "OTHER", "Left Running", "LEFTRUNNING"}
    assert not excluded & set(vt.RAW_LABEL_TO_CANONICAL)
    for label in excluded:
        assert vt.canonical_type(label) is None


def test_every_excluded_type_has_a_stated_reason():
    assert set(vt.EXCLUSION_REASONS) == set(vt.EXCLUDED_TYPES)
    for reason in vt.EXCLUSION_REASONS.values():
        assert reason  # non-empty


def test_ambiguous_labels_are_not_grouped_and_carry_a_note():
    for raw_label, entry in vt.AMBIGUOUS_LABELS.items():
        assert vt.canonical_type(raw_label) is None
        assert raw_label not in vt.RAW_LABEL_TO_CANONICAL
        assert entry["note"], f"{raw_label!r} has no note explaining the ambiguity"
        assert entry["count"] > 0


def test_ambiguous_labels_are_the_known_two():
    # A change here is a real finding (a new ambiguous label showed up, or one of
    # these two got resolved) and should be a deliberate edit to this test, not a
    # silent pass.
    assert set(vt.AMBIGUOUS_LABELS) == {"OVERTIME", "VIOLATION"}


def test_unknown_label_returns_none_rather_than_raising():
    assert vt.canonical_type("something HRM has never published") is None


def test_recorded_label_count_matches_the_frozen_observation():
    excluded = {
        raw_label
        for raw_labels in vt.EXCLUDED_TYPES.values()
        for raw_label, _count in raw_labels
    }
    all_known = set(vt.RAW_LABEL_TO_CANONICAL) | excluded | set(vt.AMBIGUOUS_LABELS)
    assert len(all_known) == vt.DISTINCT_RAW_LABELS == 71


def test_counts_reconcile_to_the_observed_total():
    total = sum(row.count for row in vt.iter_counts())
    total += sum(
        count for labels in vt.EXCLUDED_TYPES.values() for _label, count in labels
    )
    total += sum(entry["count"] for entry in vt.AMBIGUOUS_LABELS.values())
    assert total == vt.TOTAL_ALLEGED_VIOLATION_ROWS - vt.NULL_VALUE_ROWS


def test_coverage_is_the_great_majority_of_non_excluded_rows():
    summary = vt.coverage()
    assert summary["total_rows"] == 110_900
    assert summary["null_rows"] == 66
    assert summary["excluded_total"] == 7_567  # Other (7,474 + 68) + Left Running (23 + 2)
    assert summary["ambiguous_total"] == 5  # OVERTIME (1) + VIOLATION (4)
    assert summary["assigned_total"] == 103_262
    assert summary["non_excluded_labeled_total"] == 103_267
    # Every canonical type accounts for all but the 5 deliberately-unmapped calls.
    assert summary["share"] > 0.999


def test_iter_counts_yields_one_row_per_raw_label():
    rows = list(vt.iter_counts())
    total_raw_labels = sum(len(labels) for labels in vt.CANONICAL_TYPES.values())
    assert len(rows) == total_raw_labels
    for row in rows:
        assert row.canonical_type in vt.CANONICAL_TYPES
        assert row.count > 0


def test_raw_labels_for_unknown_type_raises():
    import pytest

    with pytest.raises(KeyError):
        vt.raw_labels_for("Not A Canonical Type")


def test_no_parking_sign_is_the_largest_type_and_flagged_for_r2():
    """design.md D18 names this type as the one where R2's string-reduction concern
    starts to strain (task 4.18); it should stay the largest so that task stays
    pointed at the right type."""
    counts = {
        canonical: sum(count for _label, count in labels)
        for canonical, labels in vt.CANONICAL_TYPES.items()
    }
    assert max(counts, key=counts.get) == "No Parking Sign"
    assert counts["No Parking Sign"] == 27_669
