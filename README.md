# Doorways enforcement cannot fix

Claude Community Impact Lab Halifax, 2026-09-12.
Problem 2, The Same Blocked Driveway.

Halifax answers a blocked driveway call in 41 minutes, closes 94 per cent of them as done, and the same doorway calls again two weeks later.

This finds the doorways where enforcement has already been tried and has not worked, and sends them to whoever owns signs, bollards and curb paint.

## Open the board

`out/triage-board.html` is the whole thing as one file. Double-click it. No server, no install,
no account. It carries the 71 blocks, the 363 doorways, and a zoomable map of Halifax drawn from
HRM's own street network, and it remembers your triage decisions in your browser.

The hosted copy shares those decisions with everyone who opens it, so a team triages one list
instead of three.

## Run it

```bash
python3 src/hotspots.py
```

No keys and no install — for this pipeline and for anyone opening the board.
It reads HRM open data over HTTPS and writes every output: the doorway list, the block list, and
the standalone board.

The mirror and the server that will replace this pipeline do have dependencies, listed in
`requirements.txt` (this project previously had no manifest at all). `src/hotspots.py` stays
stdlib-only, and a viewer still installs nothing.

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

- 9,791 blocked driveway calls, 2020 to 2026-09-04.
- 4.6 per cent ended in a tow.
- 94.1 per cent were closed as "Requested Service Provided".
- 44.6 per cent were followed by another call at the same doorway within a year.
- 363 doorways are still calling right now.
- 71 blocks hold 283 of them, so 78 per cent are not an isolated doorway.

**A tow does not change anything.** Towed calls recur at 44.7 per cent. Not-towed calls recur at 44.6 per cent. The difference is 0.1 points.

**It is a different car every time.** Across the 363 doorways, 2,473 distinct vehicles produced 2,588 calls. That is 96 per cent unique. 28 Queen St has 58 calls and 58 different vehicles, with no vehicle appearing twice.

There is no repeat offender to deter. The street produces the violation, not the driver.

## Blocks, not doorways

Grouping by address alone hid the bigger problem. 28 Queen St and 70 Ochterloney St were rows 1
and 3 of the doorway list. They are the same census block.

The worst block has **14 doorways still calling and 69 calls in 12 months across 609 dwellings**.
The worst single doorway had 26. Fourteen signs is the wrong answer to one block.

Three coarser groupings were tried and thrown out. The `COMMUNITY` field on the call record is
useless: 7,651 of 9,791 driveway calls just say HALIFAX. HRM's Community Boundaries layer fails the
same way. Community Plan Areas has 22 polygons for the whole municipality. Census 2021
Dissemination Areas is the grain that works, and it carries a dwelling count, so a block's load can
be a rate instead of a raw total.

## What we tested and threw away

A per-address four-hour patrol window. It does not survive.

`DATE_INITIATED` is when a staff member keyed the call, not when the driveway was blocked. The `INTERNAL` channel carries 85 per cent of these calls and records 4 out of 8,345 between 21:00 and 07:00, Halifax local time. Out of sample, a tuned per-address window beats one city-wide window by only 5.8 points, and weekday tuning loses outright.

Most teams on this problem will build that window. It is the office clock.

## Read the work

Start at [docs/index.md](docs/index.md).

- [Why this problem and not the other four](docs/problem-selection/five-problems-research.md)
- [What the product is, real against stubbed](docs/parking-hotspots/product.md)
- [The datasets and their limits](docs/parking-hotspots/data-sources.md)

## One note for the room

The handout says "Service-request data ends December 2024".
That may describe the handed-out export.
The live ArcGIS layer holds 68,857 calls from 2025 and 50,895 from 2026, through 2026-09-05.
Query the service directly and you get 119,752 more calls.
