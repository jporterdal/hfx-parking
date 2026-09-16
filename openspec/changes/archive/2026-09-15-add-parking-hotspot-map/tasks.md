Sections 1 to 7 record behaviour already shipped in `src/hotspots.py`, `web/`, and
`.github/workflows/nightly.yml`, and are marked done. Sections 8 to 12 are the outstanding
corrections: places where the specs are right and the implementation is not.

## 1. Ingest and join (shipped)

- [x] 1.1 Select request ids by case-insensitive substring match on `Alleged Violation` from the custom-fields layer, then fetch those requests by id in chunks — verified by the run reporting 9,791 calls for `Driveway`, matching the live total of `Blocking Driveway (DISPATCH)` (9,729) plus `DRIVEWAY` (62)
- [x] 1.2 Report the distinct violation labels a selection resolved to — verified by the run printing `labels matched:` before results are used
- [x] 1.3 Page the tabular layers to exhaustion at 1,000 rows and the census layer at 200 with geometry — verified by retrieved counts matching the service's own count-only queries
- [x] 1.4 Attach tow flag, property ownership, and vehicle make, model and colour to each call, retaining calls that lack them — verified by `vehicles_seen` differing from call count on addresses with partial vehicle data
- [x] 1.5 Reduce each address to a doorway key by taking text before the first comma and collapsing whitespace — verified by postal-code variants of one address collapsing to a single row
- [x] 1.6 Locate each doorway at the median of its calls' coordinates — verified by an outlier coordinate on one call leaving the doorway's position unchanged
- [x] 1.7 Assign each located doorway to its census dissemination area by local point-in-polygon with a bounding-box prefilter and even-odd ray cast across all rings — verified against the service's own spatial query on six addresses, 6 of 6 matching
- [x] 1.8 Report how many doorways fell in no census block — verified by the run's closing `note:` line

## 2. Doorway watchlist (shipped)

- [x] 2.1 Group calls by doorway key — verified by each output row naming one address
- [x] 2.2 Drop doorways with no call inside the recency window — verified by the listed count falling from 406 to 363 when the twelve-month filter was introduced, removing 155 dormant addresses
- [x] 2.3 Require a configurable minimum of recent calls, defaulting to two — verified by `--min-calls` changing the listed count
- [x] 2.4 Carry recent and all-time calls, tows, vehicles seen and distinct, repeat calls, median gap, last call, district, community, street and property ownership per doorway — verified by the columns present in `out/watchlist.csv`
- [x] 2.5 Rank by recent calls weighted by one minus the tow rate — verified by a high-tow-rate doorway ranking below an equal-volume doorway with no tows
- [x] 2.6 Carry the count of still-calling doorways on each doorway's block, defaulting to one where unplaced — verified by the "On this block" column in `out/watchlist.md`
- [x] 2.7 Support restricting the list to one district, and report when nothing meets the threshold — verified by `--district 7` narrowing the list and by the `no addresses met the threshold` path

## 3. Block rollup (shipped)

- [x] 3.1 Group listed doorways by census dissemination area, omitting unplaced doorways from the rollup only — verified by 71 blocks holding 283 of the 363 listed doorways
- [x] 3.2 Require a configurable minimum of still-calling doorways per block, defaulting to two — verified by `--min-doorways` changing the block count
- [x] 3.3 Carry doorway count, recent and all-time calls, tows, dwellings, per-dwelling rate, district, worst doorway and member addresses per block — verified by the columns in `out/blocks.csv`
- [x] 3.4 Express block load as calls per thousand dwellings alongside the raw count, reporting it unavailable where no dwelling count exists — verified by the rate column showing `-` for blocks with no dwelling count
- [x] 3.5 Label each block by the two streets its calls predominantly come from, retaining the census identifier — verified by `out/blocks.md` naming streets and the CSV carrying `block`
- [x] 3.6 Rank blocks by still-calling doorway count, then by per-dwelling rate — verified by the worst block (14 doorways, 69 calls, 609 dwellings) leading the list ahead of higher-volume blocks

## 4. Effectiveness evidence (shipped in part)

- [x] 4.1 Count recurrence per doorway as a further call within a configurable window, defaulting to 365 days — verified by `repeat_calls` and `median_gap_days` in `out/watchlist.csv` and by the brief stating the window used
- [x] 4.2 Derive vehicle identity from make, model and colour, publishing distinct against seen — verified by `out/watchlist.md` rendering "distinct of seen" per row
- [x] 4.3 State that vehicle identity is not a plate so the distinct count is a floor — verified by the closing notes in `out/watchlist.md`
- [x] 4.4 Use the tow flag as the only outcome signal and state that a call with no tow carries no recorded outcome — verified by the closing notes in `out/watchlist.md`

## 5. Triage board (shipped in part)

- [x] 5.1 Build one self-contained HTML file by substituting data and map geometry into the template — verified by `out/triage-board.html` opening in a browser with no server and no network access
- [x] 5.2 Build the board from the same run that writes the briefs, reporting a skip that names the missing input — verified by the `skipped the board:` path when a template is absent
- [x] 5.3 Present both the doorway and block lists with their per-row detail — verified in a browser by switching between the two lists
- [x] 5.4 Draw a zoomable map from the embedded HRM street network and place located doorways on it — verified in a browser by zooming and by a doorway appearing at its coordinates
- [x] 5.5 Record a triage decision and optional note per doorway and per block, showing when each was last changed — verified in a browser by recording a decision and reopening the file
- [x] 5.6 Share decisions where the host provides shared storage, fall back to browser storage, and state which is in effect — verified in a browser by the status note differing between hosted and local copies
- [x] 5.7 State when live shared updates stop while local saves continue — verified by the live-updates error path
- [x] 5.8 Render legibly in light and dark and follow a change of preference while open — verified in a browser by toggling the system colour scheme

## 6. Text briefs (shipped in part)

- [x] 6.1 Write a bounded readable brief and a complete table for each list — verified by `out/watchlist.md` showing 40 rows while `out/watchlist.csv` carries all 363
- [x] 6.2 State the full total where the brief is bounded — verified by the brief's "Addresses below" line naming 363
- [x] 6.3 Omit block outputs when no block qualifies, still writing doorway outputs — verified by the `if blocks:` path
- [x] 6.4 Explain how to act on the block neighbour count before ordering a remedy — verified by the "Read the 'On this block' column" guidance in `out/watchlist.md`
- [x] 6.5 State the recurrence window, the tow-outcome limit and the vehicle-identity limit — verified by the closing notes in `out/watchlist.md`
- [x] 6.6 State that block labels are derived rather than official and retain the census id for joining — verified by the closing notes in `out/blocks.md`
- [x] 6.7 Name the joined datasets and the most recent call in the data — verified by the header lines of `out/watchlist.md`
- [x] 6.8 Document the generated files as not to be hand-edited — verified by the "Outputs, not docs" section of `docs/index.md`

## 7. Scheduled run (shipped)

- [x] 7.1 Run the pipeline on a schedule against the public API with no credentials and commit the result — verified by `.github/workflows/nightly.yml` and its `workflow_dispatch` trigger
- [x] 7.2 Run on the Python standard library with no dependency manifest or install step — verified by `python3 src/hotspots.py` succeeding on a clean Python 3.12

## 8. Correct the timezone conversion (open)

Reference `design.md` D15. `src/hotspots.py:37` applies a constant `-3`, so every record outside daylight saving across the 2020-2026 range is an hour out.

- [ ] 8.1 Replace the fixed `ATLANTIC` offset with daylight-saving-aware conversion for the date of each timestamp, and verify two calls at the same local clock time in July and in January report the same local time
- [ ] 8.2 Re-derive the hour-of-day and channel figures quoted in `docs/parking-hotspots/data-sources.md` and `product.md` under the corrected conversion, and verify the published numbers match the corrected output or are updated to it
- [ ] 8.3 Confirm the corrected conversion does not shift any doorway across the twelve-month boundary or change `last_call` dates, and verify by diffing `out/watchlist.csv` before and after

## 9. Stamp every output with its provenance (open)

Reference `design.md` D16. Nothing records when a run executed, and the scheduled job commits output automatically, so a week-stale board is indistinguishable from a fresh one.

- [ ] 9.1 Record the run execution time and carry it into every output, and verify each of the four briefs and tables plus the board displays it
- [ ] 9.2 Display the run time and the most-recent-call date on the board itself, and verify in a browser that an older committed board is identifiable as old without consulting git
- [ ] 9.3 Replace the template's undated "Regenerated from the live service" assertion with the actual run time, and verify the rendered board shows a date rather than a claim

## 10. Make the recency window's anchor explicit (open)

Reference `design.md` D16. `latest` is derived from the data, so a stalled source slides the twelve-month window backwards silently.

- [ ] 10.1 Anchor the recency window to wall-clock time, or keep the data anchor and state the anchor date in every output, and verify the chosen anchor date appears with the results
- [ ] 10.2 Warn when the most recent call in the data is materially older than the run time, and verify the warning fires against a deliberately stale input
- [ ] 10.3 State in the briefs what "still calling in the last 12 months" is measured from, and verify the wording names the anchor date

## 11. Make the published headline figures reproducible (open)

Reference `design.md` D17. `docs/parking-hotspots/product.md:82` states every number in that folder is produced by `src/hotspots.py`. Two central figures are not: the tow-versus-recurrence comparison and the closure time. Neither appears in any generated output.

- [ ] 11.1 Compute recurrence split by tow status and emit it, and verify the generated output reproduces the 44.7 per cent against 44.6 per cent comparison quoted in `README.md` and `product.md`
- [ ] 11.2 Report the effect size the comparison can rule out, and verify the generated output states a bound rather than only an absence of difference
- [ ] 11.3 State in the generated output that the tow comparison is observational and that tows may cluster at the worst addresses, and verify the caveat travels with the figures
- [ ] 11.4 Compute median elapsed time from `DATE_INITIATED` to `DATE_CLOSED`, excluding and separately counting calls never closed, and verify the generated output reproduces the response-time figure opening `README.md`
- [ ] 11.5 Reconcile the three call denominators in circulation — 9,729 for the DISPATCH label alone, 9,791 including `DRIVEWAY`, and 9,651 in the tow comparison cohort — and verify each published figure names which population it is drawn from

## 12. Close the remaining spec gaps (open)

- [ ] 12.1 Reference `design.md` D14a. Apply the bounded retry used by `query()` to the census fetch, which currently calls `urlopen` directly at `src/hotspots.py:109`, and verify a simulated transient failure during the census fetch recovers rather than aborting the run
- [ ] 12.2 State the address-reduction limit in the generated briefs, not only in `docs/`, and verify `out/watchlist.md` says some doorways may still split into separate rows
- [ ] 12.3 State in the block brief that the per-dwelling rate spans the whole block rather than the street the calls are on, and verify `out/blocks.md` carries it
- [ ] 12.4 Add automated coverage for the pure functions — address reduction, street extraction, point-in-polygon including interior holes, vehicle identity, recurrence counting — and verify the suite passes without network access

## 13. Verification after the corrections

- [ ] 13.1 Run the pipeline end to end and verify every figure quoted in `README.md` and `docs/parking-hotspots/` is reproduced by generated output or removed from those documents
- [ ] 13.2 Verify the board and the briefs from one run agree on every count, including the newly added provenance and comparison figures
- [ ] 13.3 Verify the board opens with no network access and still shows its map, both lists, and its build time
- [ ] 13.4 Review all published output for the interpretation limits in `design.md` D9 and R1, and verify nothing claims an enforcement outcome from `RESOLUTION` or an hour-of-day finding from pooled timestamps

## 14. Track every canonical violation type (open)

Reference `design.md` D18 and D19. The original scope narrowed to blocked driveway only; this section carries the reversal into the pipeline, the board, and the nightly schedule.

- [ ] 14.1 Freeze the canonical violation-type list against a fresh live query of `Alleged Violation`, grouping each current label with its legacy short code and `(DISPATCH)` variant (the pattern D2 documents for driveway), excluding `Other` and `Left Running`, and verify the frozen list's combined call count accounts for the great majority of non-excluded rows in the custom-fields layer
- [ ] 14.2 Parameterize the pipeline runner to batch over the frozen canonical-type list, producing an independent doorway list, block list, brief set and effectiveness evidence per type, and verify no doorway or block from one type appears in another type's output
- [ ] 14.3 Extend the board to embed every tracked type's data with a viewer-facing switcher, scoping ranking, triage state and map markers to the selected type, and verify in a browser that switching types never mixes rows across types
- [ ] 14.4 Measure nightly runtime and request volume against the ArcGIS service across the full canonical-type batch, and verify the scheduled job completes within its window without triggering the source's rate limiting
- [ ] 14.5 Compute recurrence, the tow comparison, and vehicle uniqueness independently for each canonical type, and verify a type whose figures do not resemble driveway's states its own conclusion rather than driveway's
- [ ] 14.6 Check `No Parking Sign` (or any other high-volume type) against R2's string-reduction concern before it ships, and verify whether address collisions at that volume require coordinate keying ahead of the other types
- [ ] 14.7 Update generated-output documentation (brief headers, board labels) to name which canonical type each output covers, and verify a reader can tell which type they are looking at without cross-referencing a filename
