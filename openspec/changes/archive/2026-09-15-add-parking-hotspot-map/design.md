## Context

See `proposal.md` — Why for motivation. This document records the decisions the shipped pipeline actually embodies, and preserves the ones an earlier draft of this change got wrong along with the evidence that overturned them.

`src/hotspots.py` is the whole pipeline: roughly 520 lines of Python 3.12 standard library, no dependencies, no install, no keys. `web/template.html` plus `web/map-network.json` are the board's build inputs. `out/` is generated. `.github/workflows/nightly.yml` runs it at 05:30 Atlantic and commits the result.

Three HRM ArcGIS layers, all public, all non-spatial tables except the census layer:

| Layer | Rows | Role |
|---|---:|---|
| `Cityworks_Service_Requests` | 477,343 | one row per call; address, coordinates, timestamps, channel, resolution |
| `Cityworks_Service_Requests_Custom_Fields` | 1,153,448 | key-value, joined by request id; violation, tow, ownership, vehicle |
| `Census_2021_Dissemination_Areas` | 610 polygons | the neighbourhood grain, with a dwelling count |

The blocked-driveway selection is 9,791 calls, 2020 to 2026-09-04, yielding 363 doorways still calling and 71 blocks holding 283 of them.

## Goals / Non-Goals

**Goals:**

- One run produces every output, so the board and the briefs cannot disagree.
- Run with no person, no install and no credentials, against public endpoints.
- Carry each interpretation limit in the outputs themselves, because briefs circulate without their context.
- Keep the pipeline reusable across violation types by argument rather than by edit.

**Non-Goals:**

- Any persistence layer. The run is stateless and re-queries the source.
- Any served API or build step. The board is a file.
- Time-of-day analysis. Rejected on evidence; see R1.
- Effect measurement of an installed remedy, and per-address remedy choice.

## Decisions

### D1. Select from the custom-fields side first, then fetch those calls by id

The violation type is not on the call record — it lives in the key-value layer. So selection queries that layer for request ids whose alleged violation matches, then fetches those requests by id in chunks.

This inverts the obvious order (fetch all parking calls, then filter) and is far cheaper for a narrow slice: 9,791 ids instead of paging 121,633 parking calls to discard 92 per cent of them.

### D2. Match the violation as a case-insensitive substring

HRM files one problem under more than one label. Verified live:

```
Blocking Driveway (DISPATCH)   9,729
DRIVEWAY                          62
                        total   9,791
```

Substring matching merges both. The run prints the labels it resolved to, so an operator can see what was included.

*Note on a related trap:* grouping on `CUSTOM_FIELD_VALUE` through the service appears to be case-insensitive — the same count came back once as `Other` and once as `OTHER` — so the service cannot be trusted to enumerate case variants. Substring matching sidesteps this; a rule built on exact labels would not.

### D3. The doorway is the unit of action; the block is the unit of diagnosis

Two levels, deliberately:

```
call --> doorway (cleaned address)  --> block (census dissemination area)
         "where the bollard goes"       "whether a bollard is the answer"
```

The doorway is primary because the remedy is physical and installed at one address; no coarser unit can say where to put it. The block exists because several doorways all still calling is one problem, not several, and a sign at each address is the wrong answer to it.

The bridge between them is `block_doorways_calling`, carried on every doorway row. Reads 1: install the bollard. Reads 10: the block needs a permit zone, curb management or a parking study.

The earlier draft of this change forbade address keying outright. That was too strong — it assumed the unit of interest is an area, when the unit of *action* is a doorway.

### D4. Reduce the address by string, and say so

The address field carries community and postal code, so one doorway appears under several spellings:

```
5214 GERRISH ST,  HALIFAX,  B3K 5K3
5214 GERRISH ST,  HALIFAX            same place
```

Taking the text before the first comma and collapsing whitespace merges them in one line, with no per-grammar parser. It is a string reduction, not a geocode, and some doorways still split — stated in every brief rather than hidden.

The earlier draft rejected this as requiring "a parser per grammar." It does not. Coordinate keying remains the better answer and is the natural next step (see R2), but the interim is cheap and honest about its residual.

### D5. Locate a doorway at the median of its calls, and test containment locally

A doorway's location is the median latitude and longitude of its calls, so one mistyped coordinate cannot move it.

Containment against the 610 census polygons runs locally: a bounding-box prefilter, then an even-odd ray cast across every ring so interior holes exclude correctly. The alternative is one server request per address. Verified against the service's own spatial query on six addresses, 6 of 6 matching.

### D6. Census dissemination areas are the neighbourhood grain

Four candidates were tested:

| Candidate | Result |
|---|---|
| `COMMUNITY` on the call record | 7,651 of 9,791 driveway calls say `HALIFAX` — rejected |
| Community Boundaries, 200 polygons | `HALIFAX` holds 3,121 of 4,255 addresses — same failure |
| Community Plan Areas, 22 polygons | far too coarse — rejected |
| Street name, 1,039 groups | works and reads well, but weaker: top 20 streets hold 25 per cent of recent calls against 35 per cent for the top 20 blocks. Kept as a column and used to name blocks |

Census dissemination areas win on two counts: they are the sharper cut, and they carry `DATDWELL20`.

The earlier draft rejected "administrative boundaries such as postal areas or districts" as too coarse on the strength of the `COMMUNITY` failure alone. That dismissed a whole family on its worst member.

### D7. Normalize block load by dwellings, not only by time

`DATDWELL20` is the denominator that stops a dense block outranking a genuinely worse one just for having more front doors, and it arrives with the census join at no extra cost.

This is an *exposure* denominator. The earlier draft normalized only by time — requests per year — which a fixed-area grid has no way to improve on, because a hexbin carries no dwelling count. This is the concrete advantage the census grain has over a grid.

The rate spans the whole block rather than the street the calls are on, which is stated wherever it is published.

### D8. Rank doorways by calls still arriving, discounted by enforcement applied

`calls_12mo * (1 - tow_rate)`, with any doorway carrying no call in twelve months dropped outright.

An all-time ranking put 155 dead addresses onto a 406-row list — 38 per cent of it — one of which had not called since 2024-01-22. A work list of stale addresses is the most expensive kind of error, because a person spends the morning on it.

Blocks rank by number of still-calling doorways first, then per-dwelling rate. Spread ranks first because it is what separates a block-wide problem from one bad address.

### D9. The tow flag is the only enforcement outcome; `RESOLUTION` is discarded

HRM publishes no ticketing field — not among the custom field names, and `RESOLUTION` carries no ticket value. Its distribution for parking is 95.1 per cent `Requested Service Provided`, consistent with a default applied at closure rather than a per-case judgement.

So a call with no tow is a call with **no recorded outcome**, not a call where nothing was done. That phrasing is required in the specs because the weaker claim is the defensible one.

The earlier draft contradicted itself here: it described `RESOLUTION` as a probable default and its derived rate as a lower bound, then made `RESOLUTION` the primary outcome classifier anyway, which would have labelled about 94 per cent of calls "actioned" off that same default. Discarding the field resolves the tension.

### D10. Vehicle identity from make, model and colour, reported as a floor

There is no plate in the data. Make plus model plus colour is a rough identity: two identical cars count as one, so a distinct count is a floor and never a ceiling.

It is enough for the finding — 2,473 distinct vehicles across 2,588 calls, 96 per cent unique — because the direction of the bias is known and runs against the claim. Sparse coverage is the real hazard: one listed address records a vehicle on 1 of its 11 calls, so `vehicles_seen` must be published beside `vehicles_distinct` or the ratio misleads.

### D11. The board is one self-contained file

Python string-substitutes the data and the map geometry into `web/template.html` and writes a single ~620 KB HTML file. No server, no install, no account, no build step, and no framework.

This follows from who uses it. A work list that needs infrastructure to open is a work list nobody opens, and the team that installs bollards is not going to run a dev server. It also makes the board mailable and archivable: the committed file *is* the record of that day's list.

The earlier draft specified a Python backend serving JSON to a TypeScript frontend. That stack does not exist in the repository and would add an operational dependency for no gain at this scale, since the data is small enough to embed whole.

### D12. Map geometry is embedded, not fetched

The map draws HRM's own street network from `web/map-network.json`, embedded at build time rather than loaded from a tile service.

Two reasons. A single file cannot depend on an external service and still open anywhere. And where the board is hosted as a sandboxed page, external image and network requests are blocked outright, so embedded vector geometry is the only approach that works in both settings.

### D13. Shared triage state where available, browser storage otherwise, always disclosed

Decisions persist to shared storage offered by the hosting environment, with live updates, falling back to the viewer's own browser when that is absent.

The disclosure is the load-bearing part. A viewer who believes their triage reached colleagues when it sat in their own browser is worse off than one told plainly it is local, so the board states which mode is in effect and says so again if live updates drop.

### D14. Stateless runs against the live source

No snapshot, no database. Each run re-queries the layers.

At this scope the snapshot buys nothing: the selection is ~33 id-chunk fetches plus 610 census polygons, and the scheduled job runs nightly against a weekly-refreshed source. A mid-run failure repeats the run, which is acceptable for a nightly job and was the explicit trade.

The earlier draft required a resumable snapshot store. That was sized for a 110-page all-parking pull and does not apply to a violation-scoped selection.

### D14a. The retry wrapper does not cover every source fetch — the implementation does not yet

Retry is meant to be uniform across every fetch under D14, but it is not. `query()` retries a failed request up to four times with a delay between attempts, and every call and custom-field fetch goes through it. `fetch_blocks()`, which retrieves the 610 census polygons, calls `urlopen` directly and raises immediately on the first failure.

The asymmetry matters because the census fetch runs once per invocation regardless of how narrow the violation selection is, so it is proportionally the request most exposed to a single transient failure ending the whole run. It was very likely an oversight rather than a considered choice — nothing in `docs/` argues for treating the two fetches differently.

### D15. Timestamps convert with daylight saving — the implementation does not yet

Source timestamps are UTC and Halifax runs UTC−3 in summer, UTC−4 in winter. Conversion must be daylight-saving-aware per timestamp.

`src/hotspots.py:37` applies a constant `-3`. Across a 2020–2026 range every record outside daylight saving is an hour out. The published hour-of-day figures in `docs/` were computed this way, so those specific numbers are an hour off for winter records — the conclusion they support survives easily, the figures are not exact.

This is the one place the earlier draft was right and the code is wrong, and it stays as an open task rather than being written down as intended behaviour.

### D16. Every output carries its run provenance — the implementation does not yet

Two gaps compound each other.

Nothing stamps the time a run executed. The briefs name the most recent call in the data; the board asserts it was "regenerated from the live service" without a date. Since the scheduled job commits `out/` automatically, a job that has been failing for a week produces a board indistinguishable from a fresh one.

And the recency window is anchored to the data, not the clock:

```python
latest = max(to_local(c["DATE_INITIATED"]) for c in calls if c["DATE_INITIATED"])
recent_from = latest - datetime.timedelta(days=365)
```

If the source stalls, "still calling in the last 12 months" quietly becomes "in the 12 months before whenever the data stopped." Neither the window's anchor nor the staleness is visible to a reader.

### D17. The headline figures must come from the pipeline, not from a one-off analysis

`product.md:82` states plainly: "Every number in this folder is produced by `src/hotspots.py`." Two of the numbers doing the most work for the product's central claim are not.

**Closure time.** `DATE_CLOSED` is fetched at `src/hotspots.py:188` and used nowhere else. The 41-minute figure opening both `README.md` and `product.md` has no corresponding computation.

**Recurrence split by tow status.** `build()` computes `repeats` per doorway from the same-address time series, but never partitions it by whether the doorway's calls were towed. The 44.7 per cent against 44.6 per cent comparison — the number that carries the entire "a tow does not change anything" argument — exists only in hand-written prose in `README.md`, `decisions.md`, and `product.md`, and appears in none of `out/watchlist.csv`, `out/watchlist.md`, `out/blocks.csv`, `out/blocks.md`, or the board.

This is a bigger risk than the closure-time gap. A stray number in an opening line is a citation error; an unreproducible number behind the product's one-sentence thesis is a claim nobody re-running the pipeline can check. Both are cheap to close, since every field either figure needs is already retrieved — computing them is a matter of adding the aggregation and the output line, not a new query.

### D18. Track every canonical violation type, not one hardcoded label

D1 through D9 were verified against a single label group (`Blocking Driveway (DISPATCH)` / `DRIVEWAY`). Re-querying the custom-fields layer live (2026-09-12) for every distinct `Alleged Violation` value found roughly sixty raw labels. The dual-labelling pattern D2 documented for driveway — a current mixed-case label plus a legacy uppercase short code — repeats across most of them:

```
No Stopping Sign          2,770  +  NOSTOPPING             30
Within 5M of Hydrant      2,017  +  HYDRANT                31  (+ 2,383 DISPATCH variant)
Obstructing Snow Removal  2,579  +  SNOW                    91  (+ 1,236 DISPATCH variant)
On Sidewalk                 864  +  SIDEWALK                20  (+ 1,080 DISPATCH variant)
Private Property         17,029  +  PRIVATE                71  +  On Private Property   62
```

A canonical violation type is therefore a group of raw labels, exactly as D2 already treats driveway, not a single string.

Two labels are excluded from the tracked set rather than silently merged in:

- **`Other` (7,502 calls).** An ambiguous catch-all with no defined meaning, the same caution D9 raises about trusting an unexamined default.
- **`Left Running` / `LEFTRUNNING` (24 calls).** Filed through the same Alleged Violation field but regulates idling, not where a vehicle is stopped — a different bylaw question than every other label here.

The pipeline runs once per canonical type, in the existing order (D1), producing an independent doorway list, block list, brief set and effectiveness evidence per type. Types are not pooled: the "no repeat offender" finding (D8-D9's basis) was measured on driveway calls only, and nothing in the data says it transfers to, say, `On Highway Over 24 Hours` or `Within 5M of Hydrant`. Each type's evidence is computed and read on its own; see D19.

The full canonical list — around thirty types once the dual-labelling groups above are resolved — is finalized as an implementation task (`tasks.md` #14.1) rather than frozen here, because the live label set can drift between this writing and implementation.

This reopens the earlier draft's premise that "all-parking scope" is out of reach — R2 was right that a wider scope changes what the primary unit should be. It does not overturn R2: coordinate binning still isn't needed for every type, only for the highest-volume ones (`No Parking Sign` at 27,302 calls is the type R2 already named as where string-reduced addressing starts to strain). That threshold should be checked per type as it is onboarded, not assumed clear for all thirty.

### D19. Effectiveness evidence and its conclusion are computed per type, never assumed

Because D18 batches independently rather than pooling, `enforcement-effectiveness` runs its recurrence, tow and vehicle-uniqueness comparison separately for each canonical type. A type whose tow-versus-no-tow recurrence gap is large, or whose vehicles repeat more than driveway's 96 per cent unique, does not support the same "point the conclusion at physical remedies" framing (D9) — the output must say so for that type rather than inherit driveway's language.

This follows directly from D9's own reasoning: an outcome comparison is only as strong as its own recomputation, and reusing driveway's stated conclusion for a type that has not been measured would repeat exactly the mistake D9 rejected `RESOLUTION` for — asserting an outcome the data at hand does not establish.

## Rejected, with the evidence

Kept rather than deleted, because each was argued at length in the earlier draft of this change and the reasoning is worth not re-deriving.

### R1. Time of day as an analysis dimension — disproven

The earlier draft required a day-of-week by time-of-day profile on every hot spot, with peak identification, and justified it with sparsity arithmetic. It was built, tested and removed.

`DATE_INITIATED` is when a staff member keyed the call, not when the driveway was blocked. The `INTERNAL` channel carries 85 per cent of these calls and records 6 out of 8,344 between 21:00 and 07:00, peaking at 13:00; the citizen-typed `311 Online` channel spreads across all 24 hours and peaks at 18:00. Pooled, any hour-of-day finding measures office hours.

Tested properly rather than against a naive null: against a matched null the observed 58.9 per cent sits against a null mean of 48.8 per cent, and only 17 of 58 addresses beat their own 95th percentile. Out of sample a per-address tuned window scores 47.5 per cent against 41.7 per cent for one city-wide window, and weekday tuning *loses*, 35.7 against 37.9.

The earlier draft read the hard 08:00 onset as an enforcement shift start and treated it as real signal. It is an intake artifact: a driveway blocked at 02:00 is keyed when the office opens. What survives is the channel split as a *timestamp-reliability* signal, which is why `parking-data-ingest` requires the channel be exposed and any time-of-day claim be split by it or qualified.

### R2. A coordinate-keyed fixed-area grid as the primary unit — superseded, not wrong

The earlier draft proposed binning on coordinates into 300–400 m cells, having measured that 175 m splits corridors:

```
AGRICOLA ST, HILFORD ST     122  +  MCCULLY ST, AGRICOLA ST  111
SOUTH PARK ST, LUCKNOW ST   148  +  SOUTH PARK ST, ANNANDALE 135
QUINPOOL RD, QUINGATE PL    191  +  QUINPOOL RD, PEPPERELL    95
```

That measurement stands, and coordinate keying is strictly better than string reduction. It is not the primary unit here because a grid cell cannot tell you where to install a bollard and carries no dwelling count. The doorway-then-block structure answers both.

This becomes the right approach at wider scope. Running the pipeline on `No Parking Sign` — 27,302 calls, larger than blocked driveways — is where string reduction strains and coordinate binning starts to earn its place.

### R3. `RESOLUTION`-based outcome classification — discarded, see D9.

### R4. The 311 call comparison — dataset dropped

The earlier draft required a bucketed comparison of 311 parking-call volume against service-request volume, having correctly established that no per-record join is possible: `311_Call_Details` carries `CALL_ID`, `QUEUE_NAME`, `OUTCOME`, `WRAPUP_NAME`, `ARRIVAL_DATETIME` and talk times — no address, no coordinates, no request identifier, and `OUTCOME` is 99.5 per cent `Handled`, an agent disposition rather than a municipal one.

The independent conclusion in `docs/parking-hotspots/data-sources.md` is the same, and the dataset is simply not used. The aggregate finding it would have supported — roughly 255,000 parking-enforcement calls against about 117,000 parking service requests — is real but says nothing about any particular doorway, and the product is a doorway list. The layer also carries a timestamp HRM documents as shifted 3–4 hours from true UTC, in the opposite direction to the requests layer, so any comparison needs two different corrections.

### R5. A snapshot database with resumable ingest — see D14.

### R6. Severity tiers and per-year rate normalization — poor fit

The earlier draft required severity tiers with published boundaries and a legend reading them, plus rates normalized per year so arbitrary date ranges stay comparable.

A work list does not want tiers. It wants an order, and it wants stale rows gone. The fixed twelve-month window and the ranking in D8 do that directly, and `block_doorways_calling` carries more decision value than a tier would. Per-year normalization exists to compare arbitrary ranges, which this product never does — it always asks the same question about the same window.

### R7. Coordinate bounds validation — no value at this scope

The earlier draft required rejecting out-of-municipality coordinates and reporting the counts, on the strength of a handful of transposed latitude/longitude rows.

Re-measured against the actual scope: illegally-parked-vehicle records outside a plausible municipal bounding box number **zero**. The earlier figure came from the whole 477,343-row table across all categories. The median-coordinate rule in D5 already absorbs single mistyped points, so an explicit bounds stage would add a check that never fires.

### R8. Normalizing the violation by stripping the `(DISPATCH)` suffix — insufficient

The earlier draft specified stripping `(DISPATCH)` and preserving a dispatch flag. That rule yields canonical `Blocking Driveway` and leaves `DRIVEWAY` as a separate type, so it would have undercounted the selection by the 62 calls filed under the bare uppercase label. Substring matching (D2) handles both, and the case-insensitive grouping trap noted there makes any exact-label rule fragile.

## Risks / Trade-offs

- **A fixed timezone offset misplaces winter records by an hour** → D15 is an open task. Impact is contained because time of day is not in the product, but the hour figures published in `docs/` are affected and the 12-month boundary can shift a date.
- **A stale scheduled run is invisible** → D16 is an open task. Until then, the commit date in git history is the only staleness signal, and it is not in the file a person opens.
- **String address reduction splits some doorways** → stated in every brief; R2 records coordinate keying as the fix and the scope at which it becomes necessary.
- **The tow comparison is observational** → tows may cluster at the worst addresses, which would mask a real effect. Stated wherever the comparison is published, alongside the effect size the data can rule out.
- **Vehicle uniqueness rests on sparse fields** → `vehicles_seen` is published beside `vehicles_distinct` so a ratio drawn from 1 of 11 calls cannot be read as covering all 11.
- **A dissemination area is not a neighbourhood anyone names** → blocks are labelled by the streets their calls come from, stated as derived, with the census id retained for joining.
- **The per-dwelling rate spans the block, not the street** → stated wherever published.
- **No resumability** → a mid-run failure repeats the run. Accepted for a nightly job; would not be acceptable at all-parking scope.
- **The census fetch has no retry** → D14a is an open task. A single transient failure on that one request currently ends the run outright, disproportionate to how small a fix it is.
- **Shared triage state depends on the hosting environment** → the board discloses which mode is active and re-discloses if live updates drop.
- **The pipeline has no automated tests** → every figure is currently verified by re-running against the live source, which also means source drift and a code regression look the same.
- **The product's central claim is not independently reproducible** → D17 is an open task. The 44.7 versus 44.6 per cent recurrence comparison and the 41-minute closure figure exist only in hand-written docs; nobody can currently verify either by running the pipeline instead of trusting the prose.
- **A canonical type's evidence may not support the physical-remedy conclusion** → D19 requires each type's tow and vehicle comparison to be computed and read on its own; a type is only published with driveway's framing if its own numbers support it.
- **Batching every canonical type multiplies nightly query volume** → D18 flags this as unmeasured; runtime and ArcGIS rate limits need checking before all thirty types run on schedule (open, `tasks.md` #14.4).

## Migration Plan

Nothing to migrate: the pipeline is stateless, its outputs are regenerated on every run, and the only persisted state is triage decisions living outside the repository.

Rollback is `git revert` on the generated output, since `out/` is committed. The corrections in D14a and D15–D17 are independent of each other and of the rest of the pipeline, so each can land on its own.

## Open Questions

- Whether to state plainly that effect measurement is absent, or to show a stubbed before-and-after labelled as stubbed. Recorded as open for Chris in `docs/parking-hotspots/decisions.md`; the recommendation there is to say it plainly. Does not affect these specs.
- Whether the named audience is Traffic Management alone or both Traffic Management and enforcement. Also open for Chris; the recommendation is both, and `enforcement-effectiveness` is written to serve both readings.
- Whether corrected closure-time and recurrence-by-tow figures change the documentation's framing materially, which is only answerable once D17 computes them.
