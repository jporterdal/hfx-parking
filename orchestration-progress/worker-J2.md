# Worker J' progress (task 9.4, concern 24)
S=/tmp/claude-1000/-home-ross-work-hfx-parking/4fd54be1-5801-48cc-b221-530a6ba3846f/scratchpad/worker-J2
- triage_decisions BEFORE: 0
## Step 1 freshness gate: DONE, PASSED. layer_state unchanged (sr 2026-09-13 07:34:17.313 ADT, cf 07:38:16.944 ADT, last_success 2026-09-16),
  reconcile.freshness(): live == mirror for both layers, advanced=False. NO new live run; kept outputs in orchestration-progress/worker-J-artifacts/live are used.
## Step 2 comparison: in progress
## Step 2 DONE. Script slip fixed (compare_rows looked up served rows by the live UPPER-case key so the JSON doorway field check compared 0 cells; now keyed by the title-cased key). Fixed copy in worker-J-artifacts/compare_served_vs_live.py.
 Result: every check OK (JSON doorways 351 rows x17 fields, blocks 70x11, export.csv, export HTML, both briefs) except 1: figures.vehicles.conclusion "96% of 2528 seen" vs live 95 (concern 24).
 Tamper self-test (edited a live tows and a live gap) -> 2 mismatches per surface; so the comparison does detect. Report: worker-J-artifacts/served-vs-live-report.txt
 Rerun: python3 orchestration-progress/worker-J-artifacts/compare_served_vs_live.py orchestration-progress/worker-J-artifacts/live $S/served
## Step 3 concern 24: next
## Step 3 concern 24: CONFIRMED (2413/2528=0.95451 -> round 3dp 0.955 -> ':.0%' 96%; live brief f'{100*d/s:.0f}' = 95; /doorways unique_pct=95).
 FIXED in src/mirror/per_type.py: new _unique_pct_text(distinct, seen) = f"{100*distinct/seen:.0f}%" (same expression as hotspots.py:370) used in _vehicle_conclusion's 3 sentences and in _table (CLI text table, same defect at old line 442). Classification still compares the 3-dp share (unchanged); stored vehicles.share stays 3dp.
 Test: tests/test_verify_vehicle_pct_rounding.py (writing now).
## Step 4 DONE: xfail marker removed, `i` checked==position then dropped; 8 passed.
## Step 5 mutation proof: TODO. Also the stored type_figures table (computed_at 2026-09-16) holds the OLD sentence; see report note.
## Concern 24 test DONE: tests/test_verify_vehicle_pct_rounding.py, 12 pass (incl. end-to-end DB test via compute_and_store + GET /figures).
 IMPORTANT: /figures serves a STORED row (type_figures, keyed mirror_version+type+parameters; compute_and_store skips a type already stored). The real mirror's stored Driveway row (computed 2026-09-16) still says 96%; fix appears only after the next mirror version or a recompute (delete the row + compute_and_store: a WRITE to the real mirror, not done by J'). Read-only recompute of Driveway on the real mirror with the fix (per_type.compute_all, rolled back) gives "95% of 2528 seen".
## Step 5 DONE (scratch tree $S/tree, script $S/mutate.py): M1 drop row -> 5 fail; M2 w+1 -> 5 fail; M3 to_local UTC -> 1 fail (the hand-stated local-date test; derive shifts identically so the derive comparison alone would not catch it); M4 revert concern-24 -> 6 fail; restored 20 pass.
## Remaining: full suite, git status, triage count after, final report.
## ALL STEPS DONE. Full suite 864 passed (117 s) at the time. triage_decisions AFTER: 0. Final report delivered to the orchestrator.
Also found: README.md:94 says "2,413 distinct vehicles produced 2,528 calls. That is 96 per cent unique" (and docs/parking-hotspots/product.md:36, decisions.md:28, docs/problem-selection/five-problems-research.md:172): same double-rounding number (true 95.45%), left for the user (docs).
