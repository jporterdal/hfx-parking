# The five problems: what the data says

Status: Final.
Date: 2026-09-12.
Scope: Claude Community Impact Lab Halifax, Sep 12 2026.
Method: live queries against HRM open data and HRM primary documents.
Every number below comes from a query run on 2026-09-12.
No number here is an estimate.

## The answer

Pick problem 2, The Same Blocked Driveway.

It is the only one of the five where all four judging criteria hold up against the data.
Three of the other four rest on a premise the data contradicts.

## How each problem was tested

The organisers list a data source under each problem.
Each source was opened and queried.
The test was simple: does a live, machine readable source exist today, and does it show the thing the problem statement claims?

HRM publishes 353 datasets through the Halifax Data, Mapping and Analytics Hub.
The catalogue is at `https://data-hrm.hub.arcgis.com`.
The machine readable index is at `https://data-hrm.hub.arcgis.com/api/feed/dcat-us/1.1.json`.

## An advantage that applies to everyone in the room

The handout says "Service-request data ends December 2024, say so if you use it."

That may describe a snapshot the organisers exported.
It does not describe the live service.
The Cityworks Service Requests layer holds 477,343 calls.
It holds 68,857 calls from 2025 and 50,895 calls from 2026.
The most recent call in the layer was made on 2026-09-05.

The layer is at:
`https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Cityworks_Service_Requests/FeatureServer/0`

Calls per year:

| Year | Calls |
|------|------:|
| 2017 | 12,482 |
| 2018 | 32,364 |
| 2019 | 36,558 |
| 2020 | 41,685 |
| 2021 | 54,955 |
| 2022 | 60,363 |
| 2023 | 56,490 |
| 2024 | 62,689 |
| 2025 | 68,857 |
| 2026 | 50,895 |

A team that cuts at December 2024 loses 119,752 calls, including last week.
Querying the service directly gets them back.
Both things can be true: the handed-out export may stop in December 2024 while the live layer does not.
Say it that way, not as a claim that the organisers are wrong.
This applies to problems 1, 2 and 3.

## Problem 1: Reporting What's Wrong

The claim: "Only one in five reports reaches the city online."

The data: the service request layer carries an `INITIATED_BY` field.

| Channel | Calls | Share |
|---------|------:|------:|
| INTERNAL | 419,742 | 87.9% |
| 311 Online | 41,992 | 8.8% |
| Respond | 15,609 | 3.3% |

Pooled over ten years the digital share is 12.1 per cent.
Do not use that to contradict the handout, because the share is moving fast:

| Year | Calls | Digital | Digital share |
|------|------:|--------:|--------------:|
| 2023 | 56,490 | 2,009 | 3.6% |
| 2024 | 62,689 | 5,956 | 9.5% |
| 2025 | 68,857 | 9,838 | 14.3% |
| 2026 | 50,895 | 24,761 | 48.7% |

A new channel called Respond launched in 2026 and carries 15,537 calls.
"One in five" sits between the 2025 and 2026 values.
It is close to right for 2025 and low for 2026.

Verdict: real problem, weak product, and the reason is not the number.
To say who never reports, you must join call counts to population by census area.
That is a second source and a report, not a tool that sits in a workday.
HRM has already published this shape of analysis as Tree Equity Score by DA.
A team here would be re-doing municipal work with less data.

## Problem 2: The Same Blocked Driveway

The claim: parking is the biggest thing Halifax complains about, the same spots come back every week, and requests get closed without a fix.

Every part of that is true, and the data proves it.

**Parking is the biggest category.**

| Category | Calls |
|----------|------:|
| PARKING | 121,633 |
| ROWMAINTENANCE | 101,477 |
| ROWSERVICES | 61,172 |
| TRAFFICMAINTENANCE | 61,115 |
| FACILITYMAINTENANCE | 39,191 |

Inside PARKING, 110,579 calls are "Illegally Parked Vehicle".

**The violation type is published, and it names the exact problem.**

A second dataset, Cityworks Service Requests Custom Fields, holds 1,153,448 rows keyed to request id.
For every parking call it carries the alleged violation, whether the vehicle was towed, and who owns the property.
HRM publishes the call and its violation type in two separate layers with no published join.
Joining them is the whole product.

| Alleged violation | Calls |
|-------------------|------:|
| No Parking Sign | 27,302 |
| Private Property | 17,029 |
| On Highway Over 24 Hours | 12,187 |
| Blocking Driveway (DISPATCH) | 9,729 |
| Over Time Specified | 4,892 |

**The same doorways call again and again.**

Across the 110,579 illegal parking calls, of which 108,156 carry an address:

- 26,871 distinct addresses.
- 66.4 per cent of calls come from an address that calls again within a year.
- 2,098 addresses make 51.6 per cent of all calls.
- 198 addresses make 19.4 per cent of all calls.

Narrowed to blocked driveways, 9,791 calls from 2020 to 2026.
HRM files this problem under two labels, "Blocking Driveway (DISPATCH)" with 9,729 calls and "DRIVEWAY" with 62.
Match the label as a substring or you lose 62 calls.

- 9,655 of the 9,791 calls carry an address, across 4,255 distinct addresses.
- 113 addresses make 22.6 per cent of the calls that have an address.
- 44.6 per cent of those calls are followed by another call at the same doorway within a year.

**The city answers fast and nothing changes.**

- Median time from call to closed: 41 minutes. 88 per cent close within two hours.
- Vehicles towed: 454 of 9,791, or 4.6 per cent.
- Closed as "Requested Service Provided": 94.1 per cent.
That is the insight.
Halifax is not slow.
Halifax is fast, records the job as done, and the same driveway calls again 8 to 26 days later.

**It is a city problem, not a private lot problem.**

Of the blocked driveway calls, 7,496 are on HRM property and 467 are on private property.
1,780 carry no ownership value.
Of the calls where ownership is recorded, 94 per cent are on HRM property.

**A tow does not change anything, and it is a different car every time.**

This is the part that decides what to build.

| Group | Calls | Recur within 365 days |
|-------|------:|----------------------:|
| Vehicle was towed | 445 | 44.7% |
| Not towed | 9,206 | 44.6% |

The difference is 0.1 points.
Towing a vehicle does not lower the chance that the same doorway calls again.

The reason is in the vehicle fields.
Across the 363 doorways still calling, 2,473 distinct vehicles produced 2,588 calls.
That is 96 per cent unique.
28 Queen St has 58 calls and 58 different vehicles, with no vehicle appearing twice.

There is no repeat offender to deter.
The street produces the violation, not the driver.
Enforcement can only ever remove today's car.

**A time-of-day window does not survive testing.**

An earlier version of this work ranked each doorway by the four-hour window holding most of its calls, against a 17 per cent baseline.
That baseline is wrong and the finding does not hold.

Calls are not spread evenly over 24 hours, so 4 divided by 24 is not the right null.
85 per cent of these calls arrive on the `INTERNAL` channel, which records 4 calls out of 8,345 between 21:00 and 07:00, Halifax local time, and peaks at 13:00.
The citizen-typed `311 Online` channel spreads across nearly all 24 hours and peaks at 18:00.
So the timestamp is the hour a staff member keyed the call, not the hour the driveway was blocked.

Tested against a null drawn from the pooled hour distribution, with the same best-of-24 maximum:

| Measure | Value |
|---------|------:|
| Observed mean best-window share | 58.9% |
| Matched null mean | 48.8% |
| Uniform 4-in-24 null | 17% |
| Addresses beating their own 95th percentile | 17 of 58 |

Out of sample, learning each window on an address's first half of calls and scoring on the second half:

| Method | Out-of-sample share |
|--------|--------------------:|
| Per-address tuned window | 47.5% |
| One city-wide 11:00 to 15:00 window | 41.7% |

Weekday is worse.
Per-address weekday tuning scores 35.7 per cent out of sample against 37.9 per cent for a single city-wide two-day block.
Tuning loses.

Do not claim a per-address time window.

1325 Hollis St has called 68 times and no vehicle has ever been towed.
28 Queen St has called 58 times and no vehicle has ever been towed.

One limit to state.
HRM publishes no ticketing field.
`Vehicle Was Towed` is the only enforcement outcome in the data, and `RESOLUTION` has no "ticket issued" value.
A call with no tow is a call with no recorded outcome.
It is not proof that nothing was done.

**It is live.**

The most recent blocked driveway calls in the data:

| Time | Address | Resolution |
|------|---------|------------|
| 2026-09-04 19:18 | 14 Cavalier Dr, Lower Sackville | Requested Service Provided |
| 2026-09-04 18:11 | 1323 Birmingham St, Halifax | Requested Service Provided |
| 2026-09-04 17:01 | 1646 Henry St, Halifax | Requested Service Provided |
| 2026-09-04 10:40 | 1664 Henry St, Halifax | Requested Service Provided |

1323 Birmingham St and 1664 Henry St are both on the top 20 repeat list.
They called again last week.

Verdict: pick this one.

## Problem 3: Which Tree Falls First

The claim: 290 tree requests are waiting right now, and every one needs a site visit before anyone knows if it is a branch or a hazard.

The waiting count is right.
There are 34,860 tree requests in the layer.
292 are open and 9 are on hold.
The handout's "two hundred and ninety are waiting" matches the live data.

A ranking target does exist, and an earlier version of this work missed it.
The service request layer carries a `WORK_ORDER` flag.
Across the 34,860 tree requests it is Y on 18,439 and N on 16,421.
"Did this call put a truck to work" is a balanced label on 34,860 examples.
`Cityworks Work Orders` is also published and carries tree work types such as Tree - Removal and Tree - Pruning and Trimming, with a `WORK_ORDER_CAUSE` field.
`Public Trees` adds `WIRES`, `DBH`, `LOCGEN` and `PROTECT_DEV`, which are standard tree-risk inputs.

So the problem is buildable. Reject it for the real reasons instead.

- There is no free text and no photo on a request. The handout asks for "photo, description, history". The first two do not exist in the open data, so the model can use only address, date, priority and history.
- `WORK_ORDER` is a Y or N flag, not a join key. You cannot join a request to its work order, so you cannot measure the real wait or the real severity.
- The label is "work happened", not "it was dangerous". A cosmetic pruning job also scores Y. Ranking on it ranks by what the city chose to do, which is the thing the problem says is broken.

One tempting finding, and why it should not be used:
priority 1 tree calls close in a median of 11 days while priority 2 calls close in 1 day.
That looks like the urgent queue being the slowest.
It is not safe to say.
20,476 of 34,860 tree calls close with "Investigated, Work Order Opened or Linked", which means the call closes when a work order opens, not when the tree is dealt with.

Verdict: buildable, but the label you would train on is the decision you are trying to improve.
This is the strongest of the four alternatives and the one a good rival team could make work.

## Problem 4: Is the Beach Open

The claim: "Halifax tests every supervised beach each morning ... and posts the result on one webpage nobody checks."

HRM's own 2026 Beach Water Quality Monitoring Protocol shows the claim merges two different checks.

- Bacteria sampling is weekly. "Five samples are collected weekly at each beach."
- The daily morning check is a visual look for blue-green algae by staff. It is an observation, not a test, and it produces no number.
- Lab time is 24 hours for E. coli and 48 hours for enterococci. A sample dropped Monday afternoon reports by noon Wednesday.
- When a result exceeds the limit, the protocol already fires a notification chain: lifeguards told, signage placed, a public service announcement issued, and the province notified.

The daily status the handout points at does exist.
The live page at halifax.ca carries a table of 20 beaches with columns for Water Sample Results and Beach Status, with values Open, Risk Advisory in Effect, and Closed.
The page states: "Beach status in 2026 is updated weekdays by 8 a.m. and by 9 a.m. on weekends (between July 1 and August 31)."
So a watcher is buildable, and an earlier version of this work was wrong to say the daily number does not exist.

Two things kill it today instead.

First, the season is over.
Supervision ran July 1 to August 31.
Today is 2026-09-12.
Every row on the page now reads "Supervision ended for the season" and every sample result reads N/A.
A change detector has nothing to detect, and a three minute demo has nothing to show.

Second, the machine readable dataset is two seasons stale.
Beach Water Quality holds 2,966 rows and the last sample is dated 2024-08-28.
The 2025 season results exist only as a table in the back of a PDF, published a year later.
That season had 28 closures, most lasting 2 to 5 days.

Verdict: right product, wrong month.
Build it in June, not in September.

## Problem 5: The Crane Is Waiting

The claim: builders wait months for a right of way permit and a scheduled closure.

The permit data does not show months.
16,352 right of way permits, 2021 to 2026.

Submission to permit issued:

| Work type | Permits | Median | p90 | Max |
|-----------|--------:|-------:|----:|----:|
| Capital Project | 4,300 | 8 d | 19 d | 337 d |
| Utility Work | 3,465 | 7 d | 30 d | 630 d |
| Annual License Work | 2,462 | 0 d | 2 d | 43 d |
| Special Move | 1,915 | 0 d | 3 d | 133 d |
| Street Closure | 1,281 | 5 d | 18 d | 631 d |
| All | 15,069 | 4 d | 19 d | 1,065 d |

Special Move is not the crane category, and an earlier version of this work labelled it wrongly.
Searching `Work_Description` for CRANE returns 113 permits: 91 Street Closure, 11 Temporary Work, 9 Special Move, 2 Utility Work.
The real crane numbers are a median of 5 days to issue, p90 17 days, and a median of 12 days from submission to the road closure starting, p90 50 days and max 206 days.
14 of the 113 were never issued.

The delay does not hide after issuance either.
Submission to the road closure starting: median 11 days, p90 47 days.

HRM also publishes PPL&C Permit Processing Times, which splits each permit's clock into staff time and customer time.
Restricted to the `Pre Issuance` stage, which is the only stage that measures waiting for a permit, customer duration is 3,360,858 units and staff duration is 917,127.
That is 78.6 per cent of the pre-issuance clock on the applicant.
Do not use the all-stage figure of 95 per cent: it includes `Post Issuance`, which is the construction period itself.
The unit of `Total_Duration` is not documented in the layer metadata, so quote the ratio and not the absolute time.

Two more cautions.
The handout says "sixteen thousand right-of-way permits with closure dates". Only 5,884 of the 16,352 carry a road closure start date.
And the layer is named Issued: 1,282 permits carry no issuance date at all, of which 1,109 are Withdrawn and 63 Cancelled. Every median above silently drops them, and a "waiting months" complaint partly lives in the applications that never came out.

Verdict: builders' experience of waiting months is real, but the months are not in this dataset.
A team that builds the requested dashboard will show 5 to 12 days at the median and contradict its own pitch on stage.

One fair counter, and it is the reason this is the second strongest alternative.
The handout asks for a queue view and a jam finder, not for the premise to be true.
The p90 of 50 days to a crane closure, and the 12 per cent of crane permits never issued, is exactly "where it jams".
A team willing to lead with "the median is 5 days and the tail is the story" has a real build here.

## Scoreboard against the four criteria

| | Day one | Product | Idea | Problem clear |
|---|---|---|---|---|
| 1 Reporting | Needs a census join | A report, not a tool | Real, and already city work | Yes |
| 2 Driveway | Live to 2026-09-05 | Scheduled job, no person | Enforcement provably cannot win here | Yes |
| 3 Trees | Label exists, inputs do not | Model on address and date only | Real, but you train on the broken decision | Yes |
| 4 Beach | Season closed 2026-08-31 | Page watcher, nothing to watch | Right product, wrong month | Yes |
| 5 Crane | Live | Dashboard | Median 5 days, the tail is the story | Yes |

Problem 3 is the strongest rival and problem 5 is the second.
Neither is a technicality kill.
Both are buildable and both would have to be pitched against their own premise.

## Why this survives another team picking problem 2

Four teams may take the same problem.
The problem statement is not the edge.
Three things are, and none of them is a time window.

1. The join. Almost everyone will use Cityworks Service Requests alone. The violation type, the tow outcome and the vehicle live in a separate table, Cityworks Service Requests Custom Fields. Without that join you cannot say "blocked driveway", you cannot say "no tow", and you cannot count vehicles.
2. The null result. A tow does not lower the chance of a repeat: 44.7 per cent against 44.6 per cent. Most teams will assume more enforcement is the answer and recommend it. Showing that the data rules out any effect larger than about 5 points is the finding, and it is what points the work at a physical fix instead.
3. The vehicle count. Across the 363 live doorways, 2,473 distinct vehicles produced 2,588 calls. 28 Queen St has 58 calls and 58 different vehicles. That single line turns "repeat offender" into "repeat location" and tells the room why 41 minutes has never fixed it.

One more thing to say, and it is free.
The handout says service request data ends December 2024.
Show a call from 2026-09-04 at 1323 Birmingham St, which is already on the list.

## Sources

All queried on 2026-09-12.

- HRM open data catalogue: `https://data-hrm.hub.arcgis.com`
- DCAT index: `https://data-hrm.hub.arcgis.com/api/feed/dcat-us/1.1.json`
- Cityworks Service Requests: `https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Cityworks_Service_Requests/FeatureServer/0`
- Cityworks Service Requests Custom Fields: `https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Cityworks_Service_Requests_Custom_Fields/FeatureServer/0`
- Public Trees: `https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Public_Trees/FeatureServer/0`
- Beach Water Quality: `https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Beach_Water_Quality/FeatureServer/1`
- PPL&C Issued Public Works ROW Permits: `https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/PPLC_Issued_Public_Works_ROW_Permits/FeatureServer/0`
- PPL&C Permit Processing Times: `https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/PPLC_Permit_Processing_Times/FeatureServer/0`
- Halifax Beach Water Quality Monitoring Protocol, Summer 2026: `https://www.halifax.ca/sites/default/files/documents/about-the-city/energy-environment/finalhalifaxbeachwaterqualitymonitoringprotocol2026.pdf`
