"""Expose the recency window as a parameter, without editing `hotspots.py` or
`mirror.derive` -- task 5.5b.

`design.md` M11 names the defect precisely: `src/hotspots.py:214`/`:218`
hardcodes

    recent_from = latest - datetime.timedelta(days=365)          # build(), line 218

and everything downstream of that one line -- which doorways are listed at all
(`if recent == 0: continue`) and what `calls_12mo` counts, which the ranking
key `-(calls_12mo * (1 - tow_rate))` multiplies -- is fixed at 365 days. The
recurrence window (`recur_days`) is already a parameter of both
`hotspots.build()` and `mirror.derive.derive()`; the recency window is not.

Both `src/hotspots.py` and `src/mirror/derive.py` are out of scope to edit for
this change (they are also 4.9's live-baseline reconciliation target, which
must stay provably unmodified). So this module reproduces the small tail of
`mirror.derive.derive()`/`_finish()` that calls `hotspots.build()`, changing
exactly one thing: the `latest` value handed to `build()` is shifted so that
its hardcoded `- 365 days` line lands on `true_latest - recency_days` instead
of `true_latest - 365 days`.

**Why a shift, not a rewrite of the filter.** `hotspots.build()` is carried
over as-is (`design.md` M8) everywhere except this one parameter, so the
result for every other field (`tows`, `repeat_calls`, `median_gap_days`,
`block`, ranking arithmetic once `calls_12mo` is known, ...) is byte-identical
to what `hotspots.build()` itself would produce for the same calls -- this
module does not reimplement any of that logic, it only chooses what `latest`
build() sees.

**The arithmetic, and why the previous attempt at this task got it wrong.**
`build()`'s only use of its `latest` parameter is the one hardcoded line
above. Feeding it

    shifted_latest = true_latest + timedelta(days=365 - recency_days)

makes that line compute

    shifted_latest - timedelta(days=365)
      == true_latest + timedelta(days=365 - recency_days) - timedelta(days=365)
      == true_latest - timedelta(days=recency_days)

which is exactly the window this task asks for. This identity is exact
`timedelta` arithmetic (day-granularity integers, no float rounding, no
calendar special-casing), and it is exact regardless of DST: Halifax's
`zoneinfo` tzinfo is only ever consulted when a datetime's *wall-clock*
offset is asked for (comparisons, `.timestamp()`), never by `timedelta`
addition itself, so the identity holds whether or not a DST transition falls
between `true_latest` and `shifted_latest`.

A prior attempt at this exact task used this same formula and still produced
a wrong `calls_12mo` window membership at the boundary -- discarded, not
carried forward, and not diagnosed here since the code no longer exists to
inspect. What is done differently this time: the boundary is checked directly
in `tests/test_derive_recency.py` (a call exactly `recency_days` old, and one
`recency_days + 1` old, against the *actual* `>=` comparison `hotspots.build()`
performs) rather than trusted from the algebra alone. See that file's
"boundary" tests.

No network call is made anywhere here, for the same reason `mirror.derive`
makes none: every read below goes through `mirror.derive.load()`/
`census_blocks()`, which read Postgres only.
"""

import datetime

import hotspots
from mirror import derive

DEFAULT_RECENCY_DAYS = 365

# hotspots.build()'s own hardcoded window (src/hotspots.py:218) -- the value this
# module's shift is relative to. Not settable; it is a property of the function
# being wrapped, not a configuration knob of this module.
_BUILD_HARDCODED_RECENCY_DAYS = 365


def _finish_with_recency(calls, fields, ids, blocks_geo, min_calls, recur_days,
                         min_doorways, district, recency_days):
    """`mirror.derive._finish()`'s own tail, reproduced rather than imported so
    the `latest` value handed to `hotspots.build()` can be the shifted one
    (module docstring) while the `latest` this function *returns* stays the
    real one -- a viewer or export reads the mirror's true most-recent-call
    date, never the arithmetic fiction `build()` was fed to get the window
    right. Every other line here is `_finish()` unmodified; see that function
    in `mirror/derive.py` for the logic being mirrored.
    """
    found_ids = {c["REQUEST_ID"] for c in calls}
    missing_ids = sorted(ids - found_ids)
    if not calls:
        return {"rows": [], "blocks": [], "calls": calls, "fields": fields,
                "latest": None, "missing_service_request_ids": missing_ids}

    true_latest = max(
        hotspots.to_local(c["DATE_INITIATED"]) for c in calls if c["DATE_INITIATED"]
    )
    shift_days = _BUILD_HARDCODED_RECENCY_DAYS - recency_days
    shifted_latest = true_latest + datetime.timedelta(days=shift_days)

    rows = hotspots.build(calls, fields, min_calls, recur_days, shifted_latest, blocks_geo)
    if district:
        rows = [r for r in rows if str(r["district"]) == str(district)]

    blocks = hotspots.roll_blocks(rows, min_doorways) if rows else []
    neighbours = {b["block"]: b["doorways"] for b in blocks}
    for r in rows:
        r["block_doorways_calling"] = neighbours.get(r["block"], 1)

    unmatched_census = sum(1 for r in rows if not r["block"])

    return {"rows": rows, "blocks": blocks, "calls": calls, "fields": fields,
            "latest": true_latest, "missing_service_request_ids": missing_ids,
            "unmatched_census_count": unmatched_census}


def derive_with_recency(conn, violation=None, canonical_type=None, min_calls=2,
                        recur_days=365, min_doorways=2, district=None,
                        recency_days=DEFAULT_RECENCY_DAYS):
    """`mirror.derive.derive()`, with the recency window (task 5.5b) exposed as a
    parameter alongside the four `derive()` already accepts (`min_calls`,
    `recur_days`, `min_doorways`, `district` -- task 5.5a).

    `recency_days=365` reproduces `derive()`'s own (and `hotspots.build()`'s
    hardcoded) behaviour exactly -- proven in
    `tests/test_derive_recency.py::test_default_recency_matches_derive_derive_exactly`
    by comparing this function's output against `derive.derive()`'s, field for
    field, rather than assuming the default is a no-op.

    Returns the same shape `derive()` does: `rows`, `blocks`, `calls`, `fields`,
    `latest`, `missing_service_request_ids`, `unmatched_census_count`, plus the
    `derived_at`/`last_sync_success_at`/`most_recent_call_date`/`reconciled`
    metadata (unaffected by `recency_days` -- they describe the mirror's state,
    not this call's filter choice).
    """
    ids = set()
    calls, fields = derive.load(conn, violation=violation, canonical_type=canonical_type,
                                ids_out=ids)
    blocks_geo = derive.census_blocks(conn) if calls else []
    result = _finish_with_recency(calls, fields, ids, blocks_geo, min_calls, recur_days,
                                  min_doorways, district, recency_days)
    result.update(derive.derivation_metadata(conn))
    return result
