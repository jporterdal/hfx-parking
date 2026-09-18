"""Verification tasks 9.3 and 9.7 of `mirror-hrm-data-and-host-app`.

**9.3 -- no 311 call-detail value is read, referenced or implied anywhere in the
delivered system**, told apart from the `311 Online` value of `INITIATED_BY` on the
service requests layer, which is a channel label and legitimately present.

**9.7 -- no served view or export claims an enforcement outcome from `RESOLUTION`,
or an hour-of-day finding from pooled timestamps.**

These are audits, so most of them are absence checks. Two things stop an absence
check from being a check that cannot fail:

* every audit runs against text the product actually serves (the Flask test client
  against a seeded throwaway schema), not against the source that produces it, and
  the seed deliberately contains the very things the checks forbid appearing --
  a request whose channel is `311 Online`, calls carrying `RESOLUTION` values that
  read like enforcement outcomes ("Ticketed", "No violation found"), and calls a
  few minutes either side of Halifax local midnight in both summer (ADT) and
  winter (AST);
* the `RESOLUTION` claim is checked as an *invariance*: rewrite every stored
  `RESOLUTION` and the served output must not change by a byte. Any outcome
  sentence built from the field would break that, however it is worded.

Not verified here, because there is no browser in this suite: what the page's
script renders at runtime. The page is checked as served text and code (comments
stripped), which is what a browser would be handed, not as a rendered DOM.

Existing coverage relied on rather than repeated: `tests/test_scope.py` (three
layers, `311_Call_Details` only in `source.py`'s exclusion record) and
`tests/test_page_claims.py` (page markup pins for tasks 7.6 and 5.6).
"""

import csv
import datetime
import io
import json
import pathlib
import re
import zoneinfo

import pytest

from app import server as app_server
from mirror import load, source, sync
from mirror import type_figures as mirror_type_figures

REPO = pathlib.Path(__file__).resolve().parent.parent
THIS_FILE = pathlib.Path(__file__).resolve()
HALIFAX = zoneinfo.ZoneInfo("America/Halifax")
UTC = datetime.UTC


# ================================================================ 9.3 audit


SCAN_DIRS = ("src", "web", "tests", "docs", "scripts")
SCAN_FILES = ("README.md", "pyproject.toml", "requirements.txt", "wsgi.py", "pitch.html")
SKIP_DIRS = {".git", "openspec", "venv", ".venv", "node_modules", "__pycache__"}

# The delivered system: what runs or is served. Tests, docs and one-off scripts are
# in the audit too, but only the delivered system can fail it by *using* the dataset.
DELIVERED = ("src/", "web/")

# Where the exclusion of the dataset is recorded. A reference anywhere else is
# unclassified, and so a failure.
EXCLUSION_RECORDS = {
    "src/mirror/source.py",
    "tests/test_scope.py",
    "docs/parking-hotspots/data-sources.md",
    "docs/parking-hotspots/decisions.md",
}

_NEGATION = re.compile(
    r"\bnot\b|\bno\b|n/a|exclu|deliberately|\bout\b|never|reject|without", re.I
)
_CHANNEL_CONTEXT = re.compile(r"INITIATED_BY|channel|INTERNAL", re.I)
_CALL_DETAILS = re.compile(r"311[\s_]*call[\s_]*details?", re.I)


def _numeric_coincidence(text, start, end):
    """`311` inside a longer number: a census id, a coordinate, a day count."""
    before, after = text[:start], text[end:]
    if before[-1:].isdigit() or (before[-1:] == "." and before[-2:-1].isdigit()):
        return True
    return bool(after[:1].isdigit() or re.match(r"\.\d", after))


def _text_files():
    roots = [REPO / d for d in SCAN_DIRS] + [REPO / f for f in SCAN_FILES]
    for root in roots:
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = sorted(root.rglob("*"))
        else:
            continue
        for path in candidates:
            if not path.is_file() or SKIP_DIRS & set(path.relative_to(REPO).parts):
                continue
            if path.resolve() == THIS_FILE:
                continue  # this audit necessarily names the strings it forbids
            yield path


def _json_string_hits(path):
    """Strings inside a JSON data file that mention 311 as text. Numbers are
    ignored: `web/map-network.json` holds coordinates and a `,311,` array element."""
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return None
    hits = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            for m in re.finditer("311", node):
                if not _numeric_coincidence(node, m.start(), m.end()):
                    hits.append((key, node))
    walk(data)
    return hits


def audit_311():
    """Every non-numeric occurrence of `311`, as `(relpath, lineno, class, text)`.

    Classes: `a` the `311 Online` channel label; `b` a record of the exclusion
    (in an exclusion-record file, with a negation or past-tense word within three
    lines); `c` anything else -- a read, reference, join, table, layer URL, column,
    field name or claim. Only `c` is a failure.
    """
    found = []
    for path in _text_files():
        rel = path.relative_to(REPO).as_posix()
        if path.suffix == ".json":
            hits = _json_string_hits(path)
            if hits is not None:
                for key, value in hits:
                    cls = "a" if (key == "INITIATED_BY" and value == "311 Online") else "c"
                    found.append((rel, 0, cls, f"{key}: {value}"))
                continue
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines):
            for m in re.finditer("311", line):
                if _numeric_coincidence(line, m.start(), m.end()):
                    continue
                window = "\n".join(lines[max(0, i - 3): i + 4])
                if line[m.start():].startswith("311 Online"):
                    cls = "a" if _CHANNEL_CONTEXT.search(window) else "c"
                elif (_CALL_DETAILS.match(line[m.start():])
                      or line[m.start():].startswith("311_Call_Details")):
                    cls = "b" if (rel in EXCLUSION_RECORDS and _NEGATION.search(window)) else "c"
                else:
                    cls = "c"
                found.append((rel, i + 1, cls, line.strip()[:160]))
    return found


def test_every_311_in_the_repository_is_a_channel_label_or_the_exclusion_record():
    """The 9.3 audit table: classes (a) and (b) are legitimate, (c) is the failure."""
    hits = audit_311()
    assert hits, "the audit found no 311 at all -- it is not scanning what it should"
    bad = [h for h in hits if h[2] == "c"]
    assert not bad, "a 311 reference that is neither the channel label nor the " \
        "exclusion record:\n" + "\n".join(f"{p}:{n}: {t}" for p, n, _c, t in bad)
    # The audit sees both legitimate classes: a scan that saw neither would prove
    # nothing about the classifier.
    assert {h[2] for h in hits} == {"a", "b"}


def test_the_delivered_system_holds_311_only_as_the_exclusion_record():
    """`src/` and `web/` (code, schema, page, served assets) never carry the
    `311 Online` label and never carry a 311 reference of any other kind: the one
    place the dataset is named is `source.py`'s record that it is *not* mirrored."""
    in_delivered = [h for h in audit_311() if h[0].startswith(DELIVERED)]
    assert in_delivered, "expected source.py's exclusion record to be found"
    assert {h[0] for h in in_delivered} == {"src/mirror/source.py"}
    assert {h[2] for h in in_delivered} == {"b"}


# ================================================ 9.3 the layer list and sync


THREE_SERVICES = {
    "Cityworks_Service_Requests",
    "Cityworks_Service_Requests_Custom_Fields",
    "Census_2021_Dissemination_Areas",
}
_FORBIDDEN_LAYER = re.compile(r"311|call[\s_-]*details?", re.I)


def test_no_layer_url_or_table_names_311_call_details():
    for key, layer in source.LAYERS.items():
        for attr in (layer.key, layer.path, layer.table, layer.url, layer.query_url):
            assert not _FORBIDDEN_LAYER.search(attr), (key, attr)
        assert not _FORBIDDEN_LAYER.search(layer.staging().table)
    assert not _FORBIDDEN_LAYER.search(source.BASE)
    # Every layer the sync visits, and every layer the live-source one-shot pipeline
    # could name, is one of the three -- there is no fourth entry point.
    assert set(sync.SYNC_ORDER) == set(source.LAYERS)
    assert set(sync.CITYWORKS) <= set(source.LAYERS)


def test_every_arcgis_service_named_in_code_is_one_of_the_three():
    """Not only `source.LAYERS`: `src/hotspots.py` and `src/mirror/violation_types.py`
    carry their own service URLs, and `scripts/` could too."""
    seen = set()
    for folder in ("src", "scripts"):
        for path in sorted((REPO / folder).rglob("*.py")):
            seen |= set(re.findall(r"([A-Za-z0-9_]+)/FeatureServer", path.read_text()))
    assert seen, "found no FeatureServer URL at all"
    assert seen <= THREE_SERVICES, seen - THREE_SERVICES


def test_the_schema_defines_no_311_table_column_or_view(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() "
            "UNION SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
        names = {row[0] for row in cur.fetchall()}
    assert {"service_requests", "custom_fields", "census_areas", "initiated_by"} <= names
    offenders = sorted(n for n in names if _FORBIDDEN_LAYER.search(n))
    assert not offenders, offenders
    schema_sql = (REPO / "src" / "mirror" / "schema.sql").read_text()
    assert not _FORBIDDEN_LAYER.search(schema_sql)


class UrlRecordingService:
    """Stands in for HRM at the lowest level the code reaches it: `source.post`.

    It answers the poll, the count and the page for the three real layers, records
    the URL of every request, and *raises* on any URL that is not one of the three
    layers' -- so a fourth dataset being requested fails here rather than being
    quietly answered.
    """

    def __init__(self):
        self.urls = []
        self.known = {}
        rows = {
            "service_requests": _features("service_requests_page"),
            "custom_fields": _features("custom_fields_page"),
            "census_areas": _features("census_areas_page"),
        }
        for key, layer in source.LAYERS.items():
            self.known[layer.url] = (key, rows[key], "layer")
            self.known[layer.query_url] = (key, rows[key], "query")

    def post(self, url, body, attempts=4, timeout=180, sleep=None):
        self.urls.append(url)
        if url not in self.known:
            raise AssertionError(f"a request went to a URL that is not a mirrored layer: {url}")
        _key, rows, kind = self.known[url]
        if kind == "layer":
            return {"editingInfo": {"lastEditDate": 1_757_000_000_000}}
        if body.get("returnCountOnly") == "true":
            return {"count": len(rows)}
        start = int(body["resultOffset"])
        size = int(body["resultRecordCount"])
        return {"features": rows[start:start + size]}


def _features(name):
    return json.loads((REPO / "tests" / "fixtures" / f"{name}.json").read_text())["features"]


def test_the_url_recording_stub_refuses_a_call_details_url(monkeypatch):
    """The guard is live: without this, an empty request list would prove nothing."""
    service = UrlRecordingService()
    monkeypatch.setattr(source, "post", service.post)
    with pytest.raises(AssertionError, match="not a mirrored layer"):
        source.post(source.BASE + "/311_Call_Details/FeatureServer/0/query", {})
    assert service.urls == [source.BASE + "/311_Call_Details/FeatureServer/0/query"]


@pytest.mark.db
def test_a_full_sync_requests_only_the_three_mirrored_layers(clean_db, monkeypatch):
    """An initial load of each layer, a forced pull of all of them and a poll-only
    pass, at the URL level: whatever the sync layer asks HRM for is one of the
    three layers' URLs and never a 311 dataset's."""
    service = UrlRecordingService()
    monkeypatch.setattr(source, "post", service.post)

    for key in ("service_requests", "custom_fields", "census_areas"):
        load.load_layer(clean_db, source.LAYERS[key], log=lambda m: None)
    sync.sync(clean_db, force_pull=True, log=lambda m: None)
    sync.sync(clean_db, poll_only=True, log=lambda m: None)

    # Spelled out from the three service names, not read back from `source.LAYERS`:
    # a fourth layer added to LAYERS would otherwise widen its own expectation.
    expected = {f"{source.BASE}/{svc}/FeatureServer/0{suffix}"
                for svc in THREE_SERVICES for suffix in ("", "/query")}
    assert service.urls, "the sync issued no request at all"
    assert set(service.urls) == expected
    assert not [u for u in service.urls if _FORBIDDEN_LAYER.search(u)]


# ============================================================ seeded mirror


def local_utc(year, month, day, hour, minute):
    """A Halifax wall-clock time, as the UTC instant the source stores."""
    return datetime.datetime(year, month, day, hour, minute, tzinfo=HALIFAX).astimezone(UTC)


SQUARE_RING = [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]
OTHER_RING = [[[10, 10], [10, 11], [11, 11], [11, 10], [10, 10]]]

# Values chosen to read as enforcement outcomes if anything printed them, and to be
# distinctive enough that a substring search cannot match by accident.
RESOLUTIONS = (
    "Ticketed - Parking Violation",
    "No violation found",
    "Complaint unfounded",
    "Vehicle relocated by owner",
    "Cleared on arrival",
    "Warning issued",
)

# (request_id, address, Halifax local (y, m, d, hh, mm), towed, initiated_by, resolution)
#
# Midnight cases, both sides of it, in ADT (July, June) and AST (December to
# February). "3 THIRD ST" ends 23:50 AST, whose UTC instant is already the next
# calendar day; "4 FOURTH ST" ends 00:30 ADT, the mirror image.
DRIVEWAY_CALLS = [
    (4001, "1 FIRST ST, HALIFAX", (2025, 7, 10, 23, 50), "N", "311 Online", RESOLUTIONS[0]),
    (4002, "1 FIRST ST, HALIFAX", (2026, 1, 15, 0, 10), "Y", "INTERNAL", RESOLUTIONS[1]),
    (4003, "1 FIRST ST, HALIFAX", (2026, 5, 20, 9, 0), "N", "INTERNAL", RESOLUTIONS[2]),
    (4004, "2 SECOND ST, HALIFAX", (2025, 12, 3, 23, 45), "N", "INTERNAL", RESOLUTIONS[3]),
    (4005, "2 SECOND ST, HALIFAX", (2026, 2, 10, 0, 20), "N", "311 Online", RESOLUTIONS[4]),
    (4006, "3 THIRD ST, HALIFAX", (2026, 1, 15, 0, 10), "N", "INTERNAL", RESOLUTIONS[5]),
    (4007, "3 THIRD ST, HALIFAX", (2026, 2, 20, 23, 50), "N", "INTERNAL", RESOLUTIONS[0]),
    (4008, "4 FOURTH ST, HALIFAX", (2025, 7, 11, 13, 0), "N", "INTERNAL", None),
    (4009, "4 FOURTH ST, HALIFAX", (2026, 6, 1, 0, 30), "N", "311 Online", RESOLUTIONS[1]),
]
NO_PARKING_CALLS = [
    (5001, "9 NINTH AVE, HALIFAX", (2026, 3, 2, 18, 0), "N", "INTERNAL", RESOLUTIONS[2]),
    (5002, "9 NINTH AVE, HALIFAX", (2026, 4, 9, 23, 55), "Y", "INTERNAL", RESOLUTIONS[3]),
    (5003, "10 TENTH AVE, HALIFAX", (2026, 3, 3, 6, 0), "N", "311 Online", RESOLUTIONS[4]),
    (5004, "10 TENTH AVE, HALIFAX", (2026, 4, 10, 0, 5), "N", "INTERNAL", RESOLUTIONS[5]),
]


def _insert_call(conn, request_id, address, when, raw_label, towed, channel, resolution, ring_lat):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_requests (object_id, request_id, date_initiated, address, "
            "community, district, resolution, latitude, longitude, initiated_by) "
            "VALUES (%s, %s, %s, %s, 'HALIFAX', '7', %s, %s, %s, %s)",
            (request_id, request_id, when, address, resolution, ring_lat, ring_lat, channel),
        )
        base = request_id * 10
        for offset, (name, value) in enumerate([
            ("Alleged Violation", raw_label), ("Vehicle Was Towed", towed),
            ("Vehicle Make", "FORD"), ("Vehicle Model", "F150"), ("Vehicle Colour", "BLUE"),
        ]):
            cur.execute(
                "INSERT INTO custom_fields (object_id, request_id, custom_field_name, "
                "custom_field_value) VALUES (%s, %s, %s, %s)",
                (base + offset, request_id, name, value),
            )
    conn.commit()


def _insert_census(conn, dauid, rings, object_id):
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO census_areas (object_id, dauid, population, dwellings, rings, "
            "min_lon, min_lat, max_lon, max_lat) VALUES (%s, %s, 400, 200, %s, %s, %s, %s, %s)",
            (object_id, dauid, json.dumps(rings), min(xs), min(ys), max(xs), max(ys)),
        )
    conn.commit()


def seed_mirror(conn):
    _insert_census(conn, "12090999", SQUARE_RING, 1)
    _insert_census(conn, "12091111", OTHER_RING, 2)
    for rid, addr, when, towed, channel, resolution in DRIVEWAY_CALLS:
        _insert_call(conn, rid, addr, local_utc(*when), "Blocking Driveway (DISPATCH)",
                     towed, channel, resolution, 0.5)
    for rid, addr, when, towed, channel, resolution in NO_PARKING_CALLS:
        _insert_call(conn, rid, addr, local_utc(*when),
                     "No Parking Sign" if rid < 5003 else "NOPARKING",
                     towed, channel, resolution, 10.5)
    outcomes = mirror_type_figures.compute_and_store(
        conn, types=["Blocking Driveway", "No Parking Sign"], log=lambda m: None)
    assert outcomes and all(o["ok"] for o in outcomes)


@pytest.fixture
def client(clean_db):
    seed_mirror(clean_db)
    app = app_server.create_app()
    app.testing = True
    return app.test_client()


DRIVEWAY, NO_PARKING = "blocking-driveway", "no-parking-sign"
FILTERED = "?district=7&min_calls=1&min_doorways=1&recency_days=400&recur_days=200"


def surface_paths():
    """Every page, JSON route and export the app serves, for two seeded types plus
    an untracked slug and a filtered variant of each per-type route."""
    paths = ["/", "/api/types", "/api/freshness", "/types/not-a-real-type",
             "/api/types/not-a-real-type/figures"]
    for slug in (DRIVEWAY, NO_PARKING):
        for query in ("", FILTERED):
            paths += [
                f"/types/{slug}{query}",
                f"/api/types/{slug}/doorways{query}",
                f"/api/types/{slug}/blocks{query}",
                f"/api/types/{slug}/export.csv{query}",
                f"/types/{slug}/export{query}",
            ]
        paths += [f"/api/types/{slug}/figures", f"/api/types/{slug}/decisions"]
    return paths


def fetch_all(client):
    """`{path: (status, body text)}` for every served surface."""
    out = {}
    for path in surface_paths():
        resp = client.get(path)
        out[path] = (resp.status_code, resp.get_data(as_text=True))
    return out


def json_keys(node):
    keys = set()
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            keys |= json_keys(v)
    elif isinstance(node, list):
        for v in node:
            keys |= json_keys(v)
    return keys


def strip_page_comments(page):
    """The page as code and visible text: HTML comments, block comments and
    whole-line `//` comments removed, since those explain the code rather than
    reach a reader or run."""
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    page = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", page)


def strip_timestamps(text):
    """Blank the ISO instants that legitimately differ between two runs
    (`checked_at`, `Generated ...`, `computed_at`)."""
    return re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?",
                  "<ts>", text)


def test_the_seed_is_reachable_through_every_served_surface(client):
    """A guard on the harness itself: an audit of pages that all 404 would pass
    every absence check below."""
    served = fetch_all(client)
    for path, (status, _body) in served.items():
        expected = 404 if "not-a-real-type" in path else 200
        assert status == expected, (path, status)
    doorways = json.loads(served[f"/api/types/{DRIVEWAY}/doorways"][1])
    assert {r["a"] for r in doorways["rows"]} == {
        "1 First St", "2 Second St", "3 Third St", "4 Fourth St"}
    csv_text = served[f"/api/types/{DRIVEWAY}/export.csv"][1]
    assert "1 First St" in csv_text and "Blocking Driveway" in csv_text


# ================================================ 9.3 the served output


_311 = re.compile(r"(?<![\d.])311(?!\d)")
_CALL_DETAIL_WORDS = re.compile(r"call[\s_-]*details?", re.I)


def test_no_served_surface_names_or_implies_311_call_details(client):
    """Search text of the page, every JSON route and both exports, case-insensitively,
    for `311` and for "call details". `311 Online`, if it were ever shown, may appear
    only as a labelled channel; today nothing served shows it at all."""
    served = fetch_all(client)
    offenders = []
    for path, (_status, body) in served.items():
        text = strip_page_comments(body) if path.startswith(("/types/", "/")) and "<html" in body[:400].lower() else body
        for m in _311.finditer(text):
            context = text[max(0, m.start() - 120): m.end() + 120]
            if text[m.start():].startswith("311 Online") and _CHANNEL_CONTEXT.search(context):
                continue
            offenders.append((path, context.replace("\n", " ")))
        for m in _CALL_DETAIL_WORDS.finditer(text):
            offenders.append((path, text[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")))
    assert not offenders, offenders


def test_the_seeded_311_online_channel_is_stored_but_never_served(client, clean_db):
    """The channel label is legitimately *in the mirror* (that is what the seed
    checks first) and the served output does not print it: it is loaded, not
    surfaced, so there is no channel claim to be mistaken for call-detail data."""
    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM service_requests WHERE initiated_by = '311 Online'")
        assert cur.fetchone()[0] == 4
    for path, (_status, body) in fetch_all(client).items():
        assert "311 Online" not in body, path
        assert "INITIATED_BY" not in body and "initiated_by" not in body, path


def test_the_static_map_asset_names_no_311_string(client):
    resp = client.get("/map-network.json")
    assert resp.status_code == 200
    assert _json_string_hits_of(resp.get_json()) == []


def _json_string_hits_of(data):
    hits = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and (_311.search(node) or _CALL_DETAIL_WORDS.search(node)):
            hits.append(node)
    walk(data)
    return hits


# ============================================================ 9.7 RESOLUTION


def _set_resolution(conn, value):
    with conn.cursor() as cur:
        cur.execute("UPDATE service_requests SET resolution = %s", (value,))
    conn.commit()


def test_no_served_surface_contains_a_resolution_value_or_the_field_name(client):
    served = fetch_all(client)
    offenders = []
    for path, (_status, body) in served.items():
        text = strip_page_comments(body)
        low = text.lower()
        for value in RESOLUTIONS:
            if value.lower() in low:
                offenders.append((path, value))
        if "resolution" in low:
            offenders.append((path, "the word 'resolution'"))
    assert not offenders, offenders


def test_served_output_does_not_depend_on_the_resolution_field(client, clean_db):
    """The claim from the other side. Every served surface is captured with the
    seeded `RESOLUTION` values, with none, and with one enforcement-sounding value
    on every call. If any served text or number were derived from the field, at
    least one capture would differ."""
    baseline = {p: strip_timestamps(b) for p, (_s, b) in fetch_all(client).items()}
    for value in (None, "Ticketed - Parking Violation", "Vehicle towed - case closed"):
        _set_resolution(clean_db, value)
        again = {p: strip_timestamps(b) for p, (_s, b) in fetch_all(client).items()}
        changed = sorted(p for p in baseline if baseline[p] != again[p])
        assert not changed, (value, changed)


def test_the_invariance_check_can_see_the_tow_flag_change(client, clean_db):
    """Control for the test above: it is not vacuously equal. Flipping the *tow*
    flag -- the one outcome the product does state -- does change the served
    output."""
    before = {p: strip_timestamps(b) for p, (_s, b) in fetch_all(client).items()}
    with clean_db.cursor() as cur:
        cur.execute("UPDATE custom_fields SET custom_field_value = 'Y' "
                    "WHERE custom_field_name = 'Vehicle Was Towed'")
    clean_db.commit()
    after = {p: strip_timestamps(b) for p, (_s, b) in fetch_all(client).items()}
    changed = {p for p in before if before[p] != after[p]}
    assert f"/api/types/{DRIVEWAY}/doorways" in changed
    assert f"/api/types/{DRIVEWAY}/export.csv" in changed


# Outcome vocabulary that would be an enforcement claim if the product said it
# about a call. "ticketing" is deliberately absent: the tow limit says HRM
# "publishes no ticketing field", which is the opposite of a claim.
_OUTCOME_CLAIM = re.compile(
    r"\b(ticketed|ticket issued|tickets issued|fined|cited|citation|enforced|"
    r"resolved|cleared|unfounded|no violation found|violation found|warning issued)\b", re.I)


def test_no_served_text_states_an_enforcement_outcome_in_words(client):
    offenders = []
    for path, (_status, body) in fetch_all(client).items():
        text = strip_page_comments(body)
        for m in _OUTCOME_CLAIM.finditer(text):
            offenders.append((path, text[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")))
    assert not offenders, offenders


_OUTCOMEISH_KEY = re.compile(r"resol|ticket|enforc|cleared|resolved|closed|disposition", re.I)


def test_no_json_key_or_csv_column_names_an_outcome_other_than_the_tow(client):
    served = fetch_all(client)
    keys = set()
    for path, (status, body) in served.items():
        if status == 200 and path.startswith("/api/") and "export.csv" not in path:
            keys |= json_keys(json.loads(body))
    outcomeish = sorted(k for k in keys if _OUTCOMEISH_KEY.search(k))
    # `closed_calls`/`never_closed_calls` are the response-time figure's counts of
    # calls with and without a DATE_CLOSED -- an administrative date, not RESOLUTION
    # and not an outcome. Anything else outcome-like is a new claim to look at.
    assert set(outcomeish) <= {"closed_calls", "never_closed_calls"}, outcomeish

    for slug in (DRIVEWAY, NO_PARKING):
        rows = list(csv.reader(io.StringIO(served[f"/api/types/{slug}/export.csv"][1])))
        headers = {c for r in rows for c in r if c in {
            "Address", "Street", "Streets", "Tows", "Repeat Calls"}}
        assert "Tows" in headers
        table_headers = [r for r in rows if r[:1] in (["Address"], ["Block"])]
        assert table_headers
        for header in table_headers:
            assert not [c for c in header if _OUTCOMEISH_KEY.search(c)], header


def test_the_only_outcome_a_view_states_is_the_tow_and_it_says_so(client):
    """Every surface that carries the interpretation limits frames the tow as the
    only recorded outcome, and denies a ticketing field; the tow is the sole outcome
    statistic on the page's header row."""
    served = fetch_all(client)
    page = strip_page_comments(served[f"/types/{DRIVEWAY}"][1])
    csv_text = served[f"/api/types/{DRIVEWAY}/export.csv"][1]
    brief = served[f"/types/{DRIVEWAY}/export"][1]
    for label, text in (("page", page), ("csv", csv_text), ("brief", brief)):
        assert "Tow is the only recorded outcome" in text, label
        assert "HRM publishes no ticketing field" in text, label
        assert "not proof that nothing happened" in text, label
    # The page's header stats: the one outcome-shaped statistic is the tow share.
    stats = re.findall(r'<span class="k">([^<]*)</span>', page)
    assert "ended in a tow" in stats
    assert not [s for s in stats if re.search(r"resol|ticket|clear|enforc|fixed|no action", s, re.I)], stats


# ============================================================ 9.7 hour of day


_HOUR_OF_DAY_JS = re.compile(
    r"getHours|getUTCHours|toLocaleTimeString|hour12|hourCycle|\bhour\s*:|"
    r"\.hour\b|by_hour|byHour|hourly|histogram", re.I)
_HOUR_OF_DAY_WORDS = re.compile(
    r"peak hour|time of day|time-of-day|hour of day|hour-of-day|hours of the day|"
    r"rush hour|\bevenings?\b|\bmornings?\b|\bovernight\b|\bafternoons?\b|after hours|"
    r"business hours|office hours|\bnight\b|\bpeaks? (at|around|between)\b", re.I)
_HOUR_KEY = re.compile(r"hour|time.?of.?day|peak|histogram|bucket|daypart|morning|evening|night", re.I)
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")


def test_no_served_text_or_code_makes_an_hour_of_day_claim(client):
    offenders = []
    for path, (_status, body) in fetch_all(client).items():
        text = strip_page_comments(body)
        for pattern in (_HOUR_OF_DAY_JS, _HOUR_OF_DAY_WORDS):
            for m in pattern.finditer(text):
                offenders.append((path, text[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")))
    assert not offenders, offenders


def test_no_json_key_or_csv_column_is_an_hour_of_day_breakdown(client):
    served = fetch_all(client)
    keys = set()
    for path, (status, body) in served.items():
        if status == 200 and path.startswith("/api/") and "export.csv" not in path:
            keys |= json_keys(json.loads(body))
    assert keys, "no JSON keys collected"
    assert not [k for k in keys if _HOUR_KEY.search(k)], sorted(keys)
    for slug in (DRIVEWAY, NO_PARKING):
        rows = list(csv.reader(io.StringIO(served[f"/api/types/{slug}/export.csv"][1])))
        assert not [c for r in rows for c in r[:1] if _HOUR_KEY.search(c)]


def test_call_dates_in_lists_and_exports_carry_no_time_of_day(client):
    """The per-call timestamps are reduced to a Halifax calendar date before they
    reach any list: a `Last Call` cell, a doorway's `l`, `latest`. No `HH:MM`
    appears in a doorway or block row of the JSON, the CSV tables or the brief's
    tables. (The freshness clocks are instants by design and are checked
    separately below.)"""
    served = fetch_all(client)
    date_only = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for slug in (DRIVEWAY, NO_PARKING):
        doorways = json.loads(served[f"/api/types/{slug}/doorways"][1])
        assert date_only.match(doorways["latest"])
        for row in doorways["rows"]:
            assert date_only.match(row["l"]), row
        for path in (f"/api/types/{slug}/doorways", f"/api/types/{slug}/blocks",
                     f"/api/types/{slug}/figures", f"/api/types/{slug}/decisions"):
            body = json.loads(served[path][1])
            if path.endswith("/figures"):
                body = {k: v for k, v in body.items() if k != "computed_at"}
            assert not _CLOCK.findall(json.dumps(body)), path

        rows = list(csv.reader(io.StringIO(served[f"/api/types/{slug}/export.csv"][1])))
        in_table = False
        for r in rows:
            if r[:1] in (["DOORWAYS"], ["BLOCKS"]):
                in_table = True
                continue
            if in_table:
                assert not [c for c in r if _CLOCK.search(c)], r

        brief = served[f"/types/{slug}/export"][1]
        for table in re.findall(r"<table>.*?</table>", brief, re.S):
            assert not _CLOCK.search(re.sub(r"<[^>]+>", " ", table)), table[:200]


def test_a_doorways_last_call_is_its_halifax_calendar_date_either_side_of_midnight(client):
    """Local time is used for calendar dates only (task 4.6). 23:50 AST on 20 Feb is
    already 21 Feb in UTC and still 20 Feb in Halifax; 00:30 ADT on 1 Jun is still
    31 May in UTC-4 and 1 Jun in Halifax. Neither may show the shifted date."""
    doorways = json.loads(client.get(f"/api/types/{DRIVEWAY}/doorways").get_data(as_text=True))
    last = {r["a"]: r["l"] for r in doorways["rows"]}
    assert last["3 Third St"] == "2026-02-20"
    assert last["4 Fourth St"] == "2026-06-01"
    assert doorways["latest"] == "2026-06-01"


def test_freshness_clocks_are_the_only_instants_served_and_are_not_call_hours(client):
    """`/api/freshness` and the export preambles do carry instants -- when the mirror
    was checked, last synced, and its newest call. Those are the data clocks of task
    7.1, not a distribution or a claim about when calls arrive; nothing there is
    grouped or counted by hour."""
    fresh = json.loads(client.get("/api/freshness").get_data(as_text=True))
    keys = json_keys(fresh)
    assert not [k for k in keys if _HOUR_KEY.search(k)], sorted(keys)
    for slug in (DRIVEWAY, NO_PARKING):
        rows = list(csv.reader(io.StringIO(
            client.get(f"/api/types/{slug}/export.csv").get_data(as_text=True))))
        clocks = {"checked_at", "most_recent_call_date", "last_success_at", "last_attempt_at",
                  "next_due_at", "behind_reason", "stale_call_warning", "next_update"}
        for r in rows:
            if r and r[0] in ("DOORWAYS", "BLOCKS"):
                break
            if len(r) == 2 and _CLOCK.search(r[1]):
                assert r[0] in clocks, r


_LIMIT_INTAKE_TITLE = "Dates are intake, not condition."
_LIMIT_INTAKE_FRAMING = "not from when the underlying condition began or ended"


def _flat(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


def test_every_list_carrying_surface_states_the_intake_limit_with_its_framing(client):
    """The interpretation limit "Dates are intake, not condition" travels with each
    list: the page, the CSV export and the brief, for each type and each filter
    variant. The limit's own sentence is the framing -- the timestamp is when HRM
    logged the call, not when the condition began or ended -- so it must be there in
    full, not only its title."""
    served = fetch_all(client)
    carrying = [p for p in served
                if re.fullmatch(r"/types/(blocking-driveway|no-parking-sign)(/export)?(\?.*)?", p)
                or "export.csv" in p]
    assert len(carrying) == 12
    for path in carrying:
        text = _flat(strip_page_comments(served[path][1]))
        assert _LIMIT_INTAKE_TITLE in text, path
        assert _LIMIT_INTAKE_FRAMING in text, path
        assert "HRM logged the call" in text, path


_OFFICE_CLOCK = re.compile(r"office (?:clock|hours)|business hours|\bkeyed\b|staff (?:clock|keyed)"
                           r"|entered by staff|when staff", re.I)
_INTAKE_FRAMING = re.compile(
    r"\bnot (?:from )?when\b|\bnot the (?:hour|time|day)\b|rather than when|not condition"
    r"|logged the call|intake", re.I)


def test_any_served_mention_of_the_office_clock_frames_it_as_an_intake_artefact(client):
    """A sentence that speaks of the office clock, business hours or staff keying a
    call must say what that means for the timestamp -- that it is intake, not when
    the condition occurred. Today no served sentence speaks of the office clock at
    all (the limit says "logged", not "keyed"), so this holds vacuously *and* fails
    the moment one appears unframed."""
    offenders = []
    for path, (_status, body) in fetch_all(client).items():
        text = _flat(strip_page_comments(body))
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if _OFFICE_CLOCK.search(sentence) and not _INTAKE_FRAMING.search(sentence):
                offenders.append((path, sentence[:200]))
    assert not offenders, offenders


# ===================================== page file, checked without a database


PAGE_FILE = REPO / "web" / "app" / "index.html"


def _page_code():
    return strip_page_comments(PAGE_FILE.read_text())


def test_the_page_file_names_no_311_dataset_and_no_channel_label():
    text = _page_code()
    assert not _311.search(text)
    assert not _CALL_DETAIL_WORDS.search(text)
    assert "INITIATED_BY" not in text and "initiated_by" not in text


def test_the_page_file_never_reads_resolution():
    assert "resolution" not in _page_code().lower()


def test_the_page_file_extracts_no_hour_of_day_and_states_no_hour_claim():
    text = _page_code()
    assert not _HOUR_OF_DAY_JS.search(text)
    assert not _HOUR_OF_DAY_WORDS.search(text)


def test_the_page_file_states_the_tow_as_the_only_recorded_outcome():
    text = _flat(_page_code())
    assert "Tow is the only recorded outcome." in text
    assert "HRM publishes no ticketing field." in text
    assert not _OUTCOME_CLAIM.search(text)


# ===================================== source audits for RESOLUTION and hours


# Modules that shape what is served or exported. None of them mentions the field.
OUTPUT_SHAPING = (
    "src/app/server.py", "src/mirror/figures.py", "src/mirror/type_figures.py",
    "src/mirror/per_type.py", "src/mirror/triage.py", "src/mirror/status.py",
    "src/mirror/derive_recency.py", "src/mirror/history.py",
    "src/mirror/violation_types.py", "src/mirror/reconcile_figures.py",
)


def test_no_output_shaping_module_mentions_the_resolution_field():
    for rel in OUTPUT_SHAPING:
        assert "resolution" not in (REPO / rel).read_text().lower(), rel


def test_resolution_is_stored_and_carried_but_never_read_by_the_grouping_code():
    """The classification: schema/load/reconcile store and compare it; `derive.load`
    carries it in each call record; `hotspots.py` names it once, in the live
    pipeline's `outFields`, and never subscripts it. `hotspots.build` and
    `roll_blocks` -- what every list is computed from -- do not read it."""
    hotspots = (REPO / "src" / "hotspots.py").read_text()
    lines = [l for l in hotspots.splitlines() if "RESOLUTION" in l]
    assert len(lines) == 1 and "LATITUDE" in lines[0], lines
    assert not re.search(r"""\[["']RESOLUTION["']\]|\.get\(["']RESOLUTION["']""", hotspots)
    derive_text = (REPO / "src" / "mirror" / "derive.py").read_text()
    assert not re.search(r"""\[["']RESOLUTION["']\]|\.get\(["']RESOLUTION["']""", derive_text)


_HOUR_EXTRACTION = re.compile(
    r"\.hour\b|%H|%I|%p|date_part\(\s*'hour'|extract\(\s*hour|date_trunc\(\s*'hour'|"
    r"getHours|getUTCHours|toLocaleTimeString", re.I)


def test_no_source_file_extracts_the_hour_from_a_timestamp():
    """Local time is used for calendar dates (`to_local`, `%Y-%m-%d`) and nothing
    reads an hour out of a call timestamp anywhere the app or the mirror could
    group by it."""
    checked = 0
    for path in sorted((REPO / "src").rglob("*.py")) + [REPO / "src" / "mirror" / "schema.sql"]:
        text = path.read_text()
        checked += 1
        assert not _HOUR_EXTRACTION.search(text), (path.name, _HOUR_EXTRACTION.search(text).group(0))
    assert checked > 10
    assert not _HOUR_EXTRACTION.search(_page_code())
