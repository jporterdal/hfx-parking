# Doorways enforcement cannot fix

Claude Community Impact Lab Halifax, 2026-09-12.
Problem 2, The Same Blocked Driveway.

Halifax answers a blocked driveway call in 41 minutes, closes 94 per cent of them as done, and the same doorway calls again two weeks later.

This finds the doorways where enforcement has already been tried and has not worked, and sends them to whoever owns signs, bollards and curb paint.

## Open the board

`out/triage-board.html` is the whole thing as one file. Double-click it. No server, no install,
no account. It carries the 71 blocks, the 363 doorways, and a zoomable map of Halifax drawn from
HRM's own street network, and it remembers your triage decisions in your browser.

The served application persists those decisions in the mirror's own database, so anyone who
visits its URL sees the same triage state, and a team works one shared list instead of three.

## Run it

```bash
python3 src/hotspots.py
```

No keys and no install for the viewer: opening the board takes nothing but a browser. The
pipeline that builds it reads HRM open data over HTTPS and writes every output: the doorway
list, the block list, and the standalone board.

The mirror and the server that will replace this pipeline do have dependencies, listed in `requirements.txt` (this project
previously had no manifest at all): a Postgres database and a Python dependency set (Flask
behind a WSGI server such as gunicorn). `src/hotspots.py` stays stdlib-only, and a viewer still
installs nothing.

| Output | What it is |
|--------|------------|
| `out/triage-board.html` | The board. One file, opens in any browser. |
| `out/watchlist.csv` / `.md` | The 363 doorways still calling. |
| `out/blocks.csv` / `.md` | The 71 blocks where two or more doorways are still calling. |

```bash
python3 src/hotspots.py --violation "No Parking Sign" --district 7
```

Nothing runs it on a schedule any more: the nightly workflow that committed to `out/` has been
removed, and scheduled work moves to the deployment that serves the application.

## What it found

*Figures as of 2026-09-16, read from the local mirror (most recent synced call 2026-09-12 local).
Regenerate with `python -m mirror.figures --violation Driveway` (recurrence, effect bound, response
time, call counts) and `python src/mirror/derive.py <dir> --violation Driveway` (doorway and block
lists); the "Requested Service Provided" share and the `COMMUNITY` count below are counted directly
from `mirror.derive.load(conn, violation="Driveway")`'s `RESOLUTION`/`COMMUNITY` fields, which carry
no dedicated CLI flag.*

- 9,834 blocked driveway calls, 2020 through 2026-09-12, the most recent call in the mirror.
- 4.6 per cent ended in a tow (445 of the 9,698 calls with a usable address and date).
- 94.1 per cent were closed as "Requested Service Provided" (9,256 of the 9,834).
- 44.6 per cent were followed by another call at the same doorway within a year (4,329 of the 9,698 calls with a usable address and date).
- 351 doorways are still calling right now.
- 70 blocks hold 273 of them, so 78 per cent are not an isolated doorway.

**A tow does not change anything.** Towed calls recur at 44.7 per cent. Not-towed calls recur at 44.7 per cent too — on current data the two round to the same number. A cluster bootstrap resampled by doorway (95 per cent interval) rules out a reduction larger than about 5.1 points. The comparison is observational: tows may cluster at the addresses already calling the most, which could mask a real effect in either direction.

**It is a different car every time.** Across the 351 doorways, 2,413 distinct vehicles produced 2,528 calls. That is 96 per cent unique. 28 Queen St has 58 calls and 58 different vehicles, with no vehicle appearing twice.

There is no repeat offender to deter. The street produces the violation, not the driver.

## Blocks, not doorways

Grouping by address alone hid the bigger problem. 57 King St and 19 Portland St were rows 3
and 5 of the doorway list. They are the same census block.

The worst block has **15 doorways still calling and 72 calls in 12 months across 609 dwellings**.
The worst single doorway had 27. Fifteen signs is the wrong answer to one block.

Three coarser groupings were tried and thrown out. The `COMMUNITY` field on the call record is
useless: 7,683 of 9,834 driveway calls just say HALIFAX (counted from `mirror.derive.load`, current
mirror). HRM's Community Boundaries layer fails the same way (a one-off live check from 2026-09-12;
that layer is not part of the mirror, and the comparison is not reproduced by current code). Community
Plan Areas has 22 polygons for the whole municipality. Census 2021 Dissemination Areas is the grain
that works, and it carries a dwelling count, so a block's load can be a rate instead of a raw total.

## What we tested and threw away

A per-address four-hour patrol window. It does not survive.

`DATE_INITIATED` is when a staff member keyed the call, not when the driveway was blocked. The `INTERNAL` channel carries 85 per cent of these calls and records 4 out of 8,345 between 21:00 and 07:00, Halifax local time (regenerate with `scripts/hour_of_day_figures.py`). Out of sample, a tuned per-address window beat one city-wide window by only 5.8 points, and weekday tuning lost outright — a one-off measurement from 2026-09-12 against a matched-null baseline; that comparison code no longer exists in this repository and the figure is not reproduced by current code.

Most teams on this problem will build that window. It is the office clock.

## Read the work

Start at [docs/index.md](docs/index.md).

- [Why this problem and not the other four](docs/problem-selection/five-problems-research.md)
- [What the product is, real against stubbed](docs/parking-hotspots/product.md)
- [The datasets and their limits](docs/parking-hotspots/data-sources.md)

## One note for the room

The handout says "Service-request data ends December 2024".
That may describe the handed-out export.
The live ArcGIS layer holds 68,861 calls from 2025 and 52,006 from 2026, through 2026-09-12.
Query the service directly and you get 120,867 more calls.
(Counted directly against the mirror's `service_requests` table, current as of the 2026-09-16 sync.)
