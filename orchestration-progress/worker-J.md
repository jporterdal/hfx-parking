# Worker J progress (task 9.4) -- FINAL (stopped at the user's usage limit, 2026-09-18 ~17:50 ADT)

NOTE: tests/test_verify_served_matches_derive.py is UNFINISHED. 4 tests pass; the 4 parametrized
`test_served_rows_equal_derives_rows_key_by_key_on_every_surface[...]` are held as
xfail(strict=True, reason "unfinished, see orchestration-progress/worker-J.md"). Cause is a test bug:
served JSON rows carry a rank index key `i` that derive's CSV rows lack, so diff_csv reports an `i` field
mismatch. Fix: drop `i` from each served JSON row before diff_csv (JSON doorways and blocks), then remove the marker.
The scratch-tree mutation proof (drop a row / change a count / UTC date) was NOT done.

## Done
- Freshness gate PASSED (before the live run): service_requests mirror 2026-09-13 07:34:17.313 ADT == live 10:34:17.313 UTC (not advanced);
  custom_fields mirror 07:38:16.944 ADT == live 10:38:16.944 UTC (not advanced). Republish check after the run: no WARNING printed.
- Live run 1 of max 2 DONE (do not re-run): `python3 src/mirror/reconcile_figures.py --violation Driveway --out-dir $S/reconcile`
  (NOTE the CLI has no `--keep`; a given --out-dir is kept anyway). Verdict: RECONCILED (exit 0). live pipeline exit 0, mirror derivation exit 0.
  watchlist.csv live 351 / mirror 351 identical; blocks.csv live 70 / mirror 70 identical; watchlist.md 66 lines identical;
  blocks.md 48 lines identical; no discrepancies; only the documented freshness columns/note normalised.
- triage_decisions rows BEFORE: 0 (AFTER not re-counted; only GETs and a throwaway-schema test were run, so it should still be 0).

S = /tmp/claude-1000/-home-ross-work-hfx-parking/f5305660-cf05-4837-8db3-a71dce92ad31/scratchpad/worker-J
- KEPT live outputs: $S/reconcile/live/{watchlist.csv,watchlist.md,blocks.csv,blocks.md}; derive outputs $S/reconcile/mirror/ (dir listing showed empty at last look, the derive run wrote it after);
  report: $S/reconcile-report.txt; stderr: $S/reconcile-stderr.txt

## Filter defaults (state in report)
hotspots: min_calls=2, recur_days=365, min_doorways=2, district=None, recency fixed 365 days (build()). Server _FILTER_DEFAULTS: min_calls=2, min_doorways=2, recur_days=365, recency_days=365, district None. They match.
App slug for Driveway: `blocking-driveway` (canonical "Blocking Driveway"; /api/types default_slug is the same).

## Files created
- tests/test_verify_served_matches_derive.py (unfinished, above)
- $S/compare_served_vs_live.py -- WRITTEN BUT NEVER RUN. Usage: `cd /home/ross/work/hfx-parking && python3 $S/compare_served_vs_live.py $S/reconcile/live $S/served`
  (GET-only via Flask test client on the real mirror; compares JSON /doorways, /blocks, export.csv, /types/<slug>/export tables
  and both live briefs' figures against the live outputs, key by key, prints a NORMALISATIONS list). It may need small fixes when first run.
- orchestration-progress/worker-J.md (this file)

## Finding so far (from reading, not yet in a comparison run)
GET /api/types/blocking-driveway/figures: vehicles.conclusion and overall_conclusion say "96% of 2528 seen", while the live brief says
"95 per cent unique" (2413/2528 = 95.45%) and the app's own /doorways summary.unique_pct = 95. Cause: src/mirror/per_type.py:285
`share = round(distinct/seen, 3)` (0.955) then `f"{share:.0%}"` = 96%, a double rounding. Only /figures serves that sentence (the page shows a fixed
sentence for mostly_distinct; the exports carry only the tow sentence). Not fixed (product code).
Served /doorways summary for the real mirror: count 351, blocks 70, tow_pct 4.6, distinct 2413, seen 2528, unique_pct 95, latest 2026-09-11 -- consistent with the live brief's counts (351/70).

## Remaining / exact next step
1. `python3 $S/compare_served_vs_live.py $S/reconcile/live $S/served` (no live run needed), fix script slips if any, record the output (report in $S/served/report.txt).
2. Fix the `i` key in the test (above), remove the xfail marker, run only `venv/bin/pytest tests/test_verify_served_matches_derive.py`.
3. Scratch-tree proof: copy tree with `git ls-files -z --cached --others --exclude-standard | xargs -0 -I{} cp --parents {} $S/tree`, break the export (drop a row in `_doorway_payload`, change a `w` count, shift `hotspots.to_local` to UTC), confirm the test FAILS there and PASSES on the real tree.
4. Final report (task 9.4 wording), `git status --short` should show only the new test and orchestration-progress/ from this worker (server.py, index.html and test_verify_unreachable_and_overdue.py are other workers' edits), triage_decisions count after.
