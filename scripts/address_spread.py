"""Address-collision / spread measurement for the reduced-address doorway key.

Supports task 4.18: checks a canonical type (default `No Parking Sign`,
27,404 calls, the largest canonical type) against a baseline (default
`Blocking Driveway`) for the string-address-reduction concern in design.md's
R2: `hotspots.clean_address()` reduces an address to "text before the first
comma, upper-cased, whitespace collapsed", and that string alone is the
doorway key (D4). Two things can go wrong at volume:

  (a) Two distinct physical locations collapse to one reduced-address string
      (e.g. two building entrances sharing a street-number range, or a long
      block face where HRM's free-text ADDRESS field is inconsistent).
      Detected here as a *high coordinate spread within one address string*:
      if the calls grouped under one string are actually scattered far apart,
      the string is not one doorway.

  (b) One physical location is split across several distinct strings (e.g.
      "1234 BARRINGTON ST" vs "1234 BARRINGTON STREET", or a unit/suite
      variant that survives comma-splitting). Detected here as *near-
      duplicate strings*: two distinct reduced-address strings whose median
      call coordinates sit within a small radius of each other.

Data source: the local mirror, read-only (`mirror.db.connect()`, default
`mirror` schema, no `HFX_MIRROR_SCHEMA` override). No writes, no network.

Runtime: a few seconds per canonical type against the current mirror.

Run:
    venv/bin/python3 scripts/address_spread.py
    venv/bin/python3 scripts/address_spread.py --type "No Parking Sign" --baseline "Blocking Driveway"

Last reported figures: spread > 200m share of multi-call addresses --
No Parking Sign 4.7% vs Blocking Driveway (Driveway) 0.3%.
"""
import argparse
import collections
import math
import pathlib
import sys
import time

REPO_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

import hotspots  # noqa: E402
from mirror import db, derive  # noqa: E402

REF_LAT = 44.65  # central Halifax latitude, for the local equirectangular projection
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(REF_LAT))

SPREAD_THRESHOLDS_M = (50, 200)
NEAR_DUP_RADIUS_M = 30
CELL_SIZE_M = 60  # > 2x the near-dup radius, so a 3x3 cell neighbourhood always covers it


def to_xy(lat, lon):
    return (lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT)


def dist_m(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def group_by_address(calls):
    by_address = collections.defaultdict(list)
    for c in calls:
        addr = hotspots.clean_address(c["ADDRESS"])
        lat, lon = c["LATITUDE"], c["LONGITUDE"]
        if addr and lat and lon:
            by_address[addr].append(to_xy(lat, lon))
    return by_address


def spread_stats(by_address):
    """Max pairwise distance within each address string's own calls."""
    spreads = {}
    for addr, points in by_address.items():
        if len(points) < 2:
            continue
        m = 0.0
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                d = dist_m(points[i], points[j])
                if d > m:
                    m = d
        spreads[addr] = m
    return spreads


def median_points(by_address):
    medians = {}
    for addr, points in by_address.items():
        xs = sorted(p[0] for p in points)
        ys = sorted(p[1] for p in points)
        n = len(xs)
        medians[addr] = (xs[n // 2], ys[n // 2])
    return medians


def near_duplicate_pairs(medians, radius_m=NEAR_DUP_RADIUS_M, cell_size=CELL_SIZE_M):
    """Distinct address strings whose median points sit within `radius_m` of each
    other, found via a spatial grid instead of all-pairs comparison.
    """
    cells = collections.defaultdict(list)
    for addr, (x, y) in medians.items():
        cell = (int(x // cell_size), int(y // cell_size))
        cells[cell].append((addr, (x, y)))

    seen_pairs = set()
    pairs = []
    for (cx, cy), items in cells.items():
        neighbours = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbours.extend(cells.get((cx + dx, cy + dy), []))
        for i, (addr_a, pt_a) in enumerate(items):
            for addr_b, pt_b in neighbours:
                if addr_a == addr_b:
                    continue
                key = tuple(sorted((addr_a, addr_b)))
                if key in seen_pairs:
                    continue
                d = dist_m(pt_a, pt_b)
                if d <= radius_m:
                    seen_pairs.add(key)
                    pairs.append((addr_a, addr_b, d))
    return pairs


def report(conn, canonical_type):
    t0 = time.perf_counter()
    calls, _fields = derive.load(conn, canonical_type=canonical_type)
    by_address = group_by_address(calls)
    spreads = spread_stats(by_address)
    medians = median_points(by_address)
    dup_pairs = near_duplicate_pairs(medians)
    elapsed = time.perf_counter() - t0

    n_addresses = len(by_address)
    n_multi_call_addresses = len(spreads)
    over = {t: sum(1 for v in spreads.values() if v > t) for t in SPREAD_THRESHOLDS_M}
    addresses_in_dup_pairs = {a for a, b, _ in dup_pairs} | {b for a, b, _ in dup_pairs}

    print(f"=== {canonical_type} ===")
    print(f"  calls (usable address+coords, grouped): {sum(len(v) for v in by_address.values())}")
    print(f"  distinct reduced addresses: {n_addresses}")
    print(f"  addresses with >=2 calls (spread computable): {n_multi_call_addresses}")
    for t in SPREAD_THRESHOLDS_M:
        pct = 100 * over[t] / n_multi_call_addresses if n_multi_call_addresses else 0
        print(f"    spread > {t}m: {over[t]} addresses ({pct:.1f}% of multi-call addresses)")
    print(f"  near-duplicate string pairs (median coords within {NEAR_DUP_RADIUS_M}m): "
          f"{len(dup_pairs)}")
    print(f"  distinct addresses involved in >=1 near-duplicate pair: "
          f"{len(addresses_in_dup_pairs)} of {n_addresses} "
          f"({100 * len(addresses_in_dup_pairs) / n_addresses:.2f}%)")
    print(f"  wall time: {elapsed:.2f}s")
    if dup_pairs:
        print("  sample near-duplicate pairs (up to 8):")
        for a, b, d in sorted(dup_pairs, key=lambda p: p[2])[:8]:
            print(f"    {d:5.1f}m  {a!r}  <->  {b!r}")
    print()
    return {
        "n_addresses": n_addresses,
        "n_multi_call_addresses": n_multi_call_addresses,
        "over": over,
        "dup_pairs": len(dup_pairs),
        "addresses_in_dup_pairs": len(addresses_in_dup_pairs),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", dest="canonical_type", default="No Parking Sign",
                     help="canonical violation type to measure (default: %(default)s)")
    ap.add_argument("--baseline", default="Blocking Driveway",
                     help="canonical violation type to compare against (default: %(default)s)")
    args = ap.parse_args()

    conn = db.connect()
    baseline = report(conn, args.baseline)
    target = report(conn, args.canonical_type)

    print("=== comparison ===")
    for t in SPREAD_THRESHOLDS_M:
        print(f"  spread > {t}m share: {args.baseline} "
              f"{100 * baseline['over'][t] / max(baseline['n_multi_call_addresses'], 1):.1f}% "
              f"vs {args.canonical_type} "
              f"{100 * target['over'][t] / max(target['n_multi_call_addresses'], 1):.1f}%")
    print(f"  near-dup address share: {args.baseline} "
          f"{100 * baseline['addresses_in_dup_pairs'] / max(baseline['n_addresses'], 1):.2f}% "
          f"vs {args.canonical_type} "
          f"{100 * target['addresses_in_dup_pairs'] / max(target['n_addresses'], 1):.2f}%")


if __name__ == "__main__":
    main()
