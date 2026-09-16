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

No network call: like `derive.py`, this module reads only `derive.load()`'s
Postgres-backed output and `hotspots`'s pure functions -- never `hotspots.query`/
`hotspots.fetch_blocks`, never `mirror.source`.

Run:  python3 -m mirror.figures                       # Driveway, current mirror
      python3 -m mirror.figures --as-of 2026-09-04     # reproduce an earlier snapshot
      python3 -m mirror.figures --canonical-type "No Parking Sign"
"""

import argparse
import collections
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotspots  # noqa: E402 -- pure functions only, same restriction as derive.py
from mirror import db, derive  # noqa: E402

DEFAULT_VIOLATION = "Driveway"
DEFAULT_RECUR_DAYS = 365


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


def recurrence_by_tow(conn, violation=None, canonical_type=None,
                       recur_days=DEFAULT_RECUR_DAYS, as_of=None):
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
    """
    calls, fields = derive.load(conn, violation=violation, canonical_type=canonical_type)
    by_address, skipped = _grouped_calls(calls, as_of=as_of)

    groups = {
        "towed": {"ids": [], "recurring": 0},
        "not_towed": {"ids": [], "recurring": 0},
        "unknown_tow_status": {"ids": [], "recurring": 0},
    }
    for entries in by_address.values():
        entries.sort(key=lambda e: e[0])
        times = [t for t, _call in entries]
        for i, (_t, call) in enumerate(entries):
            rid = call["REQUEST_ID"]
            towed = fields[rid].get("Vehicle Was Towed")
            key = {"Y": "towed", "N": "not_towed"}.get(towed, "unknown_tow_status")
            g = groups[key]
            g["ids"].append(rid)
            if _recurs(times, i, recur_days):
                g["recurring"] += 1

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
        "caveat": (
            "Observational, not a randomized comparison: towed and not-towed calls "
            "are not otherwise alike. Tows may cluster at the addresses already "
            "calling the most, which could mask a real effect in either direction "
            "(4.12)."
        ),
    }
    for key, g in groups.items():
        n = len(g["ids"])
        result["groups"][key] = {
            "calls": n,
            "recurring": g["recurring"],
            "recurrence_pct": round(100 * g["recurring"] / n, 1) if n else None,
        }
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

    print(json.dumps({"recurrence_by_tow": recurrence, "response_time": response}, indent=2))
    print(file=sys.stderr)
    print(_readable(recurrence, response), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
