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
  GET  /map-network.json                the street network, as a static asset
                                         served from its own URL (490 KB, one
                                         file for every type; cached for a day
                                         with a conditional GET past that, per
                                         task 5.4 -- see the route below)
  GET  /api/types/<slug>/doorways       a type's doorway list, `{type, slug,
                                         count, latest, summary, rows}`
  GET  /api/types/<slug>/blocks         a type's block list, `{type, slug,
                                         count, blocks}`

Every list is computed per request from the mirror via `mirror.derive.derive`
(design.md M11) -- nothing here materializes a doorway or block list ahead of a
request.

**Why the route already looks like `/api/types/<slug>/...` when only one type
answers.** Task 5.2 routes every canonical type this way; building that switch
is explicitly out of scope for 5.1. `_SLUG_TO_CANONICAL` below already maps
every tracked type's slug to its canonical name -- computed once, from
`violation_types.CANONICAL_TYPES`, the same way 5.2 will need it -- but
`_canonical_for_slug` deliberately answers only `DEFAULT_SLUG` for now. Turning
this into full per-type routing is a one-line change to that function
(`_SLUG_TO_CANONICAL.get(slug)` in place of the `if slug != DEFAULT_SLUG`
guard), not a reshape of the route table, the payload shape or the page.

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

from flask import Flask, jsonify, send_from_directory  # noqa: E402

from mirror import db, derive, violation_types  # noqa: E402

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
    not resolve. Deliberately answers only `DEFAULT_SLUG` today -- see the
    module docstring's note on 5.2.
    """
    if slug != DEFAULT_SLUG:
        return None
    return DEFAULT_CANONICAL_TYPE


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

    @app.get("/api/types/<slug>/doorways")
    def doorways(slug):
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        conn = connect()
        try:
            result = derive.derive(conn, canonical_type=canonical)
        finally:
            conn.close()
        return jsonify(
            type=canonical, slug=slug, count=len(result["rows"]),
            latest=result["latest"].strftime("%Y-%m-%d") if result["latest"] else None,
            summary=_summary(result),
            rows=_doorway_payload(result["rows"]),
        )

    @app.get("/api/types/<slug>/blocks")
    def blocks(slug):
        canonical = _canonical_for_slug(slug)
        if canonical is None:
            return jsonify(error="untracked", slug=slug), 404
        conn = connect()
        try:
            result = derive.derive(conn, canonical_type=canonical)
        finally:
            conn.close()
        return jsonify(
            type=canonical, slug=slug, count=len(result["blocks"]),
            blocks=_block_payload(result["blocks"]),
        )

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
