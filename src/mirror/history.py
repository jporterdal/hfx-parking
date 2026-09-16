"""Retain the doorway and block lists each sync produces, and answer "which doorway
left the list, and when" from the store rather than from git.

Task 8.6 of `mirror-hrm-data-and-host-app`, and the spec requirement "Retain the
lists each sync produced" (`specs/hrm-data-mirror/spec.md`). design.md M5 explains
why this exists at all: `out/` was committed by a scheduled job, and its git history
was the only before-and-after record this project has ever held of which doorway
stopped calling. Removing the job removes that mechanism, so the record moves into
`list_snapshots` / `doorway_list_history` / `block_list_history` (`schema.sql`)
deliberately, before the job that was building it by accident is gone.

**When a snapshot is taken.** Only after a sync that actually reloaded a layer, not
after every poll. `derive.derive_all()`'s recency window is computed from the
*data's own* latest call date (`derive.py`'s `latest = max(... DATE_INITIATED
...)`), not from wall-clock "now" -- so re-deriving against an unchanged mirror
produces byte-identical `rows`/`blocks` for every type on every call. A no-op poll
changes no row in any of the three layers, which means it cannot change what
`derive.derive_all()` returns either; snapshotting one would only write a duplicate
of the previous snapshot for no observational gain, at the cost design.md M5
already budgets in real ones ("costs a few thousand rows a week"). This is also
what keeps a quiet night's request cost at zero: a snapshot is local computation
over already-mirrored rows, so choosing not to take one on a no-op poll doesn't
save a request either -- it just avoids writing rows nothing could have changed.

A type with nothing over threshold still gets a snapshot with zero doorway/block
rows, not no snapshot: an empty list is itself an observation ("nothing listed on
this date"), and dropping it would make a type going quiet indistinguishable from a
sync that simply didn't run.

**One pass, one row per type.** `snapshot_after_sync` derives every requested type
from a single `derive.derive_all()` call -- one read of `custom_fields`,
`service_requests`, `parking_call_attributes` and `census_areas` for all thirty
types, not one full derivation per type (4.16). Retention still records one
`sync_runs` row (`kind='derive'`) per type, though: each type's row is opened
before `derive_all()` runs and finished afterward, so a reader asking "did
`Blocking Driveway` get retained this sync" finds its own attempt and, on failure,
its own anomaly -- exactly as if each type had been derived separately.

**Failure isolation.** A derivation failure must not read as a failed sync, and must
not undo a reload that already committed (`sync.full_reload`'s `swap_in` commits
before this module is ever called). If `derive_all()` itself raises, every
requested type's `sync_runs` row is finished as failed and gets its own
`list_snapshot_failed` anomaly -- nothing partial is retained, since no type's
result ever came back. If `derive_all()` succeeds but one type's snapshot insert
fails (a constraint violation, a connection hiccup), only that type's row is
finished as failed; the others, already inserted, stand. Either way the reload's
own `sync_runs` row stands exactly as it finished.
"""

import json

from mirror import derive, status, sync, violation_types

# Every canonical type (4.15/`violation_types.CANONICAL_TYPES`) is snapshotted on
# every reload, in the same volume-descending order the module freezes them in.
# Earlier this listed Driveway alone, derived by substring match -- the one type
# 4.1/4.4/4.9's reconciliation exercised end to end while the other 29 canonical
# types didn't exist yet as an entry point. No real snapshots have been taken under
# that scheme (this feature has not shipped), so widening the set is not a schema
# migration or a backward-compatibility concern -- there is no prior history to
# reconcile against, just a decision about what today's first snapshot should cover.
# `snapshot()` below still takes an explicit violation-type label and a
# pre-computed result, so it never has to change to grow this list further.
SNAPSHOT_TYPES = tuple(violation_types.CANONICAL_TYPES)

# The default parameters `derive.derive()` runs under, recorded alongside every
# snapshot (design.md's "the parameters in effect") so a later reader is never left
# guessing what recency window or threshold produced a given list.
DEFAULT_PARAMETERS = {
    "min_calls": 2, "recur_days": 365, "min_doorways": 2, "district": None,
}

_TRACKED_LAYERS = ("service_requests", "custom_fields", "census_areas")

# Columns `doorway_list_history`/`block_list_history` carry directly; everything
# else `hotspots.build()`/`roll_blocks()` computed lands in `detail` instead of
# being silently dropped.
_DOORWAY_COLUMNS = {
    "address", "block", "district", "calls_12mo", "calls_total", "tows",
    "vehicles_seen", "vehicles_distinct",
}
_BLOCK_COLUMNS = {
    "block", "streets", "district", "doorways", "calls_12mo", "calls_total",
    "tows", "dwellings",
}


def _mirror_version(conn, layers=_TRACKED_LAYERS):
    """The published version each tracked layer held, read from `layer_state` at
    snapshot time -- "the mirror version it was derived from." Read fresh here
    rather than threaded through from a caller, so a snapshot always states what
    the mirror actually held when it was taken, not what a caller assumed.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT layer, source_last_edit FROM layer_state WHERE layer = ANY(%s)",
            (list(layers),),
        )
        found = dict(cur.fetchall())
    return {layer: (found.get(layer).isoformat() if found.get(layer) else None)
            for layer in layers}


def snapshot(conn, sync_run_id, violation_type, result, parameters=None,
             mirror_last_success_at=None, derived_at=None):
    """Persist one type's already-derived doorway and block lists as a snapshot.

    `result` is exactly what `derive.derive()` returns (`rows`, `blocks`, `latest`);
    this function never derives on its own, so a caller that already has a result in
    hand -- a served view, a test, this module's own `snapshot_after_sync` -- never
    pays for a second derivation just to retain the first one.

    Returns the new `list_snapshots.id`.
    """
    parameters = {**DEFAULT_PARAMETERS, **(parameters or {})}
    latest = result.get("latest")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO list_snapshots
                (sync_run_id, violation_type, parameters, mirror_version,
                 mirror_last_success_at, latest_call_date, derived_at)
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()))
            RETURNING id
            """,
            (sync_run_id, violation_type, json.dumps(parameters, default=str),
             json.dumps(_mirror_version(conn), default=str), mirror_last_success_at,
             latest.date() if latest else None, derived_at),
        )
        snapshot_id = cur.fetchone()[0]

        for rank, row in enumerate(result.get("rows") or [], start=1):
            detail = {k: v for k, v in row.items() if k not in _DOORWAY_COLUMNS}
            cur.execute(
                """
                INSERT INTO doorway_list_history
                    (snapshot_id, address, rank, block, district, calls_recent,
                     calls_total, tows, vehicles_seen, vehicles_distinct, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (snapshot_id, row["address"], rank, row.get("block"),
                 row.get("district"), row.get("calls_12mo"), row.get("calls_total"),
                 row.get("tows"), row.get("vehicles_seen"),
                 row.get("vehicles_distinct"), json.dumps(detail, default=str)),
            )

        for rank, block in enumerate(result.get("blocks") or [], start=1):
            detail = {k: v for k, v in block.items() if k not in _BLOCK_COLUMNS}
            cur.execute(
                """
                INSERT INTO block_list_history
                    (snapshot_id, block, rank, streets, district, doorways,
                     calls_recent, calls_total, tows, dwellings, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (snapshot_id, block["block"], rank, block.get("streets"),
                 block.get("district"), block.get("doorways"),
                 block.get("calls_12mo"), block.get("calls_total"),
                 block.get("tows"), block.get("dwellings"),
                 json.dumps(detail, default=str)),
            )
    conn.commit()
    return snapshot_id


def snapshot_after_sync(conn, outcome, now=None, types=SNAPSHOT_TYPES,
                         parameters=None, log=lambda msg: None):
    """Called once per `sync.sync()` run. Snapshots every tracked type's lists if,
    and only if, this run actually reloaded a layer -- see the module docstring for
    why a no-op poll does not repeat the exercise.

    `types` is a sequence of canonical type names (default: every key of
    `violation_types.CANONICAL_TYPES`, in `SNAPSHOT_TYPES`'s order). All of them are
    derived together in one `derive.derive_all()` pass -- one read of the mirror
    regardless of how many types are requested -- but each still gets its own
    `sync_runs` row and, on failure, its own anomaly; see the module docstring's
    "One pass, one row per type."

    Never raises: a derivation or snapshot failure is recorded against its own
    `sync_runs` row and as a `sync_anomalies` row, and the loop continues to the
    next type, so one type's bad day does not hide another's, and neither undoes
    the reload this was called after.
    """
    if not outcome.get("pulled"):
        return []

    now = now or sync.utcnow()
    last_success_at = status.mirror_freshness(conn)["last_success_at"]
    run_params = {**DEFAULT_PARAMETERS, **(parameters or {})}

    # Opened before `derive_all()` runs, so every requested type has a `sync_runs`
    # row to finish against whether the shared derivation succeeds or not.
    run_ids = {violation_type: sync.start_run(conn, "derive", violation_type,
                                              started_at=now)
               for violation_type in types}

    try:
        results = derive.derive_all(
            conn, types=list(types), min_calls=run_params["min_calls"],
            recur_days=run_params["recur_days"],
            min_doorways=run_params["min_doorways"],
            district=run_params["district"],
        )
    except Exception as exc:
        conn.rollback()   # nothing was inserted for any type; the reload this run
                          # followed already committed (sync.swap_in).
        outcomes = []
        for violation_type in types:
            sync.finish(conn, run_ids[violation_type], False, now,
                       error=str(exc)[:2000])
            sync.record_anomaly(
                conn, run_ids[violation_type], violation_type,
                "list_snapshot_failed",
                {"violation_type": violation_type, "error": str(exc)[:500]},
            )
            log(f"{violation_type}: list snapshot FAILED — {exc}")
            outcomes.append({"violation_type": violation_type, "ok": False,
                             "error": str(exc)})
        return outcomes

    outcomes = []
    for violation_type in types:
        run_id = run_ids[violation_type]
        result = results[violation_type]
        try:
            snapshot_id = snapshot(
                conn, run_id, violation_type, result, parameters=run_params,
                mirror_last_success_at=last_success_at, derived_at=now,
            )
        except Exception as exc:
            conn.rollback()   # undo this type's partial snapshot only; the other
                              # types' already-committed snapshots stand.
            sync.finish(conn, run_id, False, now, error=str(exc)[:2000])
            sync.record_anomaly(
                conn, run_id, violation_type, "list_snapshot_failed",
                {"violation_type": violation_type, "error": str(exc)[:500]},
            )
            log(f"{violation_type}: list snapshot FAILED — {exc}")
            outcomes.append({"violation_type": violation_type, "ok": False,
                             "error": str(exc)})
            continue
        sync.finish(conn, run_id, True, now)
        log(f"{violation_type}: retained {len(result['rows'])} doorways, "
            f"{len(result['blocks'])} blocks (snapshot {snapshot_id})")
        outcomes.append({"violation_type": violation_type, "ok": True,
                         "snapshot_id": snapshot_id,
                         "doorways": len(result["rows"]), "blocks": len(result["blocks"])})
    return outcomes


# --------------------------------------------------------------------- queries


def snapshots_for(conn, violation_type, limit=None):
    """Every retained snapshot for one type, oldest first -- the sequence a caller
    picks an "A" and a "B" out of.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, derived_at, parameters, mirror_version,
                   mirror_last_success_at, latest_call_date
            FROM list_snapshots WHERE violation_type = %s
            ORDER BY derived_at
            """ + (" LIMIT %s" if limit else ""),
            (violation_type, limit) if limit else (violation_type,),
        )
        rows = cur.fetchall()
    return [
        {"snapshot_id": r[0], "derived_at": r[1], "parameters": r[2],
         "mirror_version": r[3], "mirror_last_success_at": r[4],
         "latest_call_date": r[5]}
        for r in rows
    ]


def departed_between(conn, snapshot_a, snapshot_b):
    """Doorways listed in `snapshot_a` that are absent from `snapshot_b` -- the
    "which doorways left between snapshot A and B" query (task 8.6, spec scenario
    "A doorway leaves the list"). The caller picks which two snapshots to compare;
    they need not be adjacent.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.address, a.rank, a.calls_recent, a.calls_total, a.tows
            FROM doorway_list_history a
            WHERE a.snapshot_id = %(a)s
              AND NOT EXISTS (
                  SELECT 1 FROM doorway_list_history b
                  WHERE b.snapshot_id = %(b)s AND b.address = a.address
              )
            ORDER BY a.address
            """,
            {"a": snapshot_a, "b": snapshot_b},
        )
        rows = cur.fetchall()
    return [
        {"address": r[0], "rank_when_last_listed": r[1],
         "calls_recent_when_last_listed": r[2],
         "calls_total_when_last_listed": r[3], "tows_when_last_listed": r[4]}
        for r in rows
    ]


def blocks_departed_between(conn, snapshot_a, snapshot_b):
    """The block-list equivalent of `departed_between`."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.block, a.rank, a.doorways, a.calls_recent
            FROM block_list_history a
            WHERE a.snapshot_id = %(a)s
              AND NOT EXISTS (
                  SELECT 1 FROM block_list_history b
                  WHERE b.snapshot_id = %(b)s AND b.block = a.block
              )
            ORDER BY a.block
            """,
            {"a": snapshot_a, "b": snapshot_b},
        )
        rows = cur.fetchall()
    return [
        {"block": r[0], "rank_when_last_listed": r[1],
         "doorways_when_last_listed": r[2], "calls_recent_when_last_listed": r[3]}
        for r in rows
    ]


def doorway_presence(conn, violation_type, address):
    """Every retained snapshot of `violation_type`, oldest first, and whether
    `address` was on the doorway list at each one. The building block under both
    queries below, and useful on its own for a doorway whose full history a reader
    wants rather than just its most recent departure.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.derived_at,
                   EXISTS (
                       SELECT 1 FROM doorway_list_history d
                       WHERE d.snapshot_id = s.id AND d.address = %(address)s
                   ) AS present
            FROM list_snapshots s
            WHERE s.violation_type = %(violation_type)s
            ORDER BY s.derived_at, s.id
            """,
            {"violation_type": violation_type, "address": address},
        )
        return [{"snapshot_id": r[0], "derived_at": r[1], "present": r[2]}
                for r in cur.fetchall()]


def when_did_doorway_leave(conn, violation_type, address):
    """When did `address` leave `violation_type`'s doorway list? (task 8.6, spec
    scenario "A doorway leaves the list": "the sync at which it left can be
    identified.")

    Reports the most recent departure: the last snapshot the address was present in,
    and the first later snapshot it was absent from. If the address has since
    reappeared, that is visible in `doorway_presence` (this only answers the most
    recent leaving; a doorway can leave more than once).
    """
    presence = doorway_presence(conn, violation_type, address)
    if not presence:
        return {"violation_type": violation_type, "address": address,
                "status": "no_snapshots_retained"}
    if not any(p["present"] for p in presence):
        return {"violation_type": violation_type, "address": address,
                "status": "never_listed"}

    last_present, left_at = None, None
    for entry in presence:
        if entry["present"]:
            last_present, left_at = entry, None
        elif last_present is not None and left_at is None:
            left_at = entry

    if left_at is None:
        return {
            "violation_type": violation_type, "address": address,
            "status": "still_listed",
            "last_seen_snapshot_id": last_present["snapshot_id"],
            "last_seen_at": last_present["derived_at"],
        }
    return {
        "violation_type": violation_type, "address": address, "status": "left",
        "last_seen_snapshot_id": last_present["snapshot_id"],
        "last_seen_at": last_present["derived_at"],
        "left_snapshot_id": left_at["snapshot_id"],
        "left_at": left_at["derived_at"],
    }
