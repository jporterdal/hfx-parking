"""Compute the headline figures `README.md` rests on, from the mirror.

Tasks 4.10 and 4.13 of `mirror-hrm-data-and-host-app` (carried from the archived
`add-parking-hotspot-map` change's 11.1 and 11.4): the tow-versus-recurrence
comparison -- "Towed calls recur at 44.7 per cent. Not-towed calls recur at 44.6
per cent." -- and the response-time figure opening `README.md` -- "Halifax answers
a blocked driveway call in 41 minutes" -- exist only as hand-written prose and are
reproduced by no generated output. This module computes both from
`mirror.derive.load()`'s own output, so a reader can regenerate them.

Every figure states the population it is drawn from -- a count and a plain-English
definition -- because 4.14 (denominator reconciliation) has to reconcile several
different driveway-call counts in circulation, and 4.11/4.12 (the effect bound and
the observational caveat this module already attaches) extend a figure rather than
recomputing one. Shipping the raw counts (`calls`, `recurring`) alongside every
percentage is deliberate: 4.11 needs them to compute a confidence interval, not the
rounded percentage alone.

Task 4.11 adds `recurrence_by_tow`'s `effect_bound`: a naive two-proportion interval
and a cluster-aware bootstrap for the towed-vs-not-towed recurrence difference, with
the bootstrap as the headline because calls are not independent trials -- see
`_effect_bound`'s docstring for why the naive interval understates uncertainty and
how the bootstrap corrects for it. Task 4.12 keeps the observational caveat sitting
immediately next to that bound, in the same dict, the same JSON and the same CLI
lines, rather than as a fact stated once elsewhere and left to travel on its own.
Task 4.14 adds `call_denominators`, which reconciles the several `Driveway`-call
counts that circulate in the documentation -- the current raw label alone, the label
plus its legacy code, and the tow-comparison cohort -- against the same
`derive.load()` selection and the same `recurrence_by_tow` population the other two
figures already compute, so the mapping from a published number to the population it
counts is one query away rather than an inference.

No network call: like `derive.py`, this module reads only `derive.load()`'s
Postgres-backed output and `hotspots`'s pure functions -- never `hotspots.query`/
`hotspots.fetch_blocks`, never `mirror.source`. `_cluster_bootstrap_ci`'s resampling
uses a `random.Random` instance seeded with a fixed constant (`DEFAULT_BOOTSTRAP_SEED`),
never the global `random` module, so a run is deterministic and never perturbs any
other code that happens to use `random` in the same process.

Run:  python3 -m mirror.figures                       # Driveway, current mirror
      python3 -m mirror.figures --as-of 2026-09-04     # reproduce an earlier snapshot
      python3 -m mirror.figures --canonical-type "No Parking Sign"
"""

import argparse
import collections
import datetime
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotspots  # noqa: E402 -- pure functions only, same restriction as derive.py
from mirror import db, derive  # noqa: E402

DEFAULT_VIOLATION = "Driveway"
DEFAULT_RECUR_DAYS = 365
CONFIDENCE_LEVEL = 0.95
# Fixed, not derived from the clock or the OS -- a report generated twice from the
# same mirror state must produce the same cluster-aware bound both times (4.11).
DEFAULT_BOOTSTRAP_SEED = 20260911
DEFAULT_BOOTSTRAP_ITERATIONS = 2000


def _selection_label(violation, canonical_type):
    return f"violation={violation!r}" if violation else f"canonical_type={canonical_type!r}"


def _grouped_calls(calls, as_of=None):
    """Group calls by doorway address exactly as `hotspots.build()` does before it
    counts repeats: only calls carrying both a usable address (`clean_address`) and
    an initiation timestamp are grouped, because recurrence requires ordering the
    calls at one doorway chronologically. A call missing either contributes to
    neither doorway list nor this figure, matching `build()`'s own restriction.

    `as_of`, given as an aware `datetime`, keeps only calls initiated strictly
    before it -- not a redefinition of recurrence, but the way to reproduce a
    figure computed from an earlier, smaller mirror snapshot (see `--as-of` on the
    CLI). `None`, the default, uses every call currently in the mirror.
    """
    by_address = collections.defaultdict(list)
    skipped = 0
    for call in calls:
        started = hotspots.to_local(call["DATE_INITIATED"])
        address = hotspots.clean_address(call["ADDRESS"])
        if not (started and address):
            skipped += 1
            continue
        if as_of is not None and started >= as_of:
            continue
        by_address[address].append((started, call))
    return by_address, skipped


def _recurs(times, index, recur_days):
    """True when a later call at the same doorway falls within `recur_days` of the
    call at `times[index]`. Matches `hotspots.build()`'s own repeat-counting rule
    exactly (its `repeats` comprehension): the earlier call is the one counted as
    recurring, looking only forward -- the archived `enforcement-effectiveness`
    spec's "the earlier call is counted as recurring" scenario.
    """
    t = times[index]
    return any((times[j] - t).days <= recur_days for j in range(index + 1, len(times)))


def _naive_two_proportion_ci(p1, n1, p2, n2, confidence=CONFIDENCE_LEVEL):
    """The textbook Wald interval for `p1 - p2`, in percentage points: treats every
    one of the `n1 + n2` calls as an independent Bernoulli trial. See
    `_effect_bound`'s docstring for why that assumption is wrong here and what this
    interval is for anyway.
    """
    z = statistics.NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    diff = p1 - p2
    return (100 * (diff - z * se), 100 * (diff + z * se))


def _cluster_bootstrap_ci(address_counts, seed, iterations, confidence=CONFIDENCE_LEVEL):
    """Percentile bootstrap for the towed-vs-not-towed recurrence difference,
    resampling *doorways* rather than calls. `address_counts` is one
    `(towed_n, towed_recurring, not_towed_n, not_towed_recurring)` tuple per
    doorway; each replicate draws `len(address_counts)` doorways with replacement
    (a doorway's calls move together, never split across the resample) and
    recomputes the difference from the pooled totals. Returns `(lo, hi)` in
    percentage points, or `None` if no replicate landed with both groups
    non-empty (only possible against a tiny or heavily lopsided selection, such
    as a test fixture).

    `random.Random(seed)` is a private generator, not the `random` module's global
    state -- a caller running this alongside anything else that draws from `random`
    is not perturbed, and is not perturbed by it.
    """
    n = len(address_counts)
    if n == 0:
        return None
    rng = random.Random(seed)
    diffs = []
    population = range(n)
    for _ in range(iterations):
        towed_n = towed_r = not_towed_n = not_towed_r = 0
        for idx in rng.choices(population, k=n):
            tn, tr, ntn, ntr = address_counts[idx]
            towed_n += tn
            towed_r += tr
            not_towed_n += ntn
            not_towed_r += ntr
        if towed_n and not_towed_n:
            diffs.append(100 * (towed_r / towed_n - not_towed_r / not_towed_n))
    if not diffs:
        return None
    diffs.sort()
    alpha = (1 - confidence) / 2
    lo = diffs[min(len(diffs) - 1, max(0, round(alpha * (len(diffs) - 1))))]
    hi = diffs[min(len(diffs) - 1, max(0, round((1 - alpha) * (len(diffs) - 1))))]
    return (lo, hi)


def _effect_bound(groups, address_counts, seed=DEFAULT_BOOTSTRAP_SEED,
                   iterations=DEFAULT_BOOTSTRAP_ITERATIONS):
    """Task 4.11: the magnitude of effect the towed-vs-not-towed comparison can
    rule out, not only the observation that the two rates are close.

    **Why two intervals, and why the bootstrap is the headline.** A naive
    two-proportion interval (`_naive_two_proportion_ci`) treats every call as an
    independent trial. That is false here: recurrence is defined per doorway (a
    later call at the *same address*), so calls at a doorway that keeps calling
    are correlated with each other, and whether a doorway's vehicles get towed is
    plausibly a property of the doorway too (an address enforcement already
    watches may get towed more or less consistently than one it does not). Both
    make the effective sample size smaller than the raw call count the naive
    interval uses, so the naive interval is narrower than the data actually
    supports -- it understates uncertainty, in the direction that would make a
    real effect look more ruled-out than it is.

    The cluster bootstrap (`_cluster_bootstrap_ci`) resamples whole doorways
    instead of individual calls: a doorway that recurs a lot, or that gets towed
    consistently, is included or excluded from a replicate as one unit, which is
    what "independent" ought to mean when the thing that actually varies
    independently is the doorway, not the call. It is deterministic (a fixed seed,
    `DEFAULT_BOOTSTRAP_SEED`), not because the choice of seed matters to the
    answer, but so that regenerating the report from the same mirror state
    reproduces the same bound rather than a slightly different one each run.

    Both intervals are reported -- the naive one for a reader who wants the
    familiar textbook number and to see how much narrower it is -- but the
    cluster-aware interval is the one this module calls the headline, because it
    is the one that is actually honest about what varies independently here.

    Returns a dict with `"comparable": False` and a reason if either group is
    empty (nothing to compare), else `"comparable": True` plus both intervals and,
    for each, "rules out a reduction larger than N points" read directly off the
    interval's lower bound -- `-lo` when `lo < 0` (the interval reaches into
    "towed recurs less"), 0 when it does not (even the most favourable end of the
    interval shows no reduction at all).
    """
    towed, not_towed = groups["towed"], groups["not_towed"]
    towed_n, towed_r = towed["calls"], towed["recurring"]
    not_towed_n, not_towed_r = not_towed["calls"], not_towed["recurring"]
    if towed_n == 0 or not_towed_n == 0:
        return {
            "comparable": False,
            "reason": "the towed or not-towed group has no calls in this selection",
        }

    p1, p2 = towed_r / towed_n, not_towed_r / not_towed_n
    naive_ci = _naive_two_proportion_ci(p1, towed_n, p2, not_towed_n)
    cluster_ci = _cluster_bootstrap_ci(address_counts, seed, iterations)

    def _rules_out(ci):
        return round(max(0.0, -ci[0]), 1) if ci is not None else None

    return {
        "comparable": True,
        "difference_pct_points": round(100 * (p1 - p2), 2),
        "naive": {
            "ci_95_pct_points": [round(v, 1) for v in naive_ci],
            "rules_out_reduction_larger_than_pct_points": _rules_out(naive_ci),
            "assumption": (
                "treats every call as an independent trial -- understates "
                "uncertainty, because recurrence is defined per doorway and calls "
                "at the same address are not independent (see _effect_bound)"
            ),
        },
        "cluster_bootstrap": {
            "ci_95_pct_points": [round(v, 1) for v in cluster_ci] if cluster_ci else None,
            "rules_out_reduction_larger_than_pct_points": _rules_out(cluster_ci),
            "resample_unit": "doorway (clean address)",
            "iterations": iterations,
            "seed": seed,
        },
        "headline": "cluster_bootstrap",
    }


def recurrence_by_tow(conn, violation=None, canonical_type=None,
                       recur_days=DEFAULT_RECUR_DAYS, as_of=None,
                       bootstrap_iterations=DEFAULT_BOOTSTRAP_ITERATIONS,
                       bootstrap_seed=DEFAULT_BOOTSTRAP_SEED):
    """Task 4.10: recurrence split by tow status.

    Recurrence is computed per call, over every call in the selection that has a
    usable address and initiation date -- independent of the recency and
    min-calls filters `hotspots.build()` applies before a doorway is *listed*.
    Those filters decide what appears on the watchlist; this figure describes
    every call that was *made*, which is the population `README.md`'s "44.6 per
    cent were followed by another call at the same doorway within a year"
    describes.

    Split by the `Vehicle Was Towed` custom field: `"Y"` is towed, `"N"` is not
    towed. A call carrying neither (the field absent, or an unrecognised value) is
    reported under `unknown_tow_status` and excluded from the towed/not-towed
    comparison rather than folded into "not towed" -- an unrecorded tow flag is not
    evidence the vehicle stayed, and treating it as "no" would be an assumption
    this module does not make.

    Task 4.11 adds `effect_bound` -- a naive and a cluster-aware (bootstrap over
    doorways) confidence interval for the towed-minus-not-towed recurrence
    difference, and the magnitude of reduction each rules out; see `_effect_bound`
    for why the cluster-aware one is the headline. Task 4.12 keeps the
    observational `caveat` sitting immediately after it, in this dict and
    therefore in the JSON and CLI output built from it, rather than separated from
    the number it qualifies.
    """
    calls, fields = derive.load(conn, violation=violation, canonical_type=canonical_type)
    by_address, skipped = _grouped_calls(calls, as_of=as_of)

    groups = {
        "towed": {"ids": [], "recurring": 0},
        "not_towed": {"ids": [], "recurring": 0},
        "unknown_tow_status": {"ids": [], "recurring": 0},
    }
    # One (towed_n, towed_recurring, not_towed_n, not_towed_recurring) tuple per
    # doorway, for `_effect_bound`'s cluster bootstrap (4.11) -- a doorway's calls
    # need to move together across a resample, so they are tallied per address
    # here rather than reconstructed from `groups["ids"]` afterwards.
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
            g["ids"].append(rid)
            recurs = _recurs(times, i, recur_days)
            if recurs:
                g["recurring"] += 1
            if key == "towed":
                addr_towed_n += 1
                addr_towed_r += recurs
            elif key == "not_towed":
                addr_not_towed_n += 1
                addr_not_towed_r += recurs
        address_counts.append((addr_towed_n, addr_towed_r, addr_not_towed_n, addr_not_towed_r))

    grouped_total = sum(len(g["ids"]) for g in groups.values())
    definition = (
        f"calls matching the {_selection_label(violation, canonical_type)} selection, "
        "with a usable address and DATE_INITIATED (the same restriction "
        "hotspots.build() applies before grouping calls into doorways)"
    )
    if as_of is not None:
        last_day = (as_of - datetime.timedelta(days=1)).date()
        definition += f", initiated up to and including {last_day} local"

    result = {
        "figure": "recurrence_by_tow_status",
        "recurrence_window_days": recur_days,
        "population": {
            "count": grouped_total,
            "definition": definition,
            "raw_selection_count": len(calls),
            "excluded_no_usable_address_or_date": skipped,
        },
        "groups": {},
    }
    for key, g in groups.items():
        n = len(g["ids"])
        result["groups"][key] = {
            "calls": n,
            "recurring": g["recurring"],
            "recurrence_pct": round(100 * g["recurring"] / n, 1) if n else None,
        }
    # effect_bound directly precedes caveat -- 4.12 requires the observational
    # caveat to travel with the bound, in this dict and in everything built from it.
    result["effect_bound"] = _effect_bound(
        result["groups"], address_counts, seed=bootstrap_seed, iterations=bootstrap_iterations,
    )
    result["caveat"] = (
        "Observational, not a randomized comparison: towed and not-towed calls "
        "are not otherwise alike. Tows may cluster at the addresses already "
        "calling the most, which could mask a real effect in either direction "
        "(4.12)."
    )
    return result


def response_time(conn, violation=None, canonical_type=None, as_of=None):
    """Task 4.13: median elapsed time from `DATE_INITIATED` to `DATE_CLOSED`.

    Every call matching the selection is in the population; a call with no
    `DATE_CLOSED` -- never closed -- is excluded from the median and counted
    separately, per the archived `enforcement-effectiveness` spec's "Calls never
    closed excluded" scenario.

    Elapsed time is a plain subtraction of two UTC epoch-millisecond timestamps
    (`derive.load()`'s `_epoch_ms`), so it is unaffected by which local-timezone
    convention a viewer converts them with -- task 4.6's daylight-saving
    correction changes calendar-date fields, never an elapsed duration between two
    fixed instants.
    """
    calls, _fields = derive.load(conn, violation=violation, canonical_type=canonical_type)
    if as_of is not None:
        calls = [
            c for c in calls
            if c["DATE_INITIATED"] is not None
            and hotspots.to_local(c["DATE_INITIATED"]) < as_of
        ]

    closed = [
        c for c in calls
        if c["DATE_INITIATED"] is not None and c["DATE_CLOSED"] is not None
    ]
    never_closed = [c for c in calls if c["DATE_CLOSED"] is None]
    elapsed_minutes = [
        (c["DATE_CLOSED"] - c["DATE_INITIATED"]) / 60000 for c in closed
    ]

    definition = f"calls matching the {_selection_label(violation, canonical_type)} selection"
    if as_of is not None:
        last_day = (as_of - datetime.timedelta(days=1)).date()
        definition += f", initiated up to and including {last_day} local"

    return {
        "figure": "response_time",
        "population": {
            "count": len(calls),
            "definition": definition,
        },
        "closed_calls": len(closed),
        "never_closed_calls": len(never_closed),
        "median_elapsed_minutes": (
            round(statistics.median(elapsed_minutes), 1) if elapsed_minutes else None
        ),
        "note": (
            "Elapsed time is a difference between two fixed UTC instants; the "
            "daylight-saving-aware local conversion (4.6) does not change it."
        ),
    }


def call_denominators(conn, violation=None, canonical_type=None, as_of=None):
    """Task 4.14: reconcile the call-count denominators in circulation for a
    selection, several of which have been quoted for `Driveway` in different
    places without saying which population they were:

    - The current `Alleged Violation` label alone (`"Blocking Driveway (DISPATCH)"`
      for `Driveway`) -- what a live `outStatistics` query grouped by that field
      reports, and what `docs/parking-hotspots/data-sources.md` and `decisions.md`
      quoted at an earlier mirror snapshot (9,729; 9,770 as of this mirror).
    - That label plus its legacy short code (`"DRIVEWAY"`) -- `raw_selection_total`
      below, 9,834 as of this mirror -- which is exactly what `--violation
      "Driveway"`'s case-insensitive substring match and `--canonical-type
      "Blocking Driveway"`'s exact-membership match both select; the two methods
      are verified equal by `tests/test_derive.py`, and this function does not
      care which one produced its `ids`.
    - The same total restricted to an earlier cutoff (`raw_selection_as_of`, only
      present when `as_of` is given) -- what `README.md`'s "9,791 blocked driveway
      calls, 2020 to 2026-09-04" quotes: the raw selection with no address or date
      usability restriction, at the 2026-09-04 snapshot.
    - The tow-comparison cohort (`tow_comparison_population` and
      `tow_comparison_known_status`) -- the population `recurrence_by_tow` groups
      by tow status, and that same population with the unknown-tow-status calls
      excluded, which is the denominator the towed/not-towed percentages and
      `effect_bound` (4.11) are actually computed over: 9,655 = 445 + 9,206 + 4 as
      of the 2026-09-04 cutoff, 9,651 = 445 + 9,206 excluding the 4 unknown.

    Every count here is read from the same `derive.load()` selection and the same
    `recurrence_by_tow` population `figures.py`'s other functions already compute
    from, rather than recomputed by a separate path that could drift from them.
    """
    ids = set()
    calls, _fields = derive.load(
        conn, violation=violation, canonical_type=canonical_type, ids_out=ids,
    )
    selection_label = _selection_label(violation, canonical_type)
    selection_method = (
        "case-insensitive substring match against Alleged Violation"
        if violation is not None else
        "exact membership in violation_types.raw_labels_for(...)"
    )

    raw_label_counts = {}
    if ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT custom_field_value, COUNT(DISTINCT request_id) FROM "
                "custom_fields WHERE custom_field_name = 'Alleged Violation' "
                "AND request_id = ANY(%s) GROUP BY custom_field_value",
                (list(ids),),
            )
            raw_label_counts = dict(cur.fetchall())

    populations = {}
    for label, n in sorted(raw_label_counts.items(), key=lambda kv: -kv[1]):
        populations[f"raw_label:{label}"] = {
            "count": n,
            "definition": (
                f"calls whose Alleged Violation value is exactly {label!r} -- "
                "one raw label the selection matched"
            ),
        }
    populations["raw_selection_total"] = {
        "count": len(calls),
        "definition": (
            f"every call matching the {selection_label} selection ({selection_method}), "
            "current mirror -- the sum of the raw-label counts above"
        ),
    }

    if as_of is not None:
        last_day = (as_of - datetime.timedelta(days=1)).date()
        as_of_total = sum(
            1 for c in calls
            if c["DATE_INITIATED"] is not None
            and hotspots.to_local(c["DATE_INITIATED"]) < as_of
        )
        populations["raw_selection_as_of"] = {
            "count": as_of_total,
            "definition": (
                f"{selection_label} calls initiated up to and including {last_day} "
                "local, no address/date-usability restriction -- the count an "
                "earlier snapshot's headline quoted"
            ),
        }

    recurrence = recurrence_by_tow(
        conn, violation=violation, canonical_type=canonical_type, as_of=as_of,
    )
    towed = recurrence["groups"]["towed"]["calls"]
    not_towed = recurrence["groups"]["not_towed"]["calls"]
    unknown = recurrence["groups"]["unknown_tow_status"]["calls"]
    populations["tow_comparison_population"] = {
        "count": recurrence["population"]["count"],
        "definition": (
            recurrence["population"]["definition"] + " -- grouped by tow status: "
            f"towed {towed:,} + not-towed {not_towed:,} + unknown {unknown:,}"
        ),
    }
    populations["tow_comparison_known_status"] = {
        "count": towed + not_towed,
        "definition": (
            f"the same population with its {unknown:,} calls of unknown tow "
            "status excluded -- the cohort the towed-vs-not-towed recurrence "
            "comparison and effect_bound (4.11) are computed over"
        ),
    }
    populations["excluded_no_usable_address_or_date"] = {
        "count": recurrence["population"]["excluded_no_usable_address_or_date"],
        "definition": (
            "raw-selection calls dropped before the tow-comparison population is "
            "formed, for lacking a usable address or DATE_INITIATED"
        ),
    }

    return {
        "figure": "call_denominators",
        "selection": selection_label,
        "as_of": (
            (as_of - datetime.timedelta(days=1)).date().isoformat()
            if as_of is not None else None
        ),
        "populations": populations,
    }


def _readable(recurrence, response):
    lines = [
        "Recurrence by tow status "
        f"(population: {recurrence['population']['count']:,} -- "
        f"{recurrence['population']['definition']}):",
    ]
    for key in ("towed", "not_towed", "unknown_tow_status"):
        g = recurrence["groups"][key]
        pct = f"{g['recurrence_pct']:.1f}%" if g["recurrence_pct"] is not None else "n/a"
        lines.append(f"  {key}: {g['calls']:,} calls, {g['recurring']:,} recurring, {pct}")
    eb = recurrence["effect_bound"]
    if eb["comparable"]:
        lo, hi = eb["naive"]["ci_95_pct_points"]
        lines.append(
            f"  difference (towed - not-towed): {eb['difference_pct_points']:+.2f} points"
        )
        lines.append(
            f"  naive 95% CI: [{lo:+.1f}, {hi:+.1f}] pts -- rules out a reduction "
            f"larger than {eb['naive']['rules_out_reduction_larger_than_pct_points']:.1f} pts "
            "(treats calls as independent; understates uncertainty)"
        )
        cb = eb["cluster_bootstrap"]
        if cb["ci_95_pct_points"] is not None:
            clo, chi = cb["ci_95_pct_points"]
            lines.append(
                f"  cluster-aware 95% CI, bootstrap over doorways, HEADLINE: "
                f"[{clo:+.1f}, {chi:+.1f}] pts -- rules out a reduction larger than "
                f"{cb['rules_out_reduction_larger_than_pct_points']:.1f} pts "
                f"({cb['iterations']:,} resamples, seed {cb['seed']})"
            )
        else:
            lines.append(
                "  cluster-aware bootstrap: not enough resamples with both groups "
                "present to report an interval"
            )
    else:
        lines.append(f"  effect bound: not comparable -- {eb['reason']}")
    lines.append(f"  {recurrence['caveat']}")
    lines.append("")
    lines.append(
        "Response time "
        f"(population: {response['population']['count']:,} -- "
        f"{response['population']['definition']}):"
    )
    med = response["median_elapsed_minutes"]
    lines.append(
        f"  median {med:.1f} minutes over {response['closed_calls']:,} closed calls; "
        f"{response['never_closed_calls']:,} never closed"
        if med is not None else
        f"  no closed calls; {response['never_closed_calls']:,} never closed"
    )
    return "\n".join(lines)


def _readable_denominators(denominators):
    as_of = denominators["as_of"] or "current mirror"
    lines = [f"Call denominators ({denominators['selection']}, {as_of}):"]
    for name, pop in denominators["populations"].items():
        lines.append(f"  {name}: {pop['count']:,} -- {pop['definition']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--violation", default=None,
        help=f"substring selection, hotspots.load()-compatible (default "
             f"{DEFAULT_VIOLATION!r} if neither --violation nor --canonical-type "
             "is given)",
    )
    ap.add_argument(
        "--canonical-type", default=None,
        help="exact-membership selection from violation_types.py's frozen list",
    )
    ap.add_argument("--recur-days", type=int, default=DEFAULT_RECUR_DAYS)
    ap.add_argument(
        "--as-of", default=None,
        help="YYYY-MM-DD: include only calls initiated up to and including this "
             "local Halifax date, to reproduce an earlier snapshot's figures "
             "rather than describe the current mirror",
    )
    args = ap.parse_args()

    if args.violation and args.canonical_type:
        ap.error("give at most one of --violation and --canonical-type")
    violation = args.violation
    canonical_type = args.canonical_type
    if violation is None and canonical_type is None:
        violation = DEFAULT_VIOLATION

    as_of = None
    if args.as_of:
        last_day = datetime.date.fromisoformat(args.as_of)
        cutoff_day = last_day + datetime.timedelta(days=1)
        as_of = datetime.datetime(
            cutoff_day.year, cutoff_day.month, cutoff_day.day, tzinfo=hotspots.HALIFAX
        )

    conn = db.connect()
    recurrence = recurrence_by_tow(
        conn, violation=violation, canonical_type=canonical_type,
        recur_days=args.recur_days, as_of=as_of,
    )
    response = response_time(
        conn, violation=violation, canonical_type=canonical_type, as_of=as_of,
    )
    denominators = call_denominators(
        conn, violation=violation, canonical_type=canonical_type, as_of=as_of,
    )

    print(json.dumps(
        {
            "recurrence_by_tow": recurrence,
            "response_time": response,
            "call_denominators": denominators,
        },
        indent=2,
    ))
    print(file=sys.stderr)
    print(_readable(recurrence, response), file=sys.stderr)
    print(file=sys.stderr)
    print(_readable_denominators(denominators), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
