"""All-types derivation timing: SQL vs Python breakdown.

Supports task 4.19: times `derive.derive_all()` as shipped (no edits) against
the real mirror, over several runs, and reports the median total time along
with a breakdown of SQL read time versus Python compute time (build /
roll_blocks / containment, etc.) within the call. SQL time is measured by
wrapping the connection's `cursor()` so every `execute()` call's wall time is
accumulated; Python-side time is the remainder of the total.

Data source: the local mirror, read-only (`mirror.db.connect()`, default
`mirror` schema, no `HFX_MIRROR_SCHEMA` override). No writes, no network.

Runtime: ~2s per run x 5 runs (~10s total) against the current mirror.

Run:
    venv/bin/python3 scripts/derivation_timing.py
    venv/bin/python3 scripts/derivation_timing.py --runs 10

To additionally confirm zero network calls at full mirror scale (the existing
test suite only proves this against small fixtures), run it again under the
`unshare -rn` recipe from `tests/conftest.py`:

    unshare -rn sh -c 'ip link set lo up; venv/bin/python3 scripts/derivation_timing.py'

Last reported figure: ~2.1s median total, all types.
"""
import argparse
import pathlib
import statistics
import sys
import time

REPO_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from mirror import db, derive  # noqa: E402

DEFAULT_RUNS = 5


def timed_derive_all(conn):
    """One `derive_all()` call, with cumulative SQL execute() time captured via a
    wrapped cursor. Returns (result, total_s, sql_s).
    """
    sql_time = [0.0]
    original_cursor = conn.cursor

    class TimingCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor

        def execute(self, *a, **k):
            t0 = time.perf_counter()
            result = self._real.execute(*a, **k)
            sql_time[0] += time.perf_counter() - t0
            return result

        def fetchall(self, *a, **k):
            return self._real.fetchall(*a, **k)

        def fetchone(self, *a, **k):
            return self._real.fetchone(*a, **k)

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

    def wrapped_cursor(*a, **k):
        return TimingCursor(original_cursor(*a, **k))

    conn.cursor = wrapped_cursor
    t0 = time.perf_counter()
    try:
        result = derive.derive_all(conn)
    finally:
        conn.cursor = original_cursor
    total = time.perf_counter() - t0
    return result, total, sql_time[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                     help="number of derive_all() runs to time and take the median of "
                          "(default: %(default)s, matching the measured figure)")
    args = ap.parse_args()

    conn = db.connect()

    totals, sqls, pythons = [], [], []
    for i in range(args.runs):
        result, total, sql = timed_derive_all(conn)
        python_time = total - sql
        totals.append(total)
        sqls.append(sql)
        pythons.append(python_time)
        n_types = len(result)
        n_calls = sum(len(r["calls"]) for r in result.values())
        print(f"run {i+1}: total={total:.2f}s  sql={sql:.2f}s  python={python_time:.2f}s  "
              f"({n_types} types, {n_calls} calls)")

    print()
    print(f"median total:  {statistics.median(totals):.2f}s")
    print(f"median sql:    {statistics.median(sqls):.2f}s "
          f"({100*statistics.median(sqls)/statistics.median(totals):.1f}% of total)")
    print(f"median python: {statistics.median(pythons):.2f}s "
          f"({100*statistics.median(pythons)/statistics.median(totals):.1f}% of total)")


if __name__ == "__main__":
    main()
