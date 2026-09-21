"""Serve the doorway list, the block list and the map over HTTP (task 5.1).

`design.md` M5: "the file stops being the product." D11's standalone board
(`src/hotspots.py`'s `write_board`, filling `web/template.html`; both retired
under task 8.7, last present at commit b04731c) required nothing of a viewer but
a browser, and that property is the one worth keeping -- not the file. This
module keeps it by putting the infrastructure on the server's side of the line:
a viewer needs no install, no account, no credential and no build step, because
`web/app/index.html` is a plain HTML/CSS/JS page (no bundler, no framework) that
fetches its data from the JSON API below instead of having it embedded at
generation time.

`src/hotspots.py` survives only as the live-source baseline task 4.9 reconciles
the mirror-backed derivation against (its four list outputs, each written to a
caller-named path), and nothing here imports or calls into that path.
`web/app/index.html` is a *new* file evolved from the retired
`web/template.html` (`git show b04731c:web/template.html`) -- the same lists,
map and triage UI, in the same plain JavaScript, with its
`const MAP = __MAP__; const DATA = __DATA__;` replaced by two `fetch()` calls.
See that file's header comment for the exact diff.

design.md M12 (server shape, decided, not revisited here):
  - Flask, synchronous. The mirror is read through synchronous psycopg and a
    derivation is 0.1-3 seconds of CPU work, so an async framework would spend
    most of its time in a thread pool for no benefit. A production WSGI server
    such as gunicorn runs it (see `run()` below and the repository-root
    `wsgi.py` for the two start commands, dev and production).
  - The existing page, fed by a JSON API. No build step, no front-end
    framework, no working interface discarded.
  - Host-agnostic: the port and the database address come from the
    environment (`PORT`; the `PG*` variables via `mirror.db.connect()`, from the
    environment or a local `.env`). Nothing here names a host.

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
                                         next_update, next_source_estimate}`
                                         -- tasks 7.1/7.2/7.2a/7.2b/7.4a, see
                                         below
  GET  /api/types/<slug>/export.csv     a type's doorway and block lists as
                                         one downloadable CSV, plus the type
                                         it covers, the same clocks
                                         `/api/freshness` serves, the filter
                                         values in effect and the six
                                         interpretation limits the served
                                         page footnotes -- task 7.7, see below
  GET  /types/<slug>/export             a readable brief covering the same
                                         type/filter combination as the CSV
                                         above -- the same rows, clocks,
                                         filters and limits, rendered as a
                                         page rather than a file -- task 7.7

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

**Task 7.2b: a fourth, distinct clock -- when HRM itself is next likely to
publish**, not when this sync is next due to poll. `next_due_at`/`next_update`
above are this sync's *own* schedule (`now + sync.POLL_INTERVAL`), well-defined
the moment any sync has ever run. `next_source_estimate` is a different thing:
an estimate of *HRM's* cadence, built from `sync.source_edit_history`'s
observed gaps between the source's own past publishes (design.md M2.6b/M3).
`_next_source_estimate` refuses to guess from fewer than two observed
intervals (three recorded publishes) -- design.md M3 is explicit that a single
gap "is not evidence of any particular" cadence -- and states plainly that the
next source update is not yet known rather than fabricate a schedule from one
data point or render nothing.

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

**Task 7.7: a type's lists, exported.** Two new routes, both reusing exactly
the same read paths the served page already does -- `derive_recency.
derive_with_recency` for the rows (same filters as `doorways()`/`blocks()`),
`_freshness_for_export`'s thin wrapper over `status.mirror_freshness`/
`_freshness_payload` for the clocks (the identical picture `GET
/api/freshness` serves, never recomputed), and `type_figures.figures_for` for
the tow-effect sentence. `GET /api/types/<slug>/export.csv` is "a complete
table": every doorway and block row for the type/filter combination in
effect, with no truncation. `GET /types/<slug>/export` is "a readable brief":
the same rows, plus the canonical type name stated plainly, the clocks, the
filter values in effect and the six interpretation limits 7.5 put in the
served page's footer, rendered as an HTML page via `render_template_string`
(`_untracked_type_page`'s own pattern -- design.md M12 rules out a build
step) rather than a downloadable file. `_INTERPRETATION_LIMITS` is a second,
hand-kept copy of the six `<li id="lim-...">` entries in `web/app/
index.html`'s footer (`:503`-`:528`) -- see that constant's own docstring for
why a shared source was rejected and what keeping two copies in sync costs.
"""

import csv
import datetime
import io
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
    Flask, Response, jsonify, render_template_string, request, send_from_directory,
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
<p><strong>This page covers no violation type.</strong> It lists no doorways or
blocks, and nothing on it describes any one type -- the tracked types named
below are links, not the subject of this page.</p>
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
    """One doorway list, in the abbreviated-key shape the retired
    `hotspots.write_board`'s `page_rows` used (`a`, `d`, `c`, ... ), so
    `web/app/index.html`'s script -- copied from the retired
    `web/template.html`, which read exactly this shape off `__DATA__` -- needs
    no changes to consume it from JSON instead (both last present at b04731c).

    One divergence from `write_board`, not a behavioural change from
    `derive`/`hotspots.build`: a doorway with no located calls has `lat`/`lon`
    of `None` (an empty `statistics.median` in `hotspots.build`), and
    `write_board` rounded them unguarded. Guarded here instead, so such a row
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
    """One block list, in the retired `hotspots.write_board`'s `page_blocks`
    shape -- see `_doorway_payload`'s docstring."""
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
    """The header figures the retired `write_board` baked into the page text at
    generation time (`__TOW_PCT__`, `__UNIQUE_PCT__`, `__DISTINCT__`, `__SEEN__`,
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


def _behind_reason(freshness, sync_overdue=False):
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

    `sync_overdue` (`_next_update_state`'s `overdue`) withdraws that second
    half: "not behind" only means the last *completed* poll saw nothing HRM had
    not already published, and an overdue sync has stopped polling, so nothing
    is known about HRM since. The lag is then not put on HRM's schedule (design
    M4: never name the wrong system as the limit); a layer that is behind still
    reports HRM's publish, which was seen before the sync stopped.
    """
    currency = [
        freshness["layers"][key] for key in status.CURRENCY_LAYERS
        if key in freshness["layers"] and freshness["layers"][key].get("known")
    ]
    behind = next((v for v in currency if v["behind_source"]), None)
    if behind is not None:
        return behind["behind_reason"]
    if sync_overdue:
        return (
            "this sync is overdue, so this mirror cannot tell whether HRM has "
            "published since its last completed poll -- the lag cannot be put "
            "down to HRM's publishing schedule until a sync completes"
        )
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
            "seconds_overdue": None, "due_passed": False,
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
        # The due time is behind us, whether or not the grace period has run
        # out yet. A client must never render `next_due_at` as an upcoming
        # time while this is true (hosted-triage-app: "SHALL NOT continue
        # displaying a future update time once that time has passed"); inside
        # the grace period (`overdue` still false) it says the sync was due
        # and has not completed yet, which is all that is known. A successful
        # poll always moves `next_due_at` past its own completion time, so
        # "due time passed" and "a success since it" do not co-occur in
        # practice; the plain comparison is used so that no past time can
        # be shown as upcoming even if they somehow did.
        "due_passed": now > next_due_at,
        "days_overdue": round((now - overdue_at).total_seconds() / 86400, 1) if overdue else None,
        # The same span in whole seconds, floored (never rounded up), so a
        # client can name a period shorter than a day honestly: `days_overdue`
        # is rounded to a tenth of a day (2.4 hours) and reads 0.0 for the
        # first hour or so of being overdue.
        "seconds_overdue": int((now - overdue_at).total_seconds()) if overdue else None,
        "grace_period_days": grace_days,
        "reason": (
            f"the sync was due {round((now - next_due_at).total_seconds() / 86400, 1)} "
            f"days ago and has not completed successfully since, past the "
            f"{round(grace_days, 1)}-day grace period"
        ) if overdue else None,
    }


# Task 7.2b: how many observed intervals between HRM's own past publishes
# (`sync.source_edit_history`) are required before `_next_source_estimate`
# will state an estimate at all, rather than "not yet known". Two, not one:
# a single interval is a single number with no way to tell a typical gap from
# a fluke -- design.md M3's own words for exactly this trap: "on 2026-09-15
# the source had not been updated for two days, which is consistent with a
# cadence slower than daily and is not evidence of any particular one." Two
# intervals (three recorded publishes) is the smallest history from which a
# second gap can be compared against the first at all -- still thin evidence,
# and the served wording says "estimated", never "due" or "expected", to
# carry that uncertainty into the banner rather than dressing a two-point
# average up as a schedule. A higher bar (three or four intervals) was
# considered and rejected: design.md M2a already sets four *publishes* as the
# bar for a much higher-stakes decision (whether identifiers are stable
# enough to justify an incremental sync); reusing that same number here for a
# purely informational estimate, worded as an estimate throughout, would
# withhold it longer than the actual risk of being wrong warrants.
NEXT_SOURCE_ESTIMATE_MIN_INTERVALS = 2


def _median_timedelta(deltas):
    """The median of a non-empty list of `datetime.timedelta` values.
    `statistics.median` is not used here because its even-length averaging
    path is not documented against `timedelta` inputs; this is four lines and
    is exercised directly by this module's tests instead of trusted from
    algebra alone."""
    ordered = sorted(deltas)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _next_source_estimate(history, now=None):
    """Task 7.2b: when HRM itself is likely to publish next -- distinct from
    `_next_update_state`'s `next_due_at` (this *sync's* own polling schedule,
    `now + sync.POLL_INTERVAL`, well-defined the instant any sync has run).
    This is an estimate of the *source's* cadence, built only from what has
    actually been observed (`sync.source_edit_history`'s gaps between HRM's
    past publishes) -- there is no schedule to fall back on if HRM has not
    shown one yet.

    Refuses to estimate from fewer than `NEXT_SOURCE_ESTIMATE_MIN_INTERVALS`
    observed intervals (see that constant's comment for why two, and why not
    higher) -- with one interval or none, `known` is False and `reason`
    explains why in terms a viewer can tell apart from a broken feature: not
    enough of HRM's own publishing history has been observed yet, not "check
    back later, something is wrong here."

    With enough history, the estimate is the last observed publish plus the
    *median* observed interval (not the mean, which one unusually long or
    short gap could distort) -- kept deliberately simple, and labelled
    `estimated_next` rather than anything implying a promise.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    intervals = [entry["interval"] for entry in history if entry["interval"] is not None]
    if len(intervals) < NEXT_SOURCE_ESTIMATE_MIN_INTERVALS:
        return {
            "known": False,
            "estimated_next": None,
            "passed": False,
            "median_interval_days": None,
            "observed_intervals": len(intervals),
            "reason": (
                f"fewer than {NEXT_SOURCE_ESTIMATE_MIN_INTERVALS} observed intervals "
                "between HRM's past publishes -- not enough history yet to estimate "
                "when it will publish next"
            ),
        }
    median = _median_timedelta(intervals)
    last_publish = history[-1]["source_last_edit"]
    estimated_next = last_publish + median
    return {
        "known": True,
        "estimated_next": _iso_utc(estimated_next),
        # The estimate is behind us and nothing newer has been observed: HRM is
        # later than its typical gap, or this mirror has not looked since. A
        # client must not word it as upcoming (same rule as `next_update`'s
        # `due_passed`, and the same boundary: the instant itself is not yet
        # past).
        "passed": now > estimated_next,
        "median_interval_days": round(median.total_seconds() / 86400, 1),
        "observed_intervals": len(intervals),
        "reason": None,
    }


def _freshness_payload(freshness, now=None, history=None):
    """The `GET /api/freshness` JSON body: a thin reshaping of
    `mirror.status.mirror_freshness`'s dict (every field except
    `stale_call_warning`, `behind_reason` and `next_source_estimate` is read
    off it, not recomputed), with every timestamp normalized through
    `_iso_utc`.

    `history` (task 7.2b) is `sync.source_edit_history`'s list for the data
    layer -- a separate query the route below issues, since `mirror_freshness`
    does not carry it. Defaults to `[]` (reads as "not yet known", the same
    honest state as no sync history at all) so every existing caller of this
    function keeps working unchanged.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    next_update = _next_update_state(freshness, now=now)
    return {
        "checked_at": _iso_utc(now),
        "most_recent_call_date": _iso_utc(freshness["most_recent_call_date"]),
        "last_success_at": _iso_utc(freshness["last_success_at"]),
        "last_attempt_at": _iso_utc(freshness["last_attempt_at"]),
        "next_due_at": _iso_utc(freshness["next_due_at"]),
        "behind_source": freshness["behind_source"],
        "behind_reason": _behind_reason(freshness, sync_overdue=next_update["overdue"]),
        "stale_call_warning": _call_staleness(freshness["most_recent_call_date"], now=now),
        "next_update": next_update,
        "next_source_estimate": _next_source_estimate(history or [], now=now),
    }


# ------------------------------------------------------------------- export (7.7)


# Task 7.7's own copy of the six interpretation limits 7.5 added to
# `web/app/index.html`'s footer ("Read these numbers carefully",
# `web/app/index.html:503`-`:528`) -- intake clock, tow as the only recorded
# outcome, vehicle identity as a floor, text-address reduction, derived block
# labels and block-wide dwelling rate. There is no shared Python-side source
# of truth for that wording today: `index.html` is a static file with no
# templating step (design.md M12), so the six texts live only as hardcoded
# HTML there, and adding a templating step to a page that deliberately has
# none is a bigger lift than this task asks for. This constant is therefore a
# second copy, not a shared import. **This creates two places to keep in
# sync**: a wording change to any of the six `<li id="lim-...">` entries in
# `index.html` must be mirrored here by hand, and nothing enforces that
# automatically -- `tests/test_app.py` only pins that today's wording matches
# on both sides, not that a future edit to one is caught on the other.
_INTERPRETATION_LIMITS = [
    {
        "id": "lim-intake",
        "title": "Dates are intake, not condition.",
        "text": (
            'Every "still calling" count and every recency or recurrence '
            "window is measured from the date HRM logged the call, not from "
            "when the underlying condition began or ended. A doorway can "
            "read as active weeks after whatever was blocking it is gone, or "
            "as quiet simply because nobody has called it in yet."
        ),
    },
    {
        "id": "lim-tow",
        "title": "Tow is the only recorded outcome.",
        "text": (
            "HRM publishes no ticketing field. A call with no tow is a call "
            "with no recorded outcome, not proof that nothing happened. The "
            "tow comparison itself is observational, not a randomized trial: "
            "tows may cluster at the addresses already calling the most, "
            "which could mask a real effect in either direction."
        ),
    },
    {
        "id": "lim-vehicle",
        "title": "Vehicle identity is a floor.",
        "text": (
            "A vehicle is recorded as make, model and colour, not a plate, "
            "so two identical cars at the same address count as one. The "
            "unique-vehicle share this page shows can only ever be an "
            "undercount of the true number of distinct cars, never an "
            "overcount."
        ),
    },
    {
        "id": "lim-address",
        "title": "Addresses are matched as text, not geocoded.",
        "text": (
            "The same physical doorway logged two different ways by HRM "
            "becomes two rows here; two different doorways logged "
            "identically become one. Counts of doorways, and anything built "
            "on top of them, inherit that split-or-merge risk."
        ),
    },
    {
        "id": "lim-block-label",
        "title": "A block's street label is derived, not official.",
        "text": (
            "It names whichever street the calling doorways on this list "
            "happen to share -- not a boundary HRM, the census or the city "
            "assigns. Two blocks that look identically labelled are not "
            "guaranteed to be the same official area, and the reverse."
        ),
    },
    {
        "id": "lim-dwelling",
        "title": "The dwelling rate is block-wide.",
        "text": (
            "A block's dwelling count, and any per-1,000-dwellings rate "
            "built from it, covers every dwelling in the whole census block "
            "-- not just the street frontage the calls actually sit on. A "
            "block that wraps two streets counts dwellings from both against "
            "calls logged on only one."
        ),
    },
]


def _active_filter_bits(filters):
    """Task 7.7's Python equivalent of `web/app/index.html`'s
    `activeFilterBits()` (task 5.5d) -- which of the five filters
    `_filters_from_request()` reads differ from `_FILTER_DEFAULTS`, phrased
    identically to the client-side function (same wording, same unicode
    `≥`/`≤`) so an export and the page it was exported from state a
    filter departure in the same words. Not imported from the JS -- nothing
    here executes JavaScript -- kept in step by `tests/test_app.py` pinning
    the same literal phrases on both sides.
    """
    bits = []
    if filters.get("district"):
        bits.append(f"district {filters['district']}")
    if filters["min_calls"] != _FILTER_DEFAULTS["min_calls"]:
        bits.append(f"min. recent calls ≥ {filters['min_calls']}")
    if filters["min_doorways"] != _FILTER_DEFAULTS["min_doorways"]:
        bits.append(f"min. calling doorways/block ≥ {filters['min_doorways']}")
    if filters["recency_days"] != _FILTER_DEFAULTS["recency_days"]:
        bits.append(f"recency window ≤ {filters['recency_days']} days")
    if filters["recur_days"] != _FILTER_DEFAULTS["recur_days"]:
        bits.append(f"recurrence window ≤ {filters['recur_days']} days")
    return bits


def _active_filter_summary(filters):
    """`", ".join(_active_filter_bits(filters))` -- `""` when every filter is
    at its default, the same "nothing to say" convention
    `activeFilterSummary()` uses client-side (5.5d)."""
    return ", ".join(_active_filter_bits(filters))


def _recency_note(filters, latest):
    """The same recency-window sentence task 7.4 states on every filtered
    list (`web/app/index.html`'s `#recency-note`, filled by `render()`): the
    window's length and the true anchor date it runs back from. `""` when
    `latest` (already formatted `%Y-%m-%d`, or `None`) is unknown -- no calls
    at all for this type -- matching the page's own guard.
    """
    if not latest:
        return ""
    return f"Showing calls from the last {filters['recency_days']} days, since {latest}."


def _tow_thesis_sentence(figures_result):
    """The same sentence `web/app/index.html`'s `#tow-thesis` IIFE renders
    (tasks 5.8/7.5), reproduced here so a brief or table export states the
    identical tow-effect claim a viewer of the served page would read --
    built from `mirror.type_figures.figures_for`'s stored
    `tow.conclusion`/`tow.caveat` fragments, never re-derived from the raw
    bootstrap numbers (`per_type.py` is out of scope to edit or reimplement
    for this change, same as it is for that route).
    """
    if not figures_result.get("available"):
        reason = figures_result.get("reason")
        return (
            "Tow-effect comparison for this type is not yet available"
            + (f" ({reason})." if reason else ".")
        )
    tow = (figures_result.get("figures") or {}).get("tow") or {}
    conclusion = tow.get("conclusion")
    if not conclusion:
        return ("Tow-effect comparison for this type is not yet available "
                "(no stored conclusion for the tow comparison).")
    sample_too_small = tow.get("conclusion_key") == "sample_too_small"
    towed_pct = (tow.get("towed") or {}).get("recurrence_pct")
    not_towed_pct = (tow.get("not_towed") or {}).get("recurrence_pct")
    pct_clause = (
        f" ({towed_pct:.1f} per cent against {not_towed_pct:.1f} per cent)"
        if not sample_too_small and towed_pct is not None and not_towed_pct is not None
        else ""
    )
    caveat = tow.get("caveat")
    caveat_clause = f" {caveat[0].upper()}{caveat[1:]}." if not sample_too_small and caveat else ""
    return f"{conclusion[0].upper()}{conclusion[1:]}{pct_clause}.{caveat_clause}"


def _pin_snapshot(conn):
    """Make every read on `conn` from here to its close see one mirror state.

    An export names a mirror state (the clocks) next to the rows it lists, and
    reads them in separate queries. Under Postgres' default READ COMMITTED each
    query sees whatever is committed when it runs, so a sync committing between
    the rows read and the clocks read left an export whose clocks named a later
    sync than its rows came from. One REPEATABLE READ transaction takes its
    snapshot at the first query and keeps it, so rows, clocks and figures all come
    from the same one. `READ ONLY` states that an export writes nothing.

    Must be the first thing run on the connection after `mirror.db.connect()`
    (whose own `SET search_path` does not take a snapshot); Postgres rejects the
    change once any query has run.
    """
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")


def _freshness_for_export(conn):
    """The same clocks `GET /api/freshness` serves (`freshness()` below),
    read the identical way -- `mirror.status.mirror_freshness` plus
    `sync.source_edit_history` fed through `_freshness_payload` -- so an
    export's clocks and the live freshness banner can never disagree about
    what a lag means. Calls `_freshness_payload`/`status.mirror_freshness` as
    a black box; nothing here recomputes any of `_call_staleness`/
    `_behind_reason`/`_next_update_state`/`_next_source_estimate`.
    """
    fresh = status.mirror_freshness(conn)
    history = sync.source_edit_history(conn, status.DATA_LAYER)
    return _freshness_payload(fresh, history=history)


# Readable column headers for the CSV/brief tables, in the abbreviated keys
# `_doorway_payload`/`_block_payload` already produce -- reusing those
# functions (rather than re-reading `result["rows"]`/`result["blocks"]`
# directly) means a CSV/brief cell and the JSON API's own field describe the
# exact same computed value, never a second rounding or title-casing of it.
_DOORWAY_CSV_COLUMNS = [
    ("a", "Address"), ("d", "District"), ("c", "Community"), ("st", "Street"),
    ("bk", "Block"), ("nb", "Doorways Calling On Block"), ("m", "Calls (12mo)"),
    ("t", "Calls (Total)"), ("rp", "Repeat Calls"), ("w", "Tows"),
    ("vd", "Vehicles Distinct"), ("vs", "Vehicles Seen"),
    ("g", "Median Gap Days"), ("l", "Last Call"), ("o", "Owner"),
    ("lat", "Latitude"), ("lon", "Longitude"),
]

_BLOCK_CSV_COLUMNS = [
    ("bk", "Block"), ("s", "Streets"), ("n", "Doorways"), ("m", "Calls (12mo)"),
    ("t", "Calls (Total)"), ("w", "Tows"), ("dw", "Dwellings"),
    ("r", "Calls per 1,000 Dwellings"), ("d", "District"),
    ("worst", "Worst Doorway"), ("addrs", "Addresses"),
]


def _write_csv_table(writer, columns, payload_rows, type_name):
    """One section of the CSV export: a header row of readable column names,
    then one row per item in `payload_rows` (already `_doorway_payload`/
    `_block_payload`-shaped).

    Task 7.7a: a final "Violation Type" column repeats `type_name` on every
    row. The preamble already names the type once, but a table copied out of
    the file (into a spreadsheet already holding another type's rows, say)
    leaves the preamble behind -- and the download's filename is not part of
    the content -- so each row states the type it belongs to itself.
    """
    writer.writerow([label for _, label in columns] + ["Violation Type"])
    for row in payload_rows:
        writer.writerow([
            "; ".join(row[key]) if key == "addrs" else row.get(key)
            for key, _ in columns
        ] + [type_name])


def _export_csv_text(canonical, slug, filters, result, freshness_data, figures_result):
    """Task 7.7's "complete table": one CSV document carrying every one of
    the task's own verify-clause requirements -- every listed doorway and
    block row (`result["rows"]`/`result["blocks"]`, the identical
    `derive_recency.derive_with_recency` output `doorways()`/`blocks()`
    already read, so a row here and a row in the JSON API can never
    disagree), the canonical type it covers stated plainly (not left to the
    URL or a filename to imply), the same clocks `GET /api/freshness` serves,
    the filter values in effect, and the same six interpretation limits the
    served page footnotes.

    Shaped as a metadata preamble (`key,value` pairs) followed by a blank row
    and two data tables ("DOORWAYS", then "BLOCKS") -- a single file stays
    self-contained (a distributed CSV need not travel alongside the HTML
    brief to carry all of the above), at the cost of not being one bare
    rectangular table a naive `pandas.read_csv()` would parse unmodified; a
    person opening this in a spreadsheet, the stated audience for "a
    complete table", reads the preamble as a small labelled block above the
    real table without issue.
    """
    buf = io.StringIO()
    w = csv.writer(buf)

    latest = result["latest"].strftime("%Y-%m-%d") if result["latest"] else None

    w.writerow(["violation_type", canonical])
    w.writerow(["slug", slug])
    w.writerow(["doorway_count", len(result["rows"])])
    w.writerow(["block_count", len(result["blocks"])])
    w.writerow(["latest_call_date", latest])
    w.writerow(["recency_note", _recency_note(filters, latest)])
    w.writerow([])

    w.writerow(["filter: district", filters["district"]])
    w.writerow(["filter: min_calls", filters["min_calls"]])
    w.writerow(["filter: min_doorways", filters["min_doorways"]])
    w.writerow(["filter: recur_days", filters["recur_days"]])
    w.writerow(["filter: recency_days", filters["recency_days"]])
    bits = _active_filter_bits(filters)
    w.writerow(["filters_in_effect",
                f"Filtered by {', '.join(bits)}." if bits
                else "Default filters -- none active."])
    w.writerow([])

    w.writerow(["checked_at", freshness_data["checked_at"]])
    w.writerow(["most_recent_call_date", freshness_data["most_recent_call_date"]])
    w.writerow(["last_success_at", freshness_data["last_success_at"]])
    w.writerow(["last_attempt_at", freshness_data["last_attempt_at"]])
    w.writerow(["next_due_at", freshness_data["next_due_at"]])
    w.writerow(["behind_source", freshness_data["behind_source"]])
    w.writerow(["behind_reason", freshness_data["behind_reason"]])
    w.writerow(["stale_call_warning",
                freshness_data["stale_call_warning"]["reason"]
                or "the most recent call is not stale"])
    nu = freshness_data["next_update"]
    w.writerow(["next_update",
                nu["reason"] if nu["overdue"]
                else ("not yet known" if not nu["known"] else "on schedule")])
    w.writerow([])

    w.writerow(["tow_effect", _tow_thesis_sentence(figures_result)])
    w.writerow([])

    for limit in _INTERPRETATION_LIMITS:
        w.writerow([f"limit: {limit['id']}", f"{limit['title']} {limit['text']}"])
    w.writerow([])

    w.writerow(["DOORWAYS"])
    _write_csv_table(w, _DOORWAY_CSV_COLUMNS, _doorway_payload(result["rows"]), canonical)
    w.writerow([])
    w.writerow(["BLOCKS"])
    _write_csv_table(w, _BLOCK_CSV_COLUMNS, _block_payload(result["blocks"]), canonical)

    return buf.getvalue()


_EXPORT_BRIEF_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ type }} export brief</title>
<style>
  body{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       max-width:920px;margin:32px auto;padding:0 20px;color:#1a1a1a}
  a{color:#0b5fa5}
  table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
  th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}
  th{background:#f0f0f0}
  h1{margin-bottom:4px}
  .meta{color:#444}
  code{background:#f0f0f0;padding:0 4px;border-radius:3px}
  ol.limits li{margin-bottom:10px}
</style>
</head>
<body>
<h1>{{ type }}</h1>
<p class="meta">Doorway and block list export for the violation type
<b>{{ type }}</b> (slug <code>{{ slug }}</code>). Generated {{ freshness.checked_at }}.<br>
<a href="/types/{{ slug }}">Open the live view</a> &middot;
<a href="/api/types/{{ slug }}/export.csv{{ query_string }}">Download the complete table (CSV)</a></p>

<h2>Filters in effect</h2>
{% if filter_bits %}
<p>Filtered by {{ filter_bits|join(", ") }}.</p>
{% else %}
<p>Default filters -- none active.</p>
{% endif %}
<ul>
  <li>District: {{ filters.district or "every district" }}</li>
  <li>Minimum recent calls per doorway: {{ filters.min_calls }}</li>
  <li>Minimum still-calling doorways per block: {{ filters.min_doorways }}</li>
  <li>Recurrence window: {{ filters.recur_days }} days</li>
  <li>Recency window: {{ filters.recency_days }} days</li>
</ul>
{% if recency_note %}<p>{{ recency_note }}</p>{% endif %}

<h2>Freshness</h2>
<ul>
  <li>Checked at: {{ freshness.checked_at }}</li>
  <li>Most recent call in the data: {{ freshness.most_recent_call_date or "unknown" }}</li>
  <li>Last successful sync: {{ freshness.last_success_at or "never" }}</li>
  <li>Next update due: {{ freshness.next_due_at or "unknown" }}</li>
  <li>Behind source: {{ freshness.behind_source }}{% if freshness.behind_reason %} -- {{ freshness.behind_reason }}{% endif %}</li>
  {% if freshness.stale_call_warning.stale %}<li><b>Stale-call warning:</b> {{ freshness.stale_call_warning.reason }}</li>{% endif %}
  {% if freshness.next_update.overdue %}<li><b>Overdue:</b> {{ freshness.next_update.reason }}</li>{% endif %}
</ul>

<h2>Tow effect</h2>
<p>{{ tow_sentence }}</p>

<h2 id="read-carefully">Read these numbers carefully</h2>
<ol class="limits">
{% for limit in limits %}  <li id="{{ limit.id }}"><b>{{ limit.title }}</b> {{ limit.text }}</li>
{% endfor %}</ol>

{# Task 7.8: a value the view lacks (a null median gap, calls-per-1k rate, ...)
   is an en dash here -- the page's own convention -- and an empty cell in the
   CSV; never Jinja's default rendering of None, the word "None". #}
<h2>Doorways ({{ doorway_count }})</h2>
{% if doorways %}
<table>
<caption>{{ type }} - doorways</caption>
<tr>{% for _, label in doorway_columns %}<th>{{ label }}</th>{% endfor %}</tr>
{% for row in doorways %}<tr>{% for key, _ in doorway_columns %}<td>{{ row[key]|join("; ") if key == "addrs" else ("&ndash;"|safe if row[key] is none else row[key]) }}</td>{% endfor %}</tr>
{% endfor %}</table>
{% else %}
<p>No doorways match {{ filter_summary_sentence }}.</p>
{% endif %}

<h2>Blocks ({{ block_count }})</h2>
{% if blocks %}
<table>
<caption>{{ type }} - blocks</caption>
<tr>{% for _, label in block_columns %}<th>{{ label }}</th>{% endfor %}</tr>
{% for row in blocks %}<tr>{% for key, _ in block_columns %}<td>{{ row[key]|join("; ") if key == "addrs" else ("&ndash;"|safe if row[key] is none else row[key]) }}</td>{% endfor %}</tr>
{% endfor %}</table>
{% else %}
<p>No blocks match {{ filter_summary_sentence }}.</p>
{% endif %}

</body>
</html>
"""


# --------------------------------------------------------------- app factory


def create_app(conn_factory=None):
    """The Flask application. `conn_factory`, if given, replaces `mirror.db.connect`
    -- the hook `tests/test_app.py` uses to point every request at a throwaway
    schema via Flask's test client, the same way `tests/conftest.py`'s `clean_db`
    fixture does for the derivation tests, without a real network listener.

    A connection is opened and closed within each request; nothing is held across
    requests. Every list is computed per request (design.md M11) -- there is no
    cache here for 5.5 to have to work around.

    With no `conn_factory` the database configuration is checked here, so a
    deployment missing a required `PG*` variable fails while the worker boots
    instead of answering every route with a 500. A supplied factory takes
    responsibility for its own connections and skips the check.
    """
    if conn_factory is None:
        db.check_configured()
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
        # by filter -- every route in this module reads it from the one
        # committed file `web/map-network.json` (`src/hotspots.py --network`, an
        # option since removed, last wrote it), so there is nothing per-request
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
        """Tasks 7.1/7.3/7.4a/7.2/7.2a/7.2b: the four clocks design.md M4 names, plus
        7.4a's stale-call warning, 7.2's `behind_reason` attribution, 7.2a's
        `next_update` overdue state and 7.2b's `next_source_estimate` -- see
        this module's docstring and `_freshness_payload`'s for the shape and
        the timezone-normalization rationale. `web/app/index.html`'s header
        banner and footer read this on every view; no route parameter, no
        per-type distinction -- the mirror has one set of these clocks
        regardless of which canonical type a viewer is looking at.
        """
        conn = connect()
        try:
            fresh = status.mirror_freshness(conn)
            history = sync.source_edit_history(conn, status.DATA_LAYER)
        finally:
            conn.close()
        return jsonify(_freshness_payload(fresh, history=history))

    @app.get("/api/types/<slug>/export.csv")
    def export_csv(slug):
        """Task 7.7's "complete table": see `_export_csv_text`'s docstring for
        what one file carries and why it is shaped as a metadata preamble
        plus two data tables rather than a single bare rectangular list.
        Same five filters as `doorways()`/`blocks()` above
        (`_filters_from_request()`), and the same 404 behaviour
        (`jsonify(error="untracked", ...)`) as every other `/api/...` route
        in this module -- this is one of those, not a `/types/...` page
        route.
        """
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        filters = _filters_from_request()
        conn = connect()
        try:
            _pin_snapshot(conn)
            result = derive_recency.derive_with_recency(
                conn, canonical_type=canonical, **filters,
            )
            freshness_data = _freshness_for_export(conn)
            figures_result = type_figures.figures_for(conn, canonical)
        finally:
            conn.close()
        csv_text = _export_csv_text(
            canonical, slug, filters, result, freshness_data, figures_result,
        )
        return Response(
            csv_text, mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}-export.csv"',
            },
        )

    @app.get("/types/<slug>/export")
    def export_brief(slug):
        """Task 7.7's "readable brief": a server-rendered HTML page --
        `_untracked_type_page`'s own `render_template_string` pattern (design.md
        M12 rules out a build step, so there is no per-type file to generate)
        rather than a downloadable file. Carries the same six things
        `export_csv` above does: every listed doorway and block row for the
        type/filter combination in effect, the canonical type stated plainly,
        the same clocks `GET /api/freshness` serves, the filter values in
        effect, the same tow-effect sentence the served page's `#tow-thesis`
        shows, and the same six interpretation limits from its footer. Same
        filters (`_filters_from_request()`), and the same 404 behaviour as
        `/types/<slug>` (`_untracked_type_page`), since this is a page route,
        not an `/api/...` one.
        """
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return _untracked_type_page(slug), 404
        filters = _filters_from_request()
        conn = connect()
        try:
            _pin_snapshot(conn)
            result = derive_recency.derive_with_recency(
                conn, canonical_type=canonical, **filters,
            )
            freshness_data = _freshness_for_export(conn)
            figures_result = type_figures.figures_for(conn, canonical)
        finally:
            conn.close()

        latest = result["latest"].strftime("%Y-%m-%d") if result["latest"] else None
        doorway_rows = _doorway_payload(result["rows"])
        block_rows = _block_payload(result["blocks"])
        qs = request.query_string.decode()
        return render_template_string(
            _EXPORT_BRIEF_TEMPLATE,
            type=canonical, slug=slug, filters=filters,
            filter_bits=_active_filter_bits(filters),
            filter_summary_sentence=(_active_filter_summary(filters) or "the current filters"),
            recency_note=_recency_note(filters, latest),
            freshness=freshness_data,
            tow_sentence=_tow_thesis_sentence(figures_result),
            limits=_INTERPRETATION_LIMITS,
            doorway_count=len(doorway_rows), block_count=len(block_rows),
            doorways=doorway_rows, blocks=block_rows,
            doorway_columns=_DOORWAY_CSV_COLUMNS, block_columns=_BLOCK_CSV_COLUMNS,
            query_string=(f"?{qs}" if qs else ""),
        )

    return app


def run():
    """Dev entry point: `python3 src/app/server.py`. Not what production runs --
    that is gunicorn against the repository-root `wsgi.py` -- but useful for a
    quick local check without another dependency in the loop. Reads `PORT` and
    the `PG*` database variables from the environment (or a local `.env`), same
    as production; binds every interface (0.0.0.0) rather than naming a host,
    per design.md M12.
    """
    app = create_app()
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
