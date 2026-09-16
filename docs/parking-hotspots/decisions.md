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

## Open for Chris

**Does the demo show the effect test?**
The product cannot yet prove that a visit reduces calls.
Options: say it plainly as the next step, or stub a before and after chart and label it stubbed.
Recommendation: say it plainly. The handout rewards saying what is stubbed, and a stubbed chart invites the question the product cannot answer.

**Who is the named user?**
The product now points at whoever installs signs, bollards and curb paint, which is Public Works Traffic Management rather than parking enforcement.
The handout named enforcement.
Recommendation: say both. Enforcement gets the evidence that these calls are not theirs to win, and Traffic Management gets the list.
