# Orchestration handoff — mirror-hrm-data-and-host-app

**Temporary working document**, like `dispatch-plan.md`. Delete both when the change is archived.

`dispatch-plan.md` holds the dependency DAG; this file holds *state* — what is done, what is in
flight, and the conventions the pools have been run under.

## Read this first, before doing anything else

Verify before trusting anything below — an agent's report is a claim, not evidence:

```bash
git status --short          # whose files changed
git diff                    # what actually landed vs. what was claimed
python -m pytest -q         # 529 tests as of the Pool 9 checkpoint, all passing
openspec validate mirror-hrm-data-and-host-app --strict
```

If the tree is a partial mess and you cannot tell what is finished, `git checkout --` the uncertain
files and re-dispatch those tasks. Losing an agent's half-finished work is cheaper than committing
something nobody verified. This happened once already in this change (see "History" below).

## State at last commit

The **Pool 9 checkpoint** (`git log -1`; its parent is the doc-only `05a16ce`, after the Pool 8
checkpoint `be723cf`). 529 tests, all passing; `openspec validate --strict` clean, both run on the
working tree just before committing.

Complete through: sections 1–4 entirely, 5.1–5.8, 6.1–6.7, 7.1–7.8 entirely, 8.1 (partial, see its
note), 8.1a, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9. **Not done:** all of section 9 (9.1–9.8).
**Section 8 is complete.** Several completed tasks carry
caveats in their `tasks.md` lines (7.7, 7.8, 8.4, 8.5, 8.7, 8.9 especially); read them before treating a task
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

**Pool 9** (8.5; 8.9 + the export race fix) — two workers, both Sonnet, ~271k tokens (G 108k, H 163k),
no re-dispatch, no collisions. G owned `decisions.md` only; H owned the code, tests, `product.md` and
the sweep files. The orchestrator corrected one line of G's text (a quiet night is now 6 requests, not
3, per design) and independently re-ran the race test against a scratch copy with the fix removed: both
parametrizations fail with the stale-mix assertion, and pass with it. Per the user's instruction the
driveway footer and Ask-Claude prompt were NOT touched (concern 11, deferred to Section 9's dispatch).

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
10. ~~Export race (7.8 caveat)~~ **Fixed for the two export routes** in Pool 9: `_pin_snapshot` runs
    `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` first, and a constructed-interleaving
    test proves it. **Same shape, not fixed:** `GET /api/freshness` reads `mirror_freshness` and
    `source_edit_history` in separate queries, and `derive_with_recency` reads calls, fields and census
    separately on `/doorways` and `/blocks`; the page's four HTTP fetches can never share a snapshot.
    Bears on 9.5 (compare view and export from one mirror state, so run 9.5 with the mirror quiet).
11. **Driveway-specific text on every type's page (bears on 9.8).** The "Why blocks" footer hard-codes
    driveway numbers on every type's page, and the Ask-Claude prompt says "blocked-driveway
    complaints" for every type. 9.8 (no type presents blocked driveway's conclusion without its own
    evidence) cannot pass until both are made type-specific or removed. **User decision: these two
    text fixes are deferred, and are to be made when Section 9 is being dispatched** (as a
    precondition to 9.8), not in the next pool. Also noted on 9.8's line in `tasks.md`.
12. ~~Export race in Pool 9~~ done; the user's "what you did was correct" confirmed the reading.
13. **Interpretation in 8.7's caveat:** "the only way a list leaves the application" is read as the
    served application. `src/mirror/derive.py` and `src/hotspots.py` (the 4.9/9.4 live baseline) still
    write lists to a caller-named directory, and the JSON routes feed the page.
14. **Small UX note from the export-link worker:** the District select filters the on-screen list
    client-side, so after "Apply filters" with a district it lists only that district's options.
    The export follows the on-screen district anyway.
15. **`test_export_csv_and_brief_agree_on_row_counts_with_the_view` is now redundant** with the 7.8
    tests; harmless, can be deleted in a later sweep.
16. **Stale wording left in `docs/parking-hotspots/product.md`** (found by 8.9's worker, outside the
    sweep's search terms): line ~46 "A scheduled job that reads HRM open data and writes a ranked list"
    and line ~99 "All run live against HRM open data". Both are product wording; the user decides.
17. **Spec/proposal drift found by 8.5's cross-check, unresolved:** `proposal.md` still describes the
    retired watermark/open-set sync and "`out/` at most an export"; the spec, design M4 and 3.1 still
    list a "watermark" among recorded sync outcomes; design leaves the grace period open while 7.2a set
    it from `POLL_INTERVAL`; `ROLE_OPTIONS` in the page has a third role, "Other", that spec and design
    do not name (and design M7 says the role is chosen "on entry", the spec "before recording").

## Where the work goes next

Sections 1–8 are complete. What remains is **Section 9 (verification), 9.1–9.8**, whose dependencies
are all met. Before dispatching it:
- **Concern 11 first (user decision):** make the "Why blocks" footer and the Ask-Claude prompt
  type-specific or remove the driveway-specific text; that is a precondition to 9.8 and is to be done
  when Section 9 is being dispatched. It touches `web/app/index.html` (and possibly `server.py`).
- **9.5** (board and exports agree on every count) is best run with the mirror quiet, given concern 10's
  residual.
- 9.4 uses the live baseline (`src/hotspots.py`), which needs network access to HRM.
- Section 9 tasks are verification, so most are read-only or test-writing; check file ownership as
  usual before pairing any two in a pool.
- Open human check: no browser has ever exercised any page change (concern 1).

**Dispatch convention for this session (user instruction):** two tasks per sub-agent within a pool
wherever the regions allow it, and otherwise separate sub-agents running asynchronously.

## Cost so far

Figures below cover pools 1–5 only; pools 6 and 7 were not recorded; Pool 8 is in its History entry.
Worker agents across five completed pools: ~1.94M tokens (17 agents); Pool 8 (~567k) and Pool 9 (~271k) are in their History entries. Pool 5 (4 agents, clean on
first attempt) ran ~493k tokens total, in line with Pool 4's per-agent average once you account for
Pool 4's re-dispatch. Region-bundling (multiple tasks per agent, one region) continues to look cheaper
than one-agent-per-task, per Pool 3's original finding.
