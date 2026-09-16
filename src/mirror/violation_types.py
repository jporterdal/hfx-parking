"""The frozen canonical violation-type list.

Task 4.15 (`openspec/changes/mirror-hrm-data-and-host-app/tasks.md`), carrying
forward design.md D18/D19 from the archived `add-parking-hotspot-map` change. `Alleged
Violation` is a free-text field in the custom-fields layer, not an enum, and HRM files
the same problem under more than one label: a current mixed-case label, an old
uppercase short code from before the field was relabelled, and — for roughly half the
types — a `(DISPATCH)` variant of the current label. `src/hotspots.py`'s substring
match already handles this for one type (`Driveway`); this module freezes the same
grouping for every type so the mirror-backed derivation (4.16) does not have to
rediscover it.

**How this was built.** A live `outStatistics` query grouped by `CUSTOM_FIELD_VALUE`
against `services2.arcgis.com/11XBiaBYA9Ep0yNJ/.../Cityworks_Service_Requests_Custom_Fields`
on 2026-09-16, cross-checked against `mirror.parking_call_attributes` (loaded
2026-09-15 22:06:53 UTC, per `mirror.layer_state`). The two agree exactly once two
query-mechanics artifacts are accounted for: ArcGIS's own `GROUP BY` folds
`Other`/`OTHER` together case-insensitively (the same trap design.md D2 already
documents for grouping) and drops rows with a NULL value from the grouped result
entirely, while the mirror stores every row exactly as published. Once the 66 NULL
rows and the case fold are put back, both sources report **110,900** calls carrying an
`Alleged Violation` entry and **71** distinct non-null raw labels — matching the
`add-parking-hotspot-map` design.md table's 2026-09-15 observation exactly. There is
no drift between the loaded mirror and the live source as of this observation; the
counts below are read from the mirror, which is the one of the two that preserves
case-sensitive spelling.

**Coverage.** Of the 71 raw labels, 2 (`Other`/`OTHER`, `Left Running`/`LEFTRUNNING`)
are excluded per 4.15 and 2 (`OVERTIME`, `VIOLATION`) are ambiguous and deliberately
left unmapped — see `AMBIGUOUS_LABELS`. The remaining 67 labels group into 30
canonical types covering 103,262 of the 103,267 calls that carry a real,
non-excluded label (99.995%; 99.93% if the 66 NULL-value rows, which carry no label
at all and can never be classified, are counted in the denominator). The 5 calls the
frozen list does not reach are exactly the ambiguous labels below.
"""

import collections

OBSERVED_AT = "2026-09-16"
MIRROR_LOADED_AT = "2026-09-15T22:06:53Z"  # mirror.layer_state, custom_fields
SOURCE = (
    "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/"
    "Cityworks_Service_Requests_Custom_Fields/FeatureServer/0"
)

# Every row carrying an `Alleged Violation` custom field entry, live and mirror agree.
TOTAL_ALLEGED_VIOLATION_ROWS = 110_900
# Rows where the field is present but CUSTOM_FIELD_VALUE is NULL: no label at all, so
# there is nothing to group and nothing for `canonical_type()` to return. Not one of
# the 71 raw labels below, and not a raw label in the ordinary sense.
NULL_VALUE_ROWS = 66
# Raw, non-null, case-sensitive distinct label spellings. Matches design.md's
# 2026-09-15 observation exactly.
DISTINCT_RAW_LABELS = 71

# Excluded from the tracked set outright, per design.md D18 and tasks.md 4.15 — not
# grouped into any canonical type, not counted toward coverage. The mirror stores rows
# exactly as published, so both case spellings HRM has used appear here; ArcGIS's own
# query layer folds them together (D2's "related trap"), which is why a live grouped
# query alone would undercount this pair as one label rather than two.
EXCLUDED_TYPES = {
    "Other": (("Other", 7_474), ("OTHER", 68)),
    "Left Running": (("Left Running", 23), ("LEFTRUNNING", 2)),
}
EXCLUSION_REASONS = {
    "Other": "An ambiguous catch-all with no defined meaning.",
    "Left Running": (
        "Filed through the same Alleged Violation field but regulates idling, not "
        "where a vehicle is stopped -- a different bylaw question than every other "
        "label here."
    ),
}

# Canonical type -> the raw labels folded into it, in the order HRM's own labelling
# suggests (current DISPATCH-tagged label, then the current label, then the legacy
# short code), with the count each raw label carried in the mirror at MIRROR_LOADED_AT.
# Ordered by total volume, descending.
CANONICAL_TYPES = {
    "No Parking Sign": (
        ("No Parking Sign", 27_404),
        ("NOPARKING", 265),
    ),
    "Private Property": (
        ("Private Property", 17_061),
        ("PRIVATE", 72),
        ("On Private Property", 62),
    ),
    "On Highway Over 24 Hours": (
        ("On Highway Over 24 Hours", 12_244),
        ("ONHIGHWAY", 44),
    ),
    "Blocking Driveway": (
        ("Blocking Driveway (DISPATCH)", 9_770),
        ("DRIVEWAY", 64),
    ),
    "Over Time Specified": (
        ("Over Time Specified", 4_902),
        ("OVERTIMESPEC", 44),
    ),
    "Within 5M of Hydrant": (
        ("Within 5M of Hydrant (DISPATCH)", 2_383),
        ("Within 5M of Hydrant", 2_030),
        ("HYDRANT", 32),
    ),
    "Obstructing Snow Removal": (
        ("Obstructing Snow Removal", 2_579),
        ("Obstructing Snow Removal (DISPATCH)", 1_236),
        ("SNOW", 91),
    ),
    "Within 7.5M of Corner": (
        ("Within 7.5M of Corner (DISPATCH)", 2_417),
        ("Within 7.5M of Corner", 1_403),
        ("CORNER", 31),
    ),
    "No Stopping Sign": (
        ("No Stopping Sign", 2_780),
        ("NOSTOPPING", 30),
    ),
    "Accessible Spot on Public Property": (
        ("Accessible Spot on Public Property (DISPATCH)", 1_403),
        ("Accessible Spot on Public Property", 1_385),
        ("ACCESSPUB", 20),
    ),
    "Within 10M of Stop Sign": (
        ("Within 10M of Stop Sign (DISPATCH)", 1_819),
        ("Within 10M of Stop Sign", 881),
        ("STOPSIGN", 19),
    ),
    "On Sidewalk": (
        ("On Sidewalk (DISPATCH)", 1_080),
        ("On Sidewalk", 874),
        ("SIDEWALK", 21),
    ),
    "Too Far From Curb": (
        ("Too Far From Curb", 1_626),
        ("CURB", 20),
    ),
    "Under 3M For Traffic": (
        ("Under 3M For Traffic", 1_218),
        ("UNDER3M", 2),
    ),
    "On Wrong Side": (
        ("On Wrong Side", 1_196),
        ("WRONGSIDE", 10),
    ),
    "On or Within 5M of Crosswalk": (
        ("On or Within 5M of Crosswalk", 527),
        ("On or Within 5M of Crosswalk (DISPATCH)", 499),
        ("CROSSWALK", 42),
    ),
    "In Loading Zone": (
        ("In Loading Zone", 958),
        ("LOADING", 8),
    ),
    "In Fire Lane on Private Property": (
        ("In Fire Lane on Private Property", 572),
        ("FIREPRIVATE", 14),
    ),
    "In Bus Stop": (
        ("In Bus Stop", 426),
        ("BUSSTOP", 6),
    ),
    "Accessible Spot on Private Property": (
        ("Accessible Spot on Private Property", 383),
        ("ACCESSPRIV", 12),
    ),
    "Violating Winter Parking Ban": (
        ("Violating Winter Parking Ban", 260),
        ("WINTERBAN", 28),
    ),
    "In Fire Lane on Public Property": (
        ("In Fire Lane on Public Property", 117),
        ("In Fire Lane on Public Property (DISPATCH)", 107),
        ("FIREPUBLIC", 3),
    ),
    "Double Parked": (
        ("Double Parked", 215),
        ("DOUBLE", 7),
    ),
    "On Left Hand Side": (
        ("On Left Hand Side", 170),
        ("LEFTHAND", 9),
    ),
    "Obstructing Street Cleaning": (
        ("Obstructing Street Cleaning", 125),
        ("STREETCLEANING", 4),
    ),
    # No legacy short code observed for either of these two -- see the module
    # docstring's note on `OVERTIME`, which might belong to one of them.
    "Over Time at Meter": (
        ("Over Time at Meter", 91),
    ),
    "Parking in Bike Lane": (
        ("Parking in Bike Lane", 88),
        ("BIKELANE", 3),
    ),
    "Meter Violation": (
        ("Meter Violation", 52),
    ),
    "For Sale on Highway": (
        ("For Sale on Highway", 13),
    ),
    "Meter Feeding": (
        ("Meter Feeding", 5),
    ),
}

# Raw labels with a real value that are neither excluded nor grouped above, because
# grouping them is a judgment call rather than an obvious abbreviation match. Every
# other legacy short code observed reduces unambiguously to exactly one current label
# (`OVERTIMESPEC` -> `Over Time Specified`, `HYDRANT` -> `Within 5M of Hydrant`, and so
# on) -- these two don't, so they are recorded here for human confirmation instead of
# guessed into a group. `canonical_type()` returns `None` for both.
AMBIGUOUS_LABELS = {
    "OVERTIME": {
        "count": 1,
        "note": (
            "'Over Time Specified' already has its own short code, OVERTIMESPEC "
            "(44 calls). OVERTIME could be a pre-split catch-all, or could belong to "
            "'Over Time at Meter' (91 calls, no short code of its own). Needs human "
            "confirmation before it is merged into either group."
        ),
    },
    "VIOLATION": {
        "count": 4,
        "note": (
            "Too generic to reduce unambiguously to one current label -- no current "
            "label's name plausibly abbreviates to just 'VIOLATION' the way the other "
            "legacy codes abbreviate theirs. Needs human confirmation."
        ),
    },
}


def _build_lookup():
    """raw label -> canonical type name, built once and checked for collisions."""
    lookup = {}
    for canonical, raw_labels in CANONICAL_TYPES.items():
        for raw_label, _count in raw_labels:
            if raw_label in lookup:
                raise AssertionError(
                    f"{raw_label!r} is listed under both {lookup[raw_label]!r} and "
                    f"{canonical!r} -- a raw label may belong to only one canonical "
                    "type"
                )
            lookup[raw_label] = canonical
    return lookup


RAW_LABEL_TO_CANONICAL = _build_lookup()

# The full inventory: every raw label this module knows about, whichever bucket it
# fell into. Used only to check DISTINCT_RAW_LABELS below; not exported for lookup.
_ALL_KNOWN_LABELS = (
    set(RAW_LABEL_TO_CANONICAL)
    | {label for labels in EXCLUDED_TYPES.values() for label, _count in labels}
    | set(AMBIGUOUS_LABELS)
)

if len(_ALL_KNOWN_LABELS) != DISTINCT_RAW_LABELS:
    raise AssertionError(
        f"{len(_ALL_KNOWN_LABELS)} raw labels are recorded (canonical + excluded + "
        f"ambiguous), but DISTINCT_RAW_LABELS says {DISTINCT_RAW_LABELS} were "
        "observed -- the frozen list and the observation have drifted apart"
    )

_recorded_total = (
    sum(count for labels in CANONICAL_TYPES.values() for _label, count in labels)
    + sum(count for labels in EXCLUDED_TYPES.values() for _label, count in labels)
    + sum(entry["count"] for entry in AMBIGUOUS_LABELS.values())
)
if _recorded_total != TOTAL_ALLEGED_VIOLATION_ROWS - NULL_VALUE_ROWS:
    raise AssertionError(
        f"recorded raw-label counts sum to {_recorded_total}, but "
        f"TOTAL_ALLEGED_VIOLATION_ROWS - NULL_VALUE_ROWS = "
        f"{TOTAL_ALLEGED_VIOLATION_ROWS - NULL_VALUE_ROWS} -- a count was mistyped"
    )


def canonical_type(raw_label):
    """The canonical type a raw `Alleged Violation` label belongs to, or `None`.

    `None` covers three cases the caller cannot tell apart from this function alone:
    the label is `Other` or `Left Running` (excluded, see `EXCLUDED_TYPES`), the label
    is ambiguous and awaiting confirmation (see `AMBIGUOUS_LABELS`), or the label is
    not one this module has seen -- which, against a mirror reloaded after
    MIRROR_LOADED_AT, means the frozen list may need re-freezing rather than that the
    call should be silently dropped.
    """
    return RAW_LABEL_TO_CANONICAL.get(raw_label)


def raw_labels_for(canonical_name):
    """The raw labels (without counts) that make up one canonical type."""
    return tuple(label for label, _count in CANONICAL_TYPES[canonical_name])


def coverage():
    """Summary counts, computed from the frozen data rather than hand-totalled.

    Returns a dict: total rows, null rows, excluded total, ambiguous total, assigned
    (canonical) total, the non-excluded-non-null denominator, and the coverage share.
    """
    excluded_total = sum(
        count for labels in EXCLUDED_TYPES.values() for _label, count in labels
    )
    ambiguous_total = sum(entry["count"] for entry in AMBIGUOUS_LABELS.values())
    assigned_total = sum(
        count for labels in CANONICAL_TYPES.values() for _label, count in labels
    )
    denominator = TOTAL_ALLEGED_VIOLATION_ROWS - NULL_VALUE_ROWS - excluded_total
    return {
        "total_rows": TOTAL_ALLEGED_VIOLATION_ROWS,
        "null_rows": NULL_VALUE_ROWS,
        "excluded_total": excluded_total,
        "ambiguous_total": ambiguous_total,
        "assigned_total": assigned_total,
        "non_excluded_labeled_total": denominator,
        "share": assigned_total / denominator,
    }


CanonicalCounts = collections.namedtuple(
    "CanonicalCounts", ["canonical_type", "raw_label", "count"]
)


def iter_counts():
    """Flatten CANONICAL_TYPES into (canonical_type, raw_label, count) rows."""
    for canonical, raw_labels in CANONICAL_TYPES.items():
        for raw_label, count in raw_labels:
            yield CanonicalCounts(canonical, raw_label, count)
