# Doorways enforcement cannot fix

Status: Final.
Date: 2026-09-12.

## The problem in one line

Halifax answers a blocked driveway call in 41 minutes, records no outcome on 95 per cent of them, closes them as done, and the same doorway calls again two weeks later.

## The insight

Enforcement cannot fix these addresses, and the data proves it two ways.

**A tow changes nothing.**

| Group | Calls | Recur within 365 days |
|-------|------:|----------------------:|
| Vehicle was towed | 445 | 44.7% |
| Not towed | 9,206 | 44.6% |

The difference is 0.1 points.
The data rules out any effect larger than about 5 points.

**It is a different car every time.**

Across the 363 doorways still calling, 2,473 distinct vehicles produced 2,588 calls.
That is 96 per cent unique.
28 Queen St has 58 calls and 58 different vehicles, with no vehicle appearing twice.

There is no repeat offender to deter.
The street produces the violation, not the driver.
A sign, a bollard or a painted curb fixes it once.
An officer can only remove today's car.

## The product

A scheduled job that reads HRM open data and writes a ranked list of the doorways where enforcement has already been tried and has not worked.

Each row carries the calls still arriving, the calls all time, the tows, and how many distinct vehicles were involved.

It goes to whoever owns signs, bollards and curb paint.
It does not go to an officer's shift plan.

## What was tested and dropped

An earlier version ranked each doorway by the four-hour window holding most of its calls, against a 17 per cent baseline.
That is wrong and it is worth saying why, because most teams will build it.

- 4 divided by 24 is not the right null. Calls are not spread evenly over the day.
- 85 per cent of these calls arrive on the `INTERNAL` channel, which records 4 calls out of 8,345 between 21:00 and 07:00, Halifax local time, and peaks at 13:00. The citizen-typed `311 Online` channel spreads across nearly all 24 hours and peaks at 18:00. The timestamp is when staff keyed the call, not when the driveway was blocked.
- Against a matched null, the observed 58.9 per cent sits against a null mean of 48.8 per cent, and only 17 of 58 addresses beat their own 95th percentile.
- Out of sample, a per-address tuned window scores 47.5 per cent against 41.7 per cent for one city-wide window. Weekday tuning actually loses to a city-wide block, 35.7 against 37.9.

The window is mostly the office clock. It is not in the product.

## Run it

```bash
python3 src/hotspots.py
```

No keys and no install.
`--violation` takes any alleged-violation substring.
`--district` limits the brief to one district.

Outputs `out/watchlist.csv` and `out/watchlist.md`.

`.github/workflows/nightly.yml` runs it on a schedule at 05:30 Atlantic and commits the result, so no person has to run it.

## Why it clears the bar

1. **Runs without a person.** A scheduled GitHub Actions job against a public API.
2. **Joins data nobody has joined.** The call is in one layer. The violation type, the tow outcome and the vehicle are in a second layer, a key and value table of 1.1 million rows. HRM publishes no join between them.
3. **Sits inside someone's workday.** It is a work list for the team that installs signs, not a dashboard someone must remember to open.

## What is real and what is stubbed

Real:

- The join, the repeat detection, the tow comparison, the vehicle count, the recency filter, and both outputs. All run live against HRM open data.
- The scheduled run. The workflow file is committed but has not yet run on a schedule in this repo.
- Every number in this folder is produced by `src/hotspots.py`.

Stubbed:

- No delivery. The brief is a file in the repo. It should be an email or a Teams post.
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

1. Run the same job for the other violation types. "No Parking Sign" is 27,302 calls and is bigger than blocked driveways.
2. Geocode with LATITUDE and LONGITUDE instead of address strings.
3. Measure the effect. Pick 20 addresses, install a physical fix, and compare their call rate to 20 matched addresses left alone.
