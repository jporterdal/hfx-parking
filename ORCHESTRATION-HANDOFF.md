# Orchestration handoff — mirror-hrm-data-and-host-app

**Temporary working document**, like `dispatch-plan.md`. Delete both when the change is archived.

`dispatch-plan.md` holds the dependency DAG; this file holds *state* — what is done, what is in
flight, and the conventions the pools have been run under.

## Read this first, before doing anything else

Verify before trusting anything below — an agent's report is a claim, not evidence:

```bash
git status --short          # whose files changed
git diff                    # what actually landed vs. what was claimed
python -m pytest -q         # 481 passing as of 9fe0d7d
openspec validate mirror-hrm-data-and-host-app --strict
```

If the tree is a partial mess and you cannot tell what is finished, `git checkout --` the uncertain
files and re-dispatch those tasks. Losing an agent's half-finished work is cheaper than committing
something nobody verified. This happened once already in this change (see "History" below).

## State at last commit

`9fe0d7d` — the last checkpoint. 481 tests passing, `openspec validate --strict` clean (both run
against the working tree just before it was committed).

Complete through: sections 1–4 entirely, 5.1–5.8 entirely (incl. 5.5a–5.5e), 6.1–6.7 entirely,
7.1–7.7 entirely (7.2a and 5.5d landed in `d1b8b5f`; 7.2b and 7.7 landed in `9fe0d7d`), 8.1 (partial,
see its note), 8.1a, 8.2, 8.3, 8.6, 8.8.

**7.2b and 7.7 were ticked on the user's word**, not re-verified by the orchestrator: they were
written by a pool whose orchestrator session ended before it verified, ticked or committed, leaving
the work uncommitted in the tree. The user confirmed another agent had verified both. The
orchestrator only confirmed pytest and `openspec validate` were green before committing.

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
- **M5 scope rule.** `web/template.html` and the committed `out/` files are deprecated and
  unreachable from the served application. Their figures and claims are **out of scope to correct**;
  a task naming a stale claim is satisfied by fixing the served page. Every worker is told this,
  because both files hold near-identical copies of the served page's defects.
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

## Open concerns, carried across pools

1. **5.6 and 5.5a are ticked without a browser check.** Both ask for something a human watching a
   live page would confirm (a colour-scheme toggle; the toolbar's Apply-filters button). No browser
   exists in the agent environment; verification was code-level / over raw HTTP. Needs a human doing
   the actual interaction once, in a live app, to fully close either out.
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
6. **M12 leaves a decision open, and it now blocks 8.7**: whether `web/template.html` is folded back
   into the served page or stays a separate file (`design.md` M12 says the change "does not make" it
   and that it "needs one"). It decides whether 8.7 deletes the template and `write_board()` or keeps
   `hotspots.py`'s standalone board as an artefact. `src/app/server.py` imports functions from
   `hotspots.py`, so the module itself stays either way; only its `main()` and the `out/` outputs are
   in question. **Do not dispatch 8.7 until the user has made this call.**
7. **7.5's dwelling-rate/block-label footnotes only reach the header stats**, not the actual figures
   in the results list/detail panel (`evidenceBlock()`) — that region belonged to a different agent
   this pool. A follow-up should add the same `#lim-dwelling`/`#lim-block-label` links there.
8. **8.2's fix sits awkwardly next to the still-uncorrected "Open the board" paragraph** above it in
   `README.md` (double-click/browser-local-storage instructions, left alone per M5) — no factual
   error in either sentence, but the sequencing reads oddly until 8.4 replaces the preceding paragraph.
9. **5.5c's `"rp"` (repeat_calls) payload field is provable but not shown** — added to make the
   recency-vs-recurrence distinction testable over HTTP; nothing in the UI displays it. Worth a look
   if that figure should be user-visible, not just internally correct.

## Where the work goes next

The 7.7 bottleneck has cleared. Per `dispatch-plan.md`'s DAG, now ready:
- **7.7a** (name the canonical type each export and view covers) and **7.8** (a view and an export
  from one mirror state agree on every count). Both work in the export routes and their tests:
  `src/app/server.py`, `tests/test_app.py`, and `web/app/index.html` if the view side needs it. 7.7
  already carries the type name and a row-count agreement test, so the agent should check what is
  already covered before adding anything.
- **8.4** (replace the README "Open the board" instructions with the URL and export path, including
  `docs/index.md`'s file table). Docs only: `README.md`, `docs/index.md`.
- **8.7** (stop generating committed output to `out/`) — **held**, see concern 6. When it is
  released it touches `src/hotspots.py`, the tracked `out/` files and possibly `web/template.html`,
  and it must not edit `README.md` or `docs/index.md` (8.4's), only report what it makes stale.
  Verification for it belongs in a new test file, not `tests/test_app.py`.

**8.5** (decisions.md narrative) waits on 8.4 and 8.7. Section 9 is verification and comes last.

**Region split for the ready tasks** (checked for file overlap): 7.7a+7.8 own `server.py`,
`test_app.py` and `index.html`; 8.4 owns `README.md` and `docs/index.md`; 8.7 (when released) owns
`hotspots.py`, `out/` and any new test file. None share a file. Workers running concurrently should
run only their own test files, since the full suite can fail transiently on another agent's
half-finished edit; the orchestrator runs the full suite once at the end.

**Dispatch convention for this session (user instruction):** two tasks per sub-agent within a pool
wherever the regions allow it, and otherwise separate sub-agents running asynchronously.

## Cost so far

Figures below cover pools 1–5 only; pools 6 and 7 were not recorded. Worker agents across five
completed pools: ~1.94M tokens (17 agents). Pool 5 (4 agents, clean on
first attempt) ran ~493k tokens total, in line with Pool 4's per-agent average once you account for
Pool 4's re-dispatch. Region-bundling (multiple tasks per agent, one region) continues to look cheaper
than one-agent-per-task, per Pool 3's original finding.
