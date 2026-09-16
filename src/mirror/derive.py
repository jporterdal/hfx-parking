"""Derive doorway and block figures from the mirror instead of the live service.

Tasks 4.1 (selection, joining, outcome and vehicle attachment, address reduction,
doorway location) and 4.4 (census placement, closing D14a's retry asymmetry) of
`mirror-hrm-data-and-host-app`. `src/hotspots.py` stays stdlib-only and untouched as
the live-service baseline 4.9 reconciles against; this module reuses its pure
functions (`to_local`, `clean_address`, `street_of`, `in_block`, `find_block`,
`vehicle_key`, `build`, `roll_blocks`) rather than reimplementing them, and calls
`build()`/`roll_blocks()` unmodified on data shaped exactly like `hotspots.load()`
produces it. A discrepancy against the live run is then a mirror or derivation
defect, never a difference in logic (design.md M8).

No network call is made anywhere in this module: every read below is a Postgres
query against tables `src/mirror/load.py` already filled, never `mirror.source`'s
`pages`/`post`/`count`, and never `hotspots.query`/`hotspots.fetch_blocks` (the two
functions in `hotspots.py` that do reach the network — this module imports
`hotspots` only for its pure functions and never calls either). Census containment
stays `hotspots.in_block`/`find_block`'s even-odd ray cast in Python (`src/mirror/
db.py`'s module docstring); only the fetch moved, and it moved to one SELECT with
no retry and no paging, because Postgres does not need either -- that machinery
stays where it belongs, in the sync (`src/mirror/load.py`), closing D14a (4.4).

Run:  python3 src/mirror/derive.py /tmp/out                       # Driveway
      python3 src/mirror/derive.py /tmp/out --canonical-type "No Parking Sign"
"""

import argparse
import collections
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotspots  # noqa: E402 -- pure functions only; its query()/fetch_blocks() are
                  # never called from here. See the module docstring's no-network note.
from mirror import db, violation_types  # noqa: E402

DEFAULT_VIOLATION = "Driveway"

# The parking attributes hotspots.build()/vehicle_key() read out of `fields`, beyond
# the selection field (Alleged Violation) itself. Matches hotspots.py's VEHICLE_FIELDS
# plus the two scalar attributes load() also fetches.
_PARKING_ATTRIBUTE_COLUMNS = (
    ("vehicle_was_towed", "Vehicle Was Towed"),
    ("property_ownership", "Property Ownership"),
    ("vehicle_make", "Vehicle Make"),
    ("vehicle_model", "Vehicle Model"),
    ("vehicle_colour", "Vehicle Colour"),
)

SERVICE_REQUEST_COLUMNS = (
    "request_id", "date_initiated", "date_closed", "address", "community",
    "district", "resolution", "latitude", "longitude", "initiated_by",
)


# --------------------------------------------------------------- census (4.4)


def census_blocks(conn):
    """Every census dissemination area, shaped exactly like `hotspots.fetch_blocks()`
    returns it: one dict per area with `id`, `dwellings`, `people`, `rings` and a
    precomputed `box`, so `hotspots.in_block`/`find_block` run against it unchanged.

    One SELECT, no paging and no retry -- the mirror already holds every row (610
    at last load), and `load.py`'s CENSUS_SQL wrote `min_lon`/`min_lat`/`max_lon`/
    `max_lat` alongside `rings` at load time, so the bounding box is read rather
    than recomputed here.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT dauid, dwellings, population, rings, "
            "min_lon, min_lat, max_lon, max_lat FROM census_areas"
        )
        rows = cur.fetchall()
    blocks = []
    for dauid, dwellings, population, rings, min_lon, min_lat, max_lon, max_lat in rows:
        if not rings or min_lon is None:
            continue  # a row with no geometry can contain nothing; hotspots.fetch_blocks() skips it too
        blocks.append(
            {
                "id": dauid,
                "dwellings": dwellings or 0,
                "people": population or 0,
                "rings": rings,
                "box": (min_lon, min_lat, max_lon, max_lat),
            }
        )
    return blocks


# ------------------------------------------------------------- selection (4.1)


def _select_ids_by_substring(conn, violation):
    """Reproduces `hotspots.load()`'s selection exactly: every `request_id` carrying
    an `Alleged Violation` custom field whose value contains `violation` as a
    case-insensitive substring. Kept alongside the canonical-type selection below
    because task 4.1 requires Driveway's selection to remain identical to
    `hotspots.load("Driveway")`, and this is how that identity is proven, not
    merely asserted -- see `tests/test_derive.py`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT request_id FROM custom_fields "
            "WHERE custom_field_name = 'Alleged Violation' "
            "AND upper(custom_field_value) LIKE %s",
            (f"%{violation.upper()}%",),
        )
        return {r[0] for r in cur.fetchall()}


def _select_ids_by_canonical_type(conn, canonical_type):
    """Every `request_id` whose `Alleged Violation` value is one of the raw labels
    `violation_types.py` (task 4.15's frozen list) groups under this canonical type.
    Exact membership, not a substring: the frozen grouping already enumerates every
    raw label it covers, so a LIKE's fuzziness would only risk picking up a label
    the freeze deliberately left out (`AMBIGUOUS_LABELS`) or excluded outright
    (`EXCLUDED_TYPES`).
    """
    labels = list(violation_types.raw_labels_for(canonical_type))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT request_id FROM custom_fields "
            "WHERE custom_field_name = 'Alleged Violation' "
            "AND custom_field_value = ANY(%s)",
            (labels,),
        )
        return {r[0] for r in cur.fetchall()}


# --------------------------------------------------------- joining, outcome (4.1)


def _epoch_ms(dt):
    """The mirror stores `date_initiated`/`date_closed` as `timestamptz`; hotspots'
    pure functions (`to_local`, and therefore `build()`) expect epoch milliseconds,
    ArcGIS's own shape. `dt.timestamp()` is correct regardless of which tzinfo the
    driver attaches to the returned value -- the mirror's value came from
    `source.epoch_to_utc(epoch_ms)` (`src/mirror/source.py`) and multiplying back by
    1000 recovers it exactly.
    """
    if dt is None:
        return None
    return round(dt.timestamp() * 1000)


def _service_request_rows(conn, ids):
    """`service_requests` rows for a set of ids, as `hotspots.load()`'s `calls`
    shape: a list of dicts keyed by the same ArcGIS attribute names `hotspots.build`
    reads (`REQUEST_ID`, `DATE_INITIATED`, ..., `INITIATED_BY`).

    Ordered by `request_id` to match `hotspots.load()`'s own order exactly:
    `orderByFields="REQUEST_ID"` on each chunked query, chunks formed from a
    pre-sorted id list, so its `calls` list is globally ascending by REQUEST_ID.
    `hotspots.build()`'s tie-breaks are order-dependent -- Python's sort is stable,
    so which of several doorways with equal rank sorts first, and which of several
    tied doorways `roll_blocks()` names `worst_doorway` or credits first in
    `streets`, both fall out of the order addresses were first seen in `calls`.
    Matching that order here is what makes a mirror-derived CSV byte-identical to
    the live one rather than merely holding the same rows in a different order
    (task 4.9 diffs the two directly).
    """
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(SERVICE_REQUEST_COLUMNS)} FROM service_requests "
            "WHERE request_id = ANY(%s) ORDER BY request_id",
            (list(ids),),
        )
        rows = cur.fetchall()
    calls = []
    for request_id, initiated, closed, address, community, district, resolution, \
            lat, lon, initiated_by in rows:
        calls.append(
            {
                "REQUEST_ID": request_id,
                "DATE_INITIATED": _epoch_ms(initiated),
                "DATE_CLOSED": _epoch_ms(closed),
                "ADDRESS": address,
                "COMMUNITY": community,
                "DISTRICT": district,
                "RESOLUTION": resolution,
                "LATITUDE": lat,
                "LONGITUDE": lon,
                "INITIATED_BY": initiated_by,
            }
        )
    return calls


def _parking_fields(conn, ids):
    """`request_id -> {custom field name: value}` for the vehicle and outcome
    attributes `hotspots.build()`/`vehicle_key()` read, in `hotspots.load()`'s
    `fields` shape. Reads the mirror's `parking_call_attributes` view
    (`schema.sql`), which pivots the key-value store the same way `hotspots.load()`'s
    second fetch does -- once for the whole selection here, rather than once per
    id-chunk over the network.

    A column that is NULL is simply omitted from a request's field map rather than
    stored as `None`, which is behaviourally identical for every read `build()`
    makes (`.get(name)` returns `None` either way) and avoids `defaultdict` entries
    for calls that were never selected.
    """
    fields = collections.defaultdict(dict)
    if not ids:
        return fields
    columns = ", ".join(col for col, _name in _PARKING_ATTRIBUTE_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT request_id, {columns} FROM parking_call_attributes "
            "WHERE request_id = ANY(%s)",
            (list(ids),),
        )
        rows = cur.fetchall()
    for row in rows:
        request_id, *values = row
        field_map = fields[request_id]
        for (_col, name), value in zip(_PARKING_ATTRIBUTE_COLUMNS, values):
            if value is not None:
                field_map[name] = value
    return fields


def load(conn, violation=None, canonical_type=None):
    """Fetch every call in a violation selection, from the mirror.

    Exactly one of `violation` (case-insensitive substring match against `Alleged
    Violation`, reproducing `hotspots.load()`'s selection exactly) or
    `canonical_type` (exact membership from `violation_types.raw_labels_for`, task
    4.15's frozen list) must be given.

    Returns `(calls, fields)` in exactly the shape `hotspots.load()` returns them,
    so `hotspots.build()`/`roll_blocks()` can be called on the result unmodified.

    No network call: every read here is a Postgres query against tables `load.py`
    already filled. This is what makes task 4.1's no-network requirement true of
    the whole derivation, not just this function -- see
    `tests/test_derive.py::test_derivation_module_never_imports_source_fetch_functions`.
    """
    if (violation is None) == (canonical_type is None):
        raise ValueError("give exactly one of violation or canonical_type")

    ids = (
        _select_ids_by_substring(conn, violation)
        if violation is not None
        else _select_ids_by_canonical_type(conn, canonical_type)
    )
    if not ids:
        return [], collections.defaultdict(dict)

    calls = _service_request_rows(conn, ids)
    fields = _parking_fields(conn, ids)
    return calls, fields


# --------------------------------------------------------------- orchestration


def derive(conn, violation=None, canonical_type=None, min_calls=2, recur_days=365,
           min_doorways=2, district=None):
    """Run the mirror-backed derivation end to end for one violation selection.

    Mirrors what `src/hotspots.py`'s `main()` does after `load()`/`fetch_blocks()`,
    reading everything from the mirror instead of the live service, and calling
    `hotspots.build()`/`roll_blocks()` unmodified. Returns a dict: `rows` (the
    doorway list), `blocks` (the block list), `calls`, `fields` and `latest` -- the
    same objects `hotspots.py:main()` assembles before writing output, so a caller
    (a CLI, a test, a future served view) can write or compare them the same way.
    """
    calls, fields = load(conn, violation=violation, canonical_type=canonical_type)
    if not calls:
        return {"rows": [], "blocks": [], "calls": calls, "fields": fields, "latest": None}

    latest = max(
        hotspots.to_local(c["DATE_INITIATED"]) for c in calls if c["DATE_INITIATED"]
    )
    blocks_geo = census_blocks(conn)
    rows = hotspots.build(calls, fields, min_calls, recur_days, latest, blocks_geo)
    if district:
        rows = [r for r in rows if str(r["district"]) == str(district)]

    blocks = hotspots.roll_blocks(rows, min_doorways) if rows else []
    # Same neighbour-count attachment hotspots.py:main() does after roll_blocks().
    neighbours = {b["block"]: b["doorways"] for b in blocks}
    for r in rows:
        r["block_doorways_calling"] = neighbours.get(r["block"], 1)

    return {"rows": rows, "blocks": blocks, "calls": calls, "fields": fields, "latest": latest}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "out_dir",
        help="directory to write watchlist.csv, watchlist.md, blocks.csv and "
             "blocks.md into (created if missing). Never defaults to out/.",
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
    ap.add_argument("--min-calls", type=int, default=2,
                    help="minimum calls in the last 12 months")
    ap.add_argument("--recur-days", type=int, default=365)
    ap.add_argument("--district", help="limit the output to one district")
    ap.add_argument("--min-doorways", type=int, default=2,
                    help="minimum still-calling doorways for a block to be listed")
    args = ap.parse_args()

    if args.violation and args.canonical_type:
        ap.error("give at most one of --violation and --canonical-type")
    violation = args.violation
    canonical_type = args.canonical_type
    if violation is None and canonical_type is None:
        violation = DEFAULT_VIOLATION
    label = violation if canonical_type is None else canonical_type

    conn = db.connect()
    print(f"deriving {label!r} from the mirror ...", file=sys.stderr)
    result = derive(
        conn, violation=violation, canonical_type=canonical_type,
        min_calls=args.min_calls, recur_days=args.recur_days,
        min_doorways=args.min_doorways, district=args.district,
    )
    if not result["rows"]:
        print("no addresses met the threshold", file=sys.stderr)
        return 1

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    hotspots.write_csv(result["rows"], str(out_dir / "watchlist.csv"))
    hotspots.write_brief(
        result["rows"], result["calls"], result["fields"], label,
        str(out_dir / "watchlist.md"), args.recur_days, result["latest"],
    )
    print(f"wrote {out_dir / 'watchlist.csv'} and {out_dir / 'watchlist.md'} "
          f"({len(result['rows'])} addresses)")

    if result["blocks"]:
        hotspots.write_csv(result["blocks"], str(out_dir / "blocks.csv"))
        hotspots.write_block_brief(
            result["blocks"], result["rows"], str(out_dir / "blocks.md")
        )
        print(f"wrote {out_dir / 'blocks.csv'} and {out_dir / 'blocks.md'} "
              f"({len(result['blocks'])} blocks)")

    unmatched = sum(1 for r in result["rows"] if not r["block"])
    if unmatched:
        print(f"note: {unmatched} addresses fell in no census block", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
