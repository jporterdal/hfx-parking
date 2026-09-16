"""Row-level reconciliation: does the mirror hold what the live service holds?

Task 3.6. Verifies the mirror's `Driveway` call set and its custom-field rows are
identical to the live service's for the same selection — same request ids, same
field values, same counts. Provable with the mirror alone, before anything downstream
depends on it; figure-level reconciliation against a derivation is 4.9.

The selection reproduces `src/hotspots.py:load()` exactly: every `REQUESTID` carrying
a custom field named `Alleged Violation` whose value contains the violation label as
a case-insensitive substring — `Driveway` matches both `"DRIVEWAY"` and
`"Blocking Driveway (DISPATCH)"`.

Every comparison is keyed by a business key, never by `ObjectId`: a request by its
own `REQUEST_ID`, a custom field row by `(request_id, custom_field_id)` — the same
pair `sync.VERSION_SAMPLE` uses, because `ObjectId` is reassigned on every publish
(design.md M2) and comparing it between a live query and a mirror loaded from an
earlier version would be comparing two unrelated row counters.

`freshness()` checks first whether the source has published since the mirror was
loaded, by comparing each layer's live `editingInfo.lastEditDate` against
`layer_state`. If it has, a mismatch below is expected, not a defect — this module
never syncs or reloads to make one go away; it only reports.

Run:  python3 src/mirror/reconcile.py                      # Driveway
      python3 src/mirror/reconcile.py --violation "No Parking Sign"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror import db, source  # noqa: E402

VIOLATION = "Driveway"

# The same chunk size src/hotspots.py:load() uses for `REQUEST_ID IN (...)` queries.
CHUNK_SIZE = 300

CITYWORKS_LAYERS = ("service_requests", "custom_fields")


def identity(value):
    return value


# (ArcGIS attribute, mirror column, transform applied to the live value before
# comparison). request_id/REQUEST_ID is the key, not a compared field, and is not
# listed here.
SERVICE_REQUEST_FIELDS = (
    ("DATE_INITIATED", "date_initiated", source.epoch_to_utc),
    ("DATE_CLOSED", "date_closed", source.epoch_to_utc),
    ("DESCRIPTION", "description", identity),
    ("INITIATED_BY", "initiated_by", identity),
    ("PRIORITY", "priority", identity),
    ("ADDRESS", "address", identity),
    ("COMMUNITY", "community", identity),
    ("DISTRICT", "district", identity),
    ("REQUEST_CATEGORY", "request_category", identity),
    ("RESOLUTION", "resolution", identity),
    ("LATITUDE", "latitude", identity),
    ("LONGITUDE", "longitude", identity),
    ("STATUS", "status", identity),
    ("DEPT_RESPONSIBILITY", "dept_responsibility", identity),
    ("WORK_ORDER", "work_order", identity),
)


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------- freshness


def freshness(conn, layers=CITYWORKS_LAYERS):
    """Live `editingInfo.lastEditDate` against what `layer_state` holds, per layer.

    `advanced` true means HRM has published since the mirror was loaded: a row-level
    mismatch below is then expected, not a defect. This function only observes; it
    never triggers a sync or reload.
    """
    results = {}
    with conn.cursor() as cur:
        for key in layers:
            cur.execute(
                "SELECT source_last_edit FROM layer_state WHERE layer = %s", (key,)
            )
            row = cur.fetchone()
            mirror_edit = row[0] if row else None
            live_edit = source.last_edit_date(source.LAYERS[key])
            results[key] = {
                "mirror_last_edit": mirror_edit,
                "live_last_edit": live_edit,
                "advanced": bool(
                    live_edit and mirror_edit and live_edit > mirror_edit
                ),
            }
    return results


# --------------------------------------------------------------- live reads


def live_driveway_selection(violation=VIOLATION):
    """The `Alleged Violation` custom-field rows matching `violation`, as the live
    service holds them right now. Reproduces `src/hotspots.py:load()`'s `tagged`
    query, keyed by `(request_id, custom_field_id)`.
    """
    layer = source.LAYERS["custom_fields"]
    where = (
        "CUSTOM_FIELD_NAME='Alleged Violation' "
        f"AND UPPER(CUSTOM_FIELD_VALUE) LIKE '%{violation.upper()}%'"
    )
    rows = {}
    for _, features in source.pages(layer, where=where):
        for f in features:
            a = f["attributes"]
            rows[(a["REQUESTID"], a.get("CUSTOM_FIELD_ID"))] = a
    return rows


def live_service_requests(ids, chunk_size=CHUNK_SIZE):
    """service_requests rows for a set of request ids, as the live service holds
    them now. Chunked `REQUEST_ID IN (...)` queries, same shape as `hotspots.load()`.
    """
    layer = source.LAYERS["service_requests"]
    rows = {}
    for chunk in chunked(sorted(ids), chunk_size):
        where = f"REQUEST_ID IN ({','.join(str(i) for i in chunk)})"
        for _, features in source.pages(layer, where=where):
            for f in features:
                a = f["attributes"]
                rows[a["REQUEST_ID"]] = a
    return rows


def live_custom_fields(ids, chunk_size=CHUNK_SIZE):
    """Every custom-field row for a set of request ids — not only the seven parking
    attributes `hotspots.load()` reads, so this checks the layer 1.3 promises to hold
    in full, keyed by `(request_id, custom_field_id)`.
    """
    layer = source.LAYERS["custom_fields"]
    rows = {}
    for chunk in chunked(sorted(ids), chunk_size):
        where = f"REQUESTID IN ({','.join(str(i) for i in chunk)})"
        for _, features in source.pages(layer, where=where):
            for f in features:
                a = f["attributes"]
                rows[(a["REQUESTID"], a.get("CUSTOM_FIELD_ID"))] = a
    return rows


# ------------------------------------------------------------- mirror reads


def mirror_driveway_ids(conn, violation=VIOLATION):
    """The same selection, read from the mirror: read-only, never written to."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT request_id FROM custom_fields "
            "WHERE custom_field_name = 'Alleged Violation' "
            "AND upper(custom_field_value) LIKE %s",
            (f"%{violation.upper()}%",),
        )
        return {r[0] for r in cur.fetchall()}


def mirror_service_requests(conn, ids):
    if not ids:
        return {}
    columns = ", ".join(col for _, col, _ in SERVICE_REQUEST_FIELDS)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT request_id, {columns} FROM service_requests "
            "WHERE request_id = ANY(%s)",
            (list(ids),),
        )
        names = [d.name for d in cur.description]
        return {row[0]: dict(zip(names, row)) for row in cur.fetchall()}


def mirror_custom_fields(conn, ids):
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT request_id, custom_field_id, custom_field_name, "
            "custom_field_value FROM custom_fields WHERE request_id = ANY(%s)",
            (list(ids),),
        )
        return {
            (request_id, field_id): {"name": name, "value": value}
            for request_id, field_id, name, value in cur.fetchall()
        }


# --------------------------------------------------------------------- diff


def diff_ids(live_ids, mirror_ids):
    return {
        "live_count": len(live_ids),
        "mirror_count": len(mirror_ids),
        "missing_from_mirror": sorted(live_ids - mirror_ids),
        "missing_from_live": sorted(mirror_ids - live_ids),
    }


def diff_service_requests(live_rows, mirror_rows):
    """`live_rows`: request_id -> ArcGIS attributes. `mirror_rows`: request_id ->
    mirror row dict, as `mirror_service_requests` returns it."""
    live_ids, mirror_ids = set(live_rows), set(mirror_rows)
    mismatches = []
    for rid in sorted(live_ids & mirror_ids):
        lv, mv = live_rows[rid], mirror_rows[rid]
        for live_field, mirror_field, transform in SERVICE_REQUEST_FIELDS:
            lval = transform(lv.get(live_field))
            mval = mv.get(mirror_field)
            if lval != mval:
                mismatches.append(
                    {
                        "request_id": rid,
                        "field": mirror_field,
                        "live": lval,
                        "mirror": mval,
                    }
                )
    return {
        "live_count": len(live_ids),
        "mirror_count": len(mirror_ids),
        "missing_from_mirror": sorted(live_ids - mirror_ids),
        "missing_from_live": sorted(mirror_ids - live_ids),
        "field_mismatches": mismatches,
    }


def diff_custom_fields(live_rows, mirror_rows):
    """Both keyed by `(request_id, custom_field_id)`. `live_rows` carries raw ArcGIS
    attributes (as `live_custom_fields` returns them); `mirror_rows` carries
    `{"name": ..., "value": ...}` (as `mirror_custom_fields` returns them)."""
    live = {
        key: {"name": a.get("CUSTOM_FIELD_NAME"), "value": a.get("CUSTOM_FIELD_VALUE")}
        for key, a in live_rows.items()
    }
    live_keys, mirror_keys = set(live), set(mirror_rows)
    mismatches = []
    for key in sorted(live_keys & mirror_keys, key=lambda k: (k[0], k[1] or 0)):
        lv, mv = live[key], mirror_rows[key]
        if lv["name"] != mv["name"]:
            mismatches.append(
                {
                    "key": key,
                    "field": "custom_field_name",
                    "live": lv["name"],
                    "mirror": mv["name"],
                }
            )
        if lv["value"] != mv["value"]:
            mismatches.append(
                {
                    "key": key,
                    "field": "custom_field_value",
                    "live": lv["value"],
                    "mirror": mv["value"],
                }
            )
    return {
        "live_count": len(live_keys),
        "mirror_count": len(mirror_keys),
        "missing_from_mirror": sorted(live_keys - mirror_keys),
        "missing_from_live": sorted(mirror_keys - live_keys),
        "field_mismatches": mismatches,
    }


# ------------------------------------------------------------- orchestration


def reconcile(conn, violation=VIOLATION, chunk_size=CHUNK_SIZE, log=lambda msg: None):
    """Run the full row-level reconciliation for one violation type.

    Reads the mirror; never writes to it. Reads the live service; never syncs or
    reloads it, regardless of what `freshness()` finds.
    """
    log("checking freshness ...")
    fresh = freshness(conn)

    log(f"fetching the live {violation!r} selection ...")
    tagged = live_driveway_selection(violation)
    live_ids = {rid for rid, _ in tagged}
    log(f"  {len(live_ids):,} calls, {len(tagged):,} 'Alleged Violation' rows")

    mirror_ids = mirror_driveway_ids(conn, violation)
    id_diff = diff_ids(live_ids, mirror_ids)

    all_ids = live_ids | mirror_ids

    log(f"fetching live service_requests rows for {len(all_ids):,} ids ...")
    live_sr = live_service_requests(all_ids, chunk_size)
    mirror_sr = mirror_service_requests(conn, all_ids)
    sr_diff = diff_service_requests(live_sr, mirror_sr)

    log(f"fetching live custom_fields rows for {len(all_ids):,} ids ...")
    live_cf = live_custom_fields(all_ids, chunk_size)
    mirror_cf = mirror_custom_fields(conn, all_ids)
    cf_diff = diff_custom_fields(live_cf, mirror_cf)

    return {
        "violation": violation,
        "freshness": fresh,
        "ids": id_diff,
        "service_requests": sr_diff,
        "custom_fields": cf_diff,
    }


def is_clean(result):
    """True when every id, row and field agreed."""
    checks = (result["service_requests"], result["custom_fields"])
    return (
        not result["ids"]["missing_from_mirror"]
        and not result["ids"]["missing_from_live"]
        and all(
            not c["missing_from_mirror"]
            and not c["missing_from_live"]
            and not c["field_mismatches"]
            for c in checks
        )
    )


def _truncated(items, n=20):
    shown = items[:n]
    more = f" ... ({len(items) - n} more)" if len(items) > n else ""
    return f"{shown}{more}"


def format_report(result):
    lines = [f"Row-level reconciliation: violation={result['violation']!r}", ""]

    lines.append("Freshness (live editingInfo.lastEditDate vs mirror layer_state):")
    for layer_key, f in result["freshness"].items():
        status = (
            "ADVANCED — HRM has published since the mirror was loaded"
            if f["advanced"]
            else "current"
        )
        lines.append(
            f"  {layer_key:18} mirror={f['mirror_last_edit']}  "
            f"live={f['live_last_edit']}  {status}"
        )
    lines.append("")

    ids = result["ids"]
    lines.append(
        f"Call set (Alleged Violation LIKE '%{result['violation'].upper()}%'):"
    )
    lines.append(f"  live {ids['live_count']:,}  mirror {ids['mirror_count']:,}")
    if ids["missing_from_mirror"]:
        lines.append(
            f"  missing from mirror ({len(ids['missing_from_mirror'])}): "
            f"{_truncated(ids['missing_from_mirror'])}"
        )
    if ids["missing_from_live"]:
        lines.append(
            f"  missing from live ({len(ids['missing_from_live'])}): "
            f"{_truncated(ids['missing_from_live'])}"
        )
    if not ids["missing_from_mirror"] and not ids["missing_from_live"]:
        lines.append("  identical")
    lines.append("")

    for label, key in (
        ("service_requests rows", "service_requests"),
        ("custom_fields rows", "custom_fields"),
    ):
        d = result[key]
        lines.append(f"{label}:")
        lines.append(f"  live {d['live_count']:,}  mirror {d['mirror_count']:,}")
        if d["missing_from_mirror"]:
            lines.append(
                f"  missing from mirror ({len(d['missing_from_mirror'])}): "
                f"{_truncated(d['missing_from_mirror'])}"
            )
        if d["missing_from_live"]:
            lines.append(
                f"  missing from live ({len(d['missing_from_live'])}): "
                f"{_truncated(d['missing_from_live'])}"
            )
        if d["field_mismatches"]:
            lines.append(f"  field mismatches: {len(d['field_mismatches']):,}")
            for m in d["field_mismatches"][:20]:
                lines.append(f"    {m}")
            if len(d["field_mismatches"]) > 20:
                lines.append(f"    ... ({len(d['field_mismatches']) - 20} more)")
        if not (
            d["missing_from_mirror"] or d["missing_from_live"] or d["field_mismatches"]
        ):
            lines.append("  identical")
        lines.append("")

    lines.append("RECONCILED" if is_clean(result) else "DIVERGED")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--violation", default=VIOLATION,
        help=f"violation label to reconcile, matched as a substring (default: {VIOLATION!r})",
    )
    ap.add_argument(
        "--chunk-size", type=int, default=CHUNK_SIZE,
        help=f"REQUEST_ID IN (...) chunk size for live queries (default: {CHUNK_SIZE})",
    )
    args = ap.parse_args()

    conn = db.connect()
    result = reconcile(
        conn, args.violation, args.chunk_size,
        log=lambda msg: print(msg, file=sys.stderr),
    )
    print()
    print(format_report(result))
    return 0 if is_clean(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
