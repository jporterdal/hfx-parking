"""Compute and store each canonical type's filter-independent figures, keyed to the
mirror version they were derived from -- so the server (5.8) can read them instead of
recomputing them on every view.

Task 5.7 of `mirror-hrm-data-and-host-app`, and `design.md` M12's third settled
question: "figures that do not depend on filters are computed when the mirror
reloads. [...] Those results depend only on the mirror's published version, not on
any filter, so they are computed after each reload and stored alongside the retained
lists (8.6), and the server reads them." M11 stands for everything a filter changes --
the doorway and block lists stay per request (`derive.derive`/`derive_all`, read live
by 5.5); only the figures a filter cannot touch move here.

**What gets stored, per canonical type.** `per_type.compute_type()`'s own output --
the tow comparison (with `figures._effect_bound`'s cluster-bootstrap interval, reused
by `per_type`, never reimplemented here either), vehicle uniqueness, and the
type-phrased conclusion built from both -- plus two figures `per_type` does not
compute: `figures.response_time()` (task 4.13's median elapsed time) and
`figures.call_denominators()` (task 4.14's population reconciliation). All three are
filter-independent in exactly M11's sense: none of them read district, minimum
calls, minimum doorways or the recency window, so none of them go stale when a
viewer moves a slider -- only a new mirror version invalidates them.

**Reuse, and where it stops.** `per_type.compute_all()` already derives every
canonical type from one `derive.derive_all()` pass (4.16) rather than re-querying the
mirror once per type, and this module calls it exactly once per `compute_and_store()`
call, over whichever types are actually missing -- never per type. `response_time`/
`call_denominators` are a different story: neither accepts an already-loaded `calls`/
`fields` pair, both call `derive.load()` themselves (and `call_denominators` calls
`recurrence_by_tow`, which calls `derive.load()` again), and `figures.py` is out of
bounds for this change to edit, so there is no way to route them through
`derive_all()`'s single pass without a second copy of their logic living here --
which this module declines, for the same reason `per_type.py` declines to reimplement
the bootstrap: a second copy is a second place for the two to drift apart. So these
two do re-query the mirror once per type. Measured against the loaded mirror
(2026-09-16, 30 canonical types, default parameters): `per_type.compute_all()` 9.1s,
`response_time` x30 1.3s, `call_denominators` x30 11.7s -- about 22s total for every
tracked type, once, after a reload that already took tens of minutes. That is the
`derive_all()` pass paying for itself twice over rather than thirty times.

**Idempotency: skip, not upsert.** `compute_and_store()` reads which canonical types
already have a row for the mirror's current version *and* the given parameters
before computing anything, and calls `per_type.compute_all()`/`response_time`/
`call_denominators` only for the types still missing. A version this function has
already fully covered costs one query and no computation at all -- not a wasted 22
seconds discovering there is nothing new to write. The insert itself still carries
`ON CONFLICT (mirror_version, canonical_type, parameters) DO NOTHING` as a second,
cheaper guard against a race between two runs computing the same missing type
concurrently; either way, two calls against the same version and parameters never
produce two rows for the same type. An upsert (`DO UPDATE`) was rejected: the figures
are a pure function of the mirror's content under a given parameter set, so a second
computation against the same version can only reproduce the same numbers (the
bootstrap is seeded, `figures.py`'s module docstring) -- overwriting a row with an
identical one buys nothing a skip does not already give for less work.

**Hook placement.** `after_sync()` is called from `sync.sync()` the same way
`history.snapshot_after_sync()` is (`sync.py`'s own docstring on why that import is
local, not module-level, to avoid a cycle) -- immediately after it, and gated the
same way: only when `outcome["pulled"]` is non-empty, i.e. only on a sync that
actually reloaded a layer. A no-op poll changes no row in any mirrored table, so
`per_type.compute_all()` would reproduce exactly what is already stored for the
version currently held -- `compute_and_store()`'s own pre-check would find nothing
missing and do no work, but `after_sync()` short-circuits before even reading that,
so a quiet night costs the same zero extra requests it always has (design.md M5,
8.6's module docstring makes the identical argument for list snapshots).

**Failure isolation.** A `per_type.compute_all()` failure takes every type in the
current batch down together (it is one call, one query pass) -- each gets its own
`sync_runs` row (`kind='type_figures'`) and its own `type_figures_failed` anomaly, and
nothing is inserted for any of them. A failure computing or storing one type's
`response_time`/`call_denominators`/row *after* `compute_all()` has already
succeeded -- a transient connection error, a constraint violation -- is isolated to
that type alone: its own row rolls back, its own `sync_runs` row finishes failed, its
own anomaly is recorded, and every other type in the batch, already inserted,
stands. Either way this never touches the reload that preceded it: `sync.swap_in`
commits before `after_sync()` is ever called (matching `history.py`'s own note on
the same point), and a `type_figures` failure cannot make a successful reload look
failed.

No network call anywhere in this module: every read is `per_type.compute_all()`'s
or `figures.response_time()`/`figures.call_denominators()`'s, both of which are
Postgres-only (their own module docstrings).

Run:  python3 -m mirror.type_figures                          # every canonical type,
                                                                # the version currently
                                                                # held
      python3 -m mirror.type_figures --canonical-type "No Parking Sign"
      python3 -m mirror.type_figures --bootstrap-iterations 200   # faster, coarser
      python3 -m mirror.type_figures --json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror import db, figures, history, per_type, sync, violation_types  # noqa: E402

# The parameters `per_type.compute_type()`'s figures were computed under, recorded
# alongside every row so a later reader is never left guessing what recency window,
# threshold or bootstrap seed produced a given number -- and so the uniqueness
# constraint on (mirror_version, canonical_type, parameters) sees a bootstrap-
# iteration change as a reason to compute a new row rather than silently keep an
# old one. Matches `history.DEFAULT_PARAMETERS`'s four derivation thresholds exactly
# (same defaults, same keys) plus the two bootstrap knobs `history.py` has no reason
# to carry.
DEFAULT_PARAMETERS = {
    "min_calls": 2, "recur_days": 365, "min_doorways": 2, "district": None,
    "bootstrap_seed": figures.DEFAULT_BOOTSTRAP_SEED,
    "bootstrap_iterations": figures.DEFAULT_BOOTSTRAP_ITERATIONS,
}


def _mirror_version(conn):
    """The published version each tracked layer held, read the same way
    `list_snapshots.mirror_version` is -- `history._mirror_version()`, called
    directly rather than reimplemented, so "the mirror version this was derived
    from" means byte-identically the same thing in both tables. A private helper,
    called deliberately: `per_type.py` sets the same precedent reusing
    `figures._effect_bound()`, for the same reason -- a second copy of a five-line
    query is a second place for the two to drift apart.
    """
    return history._mirror_version(conn)


def stored_types_for(conn, mirror_version_json, parameters_json):
    """Every canonical type that already has a row for this exact version and
    parameter set -- what `compute_and_store()` checks before computing anything,
    so a version it has already covered costs one query, not a recomputation.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_type FROM type_figures "
            "WHERE mirror_version = %s AND parameters = %s",
            (mirror_version_json, parameters_json),
        )
        return {r[0] for r in cur.fetchall()}


def _store(conn, sync_run_id, canonical_type, figures_dict, mirror_version_json,
          parameters_json, computed_at):
    """One type's row. `ON CONFLICT DO NOTHING` is a race guard, not the primary
    idempotency mechanism -- `compute_and_store()`'s pre-check is -- so a conflict
    here means a concurrent call stored this exact type/version/parameters between
    the pre-check and this insert; either way, this returns the row that now exists
    rather than raising.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO type_figures
                (sync_run_id, canonical_type, mirror_version, parameters, figures,
                 computed_at)
            VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
            ON CONFLICT (mirror_version, canonical_type, parameters) DO NOTHING
            RETURNING id, computed_at
            """,
            (sync_run_id, canonical_type, mirror_version_json, parameters_json,
             json.dumps(figures_dict, default=str), computed_at),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT id, computed_at FROM type_figures WHERE canonical_type = %s "
                "AND mirror_version = %s AND parameters = %s",
                (canonical_type, mirror_version_json, parameters_json),
            )
            row = cur.fetchone()
    conn.commit()
    return row


def compute_and_store(conn, types=None, parameters=None, now=None,
                      log=lambda msg: None):
    """Compute and store filter-independent figures for the mirror version
    currently held -- the command 5.7 asks for, and what `after_sync()` calls after
    a reload.

    `types` defaults to every canonical type (`violation_types.CANONICAL_TYPES`).
    `parameters` is merged onto `DEFAULT_PARAMETERS`, matching
    `history.snapshot()`'s own merge-onto-defaults pattern, so a caller overriding
    one knob (say, `bootstrap_iterations` for a faster run) does not have to restate
    the rest.

    Returns a list of per-type outcome dicts (`{"canonical_type", "ok", ...}`),
    covering only the types that were actually missing -- a type already stored for
    this version and these parameters is silently skipped and does not appear in the
    result, the same way `history.snapshot_after_sync()` returns nothing at all on a
    quiet night rather than an empty success entry per type.
    """
    types = list(types) if types is not None else list(violation_types.CANONICAL_TYPES)
    parameters = {**DEFAULT_PARAMETERS, **(parameters or {})}
    now = now or sync.utcnow()

    mirror_version = _mirror_version(conn)
    mirror_version_json = json.dumps(mirror_version, default=str)
    parameters_json = json.dumps(parameters, default=str)

    already = stored_types_for(conn, mirror_version_json, parameters_json)
    missing = [t for t in types if t not in already]
    if not missing:
        log(f"type figures: nothing to do -- {len(types)} type(s) requested, all "
            f"already stored for this mirror version and these parameters")
        return []

    # Opened before compute_all() runs, so every missing type has a sync_runs row
    # to finish against whether the shared derivation succeeds or not -- the same
    # shape history.snapshot_after_sync() uses for the same reason.
    run_ids = {t: sync.start_run(conn, "type_figures", t, started_at=now)
              for t in missing}

    try:
        per_type_figures = per_type.compute_all(
            conn, types=missing, recur_days=parameters["recur_days"],
            min_calls=parameters["min_calls"], min_doorways=parameters["min_doorways"],
            district=parameters["district"],
            bootstrap_seed=parameters["bootstrap_seed"],
            bootstrap_iterations=parameters["bootstrap_iterations"],
        )
    except Exception as exc:
        conn.rollback()   # nothing was inserted for any type
        outcomes = []
        for t in missing:
            sync.finish(conn, run_ids[t], False, now, error=str(exc)[:2000])
            sync.record_anomaly(
                conn, run_ids[t], t, "type_figures_failed",
                {"canonical_type": t, "error": str(exc)[:500]},
            )
            log(f"{t}: type figures FAILED — {exc}")
            outcomes.append({"canonical_type": t, "ok": False, "error": str(exc)})
        return outcomes

    outcomes = []
    for t in missing:
        run_id = run_ids[t]
        try:
            response = figures.response_time(conn, canonical_type=t)
            denominators = figures.call_denominators(conn, canonical_type=t)
            combined = {
                **per_type_figures[t],
                "response_time": response,
                "call_denominators": denominators,
            }
            row = _store(conn, run_id, t, combined, mirror_version_json,
                        parameters_json, now)
        except Exception as exc:
            conn.rollback()   # undo this type's partial row only; the others stand
            sync.finish(conn, run_id, False, now, error=str(exc)[:2000])
            sync.record_anomaly(
                conn, run_id, t, "type_figures_failed",
                {"canonical_type": t, "error": str(exc)[:500]},
            )
            log(f"{t}: type figures FAILED — {exc}")
            outcomes.append({"canonical_type": t, "ok": False, "error": str(exc)})
            continue
        sync.finish(conn, run_id, True, now)
        log(f"{t}: stored type figures (id {row[0]})")
        outcomes.append({"canonical_type": t, "ok": True, "id": row[0],
                         "computed_at": row[1]})
    return outcomes


def after_sync(conn, outcome, now=None, types=None, parameters=None,
              log=lambda msg: None):
    """Called once per `sync.sync()` run, immediately after
    `history.snapshot_after_sync()`. Computes figures only when this run actually
    reloaded a layer -- see the module docstring's "Hook placement" for why a quiet
    night is not just cheap here but free: nothing is even queried.
    """
    if not outcome.get("pulled"):
        return []
    now = now or sync.utcnow()
    return compute_and_store(conn, types=types, parameters=parameters, now=now, log=log)


# --------------------------------------------------------------------- reading


def figures_for(conn, canonical_type, mirror_version=None, parameters=None):
    """The read path 5.8 calls: one canonical type's stored figures for a mirror
    version, or a stated absence -- never a computation, and never a bare `None`/
    `KeyError` for "not stored".

    `mirror_version` defaults to the mirror's *current* version (read fresh, the
    same way `history._mirror_version()` always is, rather than trusted from a
    caller who might be looking at a stale picture). `parameters` defaults to
    `DEFAULT_PARAMETERS` -- the parameter set `compute_and_store()`'s own default
    run computes under, and so the parameter set an ordinary view (no bootstrap
    override, no non-default thresholds) should read back.

    Returns `{"available": True, ..., "figures": {...}, "computed_at": ...}` or
    `{"available": False, ..., "reason": "..."}` -- the latter both when this
    canonical type has never been computed at all and when it has, but only for a
    version or parameter set other than the one asked for (a stale mirror-version
    row does not silently answer for a newer version it was never computed
    against).
    """
    mirror_version = (mirror_version if mirror_version is not None
                      else _mirror_version(conn))
    parameters = {**DEFAULT_PARAMETERS, **(parameters or {})}
    mirror_version_json = json.dumps(mirror_version, default=str)
    parameters_json = json.dumps(parameters, default=str)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT figures, computed_at FROM type_figures WHERE canonical_type = %s "
            "AND mirror_version = %s AND parameters = %s "
            "ORDER BY computed_at DESC LIMIT 1",
            (canonical_type, mirror_version_json, parameters_json),
        )
        row = cur.fetchone()

    if row is None:
        return {
            "available": False, "canonical_type": canonical_type,
            "mirror_version": mirror_version, "parameters": parameters,
            "reason": "no figures stored for this canonical type at the mirror's "
                      "current version and these parameters",
        }
    figures_json, computed_at = row
    return {
        "available": True, "canonical_type": canonical_type,
        "mirror_version": mirror_version, "parameters": parameters,
        "computed_at": computed_at, "figures": figures_json,
    }


# --------------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--canonical-type", default=None,
        help="one canonical type only (default: every tracked type)",
    )
    ap.add_argument("--recur-days", type=int, default=DEFAULT_PARAMETERS["recur_days"])
    ap.add_argument("--min-calls", type=int, default=DEFAULT_PARAMETERS["min_calls"])
    ap.add_argument("--min-doorways", type=int,
                    default=DEFAULT_PARAMETERS["min_doorways"])
    ap.add_argument("--district", default=DEFAULT_PARAMETERS["district"])
    ap.add_argument(
        "--bootstrap-iterations", type=int,
        default=DEFAULT_PARAMETERS["bootstrap_iterations"],
        help="cluster-bootstrap resamples per type's tow comparison (lower is "
             "faster, coarser)",
    )
    ap.add_argument("--bootstrap-seed", type=int,
                    default=DEFAULT_PARAMETERS["bootstrap_seed"])
    ap.add_argument("--json", action="store_true",
                    help="print outcomes as JSON instead of one line per type")
    args = ap.parse_args()

    conn = db.connect()
    db.apply_schema(conn)

    types = [args.canonical_type] if args.canonical_type else None
    parameters = {
        "recur_days": args.recur_days, "min_calls": args.min_calls,
        "min_doorways": args.min_doorways, "district": args.district,
        "bootstrap_seed": args.bootstrap_seed,
        "bootstrap_iterations": args.bootstrap_iterations,
    }

    outcomes = compute_and_store(
        conn, types=types, parameters=parameters,
        log=lambda m: print(m, file=sys.stderr),
    )

    if args.json:
        print(json.dumps(outcomes, indent=2, default=str))
    elif not outcomes:
        print("nothing to compute: figures already stored for the mirror's current "
             "version and these parameters")
    else:
        for o in outcomes:
            if o["ok"]:
                print(f"{o['canonical_type']}: stored (id {o['id']})")
            else:
                print(f"{o['canonical_type']}: FAILED — {o['error']}")

    return 0 if all(o.get("ok", True) for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
