# Parking hotspots: decisions

## Decided

**2026-09-12, Claude's proposal.**
Scope the first build to "Blocking Driveway (DISPATCH)", not all parking.

Reason.
It is the problem the handout names.
It is 9,729 calls, which is small enough to pull in one run and large enough to prove the pattern.
The same job runs unchanged on the other violation types by passing `--violation`.

**2026-09-12, Claude's proposal. Reversal of an earlier proposal the same day.**
Drop the per-address time window from the product.

Reason.
An adversarial review found the 17 per cent baseline was never queried and is the wrong null.
Re-tested against a matched null, the observed 58.9 per cent sits against a null mean of 48.8 per cent, and only 17 of 58 addresses beat their own 95th percentile.
Out of sample a tuned window scores 47.5 per cent against 41.7 per cent for one city-wide window, and weekday tuning loses outright.
`DATE_INITIATED` is also the staff intake clock: the `INTERNAL` channel carries 85 per cent of calls and records almost none at night.
The window was measuring office hours.

**2026-09-12, Claude's proposal.**
Point the product at physical fixes, not at enforcement shifts.

Reason.
A tow does not change the recurrence rate: 44.7 per cent against 44.6 per cent.
96 per cent of vehicles at the busiest doorways are unique, so there is no repeat offender to deter.
Enforcement has already been tried at these addresses and has not worked.

**2026-09-12, Claude's proposal.**
Rank by calls in the last 12 months, times one minus the tow rate, and drop any address with no call in 12 months.

Reason.
The earlier all-time ranking put 155 dead addresses on a 406-row list, 38 per cent of it.
One printed row had not called since 2024-01-22.
Day-one impact counts double, so a stale row is the most expensive kind of error.

**2026-09-12, Claude's proposal.**
Convert timestamps to UTC-3 before any time-of-day work.

Reason.
The service returns UTC.
Halifax is UTC-3 in summer, so the offset moves every hour-of-day figure by three hours.

**Correction, `mirror-hrm-data-and-host-app`.**
The fixed UTC-3 offset above was wrong for the roughly half of the year Halifax observes AST (UTC-4), not ADT (UTC-3), moving every hour-of-day figure computed outside daylight saving by an hour. `src/hotspots.py`'s `to_local()` now converts with `zoneinfo.ZoneInfo("America/Halifax")`, which tracks the actual AST/ADT transition dates instead of a fixed offset. The hour-of-day and channel figures elsewhere in this document set have been re-derived under the corrected conversion; see `docs/parking-hotspots/product.md` and `docs/parking-hotspots/data-sources.md`.

**2026-09-12, Chris.**
Group by neighbourhood as well as by address.

Chris: "make sure to not just group by addresses group by neighborhood because if some addresses are in the same neighborhood".

Reason, and what the test found.
He was right, and the address view was hiding the bigger problem.
28 Queen St and 70 Ochterloney St were rows 1 and 3 of the address list.
They are the same census block.
The address view counted them as two separate problems.

Two grains were rejected before the right one was found.
`COMMUNITY` on the call record is useless: 7,651 of 9,791 driveway calls just say HALIFAX.
HRM's Community Boundaries layer fails the same way: "HALIFAX" swallows 3,121 of the 4,255 addresses.

Census 2021 Dissemination Areas is the grain that works.
610 polygons across HRM, and each carries a dwelling count, so a block's load can be a rate and not a raw total.
The top 20 blocks hold 35 per cent of recent calls against 25 per cent for the top 20 streets, so the block is also the sharper cut.

**2026-09-12, Claude's proposal.**
Rank blocks by how many separate doorways are still calling, then by calls per 1,000 dwellings.

Reason.
Raw call volume rewards a block for having more front doors.
The dwelling count is the denominator that removes that, and it comes with the census layer at no extra join.

**2026-09-12, Claude's proposal.**
Put a "doorways calling on this block" count on every doorway row.

Reason.
It is the one number that decides the fix.
Where it reads 1, a bollard or a driveway marking settles it.
Where it reads 10, ten signs is the wrong answer and the block needs a permit zone, curb management or a parking study.

## Decided under `mirror-hrm-data-and-host-app`

Recorded 2026-09-18 from that change's `design.md` (decisions M1 to M12), its specs, and the notes on its completed tasks.
These entries record what was decided and why, not that the change is finished: the sweep of stale references (task 8.9) and the whole of section 9 (verification) are still open.
Figures were measured live on 2026-09-15 and are kept as measured, as elsewhere in this file.

**Recorded 2026-09-18, from `design.md` M1 and M10.**
Mirror the three HRM layers the product reads into a local store, filled by a separate sync, and let analysis read only from that store.

Reason.
The pipeline re-queried HRM on every run, and the query shape was the slow part.
One violation type took 3 minutes 31 seconds, almost all of it in identifier-chunk queries at about six seconds each, while offset paging the same service returns 1,000 rows in about a second.
Copying everything once, 1,640 pages, took 25 minutes 10 seconds serially.
That is less than one night of asking the same question about all thirty types the old way, which was estimated at 35 to 45 minutes.
It retires the earlier change's D14, "at this scope the snapshot buys nothing", which was true for one hardcoded type and stopped being true when D18 widened the scope to about thirty.
It also overtakes the 2026-09-12 scoping entry's reason that driveway calls were "small enough to pull in one run": the first build's scope stands, but another type is now a grouping operation over data already held, not another network pass.

Analysis reads only from the mirror, with no fallback to a live query.
A partial fallback would make it impossible to tell whether a result came from mirrored or live data.

Rejected: a resumable multi-stage snapshot pipeline, which D14 and R5 rejected and which this is not.
The mirror is one layer paged into a staging table and swapped in whole.
The only checkpoint is the page offset, and it holds only for the version the fill began on: if HRM publishes again mid-fill, the fill starts over.
A failed sync leaves the previous version serving and is retried on the next schedule.

Scope is service requests, custom fields in full, and census dissemination areas.
`311_Call_Details` is deliberately not mirrored.
It is 4,968,536 rows and about 1.2 GB, roughly three times everything else combined, and the earlier change's R4 already found it has no address, coordinates or request identifier to join on.
Having a local copy makes a join no more possible than it was over the network, so mirroring it would not un-reject it.
`311 Online` is a channel label on the service requests layer, not that dataset.

**Recorded 2026-09-18, from `design.md` M2 and M2a. Reversal of the change's earlier sync design.**
Replace a layer whole when its published version advances, and do not chase changes within a version.

Reason.
The earlier design was incremental: advance an `ObjectId` high-water mark for inserts and refetch the open requests for edits.
It was built, and then its premise was measured and found false.
The three layers are published snapshots, not append logs: `hasStaticData` is true, the service offers only `Query` and `Extract`, and `dataLastEditDate` equals `schemaLastEditDate` on all three.
`ObjectId` is assigned by ArcGIS and afresh on each publish, and the stored id space is dense from 1 to N with no gaps on both Cityworks layers, so it is a row counter written at load time and not a record identity.
A republish that interleaves new rows shifts every id after each insertion point, so a watermark would return rows that merely moved, which the upsert would duplicate under new keys, and would never see genuinely new rows that landed below the mark.
Nothing in the sync now depends on any property of `ObjectId`.

The cost argument for incremental does not survive measurement.
A quiet night was measured at 1.2 seconds and 3 requests; it is now 6 requests, since each quiet night also reconciles counts, and the time was not re-measured.
A full reload when a publish lands is 25 minutes and about 213 MB, which at a weekly publish is about 11 GB a year from a public endpoint.
The weekly rate is an assumption, since one publish has been observed, and the conclusion holds unless publishes are far more frequent.
A full reload also deletes machinery, because it retrieves current state for every row: the open-request refetch is redundant, and so is the tow-flag bias that a watermark-only sync would have introduced into the project's central finding.

Left open, on purpose.
Reload-on-publish is correct under every hypothesis about identifiers, but it is not necessarily the cheapest method.
No cross-publish observation of identifiers exists, because the mirror was loaded from a single version.
So each reload retains the version's identity before replacing it: its published edit timestamp, row count, highest identifier, and a fixed sample of `REQUEST_ID` to `ObjectId` mappings, being every 128th `REQUEST_ID`, so that two versions sample the same records.
`python3 src/mirror/sync.py --versions` reads the answer, and says "not yet answerable" while one version is retained.
The question is revisited after at least four observed publishes.
The trigger is four publishes, not elapsed time, because a single publish could keep identifiers by accident.

**Recorded 2026-09-18, from `design.md` M3.**
Pace the sync by the source's own update clock, not by an assumed interval.

Reason.
The earlier change justified a nightly run by calling the source "weekly-refreshed".
That phrase appears once in the repository, cites nothing, and is supported nowhere in `data-sources.md`, and no published HRM refresh schedule was found.
What HRM does publish is `editingInfo.lastEditDate` on each layer, in one small request with no paging.
Read on 2026-09-15, the requests layer said 2026-09-13 10:34 UTC and the custom-fields layer 10:38 UTC, so the two are loaded together, four minutes apart.
The census layer said 2024-03-19 and is static reference data, so it is not polled for a pull.

The poll runs nightly and the pull runs only when the timestamp has advanced.
They are two cadences and are deliberately not collapsed: the poll gates every pull, and nothing pulls on its own schedule.
The poll is two requests a night with the census layer excluded, against roughly 370 a night for a thirty-type live batch, and it bounds how far behind HRM the mirror can be to one day.
The cadence is observed and not declared: every sync records the source's last-edit timestamp, and any statement about the next source update comes from that history.
Until there is enough history the portal says the next update is not yet known.

Accepted risk.
If HRM edits rows without advancing `lastEditDate`, no pull fires and nothing else catches it, because the poll gates every pull.
That the layers are wholesale publishes is an inference from service metadata, not an observation of how HRM updates them.
If research into HRM's publishing method shows edits that do not advance the timestamp, a backstop independent of the poll becomes required.

**Recorded 2026-09-18, from `design.md` M4.**
Make staleness a first-class output, showing four clocks and saying whose limit each one is.

Reason.
The stateless pipeline failed honestly: a run completed against live data or failed loudly.
A mirror breaks that coupling, so the application can keep serving plausible lists from data that stopped arriving a month ago.
This is the earlier change's D16 promoted from a formatting gap to a production defect, and it is the one place this change is more exposed than what it replaces.
Every view states when HRM last updated the source, the most recent call the data holds, when the mirror last synced successfully, and when the next sync is due.
Last attempt and last success are kept apart, because a sync failing every hour has a recent attempt and is as stale as one that stopped a month ago.
Where the data lags because HRM has not published, the view says the limit is the data source's and not this project's.
The next update is a state and not a rendered date: once the scheduled time has passed by a grace period with no successful sync, the view reads overdue.
The design leaves the length of the grace period to follow from the observed cadence.

**Recorded 2026-09-18, from `design.md` M5, M11 and M12.**
Serve the application at a URL, with a route per canonical violation type, and stop treating the generated file as the product.

Reason.
D11 made the board one self-contained file because a work list that needs infrastructure to open is a work list nobody opens.
The reasoning was sound and the conclusion is obsolete: a URL puts the infrastructure on the server's side, and the viewer still needs only a browser.
What is lost is a file that was mailable, archivable and openable with no network, so the lists survive as downloads: a complete table and a readable brief.
An export names the violation type, the last successful sync time, the most recent call date, the filter values and the same interpretation limits as the view, and it is the way a list leaves the served application.
Map geometry, 490 KB of the old 611 KB board, is served as a cacheable asset and no longer embedded in every payload.
Types switch by route, `/types/<slug>`, and each view carries one type's payload.
Types are not pooled, because the evidence for the product's framing was measured per type and does not transfer, so a type whose own figures do not support the driveway conclusion states its own.

Filters are controls, and derived lists are computed per request, not materialized per filter combination.
Precomputing would mean a product of four ranges and not a table of thirty, and the largest type is a few hundred doorways from about 100,000 calls.
Caching the default parameters is the fallback, and only once a measurement says it is needed.
The recency window and the recurrence window are two separate controls, because they share a default of 365 days and govern different things: one decides which doorways are listed and feeds the ranking, and the other only defines the repeat column.
A view and an export both state the filter values that produced them, since a threshold is part of what a figure means.
The one narrow exception to per-request computation is the figures that depend on no filter, the per-type conclusions with their resampling intervals.
They take about 9 seconds for all thirty types, so they are computed after each reload and stored against the version they came from, and a new version invalidates them.

Server shape.
Flask, synchronous, because a derivation is CPU work of 0.1 to 3 seconds that an asynchronous framework would only run in a thread pool.
The standard library's `http.server` was rejected because it would mean hand-building routing, caching headers and concurrency for a public URL.
The existing page is fed by a JSON API, so there is no build step and no working interface is discarded.
The server reads its port and database address from the environment and ships one command to serve and one to sync, so nothing in the code names a host.

**Recorded 2026-09-18, from `design.md` M6.**
Move triage decisions to the store behind the application, shared by every viewer.

Reason.
D13 persisted decisions to "shared storage offered by the hosting environment", which in practice was `window.claude.use("db")` in the old template.
On any other host that expression resolves to `null` and every viewer falls back to their own browser.
Two people at a demo would triage two private lists, while the page told each of them so in small type and `README.md` told the room the opposite.
Now all viewers of a type read and write the same rows, decisions survive a restart, and there is no browser-only fallback to reach.
If a decision cannot be persisted the application says so and does not show it as recorded.
Each decision carries the time it was last changed and the role that changed it.
D13's disclosure requirement is kept and redirected: a viewer must never believe their triage reached colleagues when it sat in their own browser, and the same care now goes to how fresh the mirror is and what a role means.
Triage starts empty, because the old state lived in viewers' browsers and a Claude Artifact database, and neither is a source anyone would migrate.

**Recorded 2026-09-18, from `design.md` M7.**
Identify a viewer by a selected role, not by an account.

Reason.
The audience is HRM coordinators and parking enforcement officers at a demo, interacting in role.
A viewer selects a role, coordinator or parking enforcement officer, and it is attributed to the decisions they record.
Reading needs no role, and a role is asked for before a decision is recorded.
Rejected: accounts.
They would cost credential handling and a user store to put a login screen between the audience and the product, and they would imply an access-control guarantee that a public demo URL does not have.
The honest statement is that the parking data is public, while triage decisions are municipal operational workflow that in production would need real access control.
A public URL with shared writable triage means any visitor can mark a doorway.
At demo scale, with an unadvertised URL, that is acceptable, and it is stated and not pretended away: the application says that roles identify rather than authenticate, and grants no different access by role.

**Recorded 2026-09-18, from `design.md` M5 and `hosted-triage-app`.**
Remove this repository's scheduled job, `.github/workflows/nightly.yml`, rather than repoint it, and keep the record it was building in the store.

Reason.
Scheduled work moves to the deployment, so the repository stops being part of the runtime and nothing commits generated output any more.
The job was removed first, so it could not keep churning `out/` while the rest of the change was in progress (task 1.1).
Removal has a consequence that was caught before it could be lost by accident.
The committed weekly output was an unintended but real longitudinal record, a git history of which doorway left the list and when, and it is the only before-and-after data this project has accumulated.
It is the raw material for the effect measurement, which is a non-goal only because nobody has any.
Deleting the job deleted the mechanism that was quietly building it.
So each sync retains the doorway and block lists it produced under the default parameters, in the store.
That is queryable, joinable to the calls that drove it, and not 611 KB of duplicated map geometry per commit, and it costs a few thousand rows a week.
How long the record needs to be kept is still open.

**Recorded 2026-09-18, from `design.md` M12. Resolved by the user during orchestration.**
Retire `web/template.html` rather than fold it back into the served page.

Reason.
After the served page was forked from the template (task 5.1), the repository held two copies of the same markup.
Folding the template in removes a second place for it to drift, and keeping it separate preserves a standalone generated board independent of the server.
Because `web/app/index.html` already carries the template's functionality, the template was retired, on the condition that nothing it did was missing from the served page.
A gap would have been reported and fixed in the served page first.
Outcome: the template was retired once its functionality was confirmed present in the served page, and generation of `out/` was stopped with it (task 8.7).
The five tracked `out/` files, `blocks.csv`, `blocks.md`, `triage-board.html`, `watchlist.csv` and `watchlist.md`, were deleted with it.
The last commit that contains any of them is `b04731c`, and the template can be read with `git show b04731c:web/template.html`.
The old files' figures and defects were left uncorrected on purpose, since the served page and the documents pointing at it are what owe accuracy.

**Recorded 2026-09-18, from `design.md` M8 and M9.**
Carry the analysis over unchanged, and do not switch the doorway key to coordinates here.

Reason.
The doorway, block, census-grain, dwelling-rate, ranking, tow-flag and vehicle-identity decisions rest on evidence about HRM's data, and none of them depend on where the data is stored, so a regression in a published figure is a bug in this change and not a revision.
Coordinate keying is unblocked by a spatial store, and `No Parking Sign`, at 27,404 calls, is where string reduction starts to strain.
It is not taken here because it would move rows in every published list and confound the reconciliation that proves the mirror faithful, leaving no way to tell which change moved a number.

## Open for Chris

**Does the demo show the effect test?**
The product cannot yet prove that a visit reduces calls.
Options: say it plainly as the next step, or stub a before and after chart and label it stubbed.
Recommendation: say it plainly. The handout rewards saying what is stubbed, and a stubbed chart invites the question the product cannot answer.

**Who is the named user?**
The product now points at whoever installs signs, bollards and curb paint, which is Public Works Traffic Management rather than parking enforcement.
The handout named enforcement.
Recommendation: say both. Enforcement gets the evidence that these calls are not theirs to win, and Traffic Management gets the list.
