# Doorways enforcement cannot fix

Status: Final.
Date: 2026-09-12; figures below refreshed 2026-09-16 from the local mirror. Regenerate with
`python -m mirror.figures --violation Driveway` (tow comparison, effect bound) and
`python src/mirror/derive.py <dir> --violation Driveway` (vehicle uniqueness). The dropped-window
figures under "What was tested and dropped" are a one-off measurement from 2026-09-12 against code
that no longer exists in this repository and are marked accordingly below.

## The problem in one line

Halifax answers a blocked driveway call in 41 minutes, records no outcome on 95 per cent of them, closes them as done, and the same doorway calls again two weeks later.

## The insight

Enforcement cannot fix these addresses, and the data proves it two ways.

**A tow changes nothing.**

| Group | Calls | Recur within 365 days |
|-------|------:|----------------------:|
| Vehicle was towed | 445 | 44.7% |
| Not towed | 9,249 | 44.7% |

(Calls matching `violation='Driveway'` with a usable address and `DATE_INITIATED`, 9,698 total; 4
calls of unknown tow status excluded above.)

The two round to the same number today.
A cluster bootstrap resampled by doorway (95 per cent interval) rules out a reduction larger than
about 5.1 points. The comparison is observational: tows may cluster at the addresses already calling
the most, which could mask a real effect in either direction.

**It is a different car every time.**

Across the 351 doorways still calling, 2,413 distinct vehicles produced 2,528 calls.
That is 95 per cent unique.
28 Queen St has 58 calls and 58 different vehicles, with no vehicle appearing twice.

There is no repeat offender to deter.
The street produces the violation, not the driver.
A sign, a bollard or a painted curb fixes it once.
An officer can only remove today's car.

## The product

A served application that reads a local mirror of HRM open data, kept current by a scheduled sync, and shows a ranked list of the doorways where enforcement has already been tried and has not worked.

Each row carries the calls still arriving, the calls all time, the tows, and how many distinct vehicles were involved.

It goes to whoever owns signs, bollards and curb paint.
It does not go to an officer's shift plan.

## What was tested and dropped

Historical, 2026-09-12. The comparison code behind the four bullets below (the matched-null baseline
and the per-address tuning) no longer exists in this repository; these figures are not reproduced by
current code and are recorded here as a dated finding, not a reproducible one.

An earlier version ranked each doorway by the four-hour window holding most of its calls, against a 17 per cent baseline.
That is wrong and it is worth saying why, because most teams will build it.

- 4 divided by 24 is not the right null. Calls are not spread evenly over the day.
- 85 per cent of these calls arrive on the `INTERNAL` channel, which records 4 calls out of 8,345 between 21:00 and 07:00, Halifax local time, and peaks at 13:00. The citizen-typed `311 Online` channel spreads across nearly all 24 hours and peaks at 18:00. The timestamp is when staff keyed the call, not when the driveway was blocked. (This bullet is current — regenerate with `scripts/hour_of_day_figures.py`.)
- Against a matched null, the observed 58.9 per cent sits against a null mean of 48.8 per cent, and only 17 of 58 addresses beat their own 95th percentile.
- Out of sample, a per-address tuned window scores 47.5 per cent against 41.7 per cent for one city-wide window. Weekday tuning actually loses to a city-wide block, 35.7 against 37.9.

The window is mostly the office clock. It is not in the product.

## Run it

The lists are served by the application (`python3 src/app/server.py`, see `README.md`) and leave it
through its two export routes. From the command line, `python3 src/mirror/derive.py <dir> --violation
Driveway` derives them from the mirror into a directory you name.

`src/hotspots.py` is the original live-source pipeline, kept as the baseline the mirror's derivation
is reconciled against. It takes its four output paths on the command line and has no default:

```bash
python3 src/hotspots.py --violation Driveway --csv <file> --brief <file> --block-csv <file> --block-brief <file>
```

It needs no keys and no install. `--violation` takes any alleged-violation substring.
`--district` limits the brief to one district.

It no longer writes `out/watchlist.csv` and `out/watchlist.md`. The `out/` directory was removed
(last present at `b04731c`, readable with `git show b04731c:out/watchlist.csv`), and the nightly
workflow that committed to it was removed under task 1.1.

## Why it clears the bar

1. **Runs without a person.** The mirror is loaded and kept current from a public API (`src/mirror/sync.py`) and the application reads it. The GitHub Actions job that once committed lists to `out/` on a schedule was removed under task 1.1.
2. **Joins data nobody has joined.** The call is in one layer. The violation type, the tow outcome and the vehicle are in a second layer, a key and value table of 1.1 million rows. HRM publishes no join between them.
3. **Sits inside someone's workday.** It is a work list for the team that installs signs, not a dashboard someone must remember to open.

## What is real and what is stubbed

Real:

- The join, the repeat detection, the tow comparison, the vehicle count, the recency filter, and both outputs. All run against the local mirror of HRM open data (`src/mirror/derive.py`); `src/hotspots.py` still runs the same analysis live against HRM as the reference the mirror's derivation is reconciled against.
- The mirror sync (`src/mirror/sync.py`) and the served application that reads it. The scheduled GitHub Actions run that committed lists to `out/` was removed under task 1.1.
- Every number in this folder is produced by `src/mirror/figures.py` and `src/mirror/derive.py` against the mirror, except the dated one-off measurement under "What was tested and dropped".

Stubbed:

- No delivery. The brief is a page and a CSV the application exports on request, not a file in the repo. It should be an email or a Teams post.
- No effect measurement. The product cannot yet show that a bollard reduced the calls.
- Vehicle data is missing on some calls. One listed address records a vehicle on 1 of its 11 calls, so its distinct count is meaningless. Read `vehicles_seen` before `vehicles_distinct`.
- No recommendation per address. It says "enforcement will not fix this one". It does not say whether the fix is a sign, a bollard or paint. That needs a site visit or a street view read.
- Address matching is a string strip, not a geocode. Some doorways will split into two rows.
- Vehicle identity is make, model and colour, not a plate. Two identical cars count as one, so the distinct count is a floor.

## Limits to state on stage

HRM publishes no ticketing field.
`Vehicle Was Towed` is the only enforcement outcome in the data, and `RESOLUTION` carries no "ticket issued" value.
A call with no tow is a call with no recorded outcome.
It is not proof that nothing was done.

The tow comparison is observational.
Tows may cluster at the worst addresses, which would hide a real effect.

## Next steps

1. Run the same job for the other violation types. "No Parking Sign" is 27,404 calls and is bigger than blocked driveways.
2. Geocode with LATITUDE and LONGITUDE instead of address strings.
3. Measure the effect. Pick 20 addresses, install a physical fix, and compare their call rate to 20 matched addresses left alone.
