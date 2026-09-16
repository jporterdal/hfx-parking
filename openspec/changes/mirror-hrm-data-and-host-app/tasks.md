Section 1 stands up the mirror and clears the ground. Section 2 keeps it current. Section 3 proves
it faithful at the row level. Section 4 moves the derivation onto it and proves it faithful at the
figure level. Sections 5 to 7 replace the generated file with the served application. Section 8
reconciles the documentation. Section 9 verifies.

The sequence matters: `src/hotspots.py` keeps working against the live service until section 4, and
the application is not moved onto derived tables until 4.9 has proven the derivation reproduces
every published figure. Rollback before section 5 is deleting the store.

This change absorbs the outstanding work of `add-parking-hotspot-map`, which is archived rather
than continued. Tasks carried over from it are marked **[was N.n]** against their original number.

**Preconditions, not tasks:** a Postgres instance to mirror into, and a place to run the sync.

## 1. Stand up the mirror

- [ ] 1.1 Remove `.github/workflows/nightly.yml` first, and verify no scheduled job commits to `out/` while the rest of this change is in progress — left running, the bot churns the very files section 8 is trying to pin down
- [ ] 1.2 Fix the mirror's scope before designing for it: service requests, custom fields and census dissemination areas, each **in full**, and verify `311_Call_Details` is not mirrored — at 4,968,536 rows and ~1.2 GB it is three times everything else combined, and `design.md` M10 records why it buys nothing
- [ ] 1.3 Store the custom-fields layer key-value as published — 1,156,710 rows across 88 distinct `CUSTOM_FIELD_NAME` values — rather than pivoted on ingest, and verify the stored shape matches the source layer so its count can be checked against the service's own
- [ ] 1.4 Derive a per-call view over the seven parking attributes (`Alleged Violation`, `Property Ownership`, `Vehicle Was Towed`, `Vehicle Make`, `Vehicle Model`, `Vehicle Colour`, `Vehicle Province`), and verify it yields one row per request for the 110,900 calls carrying an alleged violation — a pivot cannot be the stored form, because 88 field names do not fit seven columns
- [ ] 1.5 Define the schema for requests, custom fields, census areas, sync history, retained list history and triage decisions, and verify each downstream section's reads are supported without a later migration
- [ ] 1.6 Stand up a `pytest` harness with a dependency manifest, a `tests/` tree and a runnable entry point, and verify `pytest` passes from a clean checkout — every "verify" in this file is a manual inspection until this exists, and the reconciliations in 3.6 and 4.9 need to be re-runnable rather than performed once **[was 12.4, partial]**
- [ ] 1.6a Make the suite runnable with no network access, and verify it passes with outbound requests blocked — fixtures stand in for the source, so a source outage and a code regression stop looking alike
- [ ] 1.6b Record that the project now has a dependency manifest where it previously had none, and verify `README.md`'s "No keys and no install" is not left describing the server (see 8.3) — the stdlib-only constraint was a hackathon property of the viewer's experience, and it survives for the viewer only
- [ ] 1.7 Load each layer by offset paging to exhaustion, and verify stored row counts match the service's own count-only query per layer — 478,458 requests and 1,156,710 custom-field rows at time of writing
- [ ] 1.8 Make the initial load resumable at page granularity, and verify a load interrupted partway continues from the last completed page rather than restarting
- [ ] 1.9 **Report observed on-disk size per mirrored layer** in the implementation findings, and verify the figures against the ~300–400 MB total estimate rather than restating the estimate
- [ ] 1.10 Measure the full load's wall-clock time and request count, and verify it is within the order predicted — roughly 1,250 pages at about 0.52s each

## 2. Sync incrementally

- [ ] 2.1 Advance a per-layer `ObjectId` watermark, retrieving rows above the mark, and verify a sync after new rows arrive stores them and moves the watermark to the highest identifier retrieved
- [ ] 2.2 Verify the monotonicity assumption at sync time on the requests layer, and verify a row arriving above the watermark with an initiation date older than rows already held is reported as an anomaly
- [ ] 2.2a Check monotonicity on the custom-fields layer through its request's date rather than its own, and verify the check is implemented — that layer carries `REQUESTID`, `CUSTOM_FIELD_ID`, `CUSTOM_FIELD_NAME`, `CUSTOM_FIELD_VALUE` and `ObjectId` and **no date field at all**, so the requests-layer check does not transfer
- [ ] 2.3 Refetch in full every request the mirror holds as open, together with its custom-field rows, and verify a request closed at the source since the last sync has its closure date, status and resolution updated — updated in place, not appended
- [ ] 2.3a Measure the open-set refetch rather than assuming it, and verify the cost of both halves: the requests side is ~4 pages for 3,351 open requests, while the custom-fields side is ~23,000 rows fetched by `REQUESTID IN (...)`, which is the slow query pattern at roughly six seconds per chunk
- [ ] 2.4 Verify a tow flag set at the source after filing is reflected for a request held as open — it is the only published enforcement outcome, so a watermark-only append would under-count tows and bias the project's central finding in the direction that flatters it
- [ ] 2.5 Handle a no-op sync, and verify a sync finding nothing new is recorded as successful with the watermark unchanged and no error raised
- [ ] 2.6 Poll each layer's `editingInfo.lastEditDate` nightly and pull only when it advances, and verify a poll against an unchanged source performs no pull and is recorded as successful
- [ ] 2.6a Keep the poll cadence and the pull cadence separate, and verify a week in which the source publishes once produces seven polls and one pull
- [ ] 2.6b Record the observed source last-edit timestamp with every sync so the cadence accumulates as measurement, and verify the interval history is queryable — no published HRM refresh schedule was found, and the repository's one "weekly-refreshed" claim is unsourced
- [ ] 2.6c Skip re-pulling static reference layers, and verify the census layer — unedited since 2024-03-19 — is not re-pulled merely because a schedule fired
- [ ] 2.6d Keep the watermark pull and open-set refetch running on their own schedule independent of the poll, and verify rows changed without `lastEditDate` advancing are still captured
- [ ] 2.6e Verify the sync runs unattended with no credentials
- [ ] 2.7 Implement full reload as a supported operation, and verify a reload after an induced divergence restores agreement with the service's counts

## 3. Make staleness impossible to miss, and prove the mirror faithful at row level

- [ ] 3.1 Record every sync attempt with its start, outcome, watermark reached, per-layer rows inserted and updated, observed source last-edit timestamp, and error on failure, and verify a failed sync appears as a row rather than as an absence
- [ ] 3.2 Expose last attempt and last success separately, and verify a sync failing repeatedly reports a recent attempt and an unchanged last-success time
- [ ] 3.3 Expose four clocks separately — source last-edit, most recent call date, last successful sync, next sync due — and verify each is distinguishable from the others, including the case where syncs succeed while the source stops advancing
- [ ] 3.4 Report whether the mirror is behind the source, and verify the case where HRM has published more recently than the last successful sync is distinguished from the case where HRM simply has not published
- [ ] 3.5 Compare per-layer stored counts against the service's count query on every sync, and verify a divergence is reported with both figures rather than silently resolved
- [ ] 3.6 **Reconcile at row level before anything depends on the mirror.** Verify the mirror's `Driveway` call set and its custom-field rows are identical to the service's for the same selection — same request ids, same field values, same counts. This is provable with the mirror alone; figure-level reconciliation is 4.9, because it needs a derivation to exist

## 4. Move the derivation onto the mirror

- [ ] 4.1 Read selection, joining, outcome and vehicle attachment, address reduction, doorway location and census placement from the mirror, and verify no network request is issued during a derivation
- [ ] 4.2 Remove the live-fallback path entirely, and verify a record absent from the mirror is reported as absent rather than fetched from the source
- [ ] 4.3 Report a derivation running against an unreconciled mirror as such, and verify the derivation does not top up missing rows itself
- [ ] 4.4 Move retry and paging out of the derivation into the mirror, and verify the census layer is read locally like every other layer, closing the retry asymmetry recorded as D14a **[was 12.1]**
- [ ] 4.5 Preserve census containment correctness including interior holes, and verify agreement with the authoritative spatial answer on the same six addresses the original implementation was verified against
- [ ] 4.6 Replace the fixed `-3` offset at `src/hotspots.py:37` with daylight-saving-aware conversion per timestamp, and verify two calls at the same local clock time in July and in January report the same local time — across a 2020–2026 range every record outside daylight saving is currently an hour out **[was 8.1]**
- [ ] 4.6a Confirm the corrected conversion does not shift any doorway across the recency boundary or change `last_call` dates, and verify by diffing the doorway list before and after **[was 8.3]**
- [ ] 4.7 Carry the derivation time, last successful sync time and most recent call date onto every output, and verify all three appear together **[was 9.1]**
- [ ] 4.8 Add automated coverage for the pure functions — address reduction, street extraction, point-in-polygon including interior holes, vehicle identity, recurrence counting — and verify the suite passes without network access **[was 12.4]**
- [ ] 4.9 **Reconcile at figure level.** Run `src/hotspots.py` against the live service and the new derivation against the mirror for `Driveway`, and verify every row and figure in `out/watchlist.csv`, `out/blocks.csv` and both briefs matches — any discrepancy is a mirror or derivation defect, not a revision of the analysis. The cheap route is to point `query()` at the mirror and diff the outputs

### 4b. Compute the figures the documentation rests on

Carried from the archived change, and a prerequisite for 8.1: two figures central to the product's
claim exist only in hand-written prose and are reproduced by no generated output.

- [ ] 4.10 Compute recurrence split by tow status and emit it, and verify the generated output reproduces the 44.7 per cent against 44.6 per cent comparison quoted in `README.md` **[was 11.1]**
- [ ] 4.11 Report the effect size the comparison can rule out, and verify the output states a bound rather than only an absence of difference **[was 11.2]**
- [ ] 4.12 State that the tow comparison is observational and that tows may cluster at the worst addresses, and verify the caveat travels with the figures **[was 11.3]**
- [ ] 4.13 Compute median elapsed time from `DATE_INITIATED` to `DATE_CLOSED`, excluding and separately counting calls never closed, and verify the output reproduces the response-time figure opening `README.md` **[was 11.4]**
- [ ] 4.14 Reconcile the call denominators in circulation — the DISPATCH label alone, the label plus `DRIVEWAY`, and the tow-comparison cohort — and verify each published figure names which population it is drawn from **[was 11.5]**

### 4c. Every canonical violation type

- [ ] 4.15 Freeze the canonical violation-type list against a fresh live query of `Alleged Violation`, grouping each current label with its legacy short code and `(DISPATCH)` variant, excluding `Other` (7,542) and `Left Running`, and verify the frozen list accounts for the great majority of non-excluded rows — 71 distinct raw labels and 110,900 calls were observed on 2026-09-15 **[was 14.1]**
- [ ] 4.16 Derive every tracked canonical type from one pass over the mirror, and verify no source query is issued and no doorway or block from one type appears in another's output **[was 14.2]**
- [ ] 4.17 Compute recurrence, the tow comparison and vehicle uniqueness independently per canonical type, and verify a type whose figures do not resemble driveway's states its own conclusion rather than driveway's **[was 14.5]**
- [ ] 4.18 Check `No Parking Sign` — 27,404 calls, the largest type — against the string-reduction concern in R2, and verify whether address collisions at that volume require coordinate keying ahead of the other types **[was 14.6]**
- [ ] 4.19 Measure the all-types derivation time against the mirror, and verify it is bounded by computation rather than by network

## 5. Serve the application

- [ ] 5.1 Serve the doorway list, block list and map over HTTP, and verify a viewer needs no install, account, credential or build step
- [ ] 5.2 Route per canonical violation type scoping lists, ranking, markers and triage state, and verify switching types never mixes rows and that a view carries only its own type's data
- [ ] 5.3 Report an untracked type as untracked, and verify a route naming one renders a stated reason rather than an empty list
- [ ] 5.4 Serve the street network as a cacheable asset, and verify moving between types does not re-download the 490 KB geometry
- [ ] 5.5 Compute derived results per request against an indexed mirror rather than materializing per filter combination (`design.md` M11), and verify a view's response time under the default parameters and under a widened threshold
- [ ] 5.5a Provide district, minimum recent calls, minimum still-calling doorways, recency window and recurrence window as interactive controls, and verify results and ranking update correctly as each changes
- [ ] 5.5b Expose the recency window, currently hardcoded at `src/hotspots.py:214` (`recent_from = latest - 365 days`), as a parameter, and verify changing it changes which doorways are listed and the ranking **[was 10.1]**
- [ ] 5.5c Present the recency and recurrence windows as two distinctly labelled controls, and verify that adjusting the recency window changes the listed set while adjusting the recurrence window changes only the repeat figure — they share a default of 365 days and govern different things
- [ ] 5.5d State the filter values in effect on every view and export, and verify a filtered list cannot be mistaken for the default list
- [ ] 5.5e Report an empty filter result as empty and name the values that excluded everything, and verify it is distinguishable from a failure
- [ ] 5.6 Preserve light and dark rendering and following a preference change while open, and verify by toggling the system colour scheme with the application open

## 6. Shared triage and role

- [ ] 6.1 Persist a decision and its note server-side, and verify a decision recorded by one viewer is visible to a second viewer of the same type's list
- [ ] 6.2 Verify decisions survive an application restart
- [ ] 6.3 Remove the browser-storage fallback and the `window.claude.use("db")` binding at `web/template.html:1007`, and verify no code path leaves a viewer believing a local-only decision was shared
- [ ] 6.4 State plainly when a decision could not be persisted, and verify an unsaved decision is not displayed as recorded
- [ ] 6.5 Attribute each decision to a role and a last-changed time, and verify both are visible on a triaged doorway and block
- [ ] 6.6 Ask for a role before a decision is recorded while leaving the lists readable without one, and verify both paths
- [ ] 6.7 State that roles identify rather than authenticate, and verify the application makes no access-control claim and grants no differing access by role

## 7. Freshness, limits and export in the served views

- [ ] 7.1 Display a prominent message on every view giving the last successful update and the expected next update, and verify it is visible without being sought **[was 9.2]**
- [ ] 7.2 Attribute a lag to whichever system owns it, and verify that a source that has not published for days reads as a limit of HRM's publishing schedule while a sync that has fallen behind a published source reads as this system being behind
- [ ] 7.2a Make the next update a state rather than a rendered date, and verify that once the scheduled time has passed by the grace period with no successful sync the message reads as overdue rather than continuing to show a future date that never arrives
- [ ] 7.2b State that the next source update is not yet known where too little history exists, and verify no estimate is fabricated from a single observation
- [ ] 7.3 Remove the template's undated "Regenerated from the live service, not from a stored export" assertion, and verify the view shows dated freshness rather than a claim — the sentence is also now false, since it is served from a mirror **[was 9.3]**
- [ ] 7.4 State the recency window's anchor date and length on every filtered list, and verify the wording names the date the window is measured from **[was 10.3]**
- [ ] 7.4a Warn when the most recent call in the data is materially older than the run time, and verify the warning fires against a deliberately stale input **[was 10.2]**
- [ ] 7.5 Carry the interpretation limits into the served views — intake clock, tow as the only recorded outcome, vehicle identity as a floor, text address reduction, derived block labels, block-wide dwelling rate — and verify each is reachable from the view carrying the figures it bears on **[was 12.2, 12.3]**
- [ ] 7.6 Explain how to read the block neighbour count before ordering a remedy, and verify a doorway with several still-calling neighbours is presented as a block-level problem
- [ ] 7.7 Export a type's doorway and block lists as a complete table and a readable brief, and verify the export carries every listed row, the violation type it covers, the clocks, the filter values and the same limits as the view
- [ ] 7.7a Name the canonical type each export and view covers, and verify a reader can tell which type they are looking at without cross-referencing a filename **[was 14.7]**
- [ ] 7.8 Verify a view and an export taken together agree on every count and name the same mirror state

## 8. Reconcile the documentation with generated output

The repository currently states 9,791 calls, 363 doorways and 71 blocks in `README.md` and under
`docs/`. A run on 2026-09-15 produced 9,834, 351 and 70. The figures were hand-written and have
drifted within days. A public URL turns that into a credibility problem. Section 4b must land
first, or the figures 8.1 needs still will not exist.

- [ ] 8.1 Replace every hand-written figure in `README.md` and `docs/parking-hotspots/` with the current generated value, and verify each published number is reproduced by output the reader can regenerate **[was 13.1]**
- [ ] 8.1a Re-derive the hour-of-day and channel figures in `docs/` under the corrected timezone conversion, and verify the published numbers match the corrected output or are updated to it **[was 8.2]**
- [ ] 8.2 Correct `README.md`'s hosted-copy claim — "The hosted copy shares those decisions with everyone who opens it, so a team triages one list instead of three" — to describe the served application, and verify it no longer describes a Claude Artifact
- [ ] 8.3 Qualify `README.md`'s "No keys and no install" to the viewer rather than the system, and verify it does not imply the server has no dependencies
- [ ] 8.4 Replace `README.md`'s "Open the board" instructions to double-click `out/triage-board.html` with the URL and the export path, and verify a reader is not directed to a file that is no longer the product
- [ ] 8.5 Record in `docs/parking-hotspots/decisions.md` the decisions this change makes — mirror, source-paced sync, served application, shared triage, role selection, and the removal of this repository's scheduled job — with their reasons, and verify the narrative record matches these specs
- [ ] 8.6 Retain the doorway and block lists per sync in the store, and verify the record of which doorway left the list and when survives the removal of the scheduled job that was building it by accident in git history
- [ ] 8.7 Stop generating committed output to `out/`, and verify the exports in 7.7 are the only way a list leaves the application
- [ ] 8.8 Correct the unsourced "weekly-refreshed source" claim wherever it is repeated, and verify no document states a source refresh interval that nothing measured supports

## 9. Verification

- [ ] 9.1 Verify the application serves correctly with the HRM service unreachable, and that its views state the mirror's freshness
- [ ] 9.2 Verify a sync stopped past its scheduled time produces a visibly overdue application rather than a healthy-looking one displaying a future update that never arrives
- [ ] 9.3 Verify no 311 call-detail value is read, referenced or implied anywhere in the delivered system, distinguishing it from the `311 Online` value of `INITIATED_BY` on the service requests layer, which is a channel label and is legitimately used
- [ ] 9.4 Verify every figure served for `Driveway` matches the pre-change live-source run, per 4.9, after the application is serving from derived tables
- [ ] 9.5 Verify the board and the exports from one mirror state agree on every count, including the provenance and comparison figures added in 4b **[was 13.2]**
- [ ] 9.6 Verify two viewers in different roles triage one shared list and each sees the other's decisions with role attribution
- [ ] 9.7 Verify no served view or export claims an enforcement outcome from the `RESOLUTION` field or an hour-of-day finding from pooled timestamps **[was 13.4]**
- [ ] 9.8 Verify no tracked violation type presents blocked driveway's conclusion without its own effectiveness evidence supporting it — D19's requirement survives the archiving of the change that stated it **[was 14.3, 14.5]**
