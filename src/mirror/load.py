"""Load the mirror by offset paging, resumably.

Each page's rows and the offset that follows them are committed together, so a load
that dies partway resumes at the first page that did not commit rather than at zero.
Rows are keyed by the source's own object id and written with ON CONFLICT DO UPDATE,
so re-reading a page costs time and changes nothing.

Observed on 2026-09-15, loading all three layers into Postgres 17.10 serially:

    layer              pages      rows        time     on disk (heap + indexes)
    census_areas           4       610         3.9 s     2.5 MB
    custom_fields      1,157 1,156,710     1,071.9 s   223.1 MB
    service_requests     476   475,458       431.9 s   150.0 MB   (3,000 rows already held)
    total              1,637 1,632,778     1,510.0 s   375.5 MB

A page costs about 0.9 s rather than the 0.52 s the design predicted, and the cost
is the fetch, not the write: 1.06 s per custom-fields page and 0.77 s per requests
page measured without writing anything.

Run:  python3 src/mirror/load.py                 # every layer, resuming if interrupted
      python3 src/mirror/load.py --layer census_areas
      python3 src/mirror/load.py --restart        # discard progress and page from zero
      python3 src/mirror/load.py --report         # stored vs the service's own counts

Every mode but `--report` takes the mirror's sync lock first (`db.sync_lock`): if another
sync, load or reload is running it prints so, changes nothing and exits 75.
"""

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror import db, source  # noqa: E402

SERVICE_REQUEST_SQL = """
INSERT INTO {table} (
    object_id, request_id, date_initiated, date_closed, description, initiated_by,
    priority, address, community, district, request_category, resolution,
    latitude, longitude, status, dept_responsibility, work_order, mirrored_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (object_id) DO UPDATE SET
    request_id = EXCLUDED.request_id,
    date_initiated = EXCLUDED.date_initiated,
    date_closed = EXCLUDED.date_closed,
    description = EXCLUDED.description,
    initiated_by = EXCLUDED.initiated_by,
    priority = EXCLUDED.priority,
    address = EXCLUDED.address,
    community = EXCLUDED.community,
    district = EXCLUDED.district,
    request_category = EXCLUDED.request_category,
    resolution = EXCLUDED.resolution,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    status = EXCLUDED.status,
    dept_responsibility = EXCLUDED.dept_responsibility,
    work_order = EXCLUDED.work_order,
    mirrored_at = now()
"""

CUSTOM_FIELD_SQL = """
INSERT INTO {table} (
    object_id, request_id, custom_field_id, custom_field_name, custom_field_value,
    mirrored_at)
VALUES (%s, %s, %s, %s, %s, now())
ON CONFLICT (object_id) DO UPDATE SET
    request_id = EXCLUDED.request_id,
    custom_field_id = EXCLUDED.custom_field_id,
    custom_field_name = EXCLUDED.custom_field_name,
    custom_field_value = EXCLUDED.custom_field_value,
    mirrored_at = now()
"""

CENSUS_SQL = """
INSERT INTO {table} (
    object_id, dauid, population, dwellings, usual_dwellings, area, pop_density,
    rep_lat, rep_lon, csd_name, cd_name, rings,
    min_lon, min_lat, max_lon, max_lat, mirrored_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (object_id) DO UPDATE SET
    dauid = EXCLUDED.dauid,
    population = EXCLUDED.population,
    dwellings = EXCLUDED.dwellings,
    usual_dwellings = EXCLUDED.usual_dwellings,
    area = EXCLUDED.area,
    pop_density = EXCLUDED.pop_density,
    rep_lat = EXCLUDED.rep_lat,
    rep_lon = EXCLUDED.rep_lon,
    csd_name = EXCLUDED.csd_name,
    cd_name = EXCLUDED.cd_name,
    rings = EXCLUDED.rings,
    min_lon = EXCLUDED.min_lon,
    min_lat = EXCLUDED.min_lat,
    max_lon = EXCLUDED.max_lon,
    max_lat = EXCLUDED.max_lat,
    mirrored_at = now()
"""


def service_request_row(feature):
    a = feature["attributes"]
    return (
        a["ObjectId"],
        a["REQUEST_ID"],
        source.epoch_to_utc(a.get("DATE_INITIATED")),
        source.epoch_to_utc(a.get("DATE_CLOSED")),
        a.get("DESCRIPTION"),
        a.get("INITIATED_BY"),
        a.get("PRIORITY"),
        a.get("ADDRESS"),
        a.get("COMMUNITY"),
        a.get("DISTRICT"),
        a.get("REQUEST_CATEGORY"),
        a.get("RESOLUTION"),
        a.get("LATITUDE"),
        a.get("LONGITUDE"),
        a.get("STATUS"),
        a.get("DEPT_RESPONSIBILITY"),
        a.get("WORK_ORDER"),
    )


def custom_field_row(feature):
    a = feature["attributes"]
    return (
        a["ObjectId"],
        a["REQUESTID"],
        a.get("CUSTOM_FIELD_ID"),
        a.get("CUSTOM_FIELD_NAME"),
        a.get("CUSTOM_FIELD_VALUE"),
    )


def census_row(feature):
    a = feature["attributes"]
    rings = (feature.get("geometry") or {}).get("rings") or []
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    box = (min(xs), min(ys), max(xs), max(ys)) if xs else (None, None, None, None)
    return (
        a["OBJECTID"],
        a.get("DAUID"),
        a.get("DAPOP2021"),
        a.get("DATDWELL20"),
        a.get("DAURDWELL2"),
        a.get("DAAREA"),
        a.get("DAPOPDEN"),
        a.get("DARPLAT"),
        a.get("DARPLONG"),
        a.get("CSDNAME"),
        a.get("CDNAME"),
        json.dumps(rings),
        box[0],
        box[1],
        box[2],
        box[3],
    )


WRITERS = {
    "service_requests": (SERVICE_REQUEST_SQL, service_request_row),
    "custom_fields": (CUSTOM_FIELD_SQL, custom_field_row),
    "census_areas": (CENSUS_SQL, census_row),
}


def write_page(conn, layer, features):
    """Write one page into whichever table this layer is currently filling.

    The table is a parameter because a reload fills a staging table with exactly
    these rows and swaps it in afterwards; the row builders do not change.
    """
    sql, to_row = WRITERS[layer.base_key]
    with conn.cursor() as cur:
        cur.executemany(sql.format(table=layer.table), [to_row(f) for f in features])


def stored_count(conn, layer):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {layer.table}")
        return cur.fetchone()[0]


def highest_object_id(conn, layer):
    """The largest object id held. An observation about the version, nothing more:
    the source reassigns object ids on each publish, so this is not a resume point."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT coalesce(max(object_id), 0) FROM {layer.table}")
        return cur.fetchone()[0]


def progress_of(conn, layer, page_size, source_count, restart=False):
    """The offset this load starts from, created or reset as asked."""
    with conn.cursor() as cur:
        if restart:
            cur.execute("DELETE FROM load_progress WHERE layer = %s", (layer.key,))
        cur.execute(
            """
            INSERT INTO load_progress (layer, page_size, source_count)
            VALUES (%s, %s, %s)
            ON CONFLICT (layer) DO UPDATE SET
                source_count = EXCLUDED.source_count,
                updated_at = now()
            RETURNING next_offset, pages_completed, rows_loaded, page_size
            """,
            (layer.key, page_size, source_count),
        )
        next_offset, pages_completed, rows_loaded, stored_page_size = cur.fetchone()
    conn.commit()
    if stored_page_size != page_size and next_offset:
        raise RuntimeError(
            f"{layer.key}: resuming at offset {next_offset} recorded with page size "
            f"{stored_page_size}, but this load uses {page_size}. "
            f"Re-run with --page-size {stored_page_size} or --restart."
        )
    return next_offset, pages_completed, rows_loaded


def start_run(conn, kind, layer_key, source_count):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sync_runs (kind, layer, source_count) VALUES (%s, %s, %s) "
            "RETURNING id",
            (kind, layer_key, source_count),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id, ok, **fields):
    sets = ", ".join(f"{k} = %s" for k in fields)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE sync_runs SET finished_at = now(), ok = %s"
            + (f", {sets}" if fields else "")
            + " WHERE id = %s",
            [ok, *fields.values(), run_id],
        )
    conn.commit()


def load_layer(conn, layer, page_size=None, restart=False, limit_pages=None,
               log=lambda msg: print(msg, file=sys.stderr), record_state=True):
    """Page a layer to exhaustion, committing progress with every page.

    `record_state` is false when the rows are landing in a staging table: a staging
    fill has not replaced anything yet, so writing its counts and its clocks into
    `layer_state` would report the mirror as holding a version it has not yet sworn
    in. `sync.full_reload` records the state itself, after the swap.
    """
    size = page_size or layer.page_size
    src_count = source.count(layer)
    requests = 1
    offset, pages_done, rows_before = progress_of(conn, layer, size, src_count, restart)
    run_id = start_run(conn, "full_load", layer.key, src_count)
    started = time.monotonic()
    rows_this_run, pages_this_run, exhausted = 0, 0, False

    log(f"{layer.key}: {src_count:,} rows at the source, resuming at offset {offset:,}")
    try:
        for page_offset, features in source.pages(layer, offset, size):
            requests += 1
            with conn.cursor() as cur:
                if features:
                    write_page(conn, layer, features)
                cur.execute(
                    """
                    UPDATE load_progress
                    SET next_offset = %s,
                        pages_completed = pages_completed + 1,
                        rows_loaded = rows_loaded + %s,
                        updated_at = now()
                    WHERE layer = %s
                    """,
                    (page_offset + len(features), len(features), layer.key),
                )
            conn.commit()  # The page and the offset that follows it land together.
            rows_this_run += len(features)
            pages_this_run += 1
            if pages_this_run % 50 == 0:
                log(f"  {layer.key}: {pages_this_run:,} pages, {rows_this_run:,} rows")
            if len(features) < size:
                exhausted = True
                break
            if limit_pages and pages_this_run >= limit_pages:
                log(f"  {layer.key}: stopping after {pages_this_run} pages as asked")
                break
    except Exception as exc:
        conn.rollback()  # the failing page rolls back; the pages before it stand
        # rows_updated is 0 here for the same reason as the success path below: a
        # resumed load never re-reads a page it already committed, so nothing this
        # run has written was a row already held. See the note in `sync.full_reload`
        # for the wholesale-replace case (3.1).
        finish_run(conn, run_id, False, pages_fetched=pages_this_run,
                   rows_inserted=rows_this_run, rows_updated=0, error=str(exc)[:2000])
        raise

    elapsed = time.monotonic() - started
    held = stored_count(conn, layer)
    highest = highest_object_id(conn, layer)
    source_last_edit = source.last_edit_date(layer)
    requests += 1  # the last-edit metadata read
    with conn.cursor() as cur:
        if exhausted:
            cur.execute(
                "UPDATE load_progress SET completed_at = now(), updated_at = now() "
                "WHERE layer = %s",
                (layer.key,),
            )
        if record_state:
            cur.execute(
                """
                INSERT INTO layer_state (
                    layer, watermark, source_count, stored_count, source_last_edit,
                    full_load_completed_at, last_attempt_at, last_attempt_ok,
                    last_success_at, static)
                VALUES (%s, %s, %s, %s, %s, %s, now(), true, now(), %s)
                ON CONFLICT (layer) DO UPDATE SET
                    watermark = EXCLUDED.watermark,
                    source_count = EXCLUDED.source_count,
                    stored_count = EXCLUDED.stored_count,
                    source_last_edit = EXCLUDED.source_last_edit,
                    full_load_completed_at = coalesce(
                        EXCLUDED.full_load_completed_at,
                        layer_state.full_load_completed_at),
                    last_attempt_at = now(),
                    last_attempt_ok = true,
                    last_success_at = now()
                """,
                (
                    layer.key,
                    highest,
                    src_count,
                    held,
                    source_last_edit,
                    datetime.datetime.now(datetime.UTC) if exhausted else None,
                    layer.base_key == "census_areas",
                ),
            )
    conn.commit()

    # rows_updated is recorded as 0, not left at the column default by omission.
    # `--restart` aside, a load never re-reads a page it already committed
    # (`progress_of` resumes past it), so every row this run wrote landed in a
    # position nothing else had touched: there is nothing here that was "updated"
    # as distinct from "inserted." 3.1; see `sync.full_reload` for the reload path.
    finish_run(
        conn, run_id, True,
        watermark_reached=highest,
        rows_inserted=rows_this_run,
        rows_updated=0,
        pages_fetched=pages_this_run,
        stored_count=held,
        source_last_edit=source_last_edit,
        reconciled=(held == src_count),
    )

    return {
        "layer": layer.key,
        "pages": pages_this_run,
        "rows_this_run": rows_this_run,
        "stored": held,
        "source": src_count,
        "reconciled": held == src_count,
        "complete": exhausted,
        "watermark": highest,
        "source_last_edit": source_last_edit,
        "seconds": elapsed,
        "requests": requests,
        "rows_before": rows_before,
    }


def report(conn, layers):
    """Stored counts against the service's own, and observed size per layer."""
    lines, sizes = [], db.table_sizes(conn, [l.table for l in layers])
    total = 0
    for layer in layers:
        held = stored_count(conn, layer)
        src = source.count(layer)
        size = sizes[layer.table]
        total += size["total"]
        agree = "ok" if held == src else "DIVERGED"
        lines.append(
            f"{layer.key:18} stored {held:>9,}  source {src:>9,}  {agree:9} "
            f"size {db.human(size['total']):>9} "
            f"(heap {db.human(size['heap'])}, indexes {db.human(size['indexes'])})"
        )
    lines.append(f"{'total':18} {'':>38} size {db.human(total):>9}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--layer", action="append", choices=sorted(source.LAYERS),
                    help="load one layer; repeatable. Default: all three.")
    ap.add_argument("--page-size", type=int, help="override the layer's page size")
    ap.add_argument("--restart", action="store_true",
                    help="discard recorded progress and page from zero")
    ap.add_argument("--limit-pages", type=int,
                    help="stop after this many pages (for testing resumption)")
    ap.add_argument("--report", action="store_true",
                    help="report stored counts and on-disk size, and load nothing")
    args = ap.parse_args()

    layers = [source.LAYERS[k] for k in (args.layer or sorted(source.LAYERS))]
    conn = db.connect()

    if args.report:
        # Reads only, so it neither takes the lock nor waits behind a running load.
        db.apply_schema(conn)
        print(report(conn, layers))
        return 0

    # Everything below writes, and a second run would race this one's staging table.
    try:
        with db.sync_lock(conn):
            db.apply_schema(conn)
            return load_all(conn, layers, args)
    except db.SyncInProgress as exc:
        print(f"load.py: {exc}", file=sys.stderr)
        return db.EXIT_SYNC_IN_PROGRESS


def load_all(conn, layers, args):
    started = time.monotonic()
    results = [
        load_layer(conn, layer, args.page_size, args.restart, args.limit_pages)
        for layer in layers
    ]
    elapsed = time.monotonic() - started

    print()
    for r in results:
        state = "complete" if r["complete"] else "partial"
        print(
            f"{r['layer']:18} {r['pages']:>5} pages  {r['rows_this_run']:>9,} rows  "
            f"{r['seconds']:>7.1f}s  stored {r['stored']:,} of {r['source']:,}  "
            f"{state}, {'reconciled' if r['reconciled'] else 'DIVERGED'}"
        )
    print(
        f"{'total':18} {sum(r['pages'] for r in results):>5} pages  "
        f"{sum(r['rows_this_run'] for r in results):>9,} rows  {elapsed:>7.1f}s  "
        f"{sum(r['requests'] for r in results)} requests"
    )
    print()
    print(report(conn, layers))
    return 0 if all(r["reconciled"] and r["complete"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
