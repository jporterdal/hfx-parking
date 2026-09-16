"""Keep the mirror current: poll the source's clock, and replace the version it names.

Two mechanisms move data, and deliberately only two; a third checks rather than moves.

**The poll** reads each layer's `editingInfo.lastEditDate` — one small request, no
paging, about 0.4 s. It runs nightly and it is the only thing that runs nightly.

**The reload** replaces a layer wholly. When the poll finds the published timestamp
advanced, the layer is fetched from the source in full and swapped in; nothing tries
to work out which rows changed within a version.

**The reconcile** (3.5) is the third, and it moves nothing. On a night the poll finds
no advance, it compares the mirror's stored count against the service's own — one more
cheap request per layer, run precisely because a reload isn't happening this cycle to
prove the counts agree on its own. A divergence is reported, never repaired here; the
remedy is `--reload`. See `reconcile_layer`.

That is not a simplification, it is the only correct reading of the source. The three
layers are published snapshots, not append logs: `hasStaticData` is true, the service
offers `Query` and `Extract` and no edit operation at all, and `dataLastEditDate`
equals `schemaLastEditDate` on all three. `ObjectId` is system-maintained by ArcGIS
and assigned afresh on each publish — the stored id space is dense from 1 to N with
zero gaps across 478,458 and 1,156,710 rows, which is a row counter written at load
time, not a durable record identity. An earlier version of this file advanced a
high-water mark on that identifier. It was measured and abandoned; `design.md` M2
records why, and `design.md` M2a records what would have to be observed to reopen it.

    a night with nothing published    2.4 s     6 requests      poll + reconcile
    a night with a publish           25 min   1,643 requests    full reload, ~213 MB
    at ~52 publishes a year          ~11 GB from a public endpoint

The "poll + reconcile" row grew from the original 3-request estimate (design.md M3)
once 3.5 added a per-layer count comparison to the no-op path: the poll is still one
`lastEditDate` request per layer, and reconciliation is a second, equally cheap
count-only request per layer, run only when nothing is about to be pulled — a reload
already reconciles itself after the swap (2.8), so checking again there would cost a
request to confirm what the reload just proved.

**A reload is atomic.** Rows land in a staging table, page by page and resumably; the
live table is untouched until one transaction empties it and refills it from staging.
A reload interrupted at any point before that transaction commits leaves the previous
version whole and queryable, and no reader ever sees a mixture of two versions. The
swap moves contents rather than renaming relations because `parking_call_attributes`
is bound to `custom_fields` by object id, and renaming tables under a view leaves the
view reading the version that was just discarded.

**A reload retains what it is about to destroy.** Before the swap, and again after
it, the version's identity is recorded — published timestamp, row count, highest
object id, and a sample of business key to object id. That is what makes `design.md`
M2a answerable later from observation instead of from a second round of
instrumentation. See `retain_version` and `renumbering_report`.

Run:  python3 src/mirror/sync.py                  # poll every layer, reload what moved
      python3 src/mirror/sync.py --poll-only      # the nightly half, never reloads
      python3 src/mirror/sync.py --force-pull     # reload regardless of the clock
      python3 src/mirror/sync.py --reload --layer service_requests
      python3 src/mirror/sync.py --history        # observed source-update intervals
      python3 src/mirror/sync.py --versions       # retained versions, and whether
                                                  # identifiers survived a publish
"""

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror import db, load, source  # noqa: E402

# The poll is cheap enough to run nightly, which bounds how far behind the source
# the mirror can fall to one day. design.md M3.
POLL_INTERVAL = datetime.timedelta(days=1)

CITYWORKS = ("service_requests", "custom_fields")

# Requests before custom fields, census last. Nothing depends on this order any more —
# each layer is replaced on its own — but the two Cityworks layers are published four
# minutes apart as one event, and reloading them in that order keeps the custom-field
# rows' requests present in the mirror throughout.
SYNC_ORDER = ("service_requests", "custom_fields", "census_areas")

# One business key in every SAMPLE_STRIDE is retained per version. A fixed stride, not
# a fraction of the row count: two versions must sample the *same* records or their
# samples cannot be compared at all. Two random 4,000-row samples of 478,458 rows
# would overlap in about nineteen records; a residue class of the request id overlaps
# completely, on every record present in both versions.
#
# 128 yields 3,721 requests and 9,229 custom-field rows against the loaded mirror.
# The question they answer is yes-or-no: did a record keep its ObjectId across a
# publish? If any fraction p of records were renumbered, a sample of 3,721 misses it
# entirely with probability (1-p)^3721 — under 2 per cent even at p = 0.001. Whole-
# layer renumbering, the hypothesis that actually matters, shows up in the first row.
# Retaining the full mapping instead would cost 478,458 rows per version to answer the
# same question, which design.md M2a declines for that reason.
SAMPLE_STRIDE = 128

# How each layer names the record whose identity is in question, and which rows are
# sampled. The census layer is 610 rows, so its sample is the layer.
VERSION_SAMPLE = {
    "service_requests": {
        "business_key": "request_id::text",
        "where": "mod(request_id, %(stride)s) = 0",
    },
    "custom_fields": {
        # REQUESTID alone is not unique here — a request carries up to 88 fields — so
        # the key is the request and the field together.
        "business_key":
            "request_id::text || ':' || coalesce(custom_field_id::text, '-')",
        "where": "mod(request_id, %(stride)s) = 0",
    },
    "census_areas": {
        "business_key": "dauid",
        "where": "dauid IS NOT NULL",
    },
}


# ---------------------------------------------------------------- recording


def start_run(conn, kind, layer_key, **fields):
    columns = ", ".join(["kind", "layer", *fields])
    values = ", ".join(["%s"] * (2 + len(fields)))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO sync_runs ({columns}) VALUES ({values}) RETURNING id",
            [kind, layer_key, *fields.values()],
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def record_anomaly(conn, run_id, layer_key, kind, detail):
    """An anomaly is a row. Reported, never silently resolved."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sync_anomalies (sync_run_id, layer, kind, detail) "
            "VALUES (%s, %s, %s, %s)",
            (run_id, layer_key, kind, json.dumps(detail, default=str)),
        )
    conn.commit()


def touch_layer(conn, layer_key, now, ok, **fields):
    """Record the attempt against the layer, whether or not anything was pulled.

    Every attempt lands here, successful or not, so a sync failing repeatedly shows a
    recent attempt against an unchanged last success rather than looking like one
    that never ran.
    """
    extra = "".join(f", {k}" for k in fields)
    placeholders = "".join(f", %({k})s" for k in fields)
    assignments = "".join(f", {k} = %({k})s" for k in fields)
    params = {"layer": layer_key, "now": now, "ok": ok, **fields}
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO layer_state (layer, last_attempt_at, last_attempt_ok{extra})
            VALUES (%(layer)s, %(now)s, %(ok)s{placeholders})
            ON CONFLICT (layer) DO UPDATE SET
                last_attempt_at = %(now)s,
                last_attempt_ok = %(ok)s{assignments}
            """,
            params,
        )
    conn.commit()


def finish(conn, run_id, ok, now=None, **fields):
    sets = "".join(f", {k} = %s" for k in fields)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE sync_runs SET finished_at = %s, ok = %s{sets} WHERE id = %s",
            [now or utcnow(), ok, *fields.values(), run_id],
        )
    conn.commit()


def utcnow():
    return datetime.datetime.now(datetime.UTC)


# ---------------------------------------------------------------- the poll


def last_pulled_source_edit(conn, layer_key):
    """The source timestamp the last successful *pull* saw, which is what an advance
    is measured against.

    Not the last *poll*: `layer_state.source_last_edit` is updated by every poll, so
    comparing against it would mean the source never appears to advance. Where no
    pull has recorded one — the initial load predates the column being written —
    `layer_state` is the fallback, so a first sync does not reload the static census
    layer merely because the history is thin.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_last_edit FROM sync_runs "
            "WHERE layer = %s AND ok AND source_last_edit IS NOT NULL "
            "AND kind IN ('full_load', 'reload') "
            "ORDER BY finished_at DESC NULLS LAST, id DESC LIMIT 1",
            (layer_key,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
        cur.execute("SELECT source_last_edit FROM layer_state WHERE layer = %s",
                    (layer_key,))
        row = cur.fetchone()
        return row[0] if row else None


def poll_layer(conn, layer, now=None, log=lambda msg: None):
    """One metadata request. Records the timestamp observed and whether it advanced.

    The observed timestamp is recorded on every poll, not only on every reload, so the
    interval between HRM's updates accumulates as measurement. No published HRM
    refresh schedule exists, so the cadence has to be measured or it is guessed.
    """
    now = now or utcnow()
    baseline = last_pulled_source_edit(conn, layer.key)
    run_id = start_run(conn, "poll", layer.key, started_at=now)
    try:
        observed = source.last_edit_date(layer)
    except Exception as exc:
        finish(conn, run_id, False, now, error=str(exc)[:2000])
        touch_layer(conn, layer.key, now, False)
        raise
    advanced = observed is not None and (baseline is None or observed > baseline)
    finish(conn, run_id, True, now, source_last_edit=observed, pages_fetched=1)
    touch_layer(
        conn, layer.key, now, True,
        source_last_edit=observed,
        next_due_at=now + POLL_INTERVAL,
        static=layer.static,
        # A poll that completes without error is a successful sync attempt in its
        # own right (design.md M4: "the sync clock says whether this system is
        # still collecting"), not only the reloads that happen to follow one. 3.2
        # needs this distinguishable from last_attempt_at, which every attempt
        # advances regardless of outcome — see the `except` branch above, which
        # deliberately does not pass last_success_at.
        last_success_at=now,
        # The highest object id actually held, so a state row created by a poll
        # reports the version's size rather than a default of zero. It is an
        # observation: no pull reads it, and the source reassigns it on publish.
        watermark=load.highest_object_id(conn, layer),
    )
    log(f"{layer.key}: source last edited {observed}"
        f"{' (advanced)' if advanced else ' (unchanged)'}")
    return {"layer": layer.key, "run_id": run_id, "observed": observed,
            "baseline": baseline, "advanced": advanced}


# ------------------------------------------------------------- reconciliation


def reconcile_layer(conn, layer, run_id, now=None, log=lambda msg: None):
    """Compare what the mirror holds against the service's own count, and report
    rather than resolve a divergence. design.md's "Reconcile the mirror against the
    source" requirement.

    Called from `sync` for a layer that is *not* about to be reloaded this run. A
    layer that is being pulled reconciles itself after the swap (`full_reload`,
    2.8) against the fresh counts the reload just fetched; checking again here,
    before the swap, would compare the old stored count against the new source
    count and report a divergence for the uninteresting reason that a publish just
    landed — not the reason this check exists to catch.

    One count-only request, the same order of cost as the `lastEditDate` poll
    itself, which is what makes it affordable on every quiet night rather than
    only when something is already being fetched.
    """
    now = now or utcnow()
    stored = load.stored_count(conn, layer)
    observed = source.count(layer)
    reconciled = stored == observed
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE sync_runs SET stored_count = %s, source_count = %s, "
            "reconciled = %s WHERE id = %s",
            (stored, observed, reconciled, run_id),
        )
    conn.commit()
    touch_layer(conn, layer.key, now, True, stored_count=stored, source_count=observed)
    if not reconciled:
        record_anomaly(
            conn, run_id, layer.key, "count_divergence",
            {"stored": stored, "source": observed, "remedy": "reload",
             "noticed_by": "no-op poll reconciliation"},
        )
        log(f"{layer.key}: DIVERGED — stored {stored:,}, source {observed:,} "
            f"(no reload triggered; the remedy is `--reload`)")
    return {"layer": layer.key, "stored": stored, "source": observed,
            "reconciled": reconciled}


def source_edit_history(conn, layer_key):
    """Every distinct source timestamp observed, when it was first seen, and the
    interval since the one before it. This is the cadence, as measured."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_last_edit, min(started_at) AS first_observed, count(*)
            FROM sync_runs
            WHERE layer = %s AND ok AND source_last_edit IS NOT NULL
            GROUP BY source_last_edit
            ORDER BY source_last_edit
            """,
            (layer_key,),
        )
        rows = cur.fetchall()
    history, previous = [], None
    for edited, first_observed, times in rows:
        history.append({
            "source_last_edit": edited,
            "first_observed": first_observed,
            "observations": times,
            "interval": (edited - previous) if previous else None,
        })
        previous = edited
    return history


# -------------------------------------------------- what a version was (M2a)


def retain_version(conn, layer, source_last_edit, run_id=None, table=None,
                   note=None, stride=None):
    """Record a published version's identity, so it outlives the version itself.

    Called twice per reload: once on the version going out, before a row of it is
    touched, and once on the version coming in. A version is identified by its layer
    and its published timestamp, so recording the same one twice is a no-op rather
    than a duplicate — the version retained as "loaded" by one reload is the same one
    the next reload finds on its way out.

    Returns what was retained, or None if this version is already held or the table
    is empty.
    """
    spec = VERSION_SAMPLE[layer.base_key]
    stride = stride or SAMPLE_STRIDE
    table = table or layer.table
    params = {"stride": stride}
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), coalesce(max(object_id), 0) FROM {table}")
        rows, highest = cur.fetchone()
        if not rows:
            return None                      # nothing held: no version to remember
        cur.execute(
            """
            INSERT INTO layer_versions (
                layer, source_last_edit, row_count, highest_object_id,
                sample_stride, sync_run_id, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (layer, source_last_edit) DO NOTHING
            RETURNING id
            """,
            (layer.base_key, source_last_edit, rows, highest, stride, run_id, note),
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None                      # this version is already retained
        version_id = row[0]
        cur.execute(
            f"""
            INSERT INTO layer_version_samples (version_id, business_key, object_id)
            SELECT %(version)s, {spec['business_key']}, object_id
            FROM {table}
            WHERE {spec['where']}
            ON CONFLICT DO NOTHING
            """,
            {**params, "version": version_id},
        )
        sampled = cur.rowcount
        cur.execute("UPDATE layer_versions SET sample_size = %s WHERE id = %s",
                    (sampled, version_id))
    conn.commit()
    return {"version_id": version_id, "layer": layer.base_key,
            "source_last_edit": source_last_edit, "row_count": rows,
            "highest_object_id": highest, "sample_size": sampled,
            "sample_stride": stride, "note": note}


def retained_versions(conn, layer_key):
    """Every retained version of a layer, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_last_edit, row_count, highest_object_id, sample_size,
                   sample_stride, note, retained_at
            FROM layer_versions WHERE layer = %s
            ORDER BY source_last_edit NULLS FIRST, id
            """,
            (layer_key,),
        )
        return [
            {"version_id": r[0], "source_last_edit": r[1], "row_count": r[2],
             "highest_object_id": r[3], "sample_size": r[4], "sample_stride": r[5],
             "note": r[6], "retained_at": r[7]}
            for r in cur.fetchall()
        ]


def renumbering_report(conn, layer_key, older=None, newer=None):
    """Did a record keep its object id across a publish? The M2a question, answered
    from the retained samples rather than from a fresh round of instrumentation.

    Reports an answer in every case, including "not yet" — one version retained is a
    state of knowledge, not an error.
    """
    versions = retained_versions(conn, layer_key)
    report = {"layer": layer_key, "versions_retained": len(versions),
              "publishes_observed": max(len(versions) - 1, 0), "comparable": False,
              "verdict": "not yet answerable", "older": None, "newer": None,
              "shared_keys": 0, "kept_identifier": 0, "moved_identifier": 0,
              "only_in_older": 0, "only_in_newer": 0, "examples": []}
    if len(versions) < 2:
        report["reason"] = (
            f"{len(versions)} version(s) retained; two are needed to compare. A "
            f"version is retained on each reload, so this answers itself at the "
            f"next publish."
        )
        return report

    older = older or versions[-2]["version_id"]
    newer = newer or versions[-1]["version_id"]
    by_id = {v["version_id"]: v for v in versions}
    report["older"], report["newer"] = by_id.get(older), by_id.get(newer)
    params = {"older": older, "newer": newer}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE o.object_id = n.object_id)
            FROM layer_version_samples o
            JOIN layer_version_samples n
              ON n.business_key = o.business_key AND n.version_id = %(newer)s
            WHERE o.version_id = %(older)s
            """,
            params,
        )
        shared, kept = cur.fetchone()
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM layer_version_samples o
                WHERE o.version_id = %(older)s
                  AND NOT EXISTS (SELECT 1 FROM layer_version_samples n
                                  WHERE n.version_id = %(newer)s
                                    AND n.business_key = o.business_key)),
              (SELECT count(*) FROM layer_version_samples n
                WHERE n.version_id = %(newer)s
                  AND NOT EXISTS (SELECT 1 FROM layer_version_samples o
                                  WHERE o.version_id = %(older)s
                                    AND o.business_key = n.business_key))
            """,
            params,
        )
        report["only_in_older"], report["only_in_newer"] = cur.fetchone()
        cur.execute(
            """
            SELECT o.business_key, o.object_id, n.object_id
            FROM layer_version_samples o
            JOIN layer_version_samples n
              ON n.business_key = o.business_key AND n.version_id = %(newer)s
            WHERE o.version_id = %(older)s AND o.object_id <> n.object_id
            ORDER BY o.business_key LIMIT 5
            """,
            params,
        )
        report["examples"] = [
            {"business_key": k, "was": was, "now": now} for k, was, now in cur.fetchall()
        ]

    report.update({"shared_keys": shared, "kept_identifier": kept,
                   "moved_identifier": shared - kept})
    if shared == 0:
        report["verdict"] = "no shared business keys between the two samples"
        report["reason"] = (
            "the two versions share no sampled record, so identifier stability "
            "cannot be read from them"
        )
        return report
    report["comparable"] = True
    if kept == shared:
        report["verdict"] = "identifiers preserved across the publish"
    elif kept == 0:
        report["verdict"] = "identifiers reassigned by the publish"
    else:
        report["verdict"] = "identifiers partially reassigned"
    return report


# ---------------------------------------------------------------- the reload


def table_exists(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"{db.schema()}.{table}",))
        return cur.fetchone()[0] is not None


def prepare_staging(conn, layer, target_edit, page_size=None, restart=False,
                    log=lambda msg: None):
    """The empty table a reload fills, and whether an interrupted fill can continue.

    A fill resumes only against the version it began on. If HRM has published again
    since, the half-filled staging table holds pages of a version that no longer
    exists, and continuing would splice two versions together — so it starts over.
    """
    staging = layer.staging()
    size = page_size or layer.page_size
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_last_edit, page_size, next_offset FROM load_progress "
            "WHERE layer = %s", (staging.key,)
        )
        progress = cur.fetchone()
    resume = (not restart and progress is not None
              and progress[0] == target_edit and progress[1] == size
              and table_exists(conn, staging.table))
    if resume:
        log(f"{layer.key}: resuming the staged copy at offset {progress[2]:,}")
        return staging, True
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {staging.table}")
        # Defaults and NOT NULLs, plus the primary key an interrupted fill needs to
        # re-read a page as an upsert. Not the secondary indexes: they exist on the
        # live table the rows are about to land in, and building them twice is work
        # that buys nothing.
        cur.execute(f"CREATE TABLE {staging.table} "
                    f"(LIKE {layer.table} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")
        cur.execute(f"ALTER TABLE {staging.table} ADD PRIMARY KEY (object_id)")
        cur.execute("DELETE FROM load_progress WHERE layer = %s", (staging.key,))
        cur.execute(
            "INSERT INTO load_progress (layer, page_size, source_last_edit) "
            "VALUES (%s, %s, %s)", (staging.key, size, target_edit)
        )
    conn.commit()
    return staging, False


def swap_in(conn, layer, staging):
    """Replace the live table's contents with the staged copy, in one transaction.

    Everything here — the emptying, the refill, and the disposal of the staging table
    — commits together or not at all. A reader either sees the version before or the
    version after; there is no instant at which the mirror holds part of each. A
    crash before the commit leaves the previous version whole.

    Contents move rather than tables being renamed. `parking_call_attributes` is bound
    to `custom_fields` by object id, not by name, so renaming the staged table into
    place would leave the view reading the table that was just discarded.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {staging.table}")
        incoming = cur.fetchone()[0]
        cur.execute(f"TRUNCATE {layer.table}")
        cur.execute(f"INSERT INTO {layer.table} SELECT * FROM {staging.table}")
        moved = cur.rowcount
        cur.execute(f"DROP TABLE {staging.table}")
        cur.execute("DELETE FROM load_progress WHERE layer = %s", (staging.key,))
    conn.commit()
    return moved if moved is not None else incoming


def full_reload(conn, layer, source_last_edit=None, now=None, page_size=None,
                restart=False, limit_pages=None,
                log=lambda msg: print(msg, file=sys.stderr)):
    """Fetch a layer in full and replace the held version with it.

    This is the sync's ordinary path, not its fallback: it is what happens whenever a
    published version advances. It is also available on demand, for a count divergence
    or any other reason to distrust what is held.

    The count check is reported, never used to decide the swap. A staged copy that
    exhausted its pages is the source's current version; the version it replaces is
    one the source no longer publishes. A divergence is recorded as an anomaly and in
    `sync_runs.reconciled`, and the remedy for it is another reload.
    """
    now = now or utcnow()
    target = (source_last_edit if source_last_edit is not None
              else source.last_edit_date(layer))
    run_id = start_run(conn, "reload", layer.key, started_at=now,
                       source_last_edit=target)

    # Before a row is touched: what the version about to be replaced was. design.md
    # M2a — the question of identifier stability is deferred, so the evidence that
    # would settle it is retained now rather than instrumented later.
    replaced = retain_version(conn, layer, last_pulled_source_edit(conn, layer.key),
                              run_id, note="replaced")

    staging, resumed = prepare_staging(conn, layer, target, page_size, restart, log)
    log(f"{layer.key}: staging a fresh copy; the held version stays queryable "
        f"until the swap")
    started = time.monotonic()
    try:
        result = load.load_layer(conn, staging, page_size, restart=False,
                                 limit_pages=limit_pages, log=log, record_state=False)
    except Exception as exc:
        conn.rollback()
        # 3.1: a failed reload's row should say what it managed before it died, not
        # only that it died. `load.load_layer` commits each page with the offset
        # that follows it (`load.py`'s own docstring), so `load_progress` for the
        # staging table holds exactly what survived the failure even though the
        # exception itself carries nothing back.
        with conn.cursor() as cur:
            cur.execute("SELECT rows_loaded, pages_completed FROM load_progress "
                       "WHERE layer = %s", (staging.key,))
            progress = cur.fetchone()
        rows_so_far, pages_so_far = progress if progress else (0, 0)
        finish(conn, run_id, False, error=str(exc)[:2000],
               rows_inserted=rows_so_far, rows_updated=0, pages_fetched=pages_so_far)
        touch_layer(conn, layer.key, now, False)
        raise

    result["layer"] = layer.key
    result["retained"] = replaced
    result["resumed"] = resumed
    if not result["complete"]:
        # A partial staged copy replaces nothing. The previous version is still the
        # version, and the next reload continues the fill where this one stopped.
        # rows_updated is 0, not merely unset: see the note at the bottom of this
        # function on what "inserted" and "updated" mean for a reload (3.1).
        finish(conn, run_id, False, pages_fetched=result["pages"],
               rows_inserted=result["rows_this_run"], rows_updated=0,
               error="staging fill incomplete; the held version was not replaced")
        touch_layer(conn, layer.key, now, False)
        result.update({"swapped": False, "stored": load.stored_count(conn, layer),
                       "reconciled": False})
        return result

    observed_after = result.get("source_last_edit")
    if observed_after and target and observed_after > target:
        record_anomaly(
            conn, run_id, layer.key, "source_published_during_reload",
            {"version_read": target, "observed_after": observed_after,
             "remedy": "this reload is recorded against the version it started from, "
                       "so the next poll sees the newer one and reloads again"},
        )

    swapped = swap_in(conn, layer, staging)
    held = load.stored_count(conn, layer)
    highest = load.highest_object_id(conn, layer)
    reconciled = held == result["source"]
    if not reconciled:
        record_anomaly(conn, run_id, layer.key, "count_divergence",
                       {"stored": held, "source": result["source"],
                        "remedy": "reload again"})

    # And what the version coming in is, so the next reload finds it already retained
    # and the two can be compared without anyone having planned ahead.
    loaded = retain_version(conn, layer, target, run_id, note="loaded")

    # 3.1: "rows inserted and updated" is a distinction a reload cannot honestly
    # draw. The staging table is created empty (or resumed with no overlap — a
    # fill never re-reads a completed page, `prepare_staging`) and its rows are
    # never touched again after landing; the swap then discards the whole previous
    # table and moves the staged rows into its place in one statement. There is no
    # row in the finished version that was "updated" rather than freshly written
    # into staging, so rows_inserted carries the whole count — result["rows_this_run"],
    # the rows this run actually fetched and wrote — and rows_updated is recorded
    # as 0 rather than left implicitly at its column default, so a reader of this
    # row sees a decision, not an omission.
    finish(conn, run_id, True, rows_inserted=result["rows_this_run"], rows_updated=0,
           pages_fetched=result["pages"], watermark_reached=highest,
           stored_count=held, source_count=result["source"], reconciled=reconciled,
           source_last_edit=target)
    touch_layer(conn, layer.key, now, True, watermark=highest, stored_count=held,
                source_count=result["source"], source_last_edit=target,
                last_success_at=now, full_load_completed_at=now, static=layer.static)
    elapsed = time.monotonic() - started
    log(f"{layer.key}: replaced — {swapped:,} rows in, {result['pages']} pages, "
        f"{elapsed:.1f}s, "
        f"{'reconciled' if reconciled else 'DIVERGED'} against {result['source']:,}")
    result.update({"swapped": True, "stored": held, "reconciled": reconciled,
                   "rows_swapped": swapped, "highest_object_id": highest,
                   "loaded_version": loaded, "seconds": elapsed, "run_id": run_id})
    return result


# ---------------------------------------------------------------- the sync


def sync(conn, layers=None, now=None, force_pull=False, poll_only=False,
         log=lambda msg: print(msg, file=sys.stderr)):
    """One run of the schedule: poll every layer, reload the ones that moved.

    Runs unattended and with no credentials. The service is public and the sync
    sends no token, no key and no header beyond what urllib puts on a POST.

    A layer is pulled when its published version advances, and for no other reason.
    There is no second schedule underneath the poll: with the whole version replaced,
    a pull that the version clock did not ask for would re-fetch 213 MB to write back
    what the mirror already holds. The static census layer needs no special case for
    that any more — it is not re-pulled because it does not move.
    """
    now = now or utcnow()
    chosen = layers or [source.LAYERS[k] for k in SYNC_ORDER]
    outcome = {"started_at": now, "layers": {}, "pulled": []}

    for layer in chosen:
        polled = poll_layer(conn, layer, now, log)
        pull = not poll_only and (force_pull or polled["advanced"])
        entry = dict(polled)
        entry["pulled"] = pull
        entry["reason"] = ("forced" if force_pull and pull else
                           "source advanced" if polled["advanced"] else
                           "nothing to do")
        if pull:
            entry["reload"] = full_reload(conn, layer, polled["observed"], now,
                                          log=log)
            outcome["pulled"].append(layer.key)
        else:
            # 3.5: a layer not being reloaded this run still gets checked against
            # the service's own count, so a mirror that has quietly drifted is
            # caught on an ordinary quiet night rather than only at the next
            # publish.
            entry["reconcile"] = reconcile_layer(conn, layer, polled["run_id"], now,
                                                 log)
        outcome["layers"][layer.key] = entry

    return outcome


def describe(outcome):
    lines = []
    for key, entry in sorted(outcome["layers"].items()):
        state = "reloaded" if entry["pulled"] else "no pull"
        lines.append(
            f"{key:18} source {str(entry['observed']):26} {state:9} ({entry['reason']})"
        )
        reconciled = entry.get("reconcile")
        if reconciled and not reconciled["reconciled"]:
            lines.append(
                f"{'':18}   DIVERGED — stored {reconciled['stored']:,}, "
                f"source {reconciled['source']:,}"
            )
        reloaded = entry.get("reload")
        if not reloaded:
            continue
        if not reloaded.get("swapped"):
            lines.append(f"{'':18}   staged {reloaded['rows_this_run']:,} rows, "
                         f"NOT SWAPPED — the held version stands")
            continue
        lines.append(
            f"{'':18}   replaced with {reloaded['stored']:,} of "
            f"{reloaded['source']:,} rows, {reloaded['pages']} pages, "
            f"{reloaded['seconds']:.1f}s, "
            f"{'reconciled' if reloaded['reconciled'] else 'DIVERGED'}"
        )
        retained = reloaded.get("retained")
        if retained:
            lines.append(
                f"{'':18}   retained the replaced version: "
                f"{retained['row_count']:,} rows, highest id "
                f"{retained['highest_object_id']:,}, "
                f"{retained['sample_size']:,} sampled identifiers"
            )
    if not outcome["pulled"]:
        lines.append("no layer was reloaded: no published version advanced")
    return "\n".join(lines)


def describe_versions(conn, layer_key):
    lines = [f"{layer_key}:"]
    versions = retained_versions(conn, layer_key)
    for entry in versions:
        lines.append(
            f"  {str(entry['source_last_edit']):26} {entry['row_count'] or 0:>9,} rows"
            f"  highest id {entry['highest_object_id'] or 0:>9,}"
            f"  {entry['sample_size'] or 0:,} sampled  ({entry['note']})"
        )
    report = renumbering_report(conn, layer_key)
    lines.append(f"  verdict: {report['verdict']}")
    if report["comparable"]:
        lines.append(
            f"  {report['kept_identifier']:,} of {report['shared_keys']:,} sampled "
            f"records kept their ObjectId; {report['moved_identifier']:,} moved"
        )
        for example in report["examples"]:
            lines.append(f"    {example['business_key']}: "
                         f"{example['was']} -> {example['now']}")
    else:
        lines.append(f"  {report.get('reason', '')}")
    lines.append(
        f"  publishes observed: {report['publishes_observed']} — design.md M2a asks "
        f"for four before the watermark question is re-examined"
    )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--layer", action="append", choices=sorted(source.LAYERS),
                    help="act on one layer; repeatable. Default: all three.")
    ap.add_argument("--poll-only", action="store_true",
                    help="the nightly half: read each layer's clock and pull nothing")
    ap.add_argument("--force-pull", action="store_true",
                    help="reload whether or not the source's clock advanced")
    ap.add_argument("--reload", action="store_true",
                    help="replace the layer with a fresh copy from the source")
    ap.add_argument("--history", action="store_true",
                    help="report the observed source-update intervals and pull nothing")
    ap.add_argument("--versions", action="store_true",
                    help="report the retained versions and whether a record kept its "
                         "identifier across a publish (design.md M2a)")
    args = ap.parse_args()

    layers = [source.LAYERS[k] for k in (args.layer or SYNC_ORDER)]
    conn = db.connect()
    db.apply_schema(conn)

    if args.history:
        for layer in layers:
            print(f"{layer.key}:")
            history = source_edit_history(conn, layer.key)
            for entry in history:
                gap = entry["interval"]
                print(f"  {entry['source_last_edit']}  first seen "
                      f"{entry['first_observed']}  "
                      f"{'interval ' + str(gap) if gap else 'first observation'}")
            if len(history) < 2:
                print("  next source update: not yet known — fewer than two "
                      "updates observed")
        return 0

    if args.versions:
        for layer in layers:
            print(describe_versions(conn, layer.key))
        return 0

    if args.reload:
        results = [full_reload(conn, layer) for layer in layers]
        for r in results:
            print(f"{r['layer']:18} replaced with {r['stored']:,} of "
                  f"{r['source']:,}  "
                  f"{'reconciled' if r['reconciled'] else 'DIVERGED'}")
        return 0 if all(r["reconciled"] for r in results) else 1

    outcome = sync(conn, layers, poll_only=args.poll_only,
                   force_pull=args.force_pull)
    print(describe(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
