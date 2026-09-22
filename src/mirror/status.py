"""A queryable picture of the mirror's freshness. design.md M4: "the mirror can lie."

No HTTP lives here — serving is section 5's job. This module is the read-only
arithmetic underneath it: the four clocks a viewer needs to tell a stalled sync from
a source that simply has not published (3.3, 3.4), and the attempt/success
distinction that makes a sync failing repeatedly visible rather than merely stale
(3.2). Everything here reads `layer_state` and `service_requests`; nothing writes —
`sync.py` is the only thing that changes either.
"""

from mirror import db, sync

# The layer whose rows carry the calls a viewer reads. The "most recent call date"
# clock is about this layer specifically — custom_fields and census_areas carry no
# date of their own that a viewer would recognise as "when was the newest call."
DATA_LAYER = "service_requests"

# The layers whose currency a viewer cares about. Census is excluded from the
# aggregates below: it is static reference data (design.md M3) that has not changed
# in eighteen months, and folding its clock into "is the mirror behind" would read a
# schedule that never fires as staleness.
TRACKED_LAYERS = ("service_requests", "custom_fields", "census_areas")
CURRENCY_LAYERS = ("service_requests", "custom_fields")

# The mirror's readiness (spec: "Publish whether the mirror is ready to be read"). The
# values are what `/api/freshness` serves and what the page branches on.
UNINITIALISED = "uninitialised"
AWAITING_FIRST_LOAD = "awaiting_first_load"
LOADING = "loading"
READY = "ready"

_STATE_COLUMNS = (
    "layer", "source_last_edit", "last_attempt_at", "last_attempt_ok",
    "last_success_at", "next_due_at", "stored_count", "source_count", "static",
    "full_load_completed_at", "watermark",
)


def layer_state_row(conn, layer_key):
    """The raw `layer_state` row for one layer, or `None` if no sync has ever
    touched it — a store just created, or a layer key that does not exist."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_STATE_COLUMNS)} FROM layer_state WHERE layer = %s",
            (layer_key,),
        )
        row = cur.fetchone()
    return dict(zip(_STATE_COLUMNS, row)) if row else None


def mirror_readiness(conn, layers=TRACKED_LAYERS):
    """Is there a mirror to read, and if not, is one being made? design.md D5.

    - `UNINITIALISED`: the structure does not exist, so nothing has ever run here.
    - `READY`: every tracked layer, census included, has completed a load
      (`layer_state.full_load_completed_at`). A later reload in progress does not
      change this: the version held stays whole and queryable until it is swapped.
    - `LOADING` / `AWAITING_FIRST_LOAD`: not ready, told apart by whether another
      session holds the sync lock *right now*.

    The lock, not a `sync_runs` row, decides "loading". A row saying "running" outlives
    a run that was killed, and would report a dead first load as loading for ever; a
    session lock disappears with its session.

    Reads only. Note a poll writes `layer_state` (and its `last_success_at`) before any
    load has finished, which is why readiness is read from `full_load_completed_at` and
    never inferred from the presence of a row or a success time.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"{db.schema()}.layer_state",))
        if cur.fetchone()[0] is None:
            return UNINITIALISED
        cur.execute("SELECT layer FROM layer_state "
                    "WHERE full_load_completed_at IS NOT NULL")
        completed = {row[0] for row in cur.fetchall()}
    if set(layers) <= completed:
        return READY
    return LOADING if db.sync_lock_held(conn) else AWAITING_FIRST_LOAD


def most_recent_call_date(conn):
    """The data clock (3.3): the newest `date_initiated` the mirror holds.

    Distinct from every other clock here on purpose. A source that has stopped
    publishing and a sync that has stopped running both eventually leave this
    looking stale, but for different reasons — this value alone does not say which;
    `behind_source` below is what tells them apart.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT max(date_initiated) FROM {DATA_LAYER}")
        row = cur.fetchone()
    return row[0] if row else None


def layer_freshness(conn, layer_key):
    """The clocks design.md M4 names, for one layer, plus 3.2's attempt/success
    distinction and 3.4's behind-source verdict.

    `last_attempt_at`/`last_attempt_ok` and `last_success_at` are separate columns,
    written separately by `sync.py`: a sync failing repeatedly advances the first on
    every try and leaves the second exactly where it was, which is what makes a
    stuck sync distinguishable from one that is merely between successes.

    `behind_source` compares what the *last poll observed* (`source_last_edit`,
    updated on every poll whether or not it pulled) against what the *last
    successful pull actually retrieved* (`sync.last_pulled_source_edit`). The two
    agree whenever HRM has not published anything new since the last reload — the
    ordinary case — and diverge only in the window between a poll noticing an
    advance and the reload it triggers landing. If a sync then stops running
    entirely, this stays exactly where it was: `source_last_edit` is also not being
    refreshed, so the mirror correctly reports "not behind" from a clock that has
    itself gone stale — which is exactly why `last_attempt_at` next to it matters.
    """
    state = layer_state_row(conn, layer_key)
    if state is None:
        return {
            "layer": layer_key, "known": False, "static": False,
            "behind_source": False,
            "reason": "no sync has ever attempted this layer",
        }
    observed_edit = state["source_last_edit"]
    pulled_edit = sync.last_pulled_source_edit(conn, layer_key)
    behind = bool(
        observed_edit is not None and pulled_edit is not None
        and observed_edit > pulled_edit
    )
    return {
        "layer": layer_key,
        "known": True,
        "static": state["static"],
        "source_last_edit": observed_edit,
        "pulled_source_edit": pulled_edit,
        "last_attempt_at": state["last_attempt_at"],
        "last_attempt_ok": state["last_attempt_ok"],
        "last_success_at": state["last_success_at"],
        "next_due_at": state["next_due_at"],
        "stored_count": state["stored_count"],
        "source_count": state["source_count"],
        "full_load_completed_at": state["full_load_completed_at"],
        "behind_source": behind,
        "behind_reason": (
            "HRM has published since this layer was last pulled" if behind else
            "the source has not published anything this mirror has not already "
            "pulled — a lag here belongs to HRM's publishing schedule, not to this "
            "sync"
        ),
    }


def mirror_freshness(conn, layers=TRACKED_LAYERS):
    """One picture of the whole mirror: every tracked layer's clocks (3.3), plus the
    data clock that belongs to the mirror as a whole rather than to any one layer.

    The aggregate fields below are deliberately conservative rather than optimistic,
    because this module exists to stop a lagging piece from hiding behind a healthy
    one (design.md M4):

    - `last_success_at` is the *earliest* of the currency layers' — the mirror is
      only as fresh as its least-recently-synced layer, not its most.
    - `last_attempt_at` is the *latest* — it answers "is anything still trying,"
      and the most recent attempt of any kind answers that.
    - `next_due_at` is the *earliest* — the mirror needs attention as soon as any
      tracked layer's schedule says so.
    - `behind_source` is true if *any* currency layer is behind. design.md M3
      already treats the two Cityworks layers as one publish event four minutes
      apart, so a viewer does not benefit from knowing which of the pair lags.

    Static layers (census) are reported per-layer but excluded from these
    aggregates: see `CURRENCY_LAYERS`.
    """
    per_layer = {key: layer_freshness(conn, key) for key in layers}
    currency = [per_layer[key] for key in CURRENCY_LAYERS if key in per_layer]
    known = [v for v in currency if v["known"]]
    return {
        "layers": per_layer,
        "most_recent_call_date": most_recent_call_date(conn),
        "behind_source": any(v["behind_source"] for v in known),
        "last_success_at": _extreme(known, "last_success_at", min),
        "last_attempt_at": _extreme(known, "last_attempt_at", max),
        "next_due_at": _extreme(known, "next_due_at", min),
    }


def _extreme(entries, field, fn):
    values = [v[field] for v in entries if v.get(field) is not None]
    return fn(values) if values else None
