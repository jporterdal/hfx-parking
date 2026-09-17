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

**Performance stays per-5.5's measurements.** Routing to a different type
does not change what one request computes: it is still exactly one
`derive()` call for exactly one canonical type, the same shape 5.5 measured
(349ms default, 662ms for the largest type, `No Parking Sign`). Nothing here
calls `derive_all()` or loops over `_SLUG_TO_CANONICAL` to answer one
request.

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

from flask import Flask, abort, jsonify, request, send_from_directory  # noqa: E402

from mirror import db, derive, triage, violation_types  # noqa: E402

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

    @app.get("/types/<slug>")
    def type_page(slug):
        """The same page `index()` serves, at a per-type URL (task 5.2). One
        static file answers for every type -- design.md M12 rules out a build
        step, so there is no per-type file to generate -- and
        `web/app/index.html`'s boot script reads `<slug>` back out of
        `location.pathname` (its `SLUG` constant) to decide which type's data
        to fetch, the same script regardless of which type that turns out to
        be. An untracked slug 404s here the same bare way the JSON routes do
        (`_canonical_for_slug`); rendering that as a stated reason instead of
        a bare 404 is task 5.3's job, not this route's -- see the module
        docstring's "explicitly not yours" note.
        """
        if _canonical_for_slug(slug) is None:
            abort(404)
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
        note}` -- `note` optional. Upserts on `triage_decisions`'
        `(violation_type, scope, item_key)` UNIQUE constraint via
        `mirror.triage.record`, so a second call for the same doorway or
        block updates the existing row rather than duplicating it.

        `role` is not read from the request: task 6.6 is what asks a viewer
        for one and threads it through here. Until then `mirror.triage`
        writes its own placeholder -- the column is `NOT NULL`, so something
        has to be written, and writing a defensible placeholder in one place
        (see `mirror.triage.PLACEHOLDER_ROLE`) is that something.

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
