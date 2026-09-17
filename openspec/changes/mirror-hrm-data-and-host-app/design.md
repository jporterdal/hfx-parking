## Context

See `proposal.md` — Why. This change converts a stateless single-file pipeline into a mirrored data store behind a hosted application, and records which of the sibling change's decisions that overturns and which survive untouched.

The sibling change `add-parking-hotspot-map` records the product as built. Its decisions D1 through D19 are the baseline. Four of them are retired here; the rest stand.

Measurements taken live on 2026-09-15 against `services2.arcgis.com/11XBiaBYA9Ep0yNJ`:

| Measurement | Value |
|---|---|
| Offset page fetch, 1,000 rows | 0.52 s, 130 KB (re-measured during the load: 0.77 s requests, 1.06 s custom fields) |
| Current per-type run, `Driveway` | 3 m 31 s for 9,834 calls |
| — of which census polygons | 3.7 s |
| Distinct raw `Alleged Violation` labels | 71 |
| Calls carrying an alleged violation | 110,900 |
| — excluding `Other` (7,542) and `Left Running` | ~103,300 |
| Custom-fields layer, full | 1,156,710 rows, 88 field names (the 7 parking fields are 776,298 of them) |
| Requests layer, all categories | 478,458 rows |
| Requests still open (`DATE_CLOSED IS NULL`) | 3,351 |
| Highest `ObjectId` on requests layer | 478458, dated 2026-09-11 |

Two of those deserve emphasis. The current run is slow because of its query shape, not its volume: it fetches by identifier chunk at roughly six seconds per chunk, while offset paging the same service is twelve times faster per row. And the census layer, which D14a flags as the one fetch without retry protection, is 3.7 seconds of a 211-second run — the fix is as cheap as that decision claims.

## Goals / Non-Goals

**Goals:**

- Make the cost of an additional violation type a grouping operation rather than a network pass.
- Make staleness visible on the page, because a mirror can be stale in ways a live query cannot.
- Give two people at a demo one shared list, on any host.
- Preserve every interpretation limit the sibling change established. Moving data closer must not strengthen a single claim.

**Non-Goals:**

- Authentication. Role selection is attribution, not access control.
- Deployment mechanics, provisioning and environment configuration.
- Re-deriving the analysis. The doorway, block, ranking and effectiveness logic is carried over as-is.
- Freezing the canonical type list or validating per-type conclusions. Sibling change, section 14.

## Decisions

### M1. Mirror the layers locally; the live query stops being per-run

The pipeline currently re-queries HRM on every invocation. It will instead read a local store that a separate sync process fills.

The arithmetic is one-sided. A full copy of everything the product could ever need is 1,640 pages, **observed at 25 minutes 10 seconds serially** and a fraction of that in parallel. (This design first estimated 1,250 pages and eleven minutes; that figure was sized against the parking slice of the custom-fields layer, while the mirror holds it in full.) The current approach spends 3.5 minutes to answer a question about one of thirty types, and would spend 35 to 45 minutes nightly to answer it about all of them. Copying the whole corpus once costs less than one night of the status quo.

This directly retires the sibling change's D14, which argued "at this scope the snapshot buys nothing: the selection is ~33 id-chunk fetches plus 610 census polygons." That was accurate for a single hardcoded violation type. D18 then widened the scope to roughly thirty types and flagged the consequence as unmeasured. It is measured now, and it is the reason D14 no longer holds.

Note what is *not* being built. D14 rejected "a resumable snapshot store," and R5 recorded the same rejection. A mirror reloaded on publish is a smaller thing than the resumable ingest pipeline that was rejected: there is no multi-stage job, only one layer paged into a staging table and swapped in whole. The only checkpoint is the page offset of that fill, and it holds only for the version the fill began on — if HRM publishes again mid-fill, the fill starts over. A failed sync leaves the previous version serving and is retried on the next schedule, which is the same recovery story D14 accepted for a failed run.

### M2. Replace the version, do not chase changes within it

**This decision reverses an earlier one in this document.** The earlier M2 specified an incremental sync: advance a per-layer high-water mark on `ObjectId` to capture inserts, and refetch the open-request set to catch edits. It was implemented, and then the premise was measured and found false.

**What the source actually is.** The three layers are not append logs that can be read incrementally. They are published snapshots, and the service says so:

```
hasStaticData        True                  — declared not edited in place
capabilities         Query,Extract         — no Create/Update/Delete exists
uniqueIdField        ObjectId (isSystemMaintained: True)
dataLastEditDate  == schemaLastEditDate    on all three layers
```

`ObjectId` is assigned by ArcGIS, not by HRM, and a republish assigns it afresh. The stored evidence agrees: the id space is **dense 1 to N with zero gaps** on both Cityworks layers — 478,458 and 1,156,710 rows with not one hole — which no feature class retains after nine years of edits and deletions. It is a row counter written at load time, ordered by something like category rather than by date. Hence `object_id` 1 carries a 2023 call, 300,000 carries a 2020 call, and the newest call in the data sits at 262,781 while the highest id is 478,458.

**Why the watermark cannot be patched into correctness.** If a publish reloads in the same order with new rows interleaved into their category groups, every id after each insertion point shifts. `ObjectId > watermark` then returns rows that merely *moved* past the mark — which the upsert inserts under new primary keys, duplicating records already held — while genuinely new rows that landed below the mark are never seen at all. The failure is not "some rows arrive below the maximum." It is that the identifier carries no stable meaning across versions.

Chasing changes within a version is a category error when the source has no notion of a change within a version. It has versions.

**So: poll the version, and when it moves, take it whole.** `editingInfo.lastEditDate` is the version number (M3). When it advances, the layer is reloaded in full. Nothing in this depends on any property of `ObjectId`.

**The cost argument that justified incremental does not survive measurement:**

| | |
|---|---|
| Night with nothing published | 1.2 s, 3 requests as measured; 6 since each quiet night also reconciles counts (3.5), time not re-measured |
| Full reload when a publish lands | 25 minutes, ~213 MB |
| At roughly 52 publishes a year | ~11 GB from a public endpoint |

The annual figures assume a weekly publish, which is not measured: one publish has been observed. They are subject to change once the sync has recorded more of HRM's actual publishes (M3), and the conclusion holds unless publishes turn out to be far more frequent.

That is a rounding error against correctness that rests on no assumption at all.

**It also deletes machinery rather than adding it.** A full reload retrieves current state for every row, so the open-set refetch is redundant: closures, `STATUS`, `RESOLUTION` and a `Vehicle Was Towed` flag set after filing all arrive because everything arrives. The tow-bias argument the earlier M2 pressed hard — that a watermark-only sync would under-count the only published enforcement outcome and bias the project's central finding in the direction that flatters it — was a real hazard of the method being replaced, not of the source. It disappears with the method.

**What was actually true in the earlier M2** and survives: requests do get edited after filing, and 3,351 are open at any time. That is why a sync cannot be additive. The conclusion changes from "refetch the mutable subset" to "replace the whole version", which covers it and more.

### M2a. The watermark question is deferred, not closed

Reload-on-publish is correct under every hypothesis about `ObjectId`, which is why it is safe to adopt now. It is not necessarily optimal. If identifiers turn out to be stable across publishes, an incremental sync would be cheaper, and this decision should be revisited rather than treated as settled.

The reason it cannot be decided now is simply that **we have no cross-publish observation of identifiers**. The mirror was loaded after HRM's 2026-09-13 publish and no publish has occurred since, so every measurement above comes from a single version. Comparing the committed CSV snapshots either side of that publish showed records persisting — no address's all-time call count decreased and no `last_call` moved backwards across 344 shared addresses — but those files carry no `REQUEST_ID` and no `ObjectId`, so a reload that preserved every record while renumbering it would produce byte-identical output. The evidence is consistent with both hypotheses.

**So the reload records what a later decision will need.** On each reload, before the previous version is replaced, the system retains the version's identity: its `lastEditDate`, its row count, its highest identifier, and a sample of `REQUEST_ID` to `ObjectId` mappings. After several publishes that sample answers the question directly — if a request keeps its identifier across versions, a watermark becomes viable and worth costing; if it does not, the question is closed for good and this decision stands on evidence rather than on the absence of it.

A sample rather than the full mapping because 478,458 rows per version is a needless cost to answer a yes-or-no question, and a few thousand stable-or-not observations settle it. The sample is a **fixed residue class of the business key** — every 128th `REQUEST_ID`, which is 3,721 requests and 9,229 custom-field rows — and fixed is the load-bearing word. Two independent random samples of four thousand out of 478,458 would share about nineteen records; the same residue class in every version shares every record present in both. A stride derived from the row count would drift as the layer grows and produce exactly the disjoint samples it is there to avoid.

As implemented: `layer_versions` holds one row per published version, `layer_version_samples` holds its key-to-identifier sample, and both are written twice per reload — for the version going out, before a row of it is touched, and for the version coming in. The version one reload records as incoming is the one the next finds on its way out, so the chain has no gaps and no duplicates.

**Reading the answer costs nothing and needs no new instrumentation:**

```
python3 src/mirror/sync.py --versions
```

reports every retained version, and for the two most recent, how many sampled records kept their `ObjectId` and how many moved. With one version retained it says so rather than failing: "not yet answerable" is a state of knowledge.

Revisit when: at least four publishes have been observed, which at a roughly weekly cadence is about a month. That cadence is an assumption, not a measurement, and the estimate is subject to change once the recorded publish history shows the real interval; the trigger is four publishes, not the elapsed time. Four, not one, because a single publish could preserve identifiers by accident — an unchanged load order over an unchanged corpus — while the next reshuffles them. The retained samples answer it for every consecutive pair, so the question is decided on a pattern rather than on one observation.

### M3. Pace the sync by the source's own update clock, not by an assumed interval

The sibling change's D14 justifies running nightly by describing the source as "weekly-refreshed." That phrase appears exactly once in the repository, carries no citation, and is supported nowhere in `docs/parking-hotspots/data-sources.md`, which documents the datasets in detail and never states a refresh interval. An earlier draft of this design repeated the claim as established. It is not established, and no published HRM refresh schedule was found.

What HRM does publish is better than an interval. Each layer's metadata carries `editingInfo.lastEditDate`, retrievable in one small request with no paging. Read on 2026-09-15:

```
Cityworks_Service_Requests                 2026-09-13 10:34 UTC
Cityworks_Service_Requests_Custom_Fields   2026-09-13 10:38 UTC
Census_2021_Dissemination_Areas            2024-03-19 13:16 UTC
```

Three things follow. The two Cityworks layers are loaded together, four minutes apart, so they can be treated as one update event. The census layer has not changed in eighteen months and is static reference data, not something to poll. And on 2026-09-15 the source had not been updated for two days, which is consistent with a cadence slower than daily and is not evidence of any particular one.

So the sync polls `lastEditDate` and pulls when it advances, rather than pulling on a fixed schedule and hoping it matches.

**The poll runs nightly; the pull runs only when the poll says to.** The two cadences are separate and it matters that they are. The poll is one metadata request per layer with no paging — two per night once the static census layer is excluded — against the roughly 370 requests per night a thirty-type live batch would cost. Running it nightly means the mirror is never more than a day behind whatever HRM has published, at a load small enough to be irrelevant. The pull, which is the expensive half, fires only on the nights the timestamp has actually moved.

This gets the property the weekly sync was reaching for without needing to know the interval it was guessing at. If HRM publishes weekly, the pull runs weekly and the other six polls cost two requests each. If HRM publishes twice a week, or stops for a month, the mirror tracks that without anyone editing a schedule.

The cadence is then **observed rather than declared**: each sync records the source's `lastEditDate`, so the interval between HRM's updates accumulates as measurement. After a few cycles the portal can tell a viewer when the next source update is actually likely, from history rather than from an assumption. Until that history exists the portal says it does not yet know, which is the honest state and is also the one this design starts in.

This is a better outcome than the fixed weekly sync it replaces, and it arrived by checking a claim that had been carried forward twice without anyone testing it.

### M4. Staleness is a first-class output, because the mirror can lie

This is the decision that pays for the others, and it is the one place this change is strictly more exposed than what it replaces.

Today's failure mode is honest. A run either completes against live data or fails loudly, and `out/` cannot be stale without the scheduled job having visibly failed. A mirror breaks that coupling: the application keeps serving, keeps looking healthy, and keeps rendering plausible lists from data that stopped arriving a month ago. Nobody looking at the page can tell.

That is precisely the sibling change's D16 — "a job that has been failing for a week produces a board indistinguishable from a fresh one" — promoted from an output-formatting gap into a production defect. So:

- Every sync attempt records its start, its outcome, the watermark it reached, the row counts it moved and the source `lastEditDate` it observed. A failure is a row, not an absence.
- Every view states four clocks: when HRM last updated the source, the most recent call the data holds, when the mirror last synced successfully, and when the next sync is due.
- A scheduled sync that has not happened flips the portal to an overdue state, visibly, on the page.

The distinction between last attempt and last success is load-bearing. A sync failing every hour has a recent last attempt and is exactly as stale as one that stopped a month ago.

**Whose limit is it.** Four clocks rather than two, because a viewer looking at data from five days ago needs to know which of two entirely different things is true: HRM has not published anything newer, or we have stopped collecting it. The first is a property of the source and nothing in this project can improve it; the second is a bug. A portal that shows only its own sync time invites a viewer to blame the wrong system, and — worse for a demo — invites them to distrust figures that are in fact as current as the source allows. So where the data lags because HRM has not updated, the portal SHALL say so in those terms: the limit is named as the data source's, not the project's.

**"Next update" is a promise, and promises rot.** Displaying "next update scheduled for Tuesday" is only honest while the scheduler is alive. If it dies, that cheerful future date keeps rendering, never arrives, and the failure presents as health — the precise pathology this decision exists to prevent. So the next-sync display is a state machine, not a string: once the scheduled time has passed by a grace period with no successful sync, it becomes overdue and says so.

This also resolves the sibling change's D16 recency-anchor problem as a side effect. The twelve-month window is currently anchored to the newest call in the data, so a stalled source slides the window backwards silently. With a mirror the two anchors are separately known — the sync clock and the data clock — so the window can be anchored explicitly and the gap between them reported.

### M5. The application is served; the file stops being the product

D11 made the board one self-contained file, reasoning that "a work list that needs infrastructure to open is a work list nobody opens, and the team that installs bollards is not going to run a dev server." The reasoning was sound and the conclusion is now obsolete, because a URL puts the infrastructure on the server's side of the line. The viewer still needs nothing but a browser.

What is genuinely lost is worth naming. The committed file *was* the record of that day's list — mailable, archivable, openable with no network. A served application has none of those properties. Keeping a file export is cheap and preserves the first two, so the lists survive as downloads rather than as the product.

**And the repository stops being part of the runtime.** Scheduled work moves to the deployment, so this repository runs no scheduled job: `.github/workflows/nightly.yml` is removed rather than repointed, and nothing commits generated output any more.

That has a consequence worth catching before it is lost by accident. The committed weekly output was an unintended but real longitudinal record — a git history of which doorway left the list and when, which is the only before-and-after data this project has ever accumulated, and the raw material for the effect measurement that is a non-goal precisely because nobody has any. Deleting the scheduled job deletes the mechanism that was quietly building it.

So the record moves into the store rather than disappearing with the job: each sync retains the doorway and block lists it produced under the default parameters. That is strictly better than the git history it replaces — queryable, joinable to the calls that drove it, and not 611 KB of duplicated map geometry per commit — and it costs a few thousand rows a week.

D12's embedded map geometry follows D11 out. 490 KB of the 611 KB board is `web/map-network.json`, embedded because a single file cannot depend on an external service. A served application fetches it once and lets the browser cache it, which is strictly better than re-embedding it in every type's payload.

Type switching becomes routing rather than an embedded switcher. The sibling change's task 14.3 specifies embedding every tracked type's data into one file with a viewer-facing switcher; under a served application that requirement is satisfied by a route per type, and the payload per view stays the size of one type.

### M6. Triage state moves to the server, and disclosure survives the move

D13 persisted decisions to "shared storage offered by the hosting environment," which in practice means `window.claude.use("db")` at `web/template.html:1007`. On any other host that expression resolves to `null` and every viewer falls back to their own browser. Two people at a demo would triage two private lists while the page told each of them so in small type — and while `README.md` told the room the opposite.

Decisions move to the store behind the application. All viewers read and write the same rows.

D13's *disclosure* requirement is not retired; it is the part worth keeping. Its reasoning — "a viewer who believes their triage reached colleagues when it sat in their own browser is worse off than one told plainly it is local" — generalises past the storage question. The application applies it to what is now uncertain: how fresh the mirror is, and which role a decision is attributed to.

### M7. Role selection, not authentication

The audience is HRM coordinators and parking enforcement officers at a demo, interacting in role. A viewer picks a role on entry and their triage decisions carry it.

Accounts would be the wrong instrument. They would cost credential handling and a user store to produce, at a demo, a login screen between the audience and the product — and they would imply an access-control guarantee that a public demo URL does not have.

The honest statement of what this is: the parking data is public and reading it needs no permission, while triage decisions are municipal operational workflow that in production would need real access control. A public URL with shared writable triage means any visitor can mark a doorway. At demo scale, with an unadvertised URL, that is acceptable; it is a property to state, not to pretend away. The application says plainly that roles identify rather than authenticate.

### M8. The analysis is carried over, not rewritten

D3 through D10 — doorway as the unit of action, block as the unit of diagnosis, census dissemination areas as the grain, dwelling-count normalization, ranking by recent calls discounted by tow rate, the tow flag as the only outcome, vehicle identity as a floor — are product decisions grounded in evidence about HRM's data. None of them depend on where the data is stored, and all of them survive intact.

The change is confined to the layer beneath them. A regression in any published figure is therefore a bug in this change, not a revision of the analysis, and the reconciliation task exists to catch exactly that.

### M9. Coordinate keying is unblocked, and deliberately not taken here

D4 reduces an address to a doorway key by string, and R2 records coordinate binning as "strictly better than string reduction," impractical at the time because containment testing meant a hand-rolled ray cast or one server request per address.

A spatial store removes that obstacle: doorway location and census placement both become spatial operations. R2 names `No Parking Sign`, at 27,404 calls live today, as the type where string reduction starts to strain — and it is the largest type in the corpus, nearly three times blocked driveway.

This change does not make the switch. It is a change to what the doorway key *is*, which would move rows in every published list and confound the reconciliation that proves the mirror faithful. Doing both at once means being unable to tell which one moved a number. The sibling change's task 14.6 already owns the question; this change removes the reason it was hard.

### M10. Mirror only what the product reads; `311_Call_Details` stays out

The mirror holds service requests, custom fields and census dissemination areas. It does not hold `311_Call_Details`.

The cost of including it is not marginal. At 4,968,536 rows and about 1.2 GB of raw JSON — roughly 28 minutes to pull serially — it is three times larger than everything else mirrored combined, and it would dominate both the initial load and the storage footprint.

What it would buy is nothing the product can use. R4 in the sibling change established why, and the reasoning is unaffected by where the data sits: the layer carries `CALL_ID`, `QUEUE_NAME`, `OUTCOME`, `WRAPUP_NAME`, `ARRIVAL_DATETIME` and talk times, with no address, no coordinates and no request identifier, so there is no per-record join to a service request; `OUTCOME` is 99.5 per cent `Handled`, an agent disposition rather than a municipal one; and HRM documents its timestamp as shifted 3–4 hours from true UTC in the opposite direction to the requests layer, so any comparison needs two different corrections applied in opposite directions.

The distinction worth holding onto is that **mirroring a dataset is not the same as un-rejecting it**. Having a copy locally makes a join no more possible than it was over the network. If 311 data ever returns, it returns because someone found evidence that answers R4, not because storage got cheap.

A verification accompanies this decision rather than an assumption. No 311 call-detail value is read, referenced or implied anywhere in the shipped product. `src/hotspots.py` and `web/template.html` contain no occurrence of the string at all. Every other occurrence in the repository is one of three benign things:

| Occurrence | What it is |
|---|---|
| `311 Online` | a value of `INITIATED_BY` on the **service requests** layer — a channel label, not this dataset. It is real and used, in the channel-reliability reasoning behind R1. |
| `12090311`, `311.0`, `44.6481965867311` | digit sequences inside census block identifiers, day counts and coordinates |
| `data-sources.md` and R4 | the two places recording the dataset's rejection |

The first row is the one that matters, because it is the trap: a reader grepping for `311` finds live, load-bearing references and could conclude the dataset is in use. It is not. `311 Online` describes how a call arrived, and lives on a layer that is mirrored.

### M11. Filters are interactive, so derived data is computed per request

The parameters that are command-line arguments today — district, minimum recent calls per doorway, minimum still-calling doorways per block, and the recurrence window — become controls in the portal. A coordinator narrows the list to their own district; an officer widens a threshold to see what sits just below the cut.

That decides the materialization question. Precomputing results per violation type would mean precomputing them per filter combination, which is a product of four ranges rather than a table of thirty. Computing per request avoids it entirely, and the scale makes it a non-issue: the largest type yields a few hundred doorways from roughly 100,000 mirrored calls, which is an aggregation over a small indexed table rather than a batch job.

The mirrored call table is what gets indexed and reused across every filter combination; the doorway and block derivations run on the filtered slice. Where a particular view proves slow in practice, caching the default parameter set is the fallback — but caching a hot path is a smaller commitment than materializing a matrix, and it is not worth making before a measurement says it is needed.

**Two windows, not one, and only one of them is currently a knob.** `src/hotspots.py` carries two 365-day windows that are easy to mistake for each other:

```python
recent_from = latest - datetime.timedelta(days=365)     # line 214, HARDCODED
    ...
    recent = sum(1 for t in times if t >= recent_from)   # drops dormant doorways, drives the ranking
    repeats = sum(... (times[j] - t).days <= recur_days) # line 238, --recur-days, one column only
```

They share a default of 365 days and do entirely different jobs. The **recency window** decides which doorways are listed at all and feeds `calls_12mo`, which the D8 ranking multiplies — and it is not configurable today. The **recurrence window** only counts repeat calls into one published column, and it is the one the CLI exposes.

A single "window" control would therefore be a lie in both directions: moving it would not change the list a viewer is looking at, while changing a number in a column they may not be reading. The portal exposes both, separately labelled by what each governs — one filters the list, the other defines what "repeat" means. Exposing the recency window also makes it explicit, which the sibling change's D16 wanted for a different reason.

There is one constraint the filters must not break. A threshold is part of what a figure means: a doorway list at a minimum of two recent calls and the same list at a minimum of five are different claims, and a screenshot of either is indistinguishable from the other unless the view says which it is. So a view and an export both state the filter values that produced them.

### M12. Server shape

Four choices the decisions above left open, settled before the first server code was written.

**Flask, synchronous.** The mirror is read through synchronous psycopg and a derivation is CPU work of 0.1 to 3 seconds, so an asynchronous framework would run nearly everything in a thread pool and gain little. Flask is small and well understood, and a production WSGI server such as gunicorn runs it. The standard library's `http.server` was the dependency-free alternative; it would mean hand-building routing, caching headers and concurrency, which is a poor trade for a public URL. `src/hotspots.py` stays stdlib-only; the dependency is the server's, not the viewer's.

**The existing page, fed by a JSON API.** `web/template.html` already carries the lists, the map, the triage controls and light and dark rendering in plain JavaScript. It is kept and its embedded data is replaced with fetches from the server. There is no build step, which task 5.1 requires, and no working interface is discarded.

**Figures that do not depend on filters are computed when the mirror reloads.** M11 computes derived data per request and defers caching until a measurement says it is needed. The measurement now exists: the per-type conclusions, with their doorway-resampling intervals, take about 9 seconds for all thirty types and 2.7 seconds for `No Parking Sign` alone. Those results depend only on the mirror's published version, not on any filter, so they are computed after each reload and stored alongside the retained lists (8.6), and the server reads them. M11 stands for everything a filter changes — the doorway and block lists stay per request. This is the narrowest materialization the measurement justifies, and it invalidates itself: a new version brings new figures.

**Host-agnostic.** Deployment stays out of scope. The server reads its port and database address from the environment and ships two commands, one to serve and one to sync, so any host that can run a web process and a scheduled command can run it. Nothing in the code names a host.

## Risks / Trade-offs

- **A silent sync failure serves stale data behind a healthy-looking page** → the central risk, addressed by M4: recorded sync outcomes, last-successful-sync on every view, and a visible staleness warning. It is a genuine regression in failure honesty relative to a stateless pipeline, bought deliberately.
- **`ObjectId` is not monotonic with recency** → asserted from one observation and disproven on measurement (correlation 0.30, 31 per cent of adjacent pairs stepping backwards). It no longer matters to the sync: M2 reloads a layer whole when its published version advances, and nothing in that path depends on any property of `ObjectId`. It is still recorded as an observation.
- **A republish may renumber every record** → under the retired watermark this would have silently duplicated rows. Under reload-on-publish a renumbered version simply replaces the previous one, swapped in atomically, so no duplicate can arise. Whether renumbering actually happens is left open rather than guarded against: each reload retains a fixed sample of `REQUEST_ID` to `ObjectId` mappings (M2a), and comparing retained versions answers it.
- **The custom-fields layer backfills rows for years-old calls** → the newest 710 ids include a row attached to a 2022 call, so neither an id nor a call date says whether a row is new. Nothing relies on either: a publish is reloaded whole, so backfilled rows arrive with everything else, and the stored count is reconciled against the service's own count on every reload.
- **Records are edited after filing, including long-closed ones** → closures, `STATUS`, `RESOLUTION` and a tow flag set later all change existing rows. A full reload retrieves current state for every row, so no edit path is needed. The residual gap is timing: an edit is seen only once HRM publishes it, and the mirror is no fresher than the source's own schedule.
- **The mirror can diverge from the source without anyone noticing** → the reconciliation task compares mirror row counts against the service's own count queries per sync, so divergence is detected rather than assumed absent.
- **A served application loses the mailable, archivable, offline-openable file** → M5 keeps the file export for exactly this.
- **Public URL plus shared writable triage means anyone can mark a doorway** → M7 accepts this at demo scale and requires the application to say so rather than imply otherwise.
- **The analysis could regress silently while moving to local data** → the pipeline has no automated tests, a risk the sibling change already records. Reconciling every published figure against the pre-change run is the compensating control here, and it is weaker than tests.
- **Wider type coverage becomes cheap before it becomes validated** → D19 still governs. A type whose own tow and vehicle figures do not resemble driveway's must not inherit driveway's conclusion, and cheap computation makes it easier to publish a type that has not been read.
- **The sync cadence rests on an inference, not documentation** → no published HRM refresh schedule was found, and the one interval claim in the repository was unsourced. Mitigated by pacing on `lastEditDate` and measuring the cadence rather than asserting it.
- **`lastEditDate` may not move for every change** → if HRM edits rows without the layer's edit timestamp advancing, a poll-driven sync would not fire, and nothing else would catch it: the watermark pull and open-set refetch that once ran independently of the poll are retired (M2), so the poll now gates every pull. **Accepted for now.** The layers declare `hasStaticData` and offer only `Query` and `Extract`, which points to wholesale publishes rather than in-place edits, and `dataLastEditDate` equals `schemaLastEditDate` on all three. That is inference from service metadata, not observation of how HRM updates the source. Research into HRM's actual publishing method may inform or overrule this acceptance; if it shows edits that do not advance `lastEditDate`, a backstop independent of the poll — a scheduled full reload, or a count comparison on polls that do not reload — becomes required rather than optional.
- **A next-update promise outlives the scheduler that keeps it** → M4 makes the next-sync display a state machine with an overdue state rather than a rendered date.
- **Interactive filters change what a figure means** → a view and an export both state the filter values that produced them, so a screenshot cannot be read as the default list.
- **Excluding 311 could be misread as an oversight** → M10 records the exclusion, its cost, and the verification that nothing in the product depends on it.
- **New operational surface** → a store and a server replace a script and a static file. `README.md`'s "No keys and no install" stops describing the server. It still describes the viewer, which is the claim that mattered.

## Migration Plan

The mirror is additive and reversible. `src/hotspots.py` keeps working against the live service throughout, so the sequence is:

1. Build the mirror and sync alongside the existing pipeline, changing nothing that is published.
2. Reconcile: run the existing pipeline against the live service and the new derivation against the mirror, and require every figure in `out/watchlist.csv`, `out/blocks.csv` and the briefs to match. A discrepancy is a mirror bug.
3. Only then move the application onto the derived tables.

Rollback at any point before step 3 is deleting the store. After step 3 it is reverting the application, since `src/hotspots.py` and its live queries are untouched by this change.

Triage decisions are the one piece of genuinely new persistent state. They exist today in viewers' browsers and in a Claude Artifact database, neither of which is a migration source anyone would want; they start empty.

## Open Questions

Resolved since drafting, recorded here because the reasoning informs the tasks:

- **Mirror scope.** Service requests, custom fields and census only. `311_Call_Details` excluded — M10. The implementing work reports observed on-disk size per layer rather than relying on the ~300–400 MB estimate.
- **Materialized or per-request.** Per request, because filters are interactive — M11.
- **Staleness handling.** A prominent portal message carrying four clocks, attributing a lag to the source where it belongs, with an overdue state when a scheduled sync does not happen — M3 and M4.

Still open:

- **The source's true update cadence.** Unknown, and now known to be unknown: the "weekly-refreshed" claim was unsourced. `lastEditDate` polling measures it, so this answers itself after a few cycles. Until then the portal states it does not yet know the next source update rather than guessing one.
- **How far back the retained list history should go.** Resolved in principle: this repository runs no scheduled job, so nothing commits generated output and the longitudinal record moves into the store (M5). What remains open is retention depth — every sync indefinitely is a few thousand rows a week and probably fine, but nobody has said how long the record needs to be useful.
- **Grace period before a missed sync reads as overdue.** Depends on the observed cadence, so it follows from the question above rather than being set independently.
- **Whether a viewer can change filters that alter what a published figure means.** M11 requires a view and an export to state the filter values that produced them. Whether the demo also pins a canonical default that headline figures are always quoted from is a product question, not a technical one.
