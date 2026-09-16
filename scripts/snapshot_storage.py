"""On-disk storage estimate for an all-types list-snapshot.

Supports the open snapshot-retention question (how much storage a periodic
all-types snapshot costs): reads the real mirror to derive every canonical
type's current lists, then writes exactly ONE all-types snapshot into a
throwaway schema so it can be measured with Postgres's own relation-size
functions, then drops that schema.

Data source: the local mirror, read for `derive.derive_all()`
(`mirror.db.connect()`, default `mirror` schema). This read-only pass never
commits and never writes to the real `mirror` schema -- the connection is
rolled back and closed before anything is written.

Writes: exactly one snapshot, into a schema named `mirror_throwaway_snapshot_storage`
(the name makes clear it is disposable) via `HFX_MIRROR_SCHEMA`. That schema
is dropped (`DROP SCHEMA ... CASCADE`) both before writing (in case a prior
run was interrupted) and in a `finally` block after measuring, so nothing
persists. Never touches the real `mirror` schema.

Runtime: dominated by the one `derive_all()` pass, a few seconds.

Run:
    venv/bin/python3 scripts/snapshot_storage.py

Last reported figure: ~1.9 MB per all-types snapshot.
"""
import argparse
import datetime
import pathlib
import sys

REPO_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from mirror import db, derive, history  # noqa: E402

THROWAWAY_SCHEMA = "mirror_throwaway_snapshot_storage"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema", default=THROWAWAY_SCHEMA,
                     help="throwaway schema to write the one measured snapshot into and "
                          "then drop (default: %(default)s). Must not be 'mirror'.")
    args = ap.parse_args()
    if args.schema == db.DEFAULT_SCHEMA:
        raise SystemExit(f"refusing to use the real '{db.DEFAULT_SCHEMA}' schema as the "
                          f"throwaway schema")

    # ---- step 1: read-only pass against the REAL mirror schema ----
    import os
    os.environ.pop("HFX_MIRROR_SCHEMA", None)
    real_conn = db.connect()
    assert db.schema() == db.DEFAULT_SCHEMA
    try:
        results = derive.derive_all(real_conn)
    finally:
        real_conn.rollback()   # read-only: never commit anything on this connection
        real_conn.close()

    total_doorways = sum(len(r["rows"]) for r in results.values())
    total_blocks = sum(len(r["blocks"]) for r in results.values())
    print(f"types: {len(results)}")
    print(f"total doorway rows across all types: {total_doorways}")
    print(f"total block rows across all types: {total_blocks}")
    for t, r in sorted(results.items(), key=lambda kv: -len(kv[1]["rows"]))[:5]:
        print(f"  {t}: {len(r['rows'])} doorways, {len(r['blocks'])} blocks")

    # ---- step 2: write ONE all-types snapshot into a throwaway schema ----
    os.environ["HFX_MIRROR_SCHEMA"] = args.schema
    test_conn = db.connect()
    try:
        test_conn.execute(f"DROP SCHEMA IF EXISTS {args.schema} CASCADE")
        test_conn.commit()
        db.apply_schema(test_conn)

        now = datetime.datetime.now(datetime.UTC)
        for violation_type, result in results.items():
            history.snapshot(test_conn, None, violation_type, result,
                              parameters=history.DEFAULT_PARAMETERS,
                              mirror_last_success_at=None, derived_at=now)

        sizes = db.table_sizes(
            test_conn, ["list_snapshots", "doorway_list_history", "block_list_history"])
        grand_total = sum(s["total"] for s in sizes.values())
        for table, s in sizes.items():
            print(f"{table}: total={db.human(s['total'])} heap={db.human(s['heap'])} "
                  f"indexes={db.human(s['indexes'])} ({s['total']} bytes)")
        print(f"GRAND TOTAL for one all-types snapshot: {db.human(grand_total)} "
              f"({grand_total} bytes)")
        print(f"bytes per doorway+block row: "
              f"{grand_total / max(1, total_doorways + total_blocks):.1f}")
    finally:
        test_conn.execute(f"DROP SCHEMA IF EXISTS {args.schema} CASCADE")
        test_conn.commit()
        test_conn.close()


if __name__ == "__main__":
    main()
