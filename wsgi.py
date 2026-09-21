"""Production entry point for the served application (task 5.1).

    PORT=8000 venv/bin/gunicorn wsgi:app --bind 0.0.0.0:$PORT

`gunicorn` imports this file as a module and looks up the `app` attribute --
the ordinary WSGI-callable pattern, not the `module:factory()` expression
form, so no extra gunicorn flag is needed. `PORT` is read here only to build
the bind address gunicorn is told to use in the command above; gunicorn itself
does not read `PORT`. The database comes from the `PG*` variables (`PGHOST`,
`PGUSER`, `PGDATABASE`, and optionally `PGPORT` and `PGPASSWORD`), set in the
environment or in a local `.env` (see `.env.example`); `mirror.db.connect` reads
them. `create_app()` below checks them, so a missing one stops the worker booting
instead of failing the first request. Nothing in this file names a host
(design.md M12).

For a quick local check without gunicorn in the loop, `python3 src/app/server.py`
runs the Flask development server against the same `PORT` and `PG*` variables.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app.server import create_app  # noqa: E402

app = create_app()
