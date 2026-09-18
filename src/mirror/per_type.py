"""Recurrence, tow comparison and vehicle uniqueness, computed independently for
every canonical violation type -- and a conclusion for each type generated from
its own figures, never borrowed from another type's.

Task 4.17 of `mirror-hrm-data-and-host-app`, carrying forward design.md D19 from
the archived `add-parking-hotspot-map` change: "A type whose tow-versus-no-tow
recurrence gap is large, or whose vehicles repeat more than driveway's 96 per cent
unique, does not support the same 'point the conclusion at physical remedies'
framing (D9) -- the output must say so for that type rather than inherit
driveway's language." Section 9.8 restates the same requirement as a verification.

**Reopened for two defects; both are fixed here.**

1. *No uncertainty on the tow comparison.* The first pass judged the tow
   difference against a fixed `NO_DIFFERENCE_PP_THRESHOLD` on the point estimate
   alone -- no interval, so a gap measured on a couple dozen doorways read the same
   as one measured on thousands. That constant is gone. The tow classification now
   comes from the same cluster-aware (doorway-resampling bootstrap, fixed seed) 95
   per cent interval task 4.11 built for `recurrence_by_tow`, reused here via
   `figures._effect_bound` -- a private helper, called deliberately rather than
   reimplemented, because the bootstrap and its seeding are exactly the logic 4.11
   already got right and a second copy would only be a second place for the two to
   drift apart. `_effect_bound` in turn calls `figures._naive_two_proportion_ci`
   and `figures._cluster_bootstrap_ci`, also private; nothing in `figures.py` is
   edited to make this possible. The interval, not the point estimate, decides the
   verdict: entirely below zero rules "tows associated with lower recurrence",
   entirely above zero rules "higher", and an interval that reaches zero rules "no
   detectable difference" -- stated together with the reduction the interval rules
   out, never as a bare absence of a claim. `MIN_TOW_GROUP_SAMPLE` stays as an
   explicit floor ahead of the interval (see its docstring for why): the bootstrap
   still runs on a handful of calls, it just runs on a sample this module has
   already decided is too thin to trust either direction it might report, so that
   case is short-circuited before the bootstrap is even asked to run.

2. *Conclusions phrased relative to driveway.* The first pass rendered every
   type's verdict as "supports" or "does not support driveway's physical-remedy
   framing" -- readable only by comparison to a type this document is not about.
   `overall_conclusion` is now a sentence generated from that type's own figures
   and phrased about that type by name; no sentence anywhere in this module's
   output names another canonical type or reuses another type's conclusion text
   (`tests/test_per_type.py` checks this directly, across every pair the test
   suite computes together). The structured pieces a sentence is built from --
   `tow.conclusion_key`, `tow.effect_bound`, `tow.sample_sufficient`,
   `vehicles.conclusion_key`, `vehicles.sample_sufficient` -- travel alongside the
   sentence, unchanged in shape, so a future view can render the fields without
   re-parsing prose.

**Why this is not thirty calls to `figures.recurrence_by_tow`.** That function is
correct and reused here (`_grouped_calls`, `_recurs`, `_effect_bound` and the
private helpers underneath it), but it also calls `derive.load()` itself, which
issues its own selection and join queries against the mirror. Calling it once per
canonical type therefore re-queries the mirror once per type -- thirty selection
queries, thirty join pairs -- when `derive.derive_all()` already reads
`custom_fields`, `service_requests` and `parking_call_attributes` exactly once
each, total, and partitions the single result by type in Python (4.16). This
module computes every type's tow comparison and vehicle-uniqueness share from
`derive_all()`'s one-pass result, calling `figures.recurrence_by_tow` zero times.
The bootstrap itself runs once per type (it is not part of `derive_all()`'s
single pass, because it is a per-type statistic, not a per-call one); at the
default `figures.DEFAULT_BOOTSTRAP_ITERATIONS` this is measurably the slow part of
an all-types run for the highest-volume types -- see this module's docstring in
the change's report for the observed per-type and all-types timings, because the
cost scales with doorway count and iteration count rather than being a constant.
`bootstrap_iterations` is exposed on `compute_all`/`compute_type`/the CLI for
exactly that reason: turning it down trades precision for time without touching
`figures.py`'s own default for the driveway figure `README.md` quotes.

**Two different populations, matching driveway's own published figures.** The tow
comparison and recurrence figure are computed over every call with a usable
address and initiation date (`figures._grouped_calls`'s restriction) -- the same
population `README.md`'s 44.7/44.6 comparison describes, independent of whether a
doorway ever made the watchlist. Vehicle uniqueness is computed over the type's
*watchlist* doorways (`derive_all()`'s `rows`, after the recency and min-calls
filters) -- the same population `README.md`'s "across the 363 doorways" sentence
describes. Reporting both from any other population would not reproduce driveway's
own figures, which is the baseline every other type's methodology (not
conclusion) is drawn from.

**Minimum-sample thresholds are explicit constants, not judgment calls made per
type.** See `MIN_TOW_GROUP_SAMPLE`, `MIN_VEHICLE_SAMPLE`, `MOSTLY_DISTINCT_SHARE`
and `SUBSTANTIAL_REPEAT_SHARE` below, each with the reason it is set where it is.

No network call: every read is `derive.derive_all()`'s, which is itself
network-free (see `derive.py`'s module docstring); this module issues no query of
its own. The bootstrap resamples the already-loaded per-doorway tallies in memory.

Run:  python3 -m mirror.per_type                     # every canonical type
      python3 -m mirror.per_type --canonical-type "No Parking Sign"
      python3 -m mirror.per_type --bootstrap-iterations 200   # faster, coarser
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror import db, derive, figures, violation_types  # noqa: E402

# ----------------------------------------------------------------- thresholds

# Below this many calls in *either* tow group, a recurrence percentage is not
# reported as a comparison at all, and the cluster bootstrap does not even run:
# one call moves a group of 30 by more than three points, so a "difference"
# measured on fewer calls than this could be entirely one call's timing. Not a
# power calculation against a pre-registered effect size -- no such target exists
# for a type that has never been read -- but a floor below which the percentage
# is not a percentage of anything stable enough for an interval to be worth
# computing.
MIN_TOW_GROUP_SAMPLE = 30

# Below this many vehicles observed across a type's watchlist doorways, a
# uniqueness share is not reported: driveway's own floor evidence (2,473 of 2,588,
# 96 per cent) rests on well over a thousand observations, and one or two repeat
# plates swing a share computed on fewer than 30 by several points.
MIN_VEHICLE_SAMPLE = 30

# A vehicle-uniqueness share at or above this is read as "mostly distinct
# vehicles" -- close enough to driveway's own 96 per cent (2,473/2,588) that the
# same "different car every time" description would fit this type's own figures
# too.
MOSTLY_DISTINCT_SHARE = 0.90

# A vehicle-uniqueness share at or below this is read as vehicles substantially
# repeating -- a pattern driveway's evidence does not show (its share is 0.955).
# Anything strictly between this and MOSTLY_DISTINCT_SHARE is reported as mixed,
# neither claim made.
SUBSTANTIAL_REPEAT_SHARE = 0.70

# Observational caveat attached to every per-type tow statement (4.12's
# requirement, restated here per type rather than only for driveway): stated even
# when the sample is too small to compare, because the caveat describes a property
# of tow assignment, not of the sample size.
TOW_CAVEAT = (
    "observational, not a randomized comparison -- towed and not-towed calls at "
    "this type's addresses are not otherwise alike, and tows may cluster at the "
    "addresses already calling the most, which could mask a real effect in "
    "either direction"
)


# --------------------------------------------------------------- tow comparison


def _tow_recurrence(calls, fields, recur_days):
    """Recurrence split by `Vehicle Was Towed`, over every call with a usable
    address and initiation date -- `figures.recurrence_by_tow`'s own population
    and grouping rule, reused via its pure helpers (`_grouped_calls`, `_recurs`)
    rather than reimplemented, so a discrepancy against that function would be a
    bug in one of the two, not silent drift between two copies of the same logic.
    The difference from calling `figures.recurrence_by_tow` directly is only that
    `calls`/`fields` are handed in already loaded, instead of this function
    loading them itself.

    Also returns `address_counts`: one `(towed_n, towed_recurring, not_towed_n,
    not_towed_recurring)` tuple per doorway, in exactly the shape
    `figures._effect_bound`'s cluster bootstrap expects -- a doorway's calls need
    to move together across a resample, so they are tallied per address here
    rather than reconstructed afterwards (matches how
    `figures.recurrence_by_tow` builds the same tuples for the same reason).
    """
    by_address, skipped = figures._grouped_calls(calls)
    groups = {
        "towed": {"calls": 0, "recurring": 0},
        "not_towed": {"calls": 0, "recurring": 0},
        "unknown_tow_status": {"calls": 0, "recurring": 0},
    }
    address_counts = []
    for entries in by_address.values():
        entries.sort(key=lambda e: e[0])
        times = [t for t, _call in entries]
        addr_towed_n = addr_towed_r = addr_not_towed_n = addr_not_towed_r = 0
        for i, (_t, call) in enumerate(entries):
            rid = call["REQUEST_ID"]
            towed = fields[rid].get("Vehicle Was Towed")
            key = {"Y": "towed", "N": "not_towed"}.get(towed, "unknown_tow_status")
            g = groups[key]
            g["calls"] += 1
            recurs = figures._recurs(times, i, recur_days)
            if recurs:
                g["recurring"] += 1
            if key == "towed":
                addr_towed_n += 1
                addr_towed_r += recurs
            elif key == "not_towed":
                addr_not_towed_n += 1
                addr_not_towed_r += recurs
        address_counts.append((addr_towed_n, addr_towed_r, addr_not_towed_n, addr_not_towed_r))

    grouped_total = sum(g["calls"] for g in groups.values())
    for g in groups.values():
        n = g["calls"]
        g["recurrence_pct"] = round(100 * g["recurring"] / n, 1) if n else None
    return groups, grouped_total, skipped, address_counts


def _tow_conclusion(groups, effect_bound):
    """Classify the tow comparison from the cluster-aware interval, never from
    the point estimate alone (defect 1). Returns
    `(conclusion_key, fragment, sample_sufficient)`, where `fragment` is a
    sentence fragment about *this type's* tow comparison -- it names no type, so
    callers can prefix it with whichever type it belongs to.
    """
    towed, not_towed = groups["towed"], groups["not_towed"]
    if towed["calls"] < MIN_TOW_GROUP_SAMPLE or not_towed["calls"] < MIN_TOW_GROUP_SAMPLE:
        return (
            "sample_too_small",
            f"the tow sample is too small to compare recurrence (towed="
            f"{towed['calls']}, not_towed={not_towed['calls']}, need >= "
            f"{MIN_TOW_GROUP_SAMPLE} each)",
            False,
        )

    cb = effect_bound["cluster_bootstrap"]
    ci = cb["ci_95_pct_points"]
    rules_out = cb["rules_out_reduction_larger_than_pct_points"]
    if ci is None:
        return (
            "no_difference",
            "the cluster bootstrap could not resolve an interval from too few "
            "resamples with both tow groups present, so no difference is reported",
            True,
        )

    lo, hi = ci
    if hi < 0:
        return (
            "tow_lower",
            f"towed calls were followed by another call at the same address "
            f"less often than not-towed calls (95% CI [{lo:+.1f}, {hi:+.1f}] "
            f"points, cluster bootstrap over doorways)",
            True,
        )
    if lo > 0:
        return (
            "tow_higher",
            f"towed calls were followed by another call at the same address "
            f"more often than not-towed calls (95% CI [{lo:+.1f}, {hi:+.1f}] "
            f"points, cluster bootstrap over doorways)",
            True,
        )
    return (
        "no_difference",
        f"towed and not-towed calls showed no detectable difference in "
        f"recurrence (95% CI [{lo:+.1f}, {hi:+.1f}] points, cluster bootstrap "
        f"over doorways) -- rules out a reduction larger than {rules_out:.1f} "
        f"points",
        True,
    )


# ------------------------------------------------------------- vehicle uniqueness


def _vehicle_uniqueness(rows):
    """Vehicles seen and distinct, summed across a type's watchlist doorways --
    `hotspots.build()`'s own `vehicles_seen`/`vehicles_distinct` per row, already
    computed by `derive_all()`; this only totals them. Matches how `README.md`'s
    "2,473 distinct vehicles produced 2,588 calls" figure is read: summed across
    the listed doorways, not deduplicated globally across doorways (the same
    plate at two addresses counts at each -- vehicle identity is a floor, not a
    plate, per the archived change's D9/D19 caveat, restated per type below).
    """
    seen = sum(r["vehicles_seen"] for r in rows)
    distinct = sum(r["vehicles_distinct"] for r in rows)
    return {"doorways": len(rows), "seen": seen, "distinct": distinct}


def _vehicle_conclusion(vehicles):
    """Returns `(conclusion_key, fragment, sample_sufficient)` -- the fragment is
    already phrased about "vehicles at listed addresses", not about driveway.
    """
    seen, distinct = vehicles["seen"], vehicles["distinct"]
    if seen < MIN_VEHICLE_SAMPLE:
        return (
            "sample_too_small",
            f"the vehicle sample is too small to compare uniqueness (seen="
            f"{seen}, need >= {MIN_VEHICLE_SAMPLE})",
            False,
        )
    # The classification compares the same 3-decimal share `compute_type` stores
    # in `vehicles["share"]`. The percentage quoted in the sentence is rounded
    # ONCE, from the unrounded ratio (`_unique_pct_text`): rounding to 3 decimals
    # first and then to a whole percent tips a ratio such as 2413/2528 = 95.45%
    # over the .5 boundary (0.955 -> "96%") where the live brief and
    # `/doorways` `summary.unique_pct` say 95 (task 9.4, concern 24).
    share = round(distinct / seen, 3)
    pct = _unique_pct_text(distinct, seen)
    if share >= MOSTLY_DISTINCT_SHARE:
        return (
            "mostly_distinct",
            f"vehicles at listed addresses are mostly distinct ({pct} of "
            f"{seen} seen), so repeat calls are not mainly one vehicle returning",
            True,
        )
    if share <= SUBSTANTIAL_REPEAT_SHARE:
        return (
            "substantial_repeat",
            f"vehicles at listed addresses substantially repeat ({pct} of "
            f"{seen} seen), so repeat calls are largely the same vehicle "
            f"returning",
            True,
        )
    return (
        "mixed",
        f"vehicle uniqueness at listed addresses is mixed ({pct} of "
        f"{seen} seen), neither mostly distinct nor mostly repeat",
        True,
    )


def _unique_pct_text(distinct, seen):
    """`distinct` as a whole per cent of `seen`, rounded once from the unrounded
    ratio and written exactly as the live brief writes it (`hotspots.py`'s
    `100 * distinct / seen:.0f`), so a served sentence and the brief agree.
    """
    return f"{100 * distinct / seen:.0f}%"


# --------------------------------------------------------------- own conclusion


def _type_conclusion(canonical_type, tow_key, tow_fragment, vehicle_key, vehicle_fragment):
    """A conclusion generated from this type's own figures alone, phrased about
    this type by name (defect 2). Never names another canonical type and never
    reuses another type's conclusion text -- there is no shared template string
    that could leak one, only this type's own key/fragment inputs.
    """
    parts = [f"For {canonical_type}, {tow_fragment}"]
    if tow_key != "sample_too_small":
        parts.append(TOW_CAVEAT)
    parts.append(vehicle_fragment)
    return "; ".join(parts) + "."


# ------------------------------------------------------------------- per type


def compute_type(canonical_type, result, recur_days,
                 bootstrap_seed=figures.DEFAULT_BOOTSTRAP_SEED,
                 bootstrap_iterations=figures.DEFAULT_BOOTSTRAP_ITERATIONS):
    """One canonical type's figures and conclusion, from a `derive_all()`-shaped
    per-type result dict (`calls`, `fields`, `rows`) -- no query of its own.

    The cluster bootstrap only runs when both tow groups already clear
    `MIN_TOW_GROUP_SAMPLE`; below that floor the sample is too small for an
    interval to be worth computing (and `_tow_conclusion` never looks at it), so
    skipping it there is a runtime saving, not a behaviour change.
    """
    groups, grouped_total, skipped, address_counts = _tow_recurrence(
        result["calls"], result["fields"], recur_days
    )
    vehicles = _vehicle_uniqueness(result["rows"])

    towed, not_towed = groups["towed"], groups["not_towed"]
    tow_sample_ok = (
        towed["calls"] >= MIN_TOW_GROUP_SAMPLE
        and not_towed["calls"] >= MIN_TOW_GROUP_SAMPLE
    )
    effect_bound = (
        figures._effect_bound(
            groups, address_counts, seed=bootstrap_seed, iterations=bootstrap_iterations,
        )
        if tow_sample_ok else None
    )

    tow_key, tow_fragment, tow_sufficient = _tow_conclusion(groups, effect_bound)
    vehicle_key, vehicle_fragment, vehicle_sufficient = _vehicle_conclusion(vehicles)
    conclusion = _type_conclusion(canonical_type, tow_key, tow_fragment, vehicle_key, vehicle_fragment)

    return {
        "canonical_type": canonical_type,
        "calls_raw": len(result["calls"]),
        "population": grouped_total,
        "excluded_no_usable_address_or_date": skipped,
        "recurrence_window_days": recur_days,
        "tow": {
            **groups,
            "sample_sufficient": tow_sufficient,
            "conclusion_key": tow_key,
            "conclusion": tow_fragment,
            "effect_bound": effect_bound,
            "caveat": TOW_CAVEAT,
        },
        "vehicles": {
            **vehicles,
            "share": round(vehicles["distinct"] / vehicles["seen"], 3) if vehicles["seen"] else None,
            "sample_sufficient": vehicle_sufficient,
            "conclusion_key": vehicle_key,
            "conclusion": vehicle_fragment,
        },
        "overall_conclusion": conclusion,
    }


def compute_all(conn, types=None, recur_days=figures.DEFAULT_RECUR_DAYS,
                min_calls=2, min_doorways=2, district=None,
                bootstrap_seed=figures.DEFAULT_BOOTSTRAP_SEED,
                bootstrap_iterations=figures.DEFAULT_BOOTSTRAP_ITERATIONS):
    """Every canonical type's figures from one `derive_all()` pass (task 4.17).

    `types=None` covers every key of `violation_types.CANONICAL_TYPES` (all 30).
    `min_calls`/`min_doorways`/`district` pass straight through to
    `derive.derive_all()` -- they govern which calls become *watchlist* doorways,
    which is the population `_vehicle_uniqueness()` sums over (see this module's
    docstring on the two populations); the tow/recurrence comparison is
    unaffected, since it runs over every call regardless of these thresholds.
    `bootstrap_seed`/`bootstrap_iterations` pass straight through to
    `compute_type` for every type -- one fixed seed across the whole run, so
    regenerating the report from the same mirror state reproduces every type's
    interval, not just driveway's.

    Returns `{canonical_type: compute_type()-shaped dict}`, including types with
    zero calls -- reported with `population: 0` and both conclusions
    `sample_too_small` rather than omitted, so a caller can tell "not enough data"
    from "not asked for".
    """
    if types is None:
        types = list(violation_types.CANONICAL_TYPES)
    all_results = derive.derive_all(
        conn, types=types, recur_days=recur_days, min_calls=min_calls,
        min_doorways=min_doorways, district=district,
    )
    return {
        canonical: compute_type(
            canonical, result, recur_days,
            bootstrap_seed=bootstrap_seed, bootstrap_iterations=bootstrap_iterations,
        )
        for canonical, result in all_results.items()
    }


# --------------------------------------------------------------------- CLI


def _table(all_figures):
    header = (
        f"{'type':<38} {'calls':>7} {'towed_n':>8} {'rec_towed':>10} "
        f"{'rec_not':>8} {'veh_share':>10} conclusion"
    )
    lines = [header]
    for canonical, fig in all_figures.items():
        tow = fig["tow"]
        veh = fig["vehicles"]
        rec_towed = (
            f"{tow['towed']['recurrence_pct']:.1f}%"
            if tow["towed"]["recurrence_pct"] is not None else "n/a"
        )
        rec_not = (
            f"{tow['not_towed']['recurrence_pct']:.1f}%"
            if tow["not_towed"]["recurrence_pct"] is not None else "n/a"
        )
        # From the counts, not from the stored 3-decimal `share`: formatting that to a
        # whole percent would round twice (0.955 -> "96%" for 2413/2528 = 95.45%).
        share = _unique_pct_text(veh["distinct"], veh["seen"]) if veh["seen"] else "n/a"
        lines.append(
            f"{canonical:<38} {fig['population']:>7} {tow['towed']['calls']:>8} "
            f"{rec_towed:>10} {rec_not:>8} {share:>10} "
            f"[{tow['conclusion_key']}/{veh['conclusion_key']}] {fig['overall_conclusion']}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--canonical-type", default=None,
        help="one canonical type only (default: every tracked type)",
    )
    ap.add_argument("--recur-days", type=int, default=figures.DEFAULT_RECUR_DAYS)
    ap.add_argument("--min-calls", type=int, default=2,
                    help="minimum recent calls for a doorway to count toward "
                         "vehicle uniqueness (matches derive.py's default)")
    ap.add_argument(
        "--bootstrap-iterations", type=int, default=figures.DEFAULT_BOOTSTRAP_ITERATIONS,
        help="cluster-bootstrap resamples per type's tow comparison (lower is "
             "faster, coarser); default matches figures.py's driveway default",
    )
    ap.add_argument("--json", action="store_true", help="print full JSON instead of a table")
    args = ap.parse_args()

    conn = db.connect()
    types = [args.canonical_type] if args.canonical_type else None
    all_figures = compute_all(conn, types=types, recur_days=args.recur_days,
                              min_calls=args.min_calls,
                              bootstrap_iterations=args.bootstrap_iterations)

    if args.json:
        print(json.dumps(all_figures, indent=2))
    else:
        print(_table(all_figures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
