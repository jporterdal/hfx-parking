# Orchestration handoff — mirror-hrm-data-and-host-app

**Temporary working document**, like `dispatch-plan.md`. Delete both when the change is archived.

`dispatch-plan.md` holds the dependency DAG; this file holds *state* — what is done, what is in
flight, and the conventions the pools have been run under.

## Read this first, before doing anything else

Verify before trusting anything below — an agent's report is a claim, not evidence:

```bash
git status --short          # whose files changed
git diff                    # what actually landed vs. what was claimed
python -m pytest -q         # 527 tests as of the Pool 8 checkpoint, all passing
openspec validate mirror-hrm-data-and-host-app --strict
```

If the tree is a partial mess and you cannot tell what is finished, `git checkout --` the uncertain
files and re-dispatch those tasks. Losing an agent's half-finished work is cheaper than committing
something nobody verified. This happened once already in this change (see "History" below).

## State at last commit

The **Pool 8 checkpoint** (`git log -1`; its parent is `c5c8271`, plus the doc-only `b04731c`).
527 tests, all passing; `openspec validate --strict` clean, both run on the working tree just
before committing.

Complete through: sections 1–4 entirely, 5.1–5.8, 6.1–6.7, 7.1–7.8 entirely, 8.1 (partial, see its
note), 8.1a, 8.2, 8.3, 8.4, 8.6, 8.7, 8.8. **Not done:** 8.5 (decisions narrative — now unblocked),
8.9 (stale-reference sweep, added this pool) and all of section 9. Several completed tasks carry
caveats in their `tasks.md` lines (7.7, 7.8, 8.4, 8.7 especially); read them before treating a task
as fully closed.

**7.2b and 7.7 were ticked on the user's word** (another agent had verified them; the orchestrator
only confirmed pytest and `openspec validate` were green) — see Pool 7 under History.

## Conventions these pools run under

- **Commit prefix** `opsx checkpoint mirror-hrm-data-and-host-app: <brief description>`, one
  checkpoint per pool, ending with the `Co-Authored-By` attribution line.
- **Workers never commit** and never edit `tasks.md` — the orchestrator verifies, ticks and commits.
  This keeps concurrent agents off the same two files.
- **Workers run on Sonnet, not Opus.**
- **Region ownership, not task-per-agent.** The binding constraint is concurrent writes to
  `web/app/index.html` and `src/app/server.py`, which have no merge step. Tasks in one code region
  go to one agent; disjoint files can go to separate agents. Workers are told: never `Write` those
  files, only `Edit` with unique anchors, never reformat untouched code, re-read immediately before
  each edit, and report any Edit that failed because an anchor had moved. Region boundaries have held
  across two pools (8 agents) with zero real collisions — the header banner / toolbar+results /
  header-intro+footer-narrative / README split works well as a default four-way cut.
- **M5 scope rule (now historical).** `web/template.html` and the committed `out/` files were
  deprecated and unreachable from the served application, so their figures were out of scope to
  correct. Both were deleted in Pool 8; the last commit containing them is `b04731c`
  (`git show b04731c:web/template.html`), recorded in `README.md` and `docs/index.md`.
- **Task notes.** When a task is completed partially, or with a caveat, the caveat is appended to
  its line in `tasks.md` rather than left in a commit message. Several completed tasks carry these;
  read them before assuming a task is fully closed.

## History

**Pool 4** (5.5a/5.5b, 6.2–6.7, 7.1/7.3/7.4a, 5.3/5.8) had to be re-dispatched once: two of the four
agents left broken, uncommitted work (a window-boundary arithmetic bug in a new `derive_recency.py`;
a timezone-offset string-comparison bug in a new `/api/freshness` route), the other two left no trace
at all. All discarded and re-dispatched with the specific prior bugs named in each new brief; all four
landed clean the second time (committed at `e1be8c5`). One agent (freshness-message) got stuck in a
self-report loop after finishing — verified independently (diff + its tests run directly) and stopped
rather than waited out further.

**Lesson carried forward:** arithmetic/timezone bugs are exactly what "verified against a
deliberately constructed boundary case" catches and "trusted from algebra/reasoning alone" doesn't —
name this explicitly in a worker's brief whenever a task involves a computed threshold, window or
offset.

**Pool 5** (5.5c/5.5e/7.4, 7.2, 7.5, 8.2) dispatched clean on the first attempt, no re-dispatch needed.
Four-way region split: toolbar+results (5.5c/5.5e/7.4), header freshness banner (7.2), header-intro+
footer-narrative (7.5), `README.md` alone (8.2). Committed at `ab0c1fa`.

**Pool 6** (5.5d, 7.2a) committed at `d1b8b5f`. **Pool 7** (7.2b, 7.7) finished writing at about
01:09 the same night but its orchestrator session ended before verifying, ticking or committing; the
next session found it as an uncommitted +1,073/−15 diff across `server.py`, `test_app.py` and
`index.html` and this handoff still describing `ab0c1fa`. Lesson: **update this file at every
checkpoint, in the same commit or the one straight after**, not later.

**Pool 8** (7.7a, 7.8 + the 7.5 footnote-link follow-up; 8.4, 8.7; then the export link and the
`out/` deletion) — four workers, all on Sonnet, ~567k tokens (A 230k, B 162k, E 101k, F 73k), no
re-dispatch. Split by file: A owned `server.py`/`test_app.py`/`index.html`; B owned README, docs and
`hotspots.py`. The user resolved M12 (retire `web/template.html`), asked for the `out/` files to be
deleted too, and for the export link to be added to the page. Two follow-on workers (E: export link;
F: `out/` deletion and `--board` removal) ran in parallel on disjoint files. Two tasks per worker was
used where regions allowed (A: 7.7a+7.8+footnote links; B: 8.4+8.7); E and F each held one or two.
The user directed the stale-reference sweep and the `reconcile_figures` cleanup to be handled as a
later task (8.9) or one-off; the `--board` removal was done as a one-off here.

## Open concerns, carried across pools

1. **5.6 and 5.5a are ticked without a browser check.** Both ask for something a human watching a
   live page would confirm (a colour-scheme toggle; the toolbar's Apply-filters button). No browser
   exists in the agent environment; verification was code-level / over raw HTTP. Needs a human doing
   the actual interaction once, in a live app, to fully close either out. **The same is true of every
   Pool 8 page change** (type naming, footnote links, the export link and its href updates on filter
   changes); the export-link worker exercised the href logic in node with stubs only.
2. **7.6's explanation is hover-only** — `title` attributes, invisible on touch and to keyboard users,
   column hidden below 860px. Not failed (substantive framing is in the detail panel), just thinner
   than it looks. 7.5's agent explicitly chose a visible, focusable link instead for the same reason,
   for the six limits it covers — 7.6 itself was left as is.
3. **8.1 is partial by decision** — regeneration scripts for several one-off counts do not exist,
   `decisions.md` keeps its original figures as a historical record. Deferred to a future change.
4. **`"none_"` is stored as a real row**, so "never triaged" and "reverted to untriaged" are
   indistinguishable server-side. Confirmed still true; a schema/semantics question bigger than any
   one task, not fixed.
5. **5.2's design choices were the agent's own** — path routing (`/types/<slug>`) plus a full-page
   `<select>`. Defensible, unspecified, and 7.7 will build exports around it.
6. ~~M12: what happens to `web/template.html`~~ **Resolved** by the user: retired, deleted in Pool 8
   after a gap check found nothing the served page lacks.
7. ~~7.5's footnotes only reach the header~~ **Resolved** in Pool 8: links now in the detail panel and,
   for list rows, in a `#list-limits` line above the block list (rows are buttons).
8. ~~8.2's README sequencing~~ **Resolved** by 8.4's rewrite of the "Open the board" paragraph.
9. **5.5c's `"rp"` (repeat_calls) payload field is provable but not shown** — added to make the
   recency-vs-recurrence distinction testable over HTTP; nothing in the UI displays it. Worth a look
   if that figure should be user-visible, not just internally correct.
10. **Export race (7.8 caveat).** An export reads its rows and its freshness clocks in separate
    queries, so a sync landing mid-request could name a different mirror state than its rows. Fix by
    reading both in one transaction/snapshot. Bears on 9.5.
11. **Driveway-specific text on every type's page (bears on 9.8).** The "Why blocks" footer hard-codes
    driveway numbers on every type's page, and the Ask-Claude prompt says "blocked-driveway
    complaints" for every type. 9.8 (no type presents blocked driveway's conclusion without its own
    evidence) cannot pass until both are made type-specific or removed.
12. **The user has not yet decided** whether to run a follow-up pool for concerns 10 and 11 before
    section 9, or leave them for section 9's findings.
13. **Interpretation in 8.7's caveat:** "the only way a list leaves the application" is read as the
    served application. `src/mirror/derive.py` and `src/hotspots.py` (the 4.9/9.4 live baseline) still
    write lists to a caller-named directory, and the JSON routes feed the page.
14. **Small UX note from the export-link worker:** the District select filters the on-screen list
    client-side, so after "Apply filters" with a district it lists only that district's options.
    The export follows the on-screen district anyway.
15. **`test_export_csv_and_brief_agree_on_row_counts_with_the_view` is now redundant** with the 7.8
    tests; harmless, can be deleted in a later sweep.

## Where the work goes next

**Ready now** (dependencies met), per `dispatch-plan.md`:
- **8.5** (record the decisions in `docs/parking-hotspots/decisions.md`: mirror, source-paced sync,
  served application, shared triage, role selection, removal of the scheduled job; verify the
  narrative matches the specs). Its dependencies 8.1, 8.1a, 8.2, 8.3, 8.4, 8.6, 8.7, 8.8 are all
  done. Docs only. Should also record the M12 decision and the `b04731c` pointer.
- **8.9** (sweep stale references to the retired template, the deleted `out/` files and bare
  `python3 src/hotspots.py`; the known sites are listed in its `tasks.md` line). Comments, docstrings
  and docs across several files, including `server.py`, `index.html` and `test_app.py` — one worker
  owns all of those, so it cannot run beside a worker that edits them.
- **Concerns 10 and 11** above (export race; driveway-specific footer and Ask-Claude prompt). Not
  numbered tasks; both live in `server.py`/`index.html`/`test_app.py`.
- **Section 9** (verification) is now reachable: 9.1–9.8 have their dependencies met. 9.8 should
  wait for concern 11; 9.5 is best run after concern 10.

**Region split:** 8.5 touches only `docs/parking-hotspots/decisions.md`; 8.9 and concerns 10/11 all
touch `server.py`/`index.html`/`test_app.py`, so they go to one worker (or run in sequence), while
8.5 can run beside it. Workers running concurrently run only their own test files; the orchestrator
runs the full suite once at the end.

**Dispatch convention for this session (user instruction):** two tasks per sub-agent within a pool
wherever the regions allow it, and otherwise separate sub-agents running asynchronously.

## Cost so far

Figures below cover pools 1–5 only; pools 6 and 7 were not recorded; Pool 8 is in its History entry.
Worker agents across five completed pools: ~1.94M tokens (17 agents). Pool 5 (4 agents, clean on
first attempt) ran ~493k tokens total, in line with Pool 4's per-agent average once you account for
Pool 4's re-dispatch. Region-bundling (multiple tasks per agent, one region) continues to look cheaper
than one-agent-per-task, per Pool 3's original finding.
