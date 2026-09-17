"""The served application (task 5.1, `mirror-hrm-data-and-host-app`, design.md M5/M12).

A new package, deliberately separate from `mirror/`: this is the HTTP layer --
routing, request/response shaping, static asset serving -- built on top of
`mirror.derive`, `mirror.db` and `mirror.violation_types`, none of which it
edits. `mirror/` stays the sync/derive/store layer another change track owns
concurrently; `app/` is where a request becomes a response.

See `app.server` for the app factory and the route table.
"""
