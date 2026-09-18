"""Figure-level reconciliation: does the mirror-backed derivation reproduce every
row and figure `src/hotspots.py`'s live run produces, for the same violation
selection? Task 4.9 (and re-run for 9.4 once the served application depends on
derived tables). `tests/test_reconcile_figures.py` is the offline proof that the
diff logic itself is trustworthy; this module's own run against real data is 4.9's
manual "verify."

This is figure level, not row level -- `mirror.reconcile` (3.6) already proves the
mirror holds what the service holds, request by request. What this module proves is
that `mirror.derive` (4.1, 4.4), fed from a mirror already shown faithful, reproduces
`src/hotspots.py`'s own arithmetic over the same rows: the same doorways in the same
order with the same counts, the same blocks, the same brief prose. A discrepancy here
is a mirror or derivation defect, never a revision of the analysis (design.md's
Migration Plan, step 2) -- this module never patches an output to make one agree.

Same freshness caution as 3.6: `freshness()` (reused from `mirror.reconcile`, not
reimplemented) is checked first. If HRM has published since the mirror's version, a
mismatch below would be expected, not a defect, so `run_reconciliation()` reports the
staleness and does not run the comparison at all -- running it against a moving
target would waste the live run's ~3.5 minutes on a result that proves nothing, and
it is never this module's job to sync or reload to make the question go away.

That first check only covers what could be stale *before* the ~3.5-minute live run
starts. HRM can also publish *during* the run, in which case the live pipeline reads
newer data than the mirror derivation did, and the diff below reports discrepancies
that look like a derivation defect but are really just the two sides having read two
different publishes. `run_reconciliation()` therefore checks freshness a second time
right after both pipelines finish, and compares each layer's live `lastEditDate`
against what the first check observed. A difference does not abort anything, hide the
diff, or change the verdict -- the user considers a mid-run publish unlikely enough
that it does not warrant a new failure mode or an automatic re-run -- it only adds a
`republish_warning` to the result (and to the CLI report, shown next to the verdict)
naming the layer and the before/after timestamps, so a human reading a DIVERGED
result knows to check this before chasing a derivation bug.

Two things are normalised out of an otherwise byte-for-byte comparison, and both are
documented, not silently dropped:
  - the three freshness columns `derive._rows_with_freshness` appends to every
    mirror-derived CSV row (`derived_at`, `last_sync_success_at`,
    `most_recent_call_date`) -- 4.7's clocks, which the live pipeline carries nowhere
    because it has no mirror to read them from;
  - the one-line freshness note `derive._write_freshness_note` inserts right after a
    brief's title (and the single blank line the insertion adds ahead of it) -- same
    clocks, in prose, including its own `derived_at` generation timestamp.
Anything else that differs -- a cell, a missing or reordered row, a brief line that
was never a freshness artefact -- is reported as a discrepancy.

`out/triage-board.html`, the standalone board, was retired under task 8.7 (last
present at b04731c): `src/hotspots.py` no longer writes a board and `mirror.derive`
never did, so there is no board on either side to diff. `BOARD_STATUS` states this in
every report rather than the comparison silently only ever mentioning four files.

Run:  python3 src/mirror/reconcile_figures.py                       # Driveway
      python3 src/mirror/reconcile_figures.py --violation Driveway --out-dir /tmp/x --keep
"""

import argparse
import csv
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mirror import db, reconcile  # noqa: E402

VIOLATION = "Driveway"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HOTSPOTS = REPO_ROOT / "src" / "hotspots.py"
DERIVE = REPO_ROOT / "src" / "mirror" / "derive.py"

# 4.7's freshness columns, appended to every mirror-derived CSV row by
# derive._rows_with_freshness. Their presence is expected and normalised away below;
# their *values* are never inspected here -- 4.3/4.7's own tests cover that.
MIRROR_CSV_FRESHNESS_COLUMNS = ("derived_at", "last_sync_success_at", "most_recent_call_date")

# derive._freshness_note's exact shape (see derive.py:495-504), matched structurally
# rather than by copying its format string, so a change to the note's wording still
# matches as long as it keeps naming the same three clocks in the same order.
_FRESHNESS_NOTE_RE = re.compile(
    r"^Derived .* from the mirror \| last successful sync .* \| "
    r"most recent call in the data .*\.$"
)

BOARD_STATUS = (
    "out/triage-board.html: not compared. The standalone board was retired under "
    "task 8.7 (last present at b04731c): src/hotspots.py no longer writes one and "
    "mirror.derive never did, so neither side of this run produces a board. The "
    "comparison covers only the four list files (watchlist/blocks CSVs and briefs); "
    "the served application's own pages are outside it."
)

# The four files 4.9 names, and the business key each is compared by -- never
# ObjectId or file position alone, so a reordered row is detected as reordering
# rather than reported as an unrelated pair of missing/extra rows (mirror.reconcile's
# own choice of key, applied here at the figure level).
CSV_FILES = (("watchlist.csv", "address"), ("blocks.csv", "block"))
BRIEF_FILES = ("watchlist.md", "blocks.md")


# ------------------------------------------------------------- running the pipelines


def run_live(out_dir, violation=VIOLATION, extra_args=()):
    """Run `src/hotspots.py` against the live HRM service, writing its four outputs
    into `out_dir`. Takes about 3.5 minutes per `design.md`'s measurement. Returns
    the completed `subprocess.run`.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(HOTSPOTS),
        "--violation", violation,
        "--csv", str(out_dir / "watchlist.csv"),
        "--brief", str(out_dir / "watchlist.md"),
        "--block-csv", str(out_dir / "blocks.csv"),
        "--block-brief", str(out_dir / "blocks.md"),
        *extra_args,
    ]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


def run_mirror(out_dir, violation=VIOLATION, extra_args=()):
    """Run `src/mirror/derive.py`'s CLI against the mirror, writing its four
    outputs into `out_dir` (the single-violation path writes directly into
    `out_dir`, not a type subdirectory -- see `derive.py:main()`). Returns the
    completed `subprocess.run`.
    """
    out_dir = pathlib.Path(out_dir)
    cmd = [sys.executable, str(DERIVE), str(out_dir), "--violation", violation, *extra_args]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


# --------------------------------------------------------------------- CSV diffing


def read_csv_rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def strip_freshness_columns(rows):
    """Remove 4.7's freshness columns from mirror CSV rows before comparison.
    Returns `(stripped_rows, columns_actually_present)` so a caller can state what
    was normalised rather than assume the documented columns were the ones found.
    """
    present = set()
    stripped = []
    for row in rows:
        row = dict(row)
        for col in MIRROR_CSV_FRESHNESS_COLUMNS:
            if col in row:
                present.add(col)
                del row[col]
        stripped.append(row)
    return stripped, present


def diff_csv(live_rows, mirror_rows, key):
    """Cell-by-cell, order-sensitive comparison of two row lists keyed by `key`
    (an `address` or a `block` id -- never `ObjectId`, which does not survive
    across a live query and a mirror snapshot; see `mirror.reconcile`'s same
    choice). Distinguishes a row missing on one side, a row present on both sides
    but at a different position (reordering matters here: row order is rank), and
    a changed cell in a row present on both sides at the same position.
    """
    live_keys = [r[key] for r in live_rows]
    mirror_keys = [r[key] for r in mirror_rows]
    live_by_key = {r[key]: r for r in live_rows}
    mirror_by_key = {r[key]: r for r in mirror_rows}

    missing_from_mirror = [k for k in live_keys if k not in mirror_by_key]
    missing_from_live = [k for k in mirror_keys if k not in live_by_key]

    # The order of keys common to both sides, as each side has it. Equal means the
    # rank is identical wherever both sides agree a row belongs; unequal means a row
    # moved even though nothing else about it may have changed.
    common_live_order = [k for k in live_keys if k in mirror_by_key]
    common_mirror_order = [k for k in mirror_keys if k in live_by_key]
    reordered = common_live_order != common_mirror_order

    field_mismatches = []
    for k in common_live_order:
        lv, mv = live_by_key[k], mirror_by_key[k]
        for field in sorted(set(lv) | set(mv)):
            if lv.get(field) != mv.get(field):
                field_mismatches.append(
                    {"key": k, "field": field, "live": lv.get(field), "mirror": mv.get(field)}
                )

    return {
        "live_count": len(live_rows),
        "mirror_count": len(mirror_rows),
        "missing_from_mirror": missing_from_mirror,
        "missing_from_live": missing_from_live,
        "reordered": reordered,
        "live_order": common_live_order if reordered else None,
        "mirror_order": common_mirror_order if reordered else None,
        "field_mismatches": field_mismatches,
    }


# ------------------------------------------------------------------- brief diffing


def normalize_brief(text):
    """Strip 4.7's freshness note and the one blank line `derive._write_freshness_note`
    inserts ahead of it (see that function: `lines[insert_at:insert_at] = ["", note]`
    -- the blank line removed here is exactly the one that insertion added, not a
    coincidental blank line the brief already had). Returns `(normalized_lines,
    note_was_present)`.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _FRESHNESS_NOTE_RE.match(line):
            if i > 0 and lines[i - 1] == "":
                return lines[: i - 1] + lines[i + 1 :], True
            return lines[:i] + lines[i + 1 :], True
    return lines, False


def diff_brief(live_text, mirror_text):
    """Line-by-line comparison after normalising only the freshness note out of the
    mirror side. Every other line -- including the brief's own real figures, like
    "Most recent call in the data: ..." -- is compared as-is: those should agree
    because both runs read the same published version (checked by `freshness()`
    before either pipeline runs), not because this function excuses them.
    """
    live_lines = live_text.splitlines()
    mirror_lines, note_found = normalize_brief(mirror_text)

    mismatches = []
    for i in range(max(len(live_lines), len(mirror_lines))):
        lv = live_lines[i] if i < len(live_lines) else None
        mv = mirror_lines[i] if i < len(mirror_lines) else None
        if lv != mv:
            mismatches.append({"line": i + 1, "live": lv, "mirror": mv})

    return {
        "live_line_count": len(live_lines),
        "mirror_line_count_normalized": len(mirror_lines),
        "freshness_note_found": note_found,
        "line_mismatches": mismatches,
    }


# --------------------------------------------------------------- file-pair compare


def compare_outputs(live_dir, mirror_dir):
    """Compare every file 4.9 names between two already-written output directories.
    Pure filesystem reads -- no pipeline is run here, so this half is exercised
    offline in `tests/test_reconcile_figures.py` against fixture directories.
    """
    live_dir, mirror_dir = pathlib.Path(live_dir), pathlib.Path(mirror_dir)
    result = {"csv": {}, "brief": {}, "normalizations": [], "board_status": BOARD_STATUS}

    for name, key in CSV_FILES:
        live_path, mirror_path = live_dir / name, mirror_dir / name
        if not live_path.exists() or not mirror_path.exists():
            result["csv"][name] = {
                "error": f"missing file: live={live_path} exists={live_path.exists()}, "
                         f"mirror={mirror_path} exists={mirror_path.exists()}"
            }
            continue
        live_rows = read_csv_rows(live_path)
        mirror_rows_raw = read_csv_rows(mirror_path)
        mirror_rows, freshness_cols = strip_freshness_columns(mirror_rows_raw)
        if freshness_cols:
            result["normalizations"].append(
                f"{name}: dropped mirror-only freshness columns "
                f"{sorted(freshness_cols)} (4.7) before comparing"
            )
        result["csv"][name] = diff_csv(live_rows, mirror_rows, key)

    for name in BRIEF_FILES:
        live_path, mirror_path = live_dir / name, mirror_dir / name
        if not live_path.exists() or not mirror_path.exists():
            result["brief"][name] = {
                "error": f"missing file: live={live_path} exists={live_path.exists()}, "
                         f"mirror={mirror_path} exists={mirror_path.exists()}"
            }
            continue
        diff = diff_brief(live_path.read_text(), mirror_path.read_text())
        if diff["freshness_note_found"]:
            result["normalizations"].append(
                f"{name}: dropped mirror-only freshness note line and its leading "
                f"blank line (4.7) before comparing"
            )
        result["brief"][name] = diff

    return result


# ------------------------------------------------------------------- orchestration


def republish_warning(fresh_before, fresh_after):
    """Compare each layer's live `lastEditDate` from the pre-run `freshness()` call
    against a second call made right after both pipelines finish. Returns
    `(changed, warning)`: `changed` is a list of `{"layer", "before", "after"}` dicts
    (empty if nothing moved), and `warning` is a single human-readable string for the
    CLI and for 9.4 to reuse (`None` when `changed` is empty).

    This never aborts, never hides the diff below, and never changes the verdict --
    it only flags that a DIVERGED result might be explained by HRM publishing mid-run
    rather than by a derivation defect, per the user's call that this is unlikely
    enough to warrant a warning and not a new failure mode or an automatic re-run.
    """
    changed = []
    for layer, before in fresh_before.items():
        after = fresh_after.get(layer, {})
        b, a = before.get("live_last_edit"), after.get("live_last_edit")
        if b != a:
            changed.append({"layer": layer, "before": b, "after": a})
    if not changed:
        return changed, None
    detail = "; ".join(f"{c['layer']} ({c['before']} → {c['after']})" for c in changed)
    warning = (
        f"HRM published during the run ({detail}); discrepancies may reflect the "
        "new publish rather than a derivation defect -- re-run."
    )
    return changed, warning


def run_reconciliation(conn, violation=VIOLATION, out_dir=None, log=lambda msg: None,
                        freshness_fn=None, run_live_fn=None, run_mirror_fn=None,
                        compare_fn=None):
    """The full 4.9 reconciliation for one violation: check freshness, run both
    pipelines into scratch subdirectories of `out_dir` (a fresh temp dir if not
    given), check freshness again to catch a publish that happened during the run,
    and compare every file `compare_outputs` covers.

    The four `*_fn` parameters exist so this orchestration is itself testable
    offline: a test can inject a fake `freshness_fn` to prove the "HRM published
    since the mirror's version -> don't run, report it" path never calls the two
    pipeline runners, without spawning a real subprocess or touching Postgres.
    `freshness_fn` is called twice when the pipelines run -- once before, once
    after -- so a test injecting one should expect (and can vary) two calls.
    """
    freshness_fn = freshness_fn or reconcile.freshness
    run_live_fn = run_live_fn or run_live
    run_mirror_fn = run_mirror_fn or run_mirror
    compare_fn = compare_fn or compare_outputs

    log("checking freshness (mirror layer_state vs live editingInfo.lastEditDate) ...")
    fresh = freshness_fn(conn)
    advanced = [layer for layer, f in fresh.items() if f["advanced"]]
    if advanced:
        return {
            "violation": violation,
            "freshness": fresh,
            "board_status": BOARD_STATUS,
            "aborted": True,
            "reason": (
                f"HRM has published since the mirror's version on {', '.join(advanced)}. "
                "Not running the comparison: a mismatch against a moving target would "
                "be expected, not a defect, and this module does not sync or reload to "
                "make the question go away. Sync the mirror, then re-run."
            ),
        }

    base = pathlib.Path(out_dir) if out_dir else pathlib.Path(tempfile.mkdtemp(prefix="reconcile-figures-"))
    live_dir, mirror_dir = base / "live", base / "mirror"

    log(f"running the live pipeline for {violation!r} into {live_dir} (about 3.5 minutes) ...")
    live_proc = run_live_fn(live_dir, violation)
    log(f"live pipeline exited {live_proc.returncode}")

    log(f"running the mirror derivation for {violation!r} into {mirror_dir} ...")
    mirror_proc = run_mirror_fn(mirror_dir, violation)
    log(f"mirror derivation exited {mirror_proc.returncode}")

    comparison = compare_fn(live_dir, mirror_dir)

    log("re-checking freshness (did HRM publish while the run was in flight?) ...")
    fresh_after = freshness_fn(conn)
    changed, warning = republish_warning(fresh, fresh_after)

    return {
        "violation": violation,
        "freshness": fresh,
        "freshness_after": fresh_after,
        "republish_check": changed,
        "republish_warning": warning,
        "aborted": False,
        "out_dir": str(base),
        "live_returncode": live_proc.returncode,
        "live_stderr": live_proc.stderr,
        "mirror_returncode": mirror_proc.returncode,
        "mirror_stderr": mirror_proc.stderr,
        **comparison,
    }


def is_clean(result):
    """True when freshness held, both pipelines exited cleanly, and every file
    compared identically once the documented normalisations were applied.
    """
    if result.get("aborted"):
        return False
    if result.get("live_returncode") or result.get("mirror_returncode"):
        return False
    for d in result["csv"].values():
        if "error" in d:
            return False
        if d["missing_from_mirror"] or d["missing_from_live"] or d["reordered"] or d["field_mismatches"]:
            return False
    for d in result["brief"].values():
        if "error" in d:
            return False
        if d["line_mismatches"]:
            return False
    return True


def _truncated(items, n=20):
    shown = items[:n]
    more = f" ... ({len(items) - n} more)" if len(items) > n else ""
    return f"{shown}{more}"


def format_report(result):
    lines = [f"Figure-level reconciliation: violation={result['violation']!r}", ""]

    lines.append("Freshness (live editingInfo.lastEditDate vs mirror layer_state):")
    for layer_key, f in result["freshness"].items():
        status = (
            "ADVANCED — HRM has published since the mirror was loaded"
            if f["advanced"] else "current"
        )
        lines.append(
            f"  {layer_key:18} mirror={f['mirror_last_edit']}  live={f['live_last_edit']}  {status}"
        )
    lines.append("")

    if result.get("aborted"):
        lines.append(result["reason"])
        lines.append("")
        lines.append(result["board_status"])
        lines.append("")
        lines.append("NOT RUN")
        return "\n".join(lines)

    lines.append(f"scratch directory: {result['out_dir']}")
    lines.append(f"live pipeline exit: {result['live_returncode']}")
    lines.append(f"mirror derivation exit: {result['mirror_returncode']}")
    lines.append("")

    if result["normalizations"]:
        lines.append("Normalised before comparing (documented, expected differences):")
        for n in result["normalizations"]:
            lines.append(f"  - {n}")
        lines.append("")

    for name, _key in CSV_FILES:
        d = result["csv"].get(name, {"error": "not compared"})
        lines.append(f"{name}:")
        if "error" in d:
            lines.append(f"  ERROR: {d['error']}")
            lines.append("")
            continue
        lines.append(f"  live {d['live_count']:,}  mirror {d['mirror_count']:,}")
        if d["missing_from_mirror"]:
            lines.append(f"  missing from mirror ({len(d['missing_from_mirror'])}): "
                          f"{_truncated(d['missing_from_mirror'])}")
        if d["missing_from_live"]:
            lines.append(f"  missing from live ({len(d['missing_from_live'])}): "
                          f"{_truncated(d['missing_from_live'])}")
        if d["reordered"]:
            lines.append("  ROW ORDER DIFFERS between live and mirror for rows present on both sides")
        if d["field_mismatches"]:
            lines.append(f"  field mismatches: {len(d['field_mismatches']):,}")
            for m in d["field_mismatches"][:20]:
                lines.append(f"    {m}")
            if len(d["field_mismatches"]) > 20:
                lines.append(f"    ... ({len(d['field_mismatches']) - 20} more)")
        if not (d["missing_from_mirror"] or d["missing_from_live"] or d["reordered"]
                or d["field_mismatches"]):
            lines.append("  identical")
        lines.append("")

    for name in BRIEF_FILES:
        d = result["brief"].get(name, {"error": "not compared"})
        lines.append(f"{name}:")
        if "error" in d:
            lines.append(f"  ERROR: {d['error']}")
            lines.append("")
            continue
        lines.append(f"  live {d['live_line_count']} lines  mirror (normalized) "
                      f"{d['mirror_line_count_normalized']} lines")
        if d["line_mismatches"]:
            lines.append(f"  line mismatches: {len(d['line_mismatches'])}")
            for m in d["line_mismatches"][:20]:
                lines.append(f"    line {m['line']}: live={m['live']!r} mirror={m['mirror']!r}")
            if len(d["line_mismatches"]) > 20:
                lines.append(f"    ... ({len(d['line_mismatches']) - 20} more)")
        else:
            lines.append("  identical")
        lines.append("")

    lines.append(result["board_status"])
    lines.append("")
    if result.get("republish_warning"):
        lines.append(f"WARNING: {result['republish_warning']}")
        lines.append("")
    lines.append("RECONCILED" if is_clean(result) else "DIVERGED")
    return "\n".join(lines)


def main():
    """CLI entry point. Exit codes are unchanged by the republish check added above:
    0 means `is_clean(result)` was true (freshness held before the run, both
    pipelines exited 0, and every compared file was identical after normalisation);
    1 means anything else -- aborted-for-staleness, a nonzero pipeline exit, or a
    reported discrepancy. A mid-run republish never introduces a third code or an
    automatic re-run: it only adds the WARNING line `format_report` prints next to
    the verdict (and `republish_warning` in the returned dict) so a human reading a
    DIVERGED (exit 1) result knows to check for it before chasing a derivation bug.
    """
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--violation", default=VIOLATION,
                     help=f"violation label to reconcile (default: {VIOLATION!r})")
    ap.add_argument("--out-dir", default=None,
                     help="scratch parent directory for live/ and mirror/ subdirectories "
                          "(default: a fresh temp directory)")
    args = ap.parse_args()

    conn = db.connect()
    result = run_reconciliation(
        conn, args.violation, args.out_dir,
        log=lambda msg: print(msg, file=sys.stderr),
    )
    print()
    print(format_report(result))
    return 0 if is_clean(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
