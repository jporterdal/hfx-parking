# Doorways enforcement cannot fix

Claude Community Impact Lab Halifax, 2026-09-12.
Problem 2, The Same Blocked Driveway.

Halifax answers a blocked driveway call in 41 minutes, closes 94 per cent of them as done, and the same doorway calls again two weeks later.

This finds the doorways where enforcement has already been tried and has not worked, and sends them to whoever owns signs, bollards and curb paint.

## Open the board

The board is a served application, not a file. Visit its URL in a browser: no install, no
account, no key. It lists the blocks and the doorways still calling, on a map of Halifax drawn
from HRM's own street network, for one violation type at a time. `/` opens the default type,
Blocking Driveway, and `/types/<slug>` opens another (`/api/types` lists every tracked type and
its slug).

The served application persists triage decisions in the mirror's own database, so anyone who
visits its URL sees the same triage state, and a team works one shared list instead of three.

To run it yourself you need the mirror's Postgres database, configured as described under
[Database configuration](#database-configuration), and a `PORT` (default 8000):

```bash
python3 src/app/server.py                                    # development server
venv/bin/gunicorn wsgi:app --bind 0.0.0.0:$PORT              # what a deployment runs
```

Then open `http://localhost:8000/` (or the `PORT` you set). The mirror is loaded and kept current by
`python3 src/mirror/load.py` and `python3 src/mirror/sync.py`.

## Where a list leaves the application

A list leaves the application through its two export routes, both for one violation type and
both taking the same filters as the page (`district`, `min_calls`, `min_doorways`,
`recur_days`, `recency_days`):

- `GET /api/types/<slug>/export.csv` downloads the doorway and block lists as one CSV: a
  preamble naming the violation type, the counts, the freshness clocks, the filter values in
  effect and the interpretation limits, then a DOORWAYS table and a BLOCKS table.
- `GET /types/<slug>/export` is the same content as a readable page (a brief).

For example, `/api/types/blocking-driveway/export.csv?district=7`. The application does not
write lists to files on the server, and this repository no longer commits generated lists.

## Dependencies

No keys and no install for the viewer: opening the board takes nothing but a browser.

The mirror and the server do have dependencies, listed in `requirements.txt` (this project
previously had no manifest at all): a Postgres database and a Python dependency set (Flask
behind a WSGI server such as gunicorn). `src/hotspots.py` stays stdlib-only, and a viewer still
installs nothing.

## Database configuration

The database is configured with the standard Postgres environment variables, which `psql` and
`pg_isready` read too. The sync, the load, the derivation, the served application and the
tests all connect from these and from nothing else; no connection string, host, user or
password is held in the repository.

| Variable | |
| --- | --- |
| `PGHOST` | required: the server's host, or a unix socket directory |
| `PGUSER` | required: the role to connect as |
| `PGDATABASE` | required: the database to connect to |
| `PGPORT` | optional: defaults to 5432 |
| `PGPASSWORD` | optional: leave it out for passwordless authentication or a `~/.pgpass` entry |

Copy `.env.example` to `.env` at the repository root and fill it in. `.env` is git-ignored and
is read from the repository root whatever directory you run from. A variable already set in
your shell or on the host wins over the file, and a host that sets them needs no file. Install
the loader with `venv/bin/pip install -r requirements.txt`.

If a required variable is missing, or empty, the command stops with an error that names it.
The served application refuses to start rather than failing on its first request. A database
that is configured but does not answer is an error too: nothing falls back to another host.

The tests run with `venv/bin/pytest`. A test marked `db`, which is most of them, **errors**,
and is never skipped, when the database is unconfigured or unreachable, so a run with no
database cannot pass. The tests that need no database still run without any configuration.
The `db` tests create and drop a `mirror_test_<pid>` schema in whichever database
`PGDATABASE` names, and never touch the `mirror` schema, so point them at a database you do
not mind them writing to.

## Lists from the command line

Two scripts still write lists to a directory you name; neither is the product and neither
writes to `out/`:

- `python3 src/mirror/derive.py <dir> --violation Driveway` derives the lists from the mirror.
- `python3 src/hotspots.py --csv <file> --brief <file> --block-csv <file> --block-brief <file>`
  runs the original live-source pipeline against HRM's open data. It is kept as the baseline the
  mirror's derivation is reconciled against (`python3 src/mirror/reconcile_figures.py`), and it
  has no default output paths.

The `out/` directory, which held the last lists and standalone board the retired generator
committed, has been removed: nothing regenerates those files, and they were never the product. The
standalone board and its source, `web/template.html`, were removed too: the served page carries the
same lists, map and triage, and the exports above replace the one thing the file did that a URL
does not, being something you could mail or archive. The last commit that still has the `out/`
files and the template is `b04731c`; read any of them with `git show b04731c:<path>`, for example
`git show b04731c:out/watchlist.csv` or `git show b04731c:web/template.html`. Nothing runs on a schedule in this repository any more:
the nightly workflow that committed to `out/` has been removed, and scheduled work moves to the
deployment that serves the application.

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

**It is a different car every time.** Across the 351 doorways, 2,413 distinct vehicles produced 2,528 calls. That is 95 per cent unique. 28 Queen St has 58 calls and 58 different vehicles, with no vehicle appearing twice.

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
