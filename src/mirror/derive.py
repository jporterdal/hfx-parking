"""Derive doorway and block figures from the mirror instead of the live service.

Tasks 4.1 (selection, joining, outcome and vehicle attachment, address reduction,
doorway location) and 4.4 (census placement, closing D14a's retry asymmetry) of
`mirror-hrm-data-and-host-app`. `src/hotspots.py` stays stdlib-only, kept as
the live-service baseline 4.9 reconciles against (its pure functions are unmodified
by this change; only its board writer and default output paths were retired, under
task 8.7); this module reuses its pure
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
There is no fallback to a live fetch anywhere below (4.2): a record the selection
query names but the follow-up joins do not find is reported as missing (see
`missing_service_request_ids` below and `test_derive.py`'s 4.2 tests), never
retrieved from the network to paper over the gap.

`derive()`/`derive_all()` also read (never write) `layer_state`/`sync_runs` through
`mirror.status`/this module's own `reconciliation_state()`, so a caller can tell a
derivation run against a mirror that has not been shown to agree with the source
from one that has (4.3), and carry the derivation, sync and data clocks together on
every output (4.7). None of that changes what gets derived -- an unreconciled
mirror is still derived and reported as unreconciled, never topped up.

Run:  python3 src/mirror/derive.py /tmp/out                       # Driveway
      python3 src/mirror/derive.py /tmp/out --canonical-type "No Parking Sign"
      python3 src/mirror/derive.py /tmp/out --all-types            # every canonical
                                                                     # type, one
                                                                     # subdirectory
                                                                     # each (4.16)
"""

import argparse
import collections
import datetime
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotspots  # noqa: E402 -- pure functions only; its query()/fetch_blocks() are
                  # never called from here. See the module docstring's no-network note.
from mirror import db, status, violation_types  # noqa: E402

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


def load(conn, violation=None, canonical_type=None, ids_out=None):
    """Fetch every call in a violation selection, from the mirror.

    Exactly one of `violation` (case-insensitive substring match against `Alleged
    Violation`, reproducing `hotspots.load()`'s selection exactly) or
    `canonical_type` (exact membership from `violation_types.raw_labels_for`, task
    4.15's frozen list) must be given.

    Returns `(calls, fields)` in exactly the shape `hotspots.load()` returns them,
    so `hotspots.build()`/`roll_blocks()` can be called on the result unmodified.
    This return shape is a compatibility contract other modules build on (`derive`'s
    module docstring) and is kept fixed; `ids_out`, if given, is a `set` this
    function updates in place with the ids the selection query named, which is how
    `derive()` learns what a "record absent from the mirror" (4.2) would mean here
    without widening what `load()` hands back.

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
    if ids_out is not None:
        ids_out.update(ids)
    if not ids:
        return [], collections.defaultdict(dict)

    calls = _service_request_rows(conn, ids)
    fields = _parking_fields(conn, ids)
    return calls, fields


# --------------------------------------------------------- freshness (4.3, 4.7)


def reconciliation_state(conn, layers=status.CURRENCY_LAYERS):
    """Whether the mirror's currency layers were reconciled on their own most
    recent successful sync attempt (task 4.3). Read-only against `sync_runs` --
    the same `reconciled` column `sync.reconcile_layer`/`sync.full_reload` write
    (3.5) -- and never a check this module performs itself.

    A layer with no successful sync attempt on record, or whose latest one found a
    count divergence, makes the whole mirror unreconciled: a derivation cannot cite
    agreement it has not observed for even one currency layer. Census is excluded
    for the same reason `status.CURRENCY_LAYERS` excludes it from `mirror_freshness`
    -- it is static reference data, not a per-sync moving target.

    A successful row whose `reconciled` is NULL performed no count comparison at
    all (a no-op poll, or a run from before task 3.5 started recording one) -- it
    is not evidence of agreement *or* divergence, so it is skipped rather than
    read as the layer's current answer. The newest row that actually recorded an
    outcome decides; a row from before that one, successful or not, never gets a
    vote once a later recorded outcome exists.

    Returns `{"reconciled": bool, "per_layer": {layer: True | False | None}}`,
    `None` now meaning "no successful sync has ever recorded a reconciliation
    outcome for this layer", distinguished from an observed divergence (`False`).
    """
    per_layer = {}
    with conn.cursor() as cur:
        for layer in layers:
            cur.execute(
                "SELECT reconciled FROM sync_runs WHERE layer = %s AND ok "
                "AND reconciled IS NOT NULL ORDER BY started_at DESC LIMIT 1",
                (layer,),
            )
            row = cur.fetchone()
            per_layer[layer] = row[0] if row else None
    reconciled = bool(per_layer) and all(v is True for v in per_layer.values())
    return {"reconciled": reconciled, "per_layer": per_layer}


def derivation_metadata(conn):
    """The three clocks 4.7 asks every output to carry together, plus 4.3's
    reconciliation verdict -- gathered once per `derive()`/`derive_all()` call
    (never per canonical type) so a many-type run costs this query set once, not
    thirty times.

    `derived_at` is this module's own clock: when the derivation ran, not when the
    mirror last synced or when HRM last published. The other two come straight off
    `mirror.status`, read-only, which is the same source 3.3/3.4's four-clocks
    requirement is built on.
    """
    freshness = status.mirror_freshness(conn)
    return {
        "derived_at": datetime.datetime.now(datetime.UTC),
        "last_sync_success_at": freshness["last_success_at"],
        "most_recent_call_date": freshness["most_recent_call_date"],
        "reconciled": reconciliation_state(conn),
    }


# --------------------------------------------------------------- orchestration


def _finish(calls, fields, ids, blocks_geo, min_calls, recur_days, min_doorways,
           district):
    """The tail both `derive()` and `derive_all()` run once calls/fields/census are
    in hand: build doorways, filter by district, roll them into blocks, attach the
    neighbour count, and report which of `ids` never surfaced in `calls` (4.2's
    "absent, not fetched" -- a request id the selection query named but the
    `service_requests` join did not find).
    """
    found_ids = {c["REQUEST_ID"] for c in calls}
    missing_ids = sorted(ids - found_ids)
    if not calls:
        return {"rows": [], "blocks": [], "calls": calls, "fields": fields,
                "latest": None, "missing_service_request_ids": missing_ids}

    latest = max(
        hotspots.to_local(c["DATE_INITIATED"]) for c in calls if c["DATE_INITIATED"]
    )
    rows = hotspots.build(calls, fields, min_calls, recur_days, latest, blocks_geo)
    if district:
        rows = [r for r in rows if str(r["district"]) == str(district)]

    blocks = hotspots.roll_blocks(rows, min_doorways) if rows else []
    # Same neighbour-count attachment hotspots.py:main() does after roll_blocks().
    neighbours = {b["block"]: b["doorways"] for b in blocks}
    for r in rows:
        r["block_doorways_calling"] = neighbours.get(r["block"], 1)

    # 4.2: the other shape "absent, not fetched" takes here -- a doorway placed by
    # coordinates but matching no held census polygon. Already visible per row
    # (`row["block"]` is falsy), surfaced as a count here too.
    unmatched_census = sum(1 for r in rows if not r["block"])

    return {"rows": rows, "blocks": blocks, "calls": calls, "fields": fields,
            "latest": latest, "missing_service_request_ids": missing_ids,
            "unmatched_census_count": unmatched_census}


def derive(conn, violation=None, canonical_type=None, min_calls=2, recur_days=365,
           min_doorways=2, district=None):
    """Run the mirror-backed derivation end to end for one violation selection.

    Mirrors what `src/hotspots.py`'s `main()` does after `load()`/`fetch_blocks()`,
    reading everything from the mirror instead of the live service, and calling
    `hotspots.build()`/`roll_blocks()` unmodified. Returns a dict: `rows` (the
    doorway list), `blocks` (the block list), `calls`, `fields` and `latest` -- the
    same objects `hotspots.py:main()` assembles before writing output, so a caller
    (a CLI, a test, a future served view) can write or compare them the same way --
    plus `missing_service_request_ids`/`unmatched_census_count` (4.2) and
    `derived_at`/`last_sync_success_at`/`most_recent_call_date`/`reconciled` (4.3,
    4.7), which are metadata about the run rather than the figures themselves and
    are not attached to individual rows for exactly that reason: two derive() calls
    a second apart produce identical rows but a different `derived_at`.
    """
    ids = set()
    calls, fields = load(conn, violation=violation, canonical_type=canonical_type,
                         ids_out=ids)
    blocks_geo = census_blocks(conn) if calls else []
    result = _finish(calls, fields, ids, blocks_geo, min_calls, recur_days,
                     min_doorways, district)
    result.update(derivation_metadata(conn))
    return result


def derive_all(conn, types=None, min_calls=2, recur_days=365, min_doorways=2,
               district=None):
    """Derive every tracked canonical type from one pass over the mirror (4.16).

    Reads `custom_fields`, `service_requests`, `parking_call_attributes` and
    `census_areas` exactly once each, total, regardless of how many types are in
    `types` -- never once per type, which is what makes this different from calling
    `derive(canonical_type=...)` thirty times over. The one `Alleged Violation` read
    is partitioned in Python by `violation_types.canonical_type()` into
    `id_to_type`: a plain `dict`, so a request id lands under one canonical type at
    most, however many qualifying custom-field rows it has -- the invariant task
    4.16 asks for ("a call belongs to exactly one type") holds by construction, not
    by convention.

    Ordering matches calling `derive()` once per type: `_service_request_rows`
    already returns the whole selection ascending by `request_id`, and bucketing
    that single ordered list by type preserves ascending order within each
    bucket, which `hotspots.build()`/`roll_blocks()`'s order-dependent tie-breaks
    need to reproduce a lone `derive(canonical_type=...)` call exactly (see
    `tests/test_derive.py`'s equality test against `derive()`).

    Returns `{canonical_type: derive()-shaped dict}` for every name in `types`
    (default: every key of `violation_types.CANONICAL_TYPES`). Each dict has the
    same shape `derive()` returns, including its own `rows`/`blocks`/
    `missing_service_request_ids`/`unmatched_census_count`, and the same
    `derived_at`/`last_sync_success_at`/`most_recent_call_date`/`reconciled`
    metadata (4.3, 4.7) -- gathered once for the whole call, not per type, since it
    describes the mirror's state, which does not vary by canonical type.
    """
    if types is None:
        types = list(violation_types.CANONICAL_TYPES)
    types_set = set(types)

    id_to_type = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT request_id, custom_field_value FROM custom_fields "
            "WHERE custom_field_name = 'Alleged Violation'"
        )
        av_rows = cur.fetchall()
    for request_id, value in av_rows:
        canonical = violation_types.canonical_type(value)
        if canonical is not None and canonical in types_set:
            id_to_type[request_id] = canonical

    all_ids = set(id_to_type)
    calls = _service_request_rows(conn, all_ids)
    fields = _parking_fields(conn, all_ids)
    blocks_geo = census_blocks(conn)

    calls_by_type = collections.defaultdict(list)
    for call in calls:
        canonical = id_to_type.get(call["REQUEST_ID"])
        if canonical is not None:
            calls_by_type[canonical].append(call)

    ids_by_type = collections.defaultdict(set)
    for request_id, canonical in id_to_type.items():
        ids_by_type[canonical].add(request_id)

    metadata = derivation_metadata(conn)
    results = {}
    for canonical in types:
        type_calls = calls_by_type.get(canonical, [])
        type_ids = ids_by_type.get(canonical, set())
        # A per-type view of the shared fields read, not a re-query: exactly the
        # rows `_parking_fields(conn, type_ids)` would have returned on its own,
        # so a type's slice of this result matches `derive()`'s shape exactly
        # (`test_derive.py`'s equality test relies on this).
        type_fields = collections.defaultdict(
            dict, {rid: fields[rid] for rid in type_ids if rid in fields}
        )
        result = _finish(type_calls, type_fields, type_ids, blocks_geo, min_calls,
                         recur_days, min_doorways, district)
        result.update(metadata)
        results[canonical] = result
    return results


# ----------------------------------------------------------- output helpers (4.7)


def _fmt(value):
    return value.isoformat() if value else "unknown"


def _freshness_note(result):
    """One line carrying 4.7's three clocks together: when this derivation ran,
    when the mirror last synced successfully, and the most recent call the mirror
    holds. `result` is any `derive()`/`derive_all()`-shaped dict.
    """
    return (
        f"Derived {_fmt(result['derived_at'])} from the mirror | "
        f"last successful sync {_fmt(result['last_sync_success_at'])} | "
        f"most recent call in the data {_fmt(result['most_recent_call_date'])}."
    )


def _rows_with_freshness(rows, result):
    """A copy of `rows` (or `blocks`) with 4.7's three clocks attached to every
    row, for `hotspots.write_csv` only -- a CSV has no header/footer separate from
    its rows, so this is how the clocks reach the file at all. The in-memory
    `result["rows"]`/`result["blocks"]` stay untouched: see `derive()`'s docstring
    on why a call-time timestamp does not belong on the figures themselves.
    """
    stamp = {
        "derived_at": _fmt(result["derived_at"]),
        "last_sync_success_at": _fmt(result["last_sync_success_at"]),
        "most_recent_call_date": _fmt(result["most_recent_call_date"]),
    }
    return [dict(r, **stamp) for r in rows]


def _write_freshness_note(path, result):
    """Insert `_freshness_note` as one line right after a brief's title, once
    `hotspots.write_brief`/`write_block_brief` has already written it."""
    path = pathlib.Path(path)
    lines = path.read_text().splitlines()
    insert_at = 1 if lines and lines[0].startswith("#") else 0
    lines[insert_at:insert_at] = ["", _freshness_note(result)]
    path.write_text("\n".join(lines) + "\n")


def _slug(name):
    """A filesystem-safe subdirectory name for one canonical type, for
    `--all-types` (4.16): lowercase, runs of anything but a letter or digit
    collapsed to one hyphen, trimmed. 'Blocking Driveway' -> 'blocking-driveway'.
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "type"


def _write_outputs(result, label, out_dir, recur_days):
    """Write one type's watchlist/blocks CSV and brief into `out_dir`, stamped
    with 4.7's clocks. Returns `(addresses written, blocks written)`; `(0, 0)` and
    no directory created if the type had no rows, matching the single-type CLI
    path's prior behaviour of never creating `out_dir` for an empty result.
    """
    if not result["rows"]:
        return 0, 0
    out_dir.mkdir(parents=True, exist_ok=True)

    hotspots.write_csv(_rows_with_freshness(result["rows"], result),
                       str(out_dir / "watchlist.csv"))
    hotspots.write_brief(
        result["rows"], result["calls"], result["fields"], label,
        str(out_dir / "watchlist.md"), recur_days, result["latest"],
    )
    _write_freshness_note(out_dir / "watchlist.md", result)

    blocks_written = 0
    if result["blocks"]:
        hotspots.write_csv(_rows_with_freshness(result["blocks"], result),
                           str(out_dir / "blocks.csv"))
        hotspots.write_block_brief(
            result["blocks"], result["rows"], str(out_dir / "blocks.md")
        )
        _write_freshness_note(out_dir / "blocks.md", result)
        blocks_written = len(result["blocks"])

    return len(result["rows"]), blocks_written


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
    ap.add_argument(
        "--all-types", action="store_true",
        help="derive every canonical type from one pass over the mirror (4.16) "
             "and write each into its own subdirectory of out_dir, named by a "
             "slug of the type (e.g. out_dir/blocking-driveway/watchlist.csv)",
    )
    ap.add_argument("--min-calls", type=int, default=2,
                    help="minimum calls in the last 12 months")
    ap.add_argument("--recur-days", type=int, default=365)
    ap.add_argument("--district", help="limit the output to one district")
    ap.add_argument("--min-doorways", type=int, default=2,
                    help="minimum still-calling doorways for a block to be listed")
    args = ap.parse_args()

    given = sum(bool(v) for v in (args.violation, args.canonical_type, args.all_types))
    if given > 1:
        ap.error("give at most one of --violation, --canonical-type and --all-types")

    conn = db.connect()
    out_dir = pathlib.Path(args.out_dir)

    if args.all_types:
        print("deriving every canonical type from the mirror in one pass ...",
              file=sys.stderr)
        all_results = derive_all(
            conn, min_calls=args.min_calls, recur_days=args.recur_days,
            min_doorways=args.min_doorways, district=args.district,
        )
        wrote_any = False
        for canonical, result in all_results.items():
            type_dir = out_dir / _slug(canonical)
            addresses, blocks = _write_outputs(result, canonical, type_dir,
                                               args.recur_days)
            if addresses:
                wrote_any = True
                print(f"{canonical}: wrote {addresses} addresses, {blocks} blocks "
                      f"to {type_dir}")
            else:
                print(f"{canonical}: no addresses met the threshold",
                      file=sys.stderr)
        return 0 if wrote_any else 1

    violation = args.violation
    canonical_type = args.canonical_type
    if violation is None and canonical_type is None:
        violation = DEFAULT_VIOLATION
    label = violation if canonical_type is None else canonical_type

    print(f"deriving {label!r} from the mirror ...", file=sys.stderr)
    result = derive(
        conn, violation=violation, canonical_type=canonical_type,
        min_calls=args.min_calls, recur_days=args.recur_days,
        min_doorways=args.min_doorways, district=args.district,
    )
    if not result["rows"]:
        print("no addresses met the threshold", file=sys.stderr)
        return 1

    addresses, blocks = _write_outputs(result, label, out_dir, args.recur_days)
    print(f"wrote {out_dir / 'watchlist.csv'} and {out_dir / 'watchlist.md'} "
          f"({addresses} addresses)")
    if blocks:
        print(f"wrote {out_dir / 'blocks.csv'} and {out_dir / 'blocks.md'} "
              f"({blocks} blocks)")

    if result["unmatched_census_count"]:
        print(f"note: {result['unmatched_census_count']} addresses fell in no "
              f"census block", file=sys.stderr)
    if result["missing_service_request_ids"]:
        print(f"note: {len(result['missing_service_request_ids'])} selected "
              f"request ids had no service_requests row", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
