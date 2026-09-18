# Orchestration handoff — mirror-hrm-data-and-host-app

**Temporary working document**, like `dispatch-plan.md`. Delete both when the change is archived.

`dispatch-plan.md` holds the dependency DAG; this file holds *state* — what is done, what is in
flight, and the conventions the pools have been run under.

## Read this first, before doing anything else

Verify before trusting anything below — an agent's report is a claim, not evidence:

```bash
git status --short          # whose files changed
git diff                    # what actually landed vs. what was claimed
python -m pytest -q         # 435 passing as of e1be8c5
openspec validate mirror-hrm-data-and-host-app --strict
```

If the tree is a partial mess and you cannot tell what is finished, `git checkout --` the uncertain
files and re-dispatch those tasks. Losing an agent's half-finished work is cheaper than committing
something nobody verified. This happened once already in this change (see "History" below).

## State at last commit

`e1be8c5` — the last verified-good checkpoint. 435 tests passing, `openspec validate` clean.

Complete through: sections 1–4 entirely, 5.1, 5.2, 5.3, 5.4, 5.5, 5.5a, 5.5b, 5.6, 5.7, 5.8, 6.1–6.7
entirely, 7.1, 7.3, 7.4a, 7.6, 8.1 (partial, see its note), 8.1a, 8.3, 8.6, 8.8.

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
  each edit, and report any Edit that failed because an anchor had moved.
- **M5 scope rule.** `web/template.html` and the committed `out/` files are deprecated and
  unreachable from the served application. Their figures and claims are **out of scope to correct**;
  a task naming a stale claim is satisfied by fixing the served page. Every worker is told this,
  because both files hold near-identical copies of the served page's defects.
- **Task notes.** When a task is completed partially, or with a caveat, the caveat is appended to
  its line in `tasks.md` rather than left in a commit message. Several completed tasks carry these;
  read them before assuming a task is fully closed.

## History

A pool of four region-scoped agents (5.5a/5.5b, 6.2–6.7, 7.1/7.3/7.4a, 5.3/5.8) was dispatched
against `798a025`. Two of the four (freshness-message and filter-controls) left broken, uncommitted
work in the tree — 4 test failures in a new `derive_recency.py` (window-boundary arithmetic bug) and
3 in a new `/api/freshness` route (comparing raw `.isoformat()` strings across timezone offsets). The
other two (triage, thesis) had left no trace at all. All of it was discarded (`git checkout --` /
`rm`) rather than trusted, and the same four task groups were re-dispatched from a clean tree with
the specific prior bugs named in each new agent's brief, so they wouldn't repeat them. All four
landed clean this time (435 tests, `openspec validate` clean) and are committed at `e1be8c5`. One of
the four (freshness-message) got stuck in a self-report loop after finishing its edits — its work was
independently verified (diff read, its 7 tests run directly) rather than waiting further, and the
stuck agent was stopped.

**Lesson for future pools:** the arithmetic/timezone bugs were exactly the kind of thing "verified
against a deliberately constructed boundary case" catches and "trusted from algebra/reasoning alone"
doesn't. Worth naming explicitly in a worker's brief whenever the task involves a computed threshold,
window or offset.

## Open concerns, carried across pools

1. **5.6 is ticked without the browser check.** Its verify clause asks for a system colour-scheme
   toggle with the app open. No browser exists in the agent environment; verification was
   code-level. Needs ~30 seconds of a human toggling dark mode.
2. **5.5a is ticked without the browser check**, for the same reason — verified over real HTTP
   requests, not by watching the toolbar's Apply-filters button in a live page.
3. **7.6's explanation is hover-only** — `title` attributes, so invisible on touch and to keyboard
   users, and the column is hidden below 860px. The substantive framing is in the detail panel, so
   the task is not failed, but it is thinner than it looks.
4. **8.1 is partial by decision** — regeneration scripts for several one-off counts do not exist,
   and `decisions.md` keeps its original figures as a historical record. Deferred to a future
   change; the full note is on the task.
5. **The wider Driveway narrative is unfixed.** The `<h1>` "Blocks, not doorways", the non-tow
   thesis sentences and the footer essay are Driveway-specific reasoning now displayed under all 30
   routed types. 5.8 only fills the tow figure per type; it doesn't vary the qualitative claim by
   the stored conclusion (see 5.8's own task note — flagged as a judgment call worth a second look).
   This belongs to **7.5** and **9.8** and is bigger than any one sentence.
6. **`"none_"` is stored as a real row**, so "never triaged" and "reverted to untriaged" are
   indistinguishable server-side. Confirmed still true by the 6.x agent; noted as a schema/semantics
   question bigger than any one task, not fixed.
7. **5.2's design choices were the agent's own** — path routing (`/types/<slug>`) plus a full-page
   `<select>`. Defensible, unspecified, and 7.7 will build exports around it.
8. **M12 leaves a decision open**: whether `web/template.html` is folded back into the served page
   or stays a separate file. It governs 8.7.

## Where the work goes next

Per `dispatch-plan.md`'s DAG, now ready (dependencies satisfied by this checkpoint):
- **7.2** (attribute lag to owning system) — waits on 7.1, 3.4, both done
- **5.5c** (distinctly label recency vs recurrence windows), **5.5e** (report empty filter result as
  empty) — wait on 5.5a/5.5b, both done
- **7.4** (state recency window anchor/length on filtered list) — waits on 5.5b, done
- **8.2** (correct hosted-copy claim) — waits on 5.1, 6.1, both done
- **7.5** (carry interpretation limits into served views) — waits on 5.1, 5.2, 4.11, 4.12, 4.16–4.19,
  all done

7.2a → 7.2b and 5.5d open once 7.2 and 5.5c land, respectively. The next real bottleneck after that
is **7.7** (export doorway and block lists plus a brief) — waits on 5.2 (done), 5.5d, 7.1 (done) — and
unblocks 7.7a, 7.8, 8.4 and critically **8.7** (`out/` generation must not be turned off before its
replacement exists). Section 9 is verification and comes last.

## Cost so far

Worker agents across four completed pools: ~1.45M tokens (13 agents). The fourth pool (this one, 4
agents, re-dispatched once) ran ~790k tokens total — higher than the three earlier pools' ~660k/9
average, consistent with region-bundling: each of these four agents carried more tasks per region
than earlier pools did.
