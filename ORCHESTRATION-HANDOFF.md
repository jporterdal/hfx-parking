# Orchestration handoff — mirror-hrm-data-and-host-app

**Temporary working document**, like `dispatch-plan.md`. Delete both when the change is archived.

Written 2026-09-17 by an orchestrating session that was approaching its token limit, so a later
session can pick the work up without re-deriving it. `dispatch-plan.md` holds the dependency DAG;
this file holds *state* — what is done, what is in flight, and the conventions the pools have been
run under.

## Read this first, before doing anything else

Four worker agents were dispatched and **may not have finished**. Their work is in the working tree,
**uncommitted**. Nothing below is trustworthy until you check the tree yourself:

```bash
git status --short          # whose files changed
git diff                    # what actually landed vs. what was claimed
python -m pytest -q         # 400 passing as of 798a025
```

**Verify before you tick anything.** An agent's report is a claim, not evidence — over this session
one agent's self-reported token use was 45% low, and one diverged from its instructions (correctly,
as it happened, but it had to be caught by reading the diff). Read the diff, run the tests, then
tick `tasks.md` yourself.

If the tree is a partial mess and you cannot tell what is finished, `git stash` or `git checkout --`
the uncertain files and re-dispatch those tasks. Losing an agent's half-finished work is cheaper
than committing something nobody verified.

## State at last commit

`798a025` — the last verified-good checkpoint. 400 tests passing, `openspec validate` clean.

Complete through: sections 1–4 entirely, 5.1, 5.2, 5.4, 5.5, 5.6, 5.7, 6.1, 7.6, 8.1 (partial, see
its note), 8.1a, 8.3, 8.6, 8.8. 36 tasks remain unticked.

## In flight when this was written

Four agents, dispatched together, each given one named region of `web/app/index.html` because all
four touch it:

| Tasks | Region |
|---|---|
| **5.5a** (bottleneck), 5.5b — filter controls, recency window as a parameter | Toolbar, near `.district-filter` / `#type-switch` |
| 6.2, 6.3, 6.4, 6.6, 6.5, 6.7 — rest of section 6 | Triage code (`save`, `saveDecision`, `loadDecisions`) |
| 7.1, 7.3, 7.4a — freshness message | Header banner + footer "Source" paragraph |
| 5.3, 5.8 — untracked types, stored figures | Thesis paragraph (`#tow-thesis`) |

If a task above is not ticked in `tasks.md` and its work is not in the tree, treat it as not started.

## Conventions these pools ran under

- **Commit prefix** `opsx checkpoint mirror-hrm-data-and-host-app: <brief description>`, one
  checkpoint per pool, ending with the `Co-Authored-By` attribution line.
- **Workers never commit** and never edit `tasks.md` — the orchestrator verifies, ticks and commits.
  This keeps concurrent agents off the same two files.
- **Workers run on Sonnet, not Opus.**
- **Region ownership, not task-per-agent.** The binding constraint is concurrent writes to
  `web/app/index.html` and `src/app/server.py`, which have no merge step. Tasks in one code region
  go to one agent; disjoint files can go to separate agents. Workers are told: never `Write` those
  files, only `Edit` with unique anchors, never reformat untouched code, re-read immediately before
  each edit, and report any Edit that failed because an anchor had moved.
- **M5 scope rule.** `web/template.html` and the committed `out/` files are deprecated and
  unreachable from the served application. Their figures and claims are **out of scope to correct**;
  a task naming a stale claim is satisfied by fixing the served page. Every worker is told this,
  because both files hold near-identical copies of the served page's defects.
- **Task notes.** When a task is completed partially, or with a caveat, the caveat is appended to
  its line in `tasks.md` rather than left in a commit message. Several completed tasks carry these;
  read them before assuming a task is fully closed.

## Open concerns, carried across pools

1. **5.6 is ticked without the browser check.** Its verify clause asks for a system colour-scheme
   toggle with the app open. No browser exists in the agent environment; verification was
   code-level. Needs ~30 seconds of a human toggling dark mode.
2. **7.6's explanation is hover-only** — `title` attributes, so invisible on touch and to keyboard
   users, and the column is hidden below 860px. The substantive framing is in the detail panel, so
   the task is not failed, but it is thinner than it looks.
3. **8.1 is partial by decision** — regeneration scripts for several one-off counts do not exist,
   and `decisions.md` keeps its original figures as a historical record. Deferred to a future
   change; the full note is on the task.
4. **The wider Driveway narrative is unfixed.** The `<h1>` "Blocks, not doorways", the non-tow
   thesis sentences and the footer essay are Driveway-specific reasoning now displayed under all 30
   routed types. 5.8 only removes the frozen tow figure. This belongs to **7.5** and **9.8** and is
   bigger than any one sentence.
5. **A failed triage write may still display as recorded** — `save()` updates optimistically then
   POSTs. **6.4** is what must fix this; if 6.4 did not land, the defect is live.
6. **`"none_"` is stored as a real row**, so "never triaged" and "reverted to untriaged" are
   indistinguishable server-side. May matter to 6.5/6.7.
7. **5.2's design choices were the agent's own** — path routing (`/types/<slug>`) plus a full-page
   `<select>`. Defensible, unspecified, and 7.7 will build exports around it.
8. **5.5's widened-threshold timings are not from live HTTP requests** — taken by calling
   `derive()` directly, because no HTTP surface exposed filter parameters. 5.5a should re-measure.
9. **M12 leaves a decision open**: whether `web/template.html` is folded back into the served page
   or stays a separate file. It governs 8.7.

## Where the work goes next

The next real bottleneck is **7.7** (export doorway and block lists plus a brief). It waits on 5.2
(done), 5.5d and 7.1. It unblocks 7.7a, 7.8, 8.4 and critically **8.7** — `out/` generation must not
be turned off before its replacement exists.

Once the in-flight pool lands, 5.5c → 5.5d opens (after 5.5a and 5.5b), and 7.2 → 7.2a → 7.2b opens
(after 7.1). Section 9 is verification and comes last.

## Cost so far

Worker agents across three completed pools: ~660k tokens (9 agents). Single-task agents against the
served app ran 120–140k each; bundling a second task into the same region cost ~15k more rather
than a fresh ~120k, because the second task reuses the first's reading. That is the argument for
region-based bundling over one-agent-per-task.
