"""Serve the doorway list, the block list and the map over HTTP (task 5.1).

`design.md` M5: "the file stops being the product." D11's standalone board
(`src/hotspots.py`'s `write_board`, filling `web/template.html`) required nothing
of a viewer but a browser, and that property is the one worth keeping -- not the
file. This module keeps it by putting the infrastructure on the server's side of
the line: a viewer needs no install, no account, no credential and no build step,
because `web/app/index.html` is a plain HTML/CSS/JS page (no bundler, no
framework) that fetches its data from the JSON API below instead of having it
embedded at generation time.

`src/hotspots.py` and `web/template.html` are untouched. They still produce the
live-baseline board task 4.9 reconciles the mirror-backed derivation against, and
nothing here imports or calls into that path. `web/app/index.html` is a *new*
file evolved from `web/template.html` -- the same lists, map and triage UI, in
the same plain JavaScript, with its `const MAP = __MAP__; const DATA = __DATA__;`
replaced by two `fetch()` calls. See that file's header comment for the exact
diff.

design.md M12 (server shape, decided, not revisited here):
  - Flask, synchronous. The mirror is read through synchronous psycopg and a
    derivation is 0.1-3 seconds of CPU work, so an async framework would spend
    most of its time in a thread pool for no benefit. A production WSGI server
    such as gunicorn runs it (see `run()` below and the repository-root
    `wsgi.py` for the two start commands, dev and production).
  - The existing page, fed by a JSON API. No build step, no front-end
    framework, no working interface discarded.
  - Host-agnostic: the port and the database address come from the
    environment (`PORT`; `HFX_MIRROR_DSN` via `mirror.db.dsn()`, unchanged).
    Nothing here names a host.

Route table:

  GET  /                                the page (`web/app/index.html`),
                                         always for the default canonical type
  GET  /types/<slug>                    the same page, for the type <slug>
                                         names -- task 5.2, see below
  GET  /map-network.json                the street network, as a static asset
                                         served from its own URL (490 KB, one
                                         file for every type; cached for a day
                                         with a conditional GET past that, per
                                         task 5.4 -- see the route below)
  GET  /api/types                       every tracked canonical type and its
                                         slug, `{default_slug, types: [{slug,
                                         type}, ...]}` -- task 5.2, what the
                                         page's type switcher reads at boot
  GET  /api/types/<slug>/doorways       a type's doorway list, `{type, slug,
                                         count, latest, summary, rows}`
  GET  /api/types/<slug>/blocks         a type's block list, `{type, slug,
                                         count, blocks}`
  GET  /api/types/<slug>/decisions      every triage decision recorded for a
                                         type, `{type, slug, decisions: [...]}`
                                         -- task 6.1, see below
  POST /api/types/<slug>/decisions      record one decision, upserted by
                                         (type, scope, item_key) -- task 6.1
  GET  /api/types/<slug>/figures        a type's filter-independent figures --
                                         the tow comparison, vehicle uniqueness
                                         and the conclusion built from both --
                                         `{type, slug, available, figures?,
                                         reason?, computed_at?}` -- task 5.8,
                                         see below
  GET  /api/freshness                   the four clocks design.md M4 names --
                                         `{checked_at, most_recent_call_date,
                                         last_success_at, last_attempt_at,
                                         next_due_at, behind_source,
                                         behind_reason, stale_call_warning,
                                         next_update}` -- tasks
                                         7.1/7.2/7.2a/7.4a, see below

Every list is computed per request from the mirror via `mirror.derive.derive`
(design.md M11) -- nothing here materializes a doorway or block list ahead of a
request.

**Task 6.1: shared triage decisions.** `design.md` M6 and `proposal.md`
record the defect this closes -- the served page's only prior shared-storage
mechanism was `window.claude.use("db")`, which resolves to `null` off a
Claude Artifact host and silently degrades every viewer to a private
`localStorage` copy. The two routes above read and write
`mirror.triage`'s `triage_decisions` table (`schema.sql`, task 1.5) instead,
so every viewer of a type's list reads and writes the same rows -- see
`mirror/triage.py`'s module docstring for the persistence and scoping
details, and `web/app/index.html`'s `loadDecisions()`/`saveDecision()` for
the client side. `web/app/index.html` still carries the pre-6.1
`window.claude.use("db")`/`localStorage` code paths; removing them is task
6.3's job, not this one's (see that file's comments).

**Task 5.2: every tracked type now answers, not just the default.**
`_SLUG_TO_CANONICAL` maps every tracked type's slug to its canonical name --
computed once, from `violation_types.CANONICAL_TYPES` -- and
`_canonical_for_slug` now looks a slug up there directly rather than
special-casing `DEFAULT_SLUG` (5.1 deliberately restricted it to just the
default; see that task's note, now superseded, that used to sit here). This
was, as 5.1 predicted, a one-line change to `_canonical_for_slug` -- no
reshape of the route table or the payload shape. What 5.2 *does* add beyond
that lift: `GET /types/<slug>`, a page route so a type has a real URL to
land on or share rather than only being reachable by switching client-side
after loading `/`; and `GET /api/types`, the list `web/app/index.html`'s type
switcher fetches to know what those URLs are. Every list, marker and triage
route was already written generically against `<slug>` (5.1's forward-looking
shape) and derives strictly per request from the one canonical type the slug
names (`mirror.derive.derive(canonical_type=canonical)`,
`mirror.triage.list_for(conn, canonical)`) -- there is no code path in this
module that reads or returns more than one type's rows for a single request,
which is what keeps switching types from mixing them (`tests/test_app.py`'s
"routing scoped by type" section proves this against seeded data for more
than the default type, not just the default).

**Task 5.3: an untracked type is reported, not bare-404ed.** `/types/<slug>`
used to `abort(404)` for a slug `_canonical_for_slug` cannot resolve, which
Flask renders as its generic "Not Found" HTML page -- true, but unstated: a
viewer landing on a mistyped or stale link has no way to tell "this type does
not exist" from any other 404 on the internet. `_untracked_type_page` renders
a small page instead (still status 404 -- the resource genuinely does not
exist, so the status line does not change, only the body) that names the
slug and links every tracked type, built with `render_template_string` so the
untrusted `<slug>` path segment is Jinja-escaped rather than echoed raw. The
JSON routes are untouched by this task: `jsonify(error="untracked", slug=slug)`
already states a reason instead of an empty list, which is what this task
asks for -- they just never got Flask's default HTML page in the first place.

**Task 5.8: filter-independent figures are read, not recomputed.** `GET
/api/types/<slug>/figures` wraps `mirror.type_figures.figures_for` (task
5.7's read path) -- the tow comparison (with its cluster-bootstrap interval),
vehicle uniqueness and the type-phrased conclusion, computed once per mirror
reload and stored keyed to the mirror version, never derived per request. A
canonical type with nothing stored yet for the mirror's current version
answers `{"available": false, "reason": "..."}` (`figures_for`'s own stated
reason) rather than a bare `null` or a fallback computation -- this route
never calls `mirror.per_type`/`mirror.figures` itself. `web/app/index.html`'s
`#tow-thesis` sentence is what reads this route (see that file's comments);
every other number on the page still comes from `/api/types/<slug>/doorways`'s
per-request `summary`, because M11 only moved the figures that do not depend
on any filter here -- the doorway and block lists stay live.

**Performance stays per-5.5's measurements.** Routing to a different type
does not change what one request computes: it is still exactly one
`derive()` call for exactly one canonical type, the same shape 5.5 measured
(349ms default, 662ms for the largest type, `No Parking Sign`). Nothing here
calls `derive_all()` or loops over `_SLUG_TO_CANONICAL` to answer one
request.

**Tasks 7.1/7.3/7.4a/7.2/7.2a: freshness is served, not asserted.** `GET
/api/freshness` is a thin wrapper over `mirror.status.mirror_freshness` (task
3.3's four clocks) and `mirror.status.layer_freshness`'s `behind_source`
verdict (task 3.4) -- nothing here recomputes what that module already
derives from `layer_state`/`sync_runs`. The one addition is `_call_staleness`:
a warning when the most recent call the mirror holds is materially older than
the request's own clock (`STALE_CALL_WARNING_DAYS`, task 7.4a), which is not a
sync-health question `mirror.status` answers on its own -- a sync can be
perfectly healthy against a source that has itself gone quiet for a month,
and that is exactly the case this warns about. `_behind_reason` (task 7.2) is
the other half of "whose limit is it": it reads `layer_freshness`'s own
per-layer `behind_reason` text back out of the aggregate so a viewer is told
which system owns a lag -- HRM's publishing schedule, or this sync itself --
rather than a bare `behind_source` boolean with no attribution. `_next_update_state`
(task 7.2a) is a third addition, and a different axis again: whether the
*sync itself* has missed its own polling schedule (`sync.POLL_INTERVAL`) by
more than a grace period, with nothing successful since -- turning
`next_due_at` from a rendered date that would keep looking healthy forever
into an explicit `overdue` state (design.md M4).

`web/app/index.html`'s header banner (`#freshness`) and footer
(`#footer-sync-date`) fetch this route and are what makes 7.1's "last
successful update and expected next update" prominent on every view, and
what 7.3's dated freshness replaces the removed undated "regenerated from the
live service" sentence with.

**The timezone trap this route's serialization exists to avoid.** Every
`timestamptz` column here round-trips through psycopg in the *connection's
session timezone* -- `America/Halifax` in this codebase's local/test
Postgres, not UTC -- so `mirror.status`'s datetimes come back tz-aware but
offset `-03:00`/`-04:00`, not `+00:00`, even though the instant they name is
identical to a UTC-constructed one. `_iso_utc` below normalizes every
timestamp to a `+00:00`-offset ISO string before it is serialized, so two
payloads describing the same instant are also the same *string* -- comparing
`.isoformat()` output without that normalization is what broke an earlier,
discarded attempt at this route (three tests failed on offset representation
alone, not on any actual discrepancy in the instant). Test against the parsed
`datetime` (or against `_iso_utc` applied the same way to the expected value),
not against a raw `expected.isoformat()`.

**Why the default type is both a substring match and a canonical type.**
`derive.DEFAULT_VIOLATION` ("Driveway", `hotspots.load()`'s selection) and the
canonical type "Blocking Driveway" (`violation_types.CANONICAL_TYPES`, exact
label membership) currently select the identical two raw labels --
"Blocking Driveway (DISPATCH)" and "DRIVEWAY" -- so the root page and the
`blocking-driveway` API routes agree. This module uses the canonical-type
selection throughout, because that is the axis `/api/types/<slug>/...` routes
on and the one 5.2 extends; task 4.9's reconciliation is what keeps
`hotspots.load("Driveway")` and `derive.derive(canonical_type="Blocking
Driveway")` provably in step, not this module.
"""

import datetime
import os
import pathlib
import re
import sys

# src/app/server.py -> parents[0]=src/app, [1]=src, [2]=repo root. Same
# sys.path idiom src/mirror/derive.py uses to find "src" (and therefore
# "mirror" and "hotspots") when run as a script rather than imported with
# pytest's pythonpath=["src"] already in effect -- needed here for
# `python3 src/app/server.py` and for gunicorn/wsgi.py at the repo root.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from flask import (  # noqa: E402
    Flask, jsonify, render_template_string, request, send_from_directory,
)

from mirror import (  # noqa: E402
    db, derive_recency, status, sync, triage, type_figures, violation_types,
)

WEB_DIR = _REPO_ROOT / "web"
APP_WEB_DIR = WEB_DIR / "app"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name):
    """A filesystem/URL-safe slug for one canonical type. Identical logic to
    `mirror.derive._slug` (not imported from there -- that name is private to
    `derive`, and duplicating four lines costs less than coupling two modules
    on an underscore-prefixed helper neither owns for the other). Kept in step
    by `tests/test_app.py`'s slug test, which checks both against the same
    fixed name.
    """
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "type"


# Every tracked canonical type's slug, computed once. 5.2's route table is this
# dict with the restriction in `_canonical_for_slug` lifted -- see module
# docstring.
_SLUG_TO_CANONICAL = {_slug(name): name for name in violation_types.CANONICAL_TYPES}

DEFAULT_CANONICAL_TYPE = "Blocking Driveway"
DEFAULT_SLUG = _slug(DEFAULT_CANONICAL_TYPE)
assert _SLUG_TO_CANONICAL.get(DEFAULT_SLUG) == DEFAULT_CANONICAL_TYPE, (
    "DEFAULT_CANONICAL_TYPE must be one of violation_types.CANONICAL_TYPES"
)


def _canonical_for_slug(slug):
    """The canonical type name a route's `<slug>` names, or `None` when it does
    not resolve -- any slug not in `_SLUG_TO_CANONICAL`, whether it names a
    real HRM label this codebase has not frozen (task 4.15 owns the frozen
    list; see the top-level `FILE OWNERSHIP` note in this change's tasks if
    that list looks wrong) or names nothing at all. Every route below treats
    the two cases identically -- a bare 404 -- because telling them apart for
    a viewer is task 5.3's job, not this function's (module docstring).
    """
    return _SLUG_TO_CANONICAL.get(slug)


# ------------------------------------------------------------- untracked type (5.3)


_UNTRACKED_TYPE_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Type not tracked</title>
<style>
  body{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       max-width:640px;margin:64px auto;padding:0 20px;color:#1a1a1a}
  a{color:#0b5fa5}
  code{background:#f0f0f0;padding:0 4px;border-radius:3px}
  ul{padding-left:20px}
</style>
</head>
<body>
<h1>&ldquo;{{ slug }}&rdquo; is not a tracked type</h1>
<p>This application only lists doorways and blocks for the canonical violation
types it tracks (<code>violation_types.CANONICAL_TYPES</code>). That slug is
not one of them -- it may name a real HRM label this codebase has not frozen,
or nothing at all.</p>
<p><a href="/">Go to the default type</a>, or pick one of the {{ types|length }}
tracked types below:</p>
<ul>
{% for slug, name in types %}  <li><a href="/types/{{ slug }}">{{ name }}</a></li>
{% endfor %}</ul>
</body>
</html>
"""


def _untracked_type_page(slug):
    """Task 5.3: a rendered explanation for a slug `_canonical_for_slug` cannot
    resolve, in place of Flask's generic "Not Found" page -- `<slug>` is an
    untrusted path segment, so this goes through `render_template_string`
    (Jinja auto-escaping) rather than string-formatting it into the page
    directly. Still answered with a 404 status by the caller: the resource
    named by the URL genuinely does not exist, only the body now says why.
    """
    return render_template_string(
        _UNTRACKED_TYPE_PAGE, slug=slug,
        types=sorted(_SLUG_TO_CANONICAL.items(), key=lambda kv: kv[1]),
    )


# ------------------------------------------------------- filter parameters (5.5a, 5.5b)


# design.md M11 names five filters as interactive controls: district, minimum
# recent calls per doorway, minimum still-calling doorways per block, the
# recurrence window and the recency window. `mirror.derive.derive()` already
# takes the first four as arguments -- `min_calls`, `min_doorways`,
# `recur_days`, `district` -- but nothing before this task read them off a
# request; every call into `derive()` used its defaults regardless of what a
# viewer might want to see. The recency window is not a `derive()` argument at
# all yet (task 5.5b, `mirror.derive_recency`), so it is read the same way and
# passed to `derive_recency.derive_with_recency` instead.
_FILTER_DEFAULTS = {
    "min_calls": 2, "min_doorways": 2, "recur_days": 365, "recency_days": 365,
}


def _int_param(name, minimum=0):
    """One filter's value off the query string, or its `derive()`/
    `hotspots.build()` default when absent, blank or not a usable integer.
    Falling back rather than 400ing on a bad value keeps a list/ranking
    request from ever hard-failing on a malformed control -- 5.5e (reporting
    an *empty result* distinctly from a failure) is a separate, not-yet-done
    task, but a value error is not that task's concern to guard against either
    and should not read as one.
    """
    default = _FILTER_DEFAULTS[name]
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _filters_from_request():
    """Every filter task 5.5a's toolbar controls set: `{district, min_calls,
    min_doorways, recur_days, recency_days}`. `district` is an opaque string
    (matched against `derive()`'s own `str(r["district"]) == str(district)`),
    blank or absent meaning "every district" -- never `0` or `"None"`, which
    would silently select nothing.
    """
    district = request.args.get("district")
    if district is not None and district.strip() == "":
        district = None
    return {
        "district": district,
        "min_calls": _int_param("min_calls"),
        "min_doorways": _int_param("min_doorways"),
        "recur_days": _int_param("recur_days", minimum=1),
        "recency_days": _int_param("recency_days", minimum=1),
    }


# ------------------------------------------------------- response shaping


def _doorway_payload(rows):
    """One doorway list, in the abbreviated-key shape `hotspots.write_board`'s
    `page_rows` already uses (`a`, `d`, `c`, ... ), so `web/app/index.html`'s
    script -- copied from `web/template.html`, which reads exactly this shape
    off `__DATA__` -- needs no changes to consume it from JSON instead.

    One divergence from `write_board`, not a behavioural change from
    `derive`/`hotspots.build`: a doorway with no located calls has `lat`/`lon`
    of `None` (an empty `statistics.median` in `hotspots.build`), and
    `write_board` rounds them unguarded. Guarded here instead, so such a row
    reaches the page as "no location" rather than raising -- see
    `specs/hosted-triage-app/spec.md`'s "Doorway without a location" scenario.
    """
    payload = []
    for i, r in enumerate(rows):
        payload.append({
            "i": i, "a": r["address"].title(), "d": str(r["district"] or "?"),
            "c": (r["community"] or "").title(), "st": (r["street"] or "").title(),
            "bk": r["block"] or "", "nb": r.get("block_doorways_calling", 1),
            "m": r["calls_12mo"], "t": r["calls_total"], "w": r["tows"],
            "vd": r["vehicles_distinct"], "vs": r["vehicles_seen"],
            # Task 5.5c: the one figure the recurrence window (`recur_days`)
            # governs (`hotspots.build()`'s `repeat_calls`) -- exposed so the
            # claim that recur_days changes only this figure, never the
            # listed set, is something a viewer (and a test, over real HTTP)
            # can actually observe, not just something true internally.
            "rp": r["repeat_calls"],
            "g": None if r["median_gap_days"] is None else round(r["median_gap_days"]),
            "l": r["last_call"], "o": r["owner"] or "",
            "lat": None if r["lat"] is None else round(r["lat"], 6),
            "lon": None if r["lon"] is None else round(r["lon"], 6),
        })
    return payload


def _block_payload(blocks):
    """One block list, in `hotspots.write_board`'s `page_blocks` shape -- see
    `_doorway_payload`'s docstring."""
    payload = []
    for i, b in enumerate(blocks):
        payload.append({
            "i": i, "bk": b["block"], "s": b["streets"].title(), "n": b["doorways"],
            "m": b["calls_12mo"], "t": b["calls_total"], "w": b["tows"],
            "dw": b["dwellings"], "r": b["calls_per_1k_dwellings"],
            "d": str(b["district"] or "?"), "worst": b["worst_doorway"].title(),
            "addrs": [a.strip().title() for a in b["addresses"].split(";") if a.strip()],
        })
    return payload


def _summary(result):
    """The header figures `write_board` bakes into the page text at generation
    time (`__TOW_PCT__`, `__UNIQUE_PCT__`, `__DISTINCT__`, `__SEEN__`,
    `__WITH_NEIGHBOUR__`) -- computed the same way, from the same `calls`/
    `fields`/`rows` a `derive()` call already returns, so the served page can
    fill them client-side instead of at generation time. `None` where the
    denominator is zero, matching `write_board`'s own "-" fallback (formatted
    by the page, not here).
    """
    calls, fields, rows = result["calls"], result["fields"], result["rows"]
    total = len(calls)
    tows = sum(1 for c in calls if fields[c["REQUEST_ID"]].get("Vehicle Was Towed") == "Y")
    seen = sum(r["vehicles_seen"] for r in rows)
    distinct = sum(r["vehicles_distinct"] for r in rows)
    with_neighbour = sum(1 for r in rows if r.get("block_doorways_calling", 1) >= 2)
    return {
        "tow_pct": round(100 * tows / total, 1) if total else None,
        "unique_pct": round(100 * distinct / seen) if seen else None,
        "distinct": distinct,
        "seen": seen,
        "with_neighbour": with_neighbour,
    }


# ------------------------------------------------------------- freshness (7.1, 7.3, 7.4a)


def _iso_utc(dt):
    """`dt` normalized to a UTC-offset (`+00:00`) ISO 8601 string, or `None`.

    See this module's docstring ("the timezone trap this route's
    serialization exists to avoid") for why this matters: without it, two
    payloads naming the identical instant can serialize to different
    strings, because psycopg hands datetimes back in the connection's session
    timezone, not UTC.
    """
    return None if dt is None else dt.astimezone(datetime.timezone.utc).isoformat()


# Task 7.4a: "materially older" than the run time. Three weeks is longer than
# every publish interval design.md M3 has observed or guessed at (roughly
# weekly), so this warns on a source that has gone quiet for multiple missed
# publishes, not on the ordinary gap between two of them.
STALE_CALL_WARNING_DAYS = 21


def _call_staleness(most_recent_call_date, now=None):
    """Task 7.4a: is the newest call the mirror holds materially older than
    `now`? Distinct from `mirror.status`'s `behind_source` (task 3.4), which
    asks whether *this sync* has fallen behind *the source* -- a sync can be
    perfectly healthy (polling on schedule, nothing new to pull) while HRM
    itself has stopped publishing for a month, and that is a limit of HRM's
    publishing schedule (design.md M4's "whose limit is it"), not a sync
    defect. This is the one check here that is not a thin read of
    `mirror.status`, because no clock there answers this question on its own.
    """
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    if most_recent_call_date is None:
        return {
            "known": False, "stale": False, "days_old": None,
            "threshold_days": STALE_CALL_WARNING_DAYS,
            "reason": "the mirror holds no calls yet",
        }
    age_days = (now - most_recent_call_date.astimezone(datetime.timezone.utc)).total_seconds() / 86400
    stale = age_days > STALE_CALL_WARNING_DAYS
    return {
        "known": True,
        "stale": stale,
        "days_old": round(age_days, 1),
        "threshold_days": STALE_CALL_WARNING_DAYS,
        "reason": (
            f"the most recent call in the data is {round(age_days)} days old, "
            f"past the {STALE_CALL_WARNING_DAYS}-day warning threshold"
        ) if stale else None,
    }


def _behind_reason(freshness):
    """Task 7.2: attribute `behind_source`'s aggregate verdict to whichever
    system owns it, by reusing `mirror.status.layer_freshness`'s own
    per-layer `behind_reason` rather than recomputing the wording here.

    A currency layer (`mirror.status.CURRENCY_LAYERS`) that is behind means
    HRM has published something this sync has not yet pulled -- this
    system's own problem. None behind means the source simply has not
    published anything new -- a limit of HRM's publishing schedule, not a
    sync defect (this is the *other* half of the distinction `_call_staleness`
    draws for the data clock; this one is for the sync clock). Mirrors
    `mirror_freshness`'s own aggregation: any one currency layer behind is
    enough to call the whole mirror behind (design.md M3 treats the two
    Cityworks layers as one publish event four minutes apart), so a viewer
    does not need to know which layer specifically.
    """
    currency = [
        freshness["layers"][key] for key in status.CURRENCY_LAYERS
        if key in freshness["layers"] and freshness["layers"][key].get("known")
    ]
    behind = next((v for v in currency if v["behind_source"]), None)
    if behind is not None:
        return behind["behind_reason"]
    not_behind = next((v for v in currency if not v["behind_source"]), None)
    return not_behind["behind_reason"] if not_behind else None


# Task 7.2a: how long the sync's own schedule can be missed before the
# next-update promise reads as overdue rather than as a future date that
# never arrives (design.md M4: "'next update' is a promise, and promises
# rot ... the next-sync display is a state machine, not a string"). The
# grace period is grounded in `sync.POLL_INTERVAL` (currently one day) --
# the sync's *own* polling cadence -- rather than picked independently, per
# design.md's open question ("depends on the observed cadence, so it
# follows from the question above rather than being set independently").
# This is a different cadence from `STALE_CALL_WARNING_DAYS` (7.4a's 21
# days): that one is about HRM's roughly-weekly publish schedule going
# quiet; this one is about *this sync* missing its own nightly poll. One
# full extra `POLL_INTERVAL` is allowed past the due time, so a poll that
# lands a few hours late, or a due time that falls right at a schedule
# boundary, does not flip the banner to overdue the moment it passes --
# missing the *following* scheduled poll too is what overdue means here.
NEXT_UPDATE_GRACE_PERIOD = sync.POLL_INTERVAL


def _next_update_state(freshness, now=None):
    """Task 7.2a: the next-sync clock as a state machine, not a rendered
    date. `next_due_at` alone keeps looking like a healthy future promise
    forever once the scheduler that keeps it has died -- this turns "has
    the due time passed by the grace period, with nothing successful
    since" into an explicit `overdue` boolean the client branches on,
    rather than re-deriving the arithmetic (and its boundary) in
    JavaScript.

    Reads `freshness["next_due_at"]`/`["last_success_at"]` -- the same
    aggregate `mirror.status.mirror_freshness` already computes (earliest
    due time, earliest success, across the currency layers) -- rather than
    recomputing anything per layer.
    """
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    grace_days = NEXT_UPDATE_GRACE_PERIOD.total_seconds() / 86400
    next_due_at = freshness.get("next_due_at")
    if next_due_at is None:
        return {
            "known": False, "overdue": False, "days_overdue": None,
            "grace_period_days": grace_days,
            "reason": "no sync has ever recorded a due time",
        }
    next_due_at = next_due_at.astimezone(datetime.timezone.utc)
    overdue_at = next_due_at + NEXT_UPDATE_GRACE_PERIOD
    last_success_at = freshness.get("last_success_at")
    # Defends against the aggregate combining two different currency
    # layers' clocks (mirror_freshness's next_due_at is the earliest across
    # layers, last_success_at likewise but not necessarily from the same
    # layer): only a success recorded at or after the due time in question
    # counts as "no successful sync since."
    succeeded_since_due = (
        last_success_at is not None
        and last_success_at.astimezone(datetime.timezone.utc) >= next_due_at
    )
    overdue = now > overdue_at and not succeeded_since_due
    return {
        "known": True,
        "overdue": overdue,
        "days_overdue": round((now - overdue_at).total_seconds() / 86400, 1) if overdue else None,
        "grace_period_days": grace_days,
        "reason": (
            f"the sync was due {round((now - next_due_at).total_seconds() / 86400, 1)} "
            f"days ago and has not completed successfully since, past the "
            f"{round(grace_days, 1)}-day grace period"
        ) if overdue else None,
    }


def _freshness_payload(freshness, now=None):
    """The `GET /api/freshness` JSON body: a thin reshaping of
    `mirror.status.mirror_freshness`'s dict (every field except
    `stale_call_warning` and `behind_reason` is read off it, not recomputed),
    with every timestamp normalized through `_iso_utc`.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return {
        "checked_at": _iso_utc(now),
        "most_recent_call_date": _iso_utc(freshness["most_recent_call_date"]),
        "last_success_at": _iso_utc(freshness["last_success_at"]),
        "last_attempt_at": _iso_utc(freshness["last_attempt_at"]),
        "next_due_at": _iso_utc(freshness["next_due_at"]),
        "behind_source": freshness["behind_source"],
        "behind_reason": _behind_reason(freshness),
        "stale_call_warning": _call_staleness(freshness["most_recent_call_date"], now=now),
        "next_update": _next_update_state(freshness, now=now),
    }


# --------------------------------------------------------------- app factory


def create_app(conn_factory=None):
    """The Flask application. `conn_factory`, if given, replaces `mirror.db.connect`
    -- the hook `tests/test_app.py` uses to point every request at a throwaway
    schema via Flask's test client, the same way `tests/conftest.py`'s `clean_db`
    fixture does for the derivation tests, without a real network listener.

    A connection is opened and closed within each request; nothing is held across
    requests. Every list is computed per request (design.md M11) -- there is no
    cache here for 5.5 to have to work around.
    """
    app = Flask(__name__, static_folder=None)
    connect = conn_factory or db.connect

    @app.get("/")
    def index():
        return send_from_directory(APP_WEB_DIR, "index.html")

    @app.get("/types/<slug>")
    def type_page(slug):
        """The same page `index()` serves, at a per-type URL (task 5.2). One
        static file answers for every type -- design.md M12 rules out a build
        step, so there is no per-type file to generate -- and
        `web/app/index.html`'s boot script reads `<slug>` back out of
        `location.pathname` (its `SLUG` constant) to decide which type's data
        to fetch, the same script regardless of which type that turns out to
        be. An untracked slug answers a rendered explanation instead of
        Flask's bare default 404 page (task 5.3, `_untracked_type_page`) --
        still a 404 status, since the resource genuinely does not exist.
        """
        if _canonical_for_slug(slug) is None:
            return _untracked_type_page(slug), 404
        return send_from_directory(APP_WEB_DIR, "index.html")

    @app.get("/map-network.json")
    def map_network():
        # Task 5.4: the 490 KB street network never varies by canonical type or
        # by filter -- every route in this module reads it from the one file
        # `src/hotspots.py --network` last wrote, so there is nothing per-request
        # to compute here, unlike /api/types/<slug>/... Two mechanisms make that
        # cacheable rather than merely static:
        #   - `max_age` sets `Cache-Control: public, max-age=86400`, so a browser
        #     that already has it skips the request entirely -- not just the
        #     body -- for a day rather than re-asking on every navigation.
        #     Not `immutable`/a year: the file has no cache-busting name (no
        #     build step, per design.md M12), so a rare re-derivation of the
        #     network needs a bounded staleness window rather than an unbounded
        #     one under the same URL.
        #   - `conditional`/`etag` (Werkzeug's `send_file` defaults, already true
        #     without passing them -- named here so the choice reads as made,
        #     not merely inherited) mean a request past that window that finds
        #     the file unchanged gets a 304 with no body, rather than resending
        #     the 490 KB.
        # Together, switching between violation types -- which never touches
        # this route again once a viewer's browser holds one response -- cannot
        # re-download the geometry; see tests/test_app.py's cache-control and
        # conditional-GET checks for the mechanism, not just the assertion.
        return send_from_directory(
            WEB_DIR, "map-network.json", max_age=86400, conditional=True, etag=True,
        )

    @app.get("/api/types")
    def list_types():
        """Every tracked canonical type and its slug (task 5.2): `{default_slug,
        types: [{slug, type}, ...]}`, in `violation_types.CANONICAL_TYPES`'
        own order (busiest first -- see that module's docstring), not
        resorted. This is what `web/app/index.html`'s type switcher fetches
        once at boot to build its control and to turn a chosen type back into
        the slug it navigates to -- the only place that list is assembled, so
        a type added to the frozen list in `violation_types.py` needs no
        change here to show up in the switcher. No database connection: the
        list is a pure function of the frozen type table already held in
        memory (`_SLUG_TO_CANONICAL`), so this route costs nothing like a
        `derive()` call does.
        """
        return jsonify(
            default_slug=DEFAULT_SLUG,
            types=[
                {"slug": slug, "type": name}
                for slug, name in _SLUG_TO_CANONICAL.items()
            ],
        )

    @app.get("/api/types/<slug>/doorways")
    def doorways(slug):
        """Task 5.5a: `district`, `min_calls`, `min_doorways`, `recur_days` and
        `recency_days` are read off the query string (`_filters_from_request`)
        and threaded through to the derivation, so the toolbar's controls
        change what this route returns rather than only what the page filters
        client-side afterward. `filters` echoes the values this response was
        actually computed from -- default query string, default filters,
        exactly `derive()`'s own defaults.
        """
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        filters = _filters_from_request()
        conn = connect()
        try:
            result = derive_recency.derive_with_recency(
                conn, canonical_type=canonical, **filters,
            )
        finally:
            conn.close()
        return jsonify(
            type=canonical, slug=slug, count=len(result["rows"]),
            latest=result["latest"].strftime("%Y-%m-%d") if result["latest"] else None,
            summary=_summary(result), filters=filters,
            rows=_doorway_payload(result["rows"]),
        )

    @app.get("/api/types/<slug>/blocks")
    def blocks(slug):
        """Task 5.5a: same filters as `doorways()` above, read the same way --
        a block list is rolled up from exactly the doorway rows those filters
        produced (`mirror.derive_recency.derive_with_recency` runs
        `hotspots.roll_blocks` on its own filtered `rows`, same as `derive()`),
        so the two routes never disagree about which doorways a given filter
        combination includes.
        """
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        filters = _filters_from_request()
        conn = connect()
        try:
            result = derive_recency.derive_with_recency(
                conn, canonical_type=canonical, **filters,
            )
        finally:
            conn.close()
        return jsonify(
            type=canonical, slug=slug, count=len(result["blocks"]),
            filters=filters, blocks=_block_payload(result["blocks"]),
        )

    @app.get("/api/types/<slug>/decisions")
    def get_decisions(slug):
        """Every decision recorded for one type (task 6.1). Read side of
        shared triage -- `web/app/index.html`'s `loadDecisions()` calls this
        on boot so every viewer starts from the same rows, instead of the
        pre-6.1 `window.claude.use("db")` path that only ever worked inside a
        Claude Artifact host.
        """
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        conn = connect()
        try:
            decisions = triage.list_for(conn, canonical)
        finally:
            conn.close()
        return jsonify(type=canonical, slug=slug, decisions=decisions)

    @app.post("/api/types/<slug>/decisions")
    def post_decision(slug):
        """Record one decision (task 6.1). Body: `{scope, item_key, decision,
        note, role}` -- `note` and `role` optional. Upserts on
        `triage_decisions`' `(violation_type, scope, item_key)` UNIQUE
        constraint via `mirror.triage.record`, so a second call for the same
        doorway or block updates the existing row rather than duplicating it.

        `role` (task 6.6): the viewer's chosen role, asked for by
        `web/app/index.html`'s `ensureRole()` before a decision is ever sent
        here. When the body carries no `role` (an older client, or a request
        that omits it outright) `mirror.triage.record` falls back to
        `PLACEHOLDER_ROLE` -- the column is `NOT NULL`, so something sane has
        to land there either way; a real role from the request always wins.

        A malformed request (bad scope, missing item_key/decision) answers
        400 from `mirror.triage.record`'s `ValueError` without touching the
        database. Any other failure -- lost connection, constraint violation
        this validation did not anticipate -- rolls back and answers 500 with
        an `error` field rather than a 200 that silently did not persist:
        task 6.4 ("state plainly when a decision could not be persisted")
        needs a real failure response to react to, not a swallowed one.
        """
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        body = request.get_json(silent=True) or {}
        conn = connect()
        try:
            try:
                recorded = triage.record(
                    conn, canonical,
                    scope=body.get("scope"),
                    item_key=body.get("item_key"),
                    decision=body.get("decision"),
                    note=body.get("note"),
                    role=body.get("role"),
                )
            except ValueError as exc:
                conn.rollback()
                return jsonify(error="invalid", detail=str(exc)), 400
            except Exception:
                conn.rollback()
                return jsonify(error="persist_failed"), 500
            conn.commit()
        finally:
            conn.close()
        return jsonify(type=canonical, slug=slug, **recorded)

    @app.get("/api/types/<slug>/figures")
    def type_figures_route(slug):
        """A type's filter-independent figures (task 5.8): the tow comparison
        (with its cluster-bootstrap interval), vehicle uniqueness and the
        type-phrased conclusion `mirror.type_figures.compute_and_store`
        stores once per mirror reload (task 5.7) -- read here via
        `mirror.type_figures.figures_for`, never recomputed per request
        (design.md M12's "figures that do not depend on filters...").

        `available` is `false` with a stated `reason` -- never a computed
        number, never a bare `null` -- when this canonical type has no row
        yet for the mirror's current version and the default parameter set:
        a reload that has not yet run `type_figures.compute_and_store()` for
        it. `web/app/index.html`'s `#tow-thesis` sentence is the one piece of
        markup that reads this route; every other figure on the page comes
        from `/api/types/<slug>/doorways`'s per-request `summary` instead,
        because those depend on nothing this route stores.
        """
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        conn = connect()
        try:
            result = type_figures.figures_for(conn, canonical)
        finally:
            conn.close()
        payload = {"type": canonical, "slug": slug, "available": result["available"]}
        if result["available"]:
            payload["figures"] = result["figures"]
            # `_iso_utc`, not a raw `.isoformat()` -- this module's docstring's
            # "the timezone trap this route's serialization exists to avoid"
            # applies here too: psycopg hands `computed_at` back in the
            # connection's session timezone, not UTC.
            payload["computed_at"] = _iso_utc(result["computed_at"])
        else:
            payload["reason"] = result["reason"]
        return jsonify(**payload)

    @app.get("/api/freshness")
    def freshness():
        """Tasks 7.1/7.3/7.4a/7.2/7.2a: the four clocks design.md M4 names, plus
        7.4a's stale-call warning, 7.2's `behind_reason` attribution and
        7.2a's `next_update` overdue state -- see this module's docstring
        and `_freshness_payload`'s for the shape and the timezone-normalization
        rationale. `web/app/index.html`'s header
        banner and footer read this on every view; no route parameter, no
        per-type distinction -- the mirror has one set of these clocks
        regardless of which canonical type a viewer is looking at.
        """
        conn = connect()
        try:
            fresh = status.mirror_freshness(conn)
        finally:
            conn.close()
        return jsonify(_freshness_payload(fresh))

    return app


def run():
    """Dev entry point: `python3 src/app/server.py`. Not what production runs --
    that is gunicorn against the repository-root `wsgi.py` -- but useful for a
    quick local check without another dependency in the loop. Reads `PORT` from
    the environment, same as production; binds every interface (0.0.0.0) rather
    than naming a host, per design.md M12.
    """
    app = create_app()
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
