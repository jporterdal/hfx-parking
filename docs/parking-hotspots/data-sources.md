# Data sources

Status: Final.
Date: 2026-09-12.
All counts come from live queries run on 2026-09-12.

## The two datasets the product joins

HRM publishes the call and the outcome of the call in two separate places.
Nobody joins them.
The join is the product.

### 1. Cityworks Service Requests

`https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Cityworks_Service_Requests/FeatureServer/0`

477,343 rows.
One row per call.
Most recent call: 2026-09-05.

Fields used:

| Field | What it gives you |
|-------|-------------------|
| REQUEST_ID | The join key. |
| DATE_INITIATED | When the call came in, with the time. Stored in UTC. Halifax is UTC-3 (ADT) in summer, UTC-4 (AST) the rest of the year — convert with a DST-aware timezone, not a fixed offset. |
| DATE_CLOSED | When the city closed the call. |
| DESCRIPTION | The call type, for example "Illegally Parked Vehicle" or "Trees". |
| ADDRESS | Free text. Carries the city and postal code, so strip them before grouping. |
| COMMUNITY, DISTRICT | Where the call sits. |
| RESOLUTION | How the city closed it. |
| LATITUDE, LONGITUDE | Point, for a map. |
| PRIORITY | 1 to 4. |
| INITIATED_BY | INTERNAL, 311 Online, or Respond. Read this before you trust a timestamp. |
| WORK_ORDER | Y or N. Whether the call put a truck to work. A flag, not a join key. |
| REQUEST_CATEGORY | The owning group, for example PARKING or ROWMAINTENANCE. |
| DEPT_RESPONSIBILITY | The responsible department. |

**A warning about `DATE_INITIATED`.**
It is when the call entered Cityworks, not when the problem happened.
85 per cent of blocked driveway calls arrive on the `INTERNAL` channel, which records 4 calls out of 8,345 between 21:00 and 07:00, Halifax local time, and peaks at 13:00.
The citizen-typed `311 Online` channel spreads across nearly all 24 hours and peaks at 18:00.
Any hour-of-day finding built on the pooled data is measuring office hours.
Split by `INITIATED_BY` before you use the hour.

### 2. Cityworks Service Requests Custom Fields

`https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Cityworks_Service_Requests_Custom_Fields/FeatureServer/0`

1,153,448 rows.
A key and value store.
Each row is one extra field on one call: `REQUESTID`, `CUSTOM_FIELD_NAME`, `CUSTOM_FIELD_VALUE`.

The parking fields are the richest set in the whole table.
Each appears on about 110,500 calls, which is one per illegal parking call.

| Custom field | What it gives you |
|--------------|-------------------|
| Alleged Violation | The real problem. "Blocking Driveway (DISPATCH)" is 9,729 calls. |
| Vehicle Was Towed | Y or N. The outcome. 1,868 Y across all parking. |
| Property Ownership | HRM, PRIVATE, PROVINCE, FEDERAL, OTHER. |
| Vehicle Make, Model, Colour, Province | The vehicle. Counting distinct values per address shows whether one driver repeats. It is not a plate, so two identical cars count as one. |

Alleged Violation values, top five:

| Value | Calls |
|-------|------:|
| No Parking Sign | 27,302 |
| Private Property | 17,029 |
| On Highway Over 24 Hours | 12,187 |
| Blocking Driveway (DISPATCH) | 9,729 |
| Over Time Specified | 4,892 |

The 71 raw labels group into 30 canonical types, each a current label with its `(DISPATCH)` variant and its legacy short code, frozen on 2026-09-16 in `src/mirror/violation_types.py`.
Two legacy codes are left unmapped because neither reduces to one current label: `OVERTIME` (1 call) could be `Over Time at Meter` or a catch-all from before `Over Time Specified` got its own code, and `VIOLATION` (4 calls) names no type at all.
Together they are 5 calls, so the grouping covers 99.995 per cent of calls outside `Other` and `Left Running`.
66 further calls carry the field with no value.

### 3. Census 2021 Dissemination Areas

`https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Census_2021_Dissemination_Areas/FeatureServer/0`

610 polygons covering HRM.
This is the neighbourhood grain, and it is the only one of four candidates that works.

| Field | What it gives you |
|-------|-------------------|
| DAUID | The block id. Joins back to any census table. |
| DATDWELL20 | Total dwellings. The denominator that stops a dense block outranking a worse one. |
| DAPOP2021 | Population. |

The join is a point-in-polygon of each address's median coordinate.
The script does it locally with an even-odd ray cast, because the alternative is one server request per address.
Verified against the service's own `esriSpatialRelIntersects` query on six addresses: 6 of 6 matched.

**Three coarser grains were tested and rejected.**

| Candidate | Why not |
|-----------|---------|
| `COMMUNITY` on the call record | 7,651 of 9,791 driveway calls just say HALIFAX. |
| Community Boundaries (`GSA`), 200 polygons | Same failure. "HALIFAX" holds 3,121 of the 4,255 addresses. |
| Community Plan Areas, 22 polygons | Far too coarse. |
| Street name from the address, 1,039 groups | Works, and readable, but a weaker cut: the top 20 streets hold 25 per cent of recent calls against 35 per cent for the top 20 blocks. Kept as the `street` column and used to name each block. |

## Limits to state on stage

- `ADDRESS` is free text. The same doorway appears with and without a postal code. The product strips everything after the first comma. Some doorways will still split.
- 2,423 illegal parking calls carry no address at all.
- `Vehicle Was Towed` is the only enforcement outcome published. There is no ticketing field anywhere in the 87 distinct custom field names, and `RESOLUTION` has no "ticket issued" value. A call with no tow is a call with no recorded outcome, not proof that nothing was done.
- `DATE_CLOSED` is when the service request closed. For parking that is close to the real end of the job. For trees it is not, because the call closes when a work order opens.
- Timestamps are UTC. Convert to `America/Halifax` local time before you talk about hour of day — Halifax is UTC-4 (AST) for most of the year and UTC-3 (ADT) only in summer, so a fixed offset is wrong outside daylight saving; use a DST-aware conversion (`zoneinfo.ZoneInfo("America/Halifax")`).
- The ArcGIS layers page at 1,000 rows per request. Use `resultOffset`. The census layer pages at 200 when geometry is returned.
- A dissemination area is a census unit, not a neighbourhood anyone in Halifax names. The script labels each block by the two streets its calls come from, which is readable, but the label is derived and not official.
- `DATDWELL20` is total dwellings in the whole block, so calls per 1,000 dwellings is a rate across the block, not along the street the calls are on.

## The date range

The handout says "Service-request data ends December 2024".
That may describe the handed-out export.
The live layer holds 68,857 calls from 2025 and 50,895 from 2026, through 2026-09-05.
Query the service directly.

## Datasets checked and not used

| Dataset | Rows | Why not |
|---------|-----:|---------|
| Public Trees | 80,051 | No condition or risk rating. Carries species, diameter, `WIRES` and `PROTECT_DEV`, which are risk inputs but not a risk score. |
| Beach Water Quality | 2,966 | Last sample 2024-08-28. Two seasons stale. The live status is on the halifax.ca page, not here. |
| PPL&C Issued Public Works ROW Permits | 16,352 | Crane permits take a median of 5 days to issue. 1,282 permits carry no issuance date and every median silently drops them. |
| PPL&C Permit Processing Times | 155,487 | Restricted to `Pre Issuance`, the applicant holds 78.6 per cent of the clock. The all-stage figure of 95 per cent is wrong because it includes the construction period. `Total_Duration` has no documented unit. |
| 311 Call Details | n/a | Call reason only, no service request join key. |
| Cityworks Work Orders | 78,064 tree rows | Carries tree work types and `WORK_ORDER_CAUSE`. Cannot be joined back to a service request, because `WORK_ORDER` on the request is a Y/N flag. |
| Commuter Permit Parking Streets | 325 | A map of where permits apply. No calls, no dates, no outcomes, and no street name. Tested against the watch list and it does not separate a repeat doorway from any other. See below. |

## Commuter Permit Parking Streets, tested 2026-09-12

`https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/Commuter_Permit_Parking_Streets/FeatureServer/0`

The layer answers "where do parking permits apply".
It does not answer "which doorway keeps calling".

What it holds:

- 325 line features, 38.6 km of street in total.
- Fields: `OBJECTID`, `PPID`, `TYPE`, `ADDDATE`, `MODDATE`, `SDATE`, `SOURCE`, `SACC`, `COMMUTER`, `GLOBALID`, `Shape__Length`.
- 280 of 325 are commuter permit streets. 40 are not. 5 carry a null in `TYPE` or `COMMUTER`.
- `SOURCE` is Parking Enforcement on almost every row. Last edit 2026-09-06.
- Paging limit 2,000 rows, so one request returns the whole layer.

Three limits decide the result.

1. **No street name.** `PPID` is an opaque id such as `PP462`. There is no name field, so the layer cannot be joined to a call by address. Geometry is the only join path.
2. **No calls and no outcomes.** There is no date of a complaint, no resolution and no tow. It cannot find a repeat address and it cannot find a request closed without a fix.
3. **Peninsula only.** The extent is 44.628 to 44.672 N, -63.609 to -63.544 W, about 5 km by 5 km. Only 240 of the 363 watch-list doorways fall inside it at all.

**The overlay test.** Each of the 4,255 blocked driveway addresses was measured to the nearest permit street, and the 363 watch-list doorways were compared to the other 3,892.

| Distance | Watch list | Every other driveway address | Ratio |
|----------|-----------:|-----------------------------:|------:|
| 15 m | 8.0% | 7.7% | 1.04x |
| 30 m | 23.4% | 20.1% | 1.16x |
| 50 m | 30.6% | 25.8% | 1.19x |
| 100 m | 42.1% | 35.5% | 1.19x |

The gap that opens at 50 m and 100 m is density, not permits.
The permit streets are all on the peninsula, where addresses sit closer together and call more often.
Repeating the test on the 2,386 driveway addresses inside the layer's own extent removes that:

| Distance | Watch list, n=240 | Every other, n=2,146 | Difference |
|----------|------------------:|---------------------:|-----------:|
| 15 m | 12.1% | 13.9% | -1.8 pts, 0.8 SE |
| 30 m | 35.4% | 36.5% | -1.1 pts, 0.3 SE |
| 50 m | 45.8% | 46.6% | -0.8 pts, 0.2 SE |
| 100 m | 62.9% | 64.0% | -1.1 pts, 0.3 SE |

Inside the area the layer covers, a doorway that still calls is if anything slightly further from a permit street than one that stopped calling years ago, and every difference sits inside the noise.
The median driveway address across the whole municipality is 317 m from the nearest permit street, and the 75th percentile is 2.5 km.

The layer is not in the product and does not change the rank of any row.
