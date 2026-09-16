#!/usr/bin/env python3
"""Re-derive the hour-of-day / channel figures for the Driveway population.

Supports task 8.1 (README/docs hour-of-day and channel figures): re-runs the
comparison between the OLD fixed UTC-3 offset conversion and the NEW
America/Halifax, DST-aware conversion (the corrected, shipped behaviour), so
the published figures can be reproduced or checked after data changes.

Data source: LIVE. Calls `hotspots.load()`, which fetches from the public HRM
Cityworks open-data endpoints over the network (does not touch the local
mirror). Read-only; writes nothing.

Runtime: ~3-5 minutes (network-bound fetch of the full call history for the
selected violation).

Run:
    venv/bin/python3 scripts/hour_of_day_figures.py
    venv/bin/python3 scripts/hour_of_day_figures.py --violation Driveway

Last reported figures (Driveway population): INTERNAL night-window (21:00-
07:00) calls = 4 of 8,345; INTERNAL peak hour 13:00; 311 Online peak hour
18:00.
"""
import argparse
import collections
import datetime
import pathlib
import sys

REPO_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

import hotspots  # noqa: E402


def to_local_old_fixed_minus3(epoch_ms):
    """The pre-4.6 behaviour: a fixed UTC-3 offset, year round."""
    if epoch_ms in (None, ""):
        return None
    utc = datetime.datetime.fromtimestamp(epoch_ms / 1000, datetime.UTC)
    return utc - datetime.timedelta(hours=3)


def summarize(calls, localizer, label):
    by_channel_hour = collections.defaultdict(lambda: collections.Counter())
    by_channel_total = collections.Counter()
    night_internal = 0
    total_internal = 0
    for c in calls:
        dt = localizer(c["DATE_INITIATED"])
        if dt is None:
            continue
        ch = c.get("INITIATED_BY") or "(none)"
        by_channel_hour[ch][dt.hour] += 1
        by_channel_total[ch] += 1
        if ch == "INTERNAL":
            total_internal += 1
            if dt.hour >= 21 or dt.hour < 7:
                night_internal += 1

    total = sum(by_channel_total.values())
    print(f"\n=== {label} ===")
    print(f"total calls with a timestamp: {total}")
    for ch, n in by_channel_total.most_common():
        pct = 100 * n / total
        peak_hour, peak_n = by_channel_hour[ch].most_common(1)[0]
        print(f"  {ch}: {n} calls ({pct:.1f}%), peak hour {peak_hour}:00 ({peak_n} calls)")
    if total_internal:
        pct_night = 100 * night_internal / total_internal
        print(
            f"  INTERNAL night window 21:00-07:00: {night_internal} of {total_internal} "
            f"({pct_night:.2f}%)"
        )
    return {
        "total": total,
        "by_channel_total": dict(by_channel_total),
        "by_channel_hour": {k: dict(v) for k, v in by_channel_hour.items()},
        "internal_night": night_internal,
        "internal_total": total_internal,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--violation", default="Driveway",
                     help="substring match against Alleged Violation, as in hotspots.load "
                          "(default: %(default)s, matching the published figures)")
    args = ap.parse_args()

    calls, fields = hotspots.load(args.violation)
    print(f"fetched {len(calls)} calls for violation={args.violation!r} (report population)")

    old = summarize(calls, to_local_old_fixed_minus3, "OLD fixed UTC-3 (pre-4.6 behaviour)")
    new = summarize(calls, hotspots.to_local, "NEW America/Halifax, DST-aware (current code)")

    print("\n=== delta (new - old), INTERNAL hour histogram ===")
    oh = old["by_channel_hour"].get("INTERNAL", {})
    nh = new["by_channel_hour"].get("INTERNAL", {})
    for h in range(24):
        o, n = oh.get(h, 0), nh.get(h, 0)
        if o != n:
            print(f"  hour {h:02d}: old={o} new={n} delta={n - o}")

    print("\n=== delta (new - old), 311 Online hour histogram ===")
    oh = old["by_channel_hour"].get("311 Online", {})
    nh = new["by_channel_hour"].get("311 Online", {})
    for h in range(24):
        o, n = oh.get(h, 0), nh.get(h, 0)
        if o != n:
            print(f"  hour {h:02d}: old={o} new={n} delta={n - o}")


if __name__ == "__main__":
    main()
