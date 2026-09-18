"""Persist a triage decision server-side, and read them back. Task 6.1 of
`mirror-hrm-data-and-host-app`.

`design.md` M6: "Decisions move to the store behind the application. All
viewers read and write the same rows." Before this task the only
implementation of shared triage was `window.claude.use("db")` in
`web/template.html`, which resolves to `null` off a Claude Artifact host and
silently degrades every viewer to a private `localStorage` copy
(`proposal.md`'s framing of the defect this task exists to end). This module
is the replacement: a decision recorded through `record()` lands in the
`triage_decisions` table `schema.sql` already defines (task 1.5), and every
caller of `list_for()` -- any viewer, any process -- reads the same rows back.
`src/app/server.py` is the only caller today, through the
`/api/types/<slug>/decisions` routes.

Scope (this task's scope note): one row per `(violation_type, scope,
item_key)`, the table's UNIQUE constraint. `scope` is `'doorway'` or
`'block'` (the table's CHECK constraint, mirrored in `VALID_SCOPES` below so a
bad value is rejected before it reaches the database). Every read and write
here is scoped by `violation_type`, so a decision recorded against one type's
list is invisible to another type's list -- there is no query in this module
that can return a row for a type it was not asked for.

`role` (task 6.6): `web/app/index.html`'s `ensureRole()` asks a viewer to pick
one before their first decision is ever sent here, and every request from
that page since carries it in the POST body. `schema.sql` makes the column
`NOT NULL`, though, so a request that omits `role` outright -- an older
client, or a bare API call made by hand -- still needs something written.
`PLACEHOLDER_ROLE` below is that fallback, not the normal case: `record()`
only reaches for it when the caller passed no role at all.
"""

VALID_SCOPES = ("doorway", "block")

# The fallback `record()` writes when a caller supplies no role (task 6.6
# asks the served page's own viewers for one before they ever get here, so
# this is reached only by a request that skips that prompt entirely) --
# picked to read obviously as a placeholder rather than as a real role, so
# nobody mistakes an unattributed row for a viewer who declined to identify
# a role that was in fact asked for.
PLACEHOLDER_ROLE = "unspecified"


def _row_to_dict(row):
    scope, item_key, decision, note, role, updated_at = row
    return {
        "scope": scope,
        "item_key": item_key,
        "decision": decision,
        "note": note or "",
        "role": role,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def list_for(conn, violation_type):
    """Every decision recorded for one violation type, doorway and block
    together -- the shape `GET /api/types/<slug>/decisions` hands back.
    Scoped by `violation_type` so a viewer of one type's list is never shown
    another type's decisions (this task's scope note). Read-only: opens no
    transaction of its own beyond the implicit one the connection is already
    in.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT scope, item_key, decision, note, role, updated_at "
            "FROM triage_decisions WHERE violation_type = %s "
            "ORDER BY scope, item_key",
            (violation_type,),
        )
        rows = cur.fetchall()
    return [_row_to_dict(row) for row in rows]


def record(conn, violation_type, scope, item_key, decision, note=None, role=None):
    """Persist one decision, upserting on the table's
    `(violation_type, scope, item_key)` UNIQUE constraint -- a second call for
    the same triple updates the existing row (decision, note, role and
    `updated_at`) in place rather than colliding or duplicating. Returns the
    row as `list_for()`'s entries are shaped, read back with `RETURNING` so
    the caller gets the value Postgres actually stored (in particular
    `updated_at`, which the database sets) rather than one it merely sent.

    Raises `ValueError` for a `scope` outside `VALID_SCOPES` or a missing
    `item_key`/`decision`, so a caller such as `server.py`'s route can turn
    that into a 400 before it becomes a database round trip that could only
    fail the table's own CHECK constraint.

    Does not commit or roll back -- that is the caller's decision, made once
    per request rather than once per row, and it matters beyond style here:
    a write this function raises out of must reach the caller as a real
    exception rather than being swallowed, or task 6.4 ("state plainly when a
    decision could not be persisted") would have no failure to surface later.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be one of {VALID_SCOPES}, got {scope!r}")
    if not item_key or not decision:
        raise ValueError("item_key and decision are required")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO triage_decisions
                (violation_type, scope, item_key, decision, note, role)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (violation_type, scope, item_key) DO UPDATE SET
                decision = EXCLUDED.decision,
                note = EXCLUDED.note,
                role = EXCLUDED.role,
                updated_at = now()
            RETURNING scope, item_key, decision, note, role, updated_at
            """,
            (violation_type, scope, item_key, decision, note or None,
             role or PLACEHOLDER_ROLE),
        )
        row = cur.fetchone()
    return _row_to_dict(row)
