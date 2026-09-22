"""Task 9.5: the board (the served page's data routes and its own header script)
and the two exports, all read from ONE quiet mirror state, agree on every count
and figure -- including the tow-comparison and provenance figures of 4b -- across
several violation types, default and non-default filters, and a call within an
hour of local midnight.

Verification only -- nothing here changes the product.

What it leans on: `tests/test_app.py`'s 7.7/7.8 block already proves, for one type
and one clock pair, that the CSV and the brief carry the view's rows cell for cell,
name the filters and the mirror state, and that an export is one snapshot when a
sync lands between its reads. It also checks the tow sentence -- but only for a
too-small sample and only against `app_server._tow_thesis_sentence`, the code the
export itself calls. This file adds what those do not:

  * an independent oracle: every expected number is re-derived from the seed
    ledger below with plain Python (zoneinfo dates, no import of the derivation),
    so a view and two exports agreeing on one wrong answer still fail;
  * three surfaces for the header figures and the tow sentence -- the JSON view,
    the exports, and the page's OWN JavaScript run in node against the JSON the
    server returned (the served page shows figures the exports do not carry as
    such; see `test_header_figures_...`);
  * a tow comparison big enough to conclude (a CI, a rules-out bound, a caveat),
    one too small to conclude, a tracked type with no calls, a type whose figures
    were never stored, and an untracked slug;
  * the provenance figures (4.13/4.14: call denominators, response time) checked
    against the ledger and against the view's own header.

Caveats: the mirror is quiet while these run (nothing writes between requests),
so the residual the 7.8 note records -- `/api/freshness` and the derivation reads
on `/doorways` and `/blocks` are not pinned to one snapshot -- is not exercised
here. There is no browser: the page's JS is executed against stubs, so layout,
visibility and locale formatting of the header are not verified.
"""

import collections
import csv
import dataclasses
import datetime
import html
import html.parser
import io
import json
import pathlib
import re
import shutil
import statistics
import subprocess
import urllib.parse
import zoneinfo

import pytest

from mirror import type_figures as mirror_type_figures

pytestmark = pytest.mark.db

REPO = pathlib.Path(__file__).resolve().parents[1]
PAGE = (REPO / "web" / "app" / "index.html").read_text()
HALIFAX = zoneinfo.ZoneInfo("America/Halifax")
UTC = datetime.UTC

BD, NPS, METER, HYDRANT = "blocking-driveway", "no-parking-sign", "meter-violation", "within-5m-of-hydrant"
PP = "private-property"
CANONICAL = {BD: "Blocking Driveway", NPS: "No Parking Sign", METER: "Meter Violation",
             HYDRANT: "Within 5M of Hydrant", PP: "Private Property"}

RING_A = [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]
RING_B = [[[10, 10], [10, 11], [11, 11], [11, 10], [10, 10]]]
AREAS = {"A": ("12090999", RING_A, 200, 0.5), "B": ("12091111", RING_B, 100, 10.5)}
SYNC = datetime.datetime(2026, 9, 17, 4, 0, tzinfo=UTC)
LATEST = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
DAY = datetime.timedelta(days=1)


def at(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=UTC)


# ---------------------------------------------------------------- the seed ledger


@dataclasses.dataclass
class Call:
    rid: int
    type: str            # slug
    label: str           # the raw `Alleged Violation` value filed
    address: str         # "" = no usable address
    when: datetime.datetime
    towed: str
    district: str
    area: str            # "A" or "B": which census block the coordinates fall in
    colour: str = "BLUE"
    closed_after_min: int | None = None


def _build_ledger():
    """Every call the mirror holds, written out once. Counts are worked out by hand
    in the comments; the oracle below re-derives the rest.

    Blocking Driveway: 109 calls, 31 towed, 78 not towed (one of them, filed with
    no address, is outside the recurrence cohort of 108). Two raw labels (the
    DISPATCH label and the legacy DRIVEWAY code). Four "midnight" doorways carry a
    newest call within an hour of local midnight, in summer (ADT, UTC-3) and in
    winter (AST, UTC-4), before and after midnight; the newest call of the type is
    one of them (2026-06-01T02:30Z = 2026-05-31 23:30 local).
    """
    calls, rid = [], iter(range(1000, 9999))

    def add(type_, label, address, when, towed="N", district="7", area="A", colour="BLUE",
            closed=None):
        calls.append(Call(next(rid), type_, label, address, when, towed, district, area,
                          colour, closed))

    disp, legacy = "Blocking Driveway (DISPATCH)", "DRIVEWAY"
    for i in range(1, 21):                      # A: old call towed, newer not; 20 x 2 = 40 calls
        d, area = ("8", "B") if i <= 5 else ("7", "A")
        add(BD, disp, f"{i} ALDER ST, HALIFAX", LATEST - 20 * DAY, "Y", d, area, "BLUE", 120)
        add(BD, disp, f"{i} ALDER ST, HALIFAX", LATEST - 10 * DAY, "N", d, area,
            "RED" if i % 2 == 0 else "BLUE", 120)
    for i in range(1, 21):                      # B: two calls, neither towed; 40 calls
        d, area = ("8", "B") if i <= 3 else ("7", "A")
        add(BD, disp, f"{i} BIRCH ST, HALIFAX", LATEST - 25 * DAY, "N", d, area)
        add(BD, disp, f"{i} BIRCH ST, HALIFAX", LATEST - 5 * DAY, "N", d, area)
    for i in range(1, 11):                      # C: one towed call each; 10 calls
        add(BD, disp, f"{i} CEDAR ST, HALIFAX", LATEST - 15 * DAY, "Y", "7", "A", "BLUE", 30)
    for i in range(1, 11):                      # D: one call each, legacy label; 10 calls
        d, area = ("8", "B") if i <= 2 else ("7", "A")
        add(BD, legacy, f"{i} DOGWOOD ST, HALIFAX", LATEST - 15 * DAY, "N", d, area)
    # Midnight doorways (8 calls). Newest call of each, local date in the comment:
    add(BD, disp, "1 MIDNIGHT ST, HALIFAX", at("2026-05-20T15:00:00"))
    add(BD, disp, "1 MIDNIGHT ST, HALIFAX", at("2026-06-01T02:30:00"))    # 2026-05-31 23:30 ADT
    add(BD, disp, "2 MIDNIGHT ST, HALIFAX", at("2026-05-10T15:00:00"))
    add(BD, disp, "2 MIDNIGHT ST, HALIFAX", at("2026-05-30T02:30:00"))    # 2026-05-29 23:30 ADT
    add(BD, disp, "3 MIDNIGHT ST, HALIFAX", at("2026-01-10T15:00:00"), "Y")
    add(BD, disp, "3 MIDNIGHT ST, HALIFAX", at("2026-01-15T03:30:00"))    # 2026-01-14 23:30 AST
    add(BD, disp, "4 MIDNIGHT ST, HALIFAX", at("2026-05-24T03:30:00"))    # 2026-05-24 00:30 ADT
    add(BD, disp, "4 MIDNIGHT ST, HALIFAX", at("2026-05-29T03:30:00"))    # 2026-05-29 00:30 ADT
    add(BD, disp, "", LATEST - 3 * DAY)          # no usable address: counted as a call, in no doorway
    # towed = 20 (A) + 10 (C) + 1 (3 MIDNIGHT) = 31; total = 40+40+10+10+8+1 = 109

    # No Parking Sign: too small to conclude (towed=2, not towed=4).
    for n, (a, b, label) in enumerate([("Y", "N", "No Parking Sign"), ("N", "N", "No Parking Sign"),
                                       ("Y", "N", "NOPARKING")], start=1):
        add(NPS, label, f"{n} PINE AVE, HALIFAX", LATEST - 20 * DAY, a, "8", "B")
        add(NPS, label, f"{n} PINE AVE, HALIFAX", LATEST - 10 * DAY, b, "8", "B")
    # Within 5M of Hydrant: has calls, figures never stored.
    for n in (1, 2):
        add(HYDRANT, "Within 5M of Hydrant", f"{n} HYDRANT RD, HALIFAX", LATEST - 9 * DAY)
        add(HYDRANT, "Within 5M of Hydrant", f"{n} HYDRANT RD, HALIFAX", LATEST - 4 * DAY)
    # Private Property: big enough to compare and no different -- 16 addresses whose
    # two calls are both towed (32 towed calls, 16 recurring = 50.0 per cent) and 16
    # whose two calls are both not towed (32 calls, 16 recurring = 50.0 per cent).
    for n in range(1, 17):
        for flag, street in (("Y", "TOWED"), ("N", "PLAIN")):
            add(PP, "Private Property", f"{n} {street} LN, HALIFAX", LATEST - 30 * DAY, flag)
            add(PP, "Private Property", f"{n} {street} LN, HALIFAX", LATEST - 20 * DAY, flag)
    return calls          # Meter Violation: no calls at all


LEDGER = _build_ledger()


def ledger_for(slug):
    return [c for c in LEDGER if c.type == slug]


# ------------------------------------------------------------------ the oracle


def local(dt):
    return dt.astimezone(HALIFAX)


def oracle(slug, min_calls=2, min_doorways=2, recur_days=365, recency_days=365, district=None):
    """One type's doorway list, block list and header figures re-derived from the
    ledger with plain Python: Halifax-local timestamps via zoneinfo, the recency
    window measured back from the newest call's local time, recurrence as 'a later
    call at the same address within `recur_days`', blocks as the census area the
    coordinates fall in."""
    calls = ledger_for(slug)
    if not calls:
        return {"latest": None, "doors": {}, "blocks": {}, "tow_pct": None, "distinct": 0,
                "seen": 0, "with_neighbour": 0, "unique_pct": None}
    latest = max(local(c.when) for c in calls)
    recent_from = latest - datetime.timedelta(days=recency_days)
    by_address = collections.defaultdict(list)
    for c in calls:
        if c.address:
            by_address[c.address.split(",")[0].strip()].append(c)
    doors = {}
    for address, cs in by_address.items():
        cs.sort(key=lambda c: c.when)
        times = [local(c.when) for c in cs]
        recent = sum(1 for t in times if t >= recent_from)
        if recent == 0 or recent < min_calls:
            continue
        if district is not None and cs[-1].district != district:
            continue
        gaps = [(times[i + 1] - times[i]).days for i in range(len(times) - 1)]
        vehicles = {(c.colour,) for c in cs}
        doors[address.title()] = {
            "m": recent, "t": len(cs), "w": sum(c.towed == "Y" for c in cs),
            "vs": len(cs), "vd": len(vehicles),
            "rp": sum(1 for k, t in enumerate(times)
                      if any((times[j] - t).days <= recur_days for j in range(k + 1, len(times)))),
            "g": None if not gaps else round(statistics.median(gaps)),
            "l": max(times).strftime("%Y-%m-%d"), "d": cs[-1].district, "area": cs[0].area,
        }
    per_area = collections.Counter(d["area"] for d in doors.values())
    blocks = {}
    for area, n in per_area.items():
        if n >= min_doorways:
            members = [d for d in doors.values() if d["area"] == area]
            blocks[AREAS[area][0]] = {
                "n": n, "m": sum(d["m"] for d in members), "t": sum(d["t"] for d in members),
                "w": sum(d["w"] for d in members)}
    for d in doors.values():
        d["nb"] = per_area[d["area"]] if per_area[d["area"]] >= min_doorways else 1
    seen, distinct = sum(d["vs"] for d in doors.values()), sum(d["vd"] for d in doors.values())
    return {
        "latest": latest.strftime("%Y-%m-%d"), "doors": doors, "blocks": blocks,
        "tow_pct": 100 * sum(c.towed == "Y" for c in calls) / len(calls),
        "distinct": distinct, "seen": seen,
        "with_neighbour": sum(1 for d in doors.values() if d["nb"] >= 2),
        "unique_pct": (100 * distinct / seen) if seen else None,
    }


def oracle_tow(slug, recur_days=365):
    """The tow comparison's cohort and recurrence, from the ledger (calls with a
    usable address, split by tow flag, each counted as recurring when a later call
    at the same address falls within `recur_days`)."""
    by_address = collections.defaultdict(list)
    for c in ledger_for(slug):
        if c.address:
            by_address[c.address].append(c)
    groups = {"Y": [0, 0], "N": [0, 0]}
    for cs in by_address.values():
        cs.sort(key=lambda c: c.when)
        times = [local(c.when) for c in cs]
        for k, c in enumerate(cs):
            g = groups[c.towed]
            g[0] += 1
            g[1] += any((times[j] - times[k]).days <= recur_days for j in range(k + 1, len(times)))
    pct = lambda g: round(100 * g[1] / g[0], 1) if g[0] else None   # noqa: E731
    return {"towed": groups["Y"][0], "not_towed": groups["N"][0],
            "towed_pct": pct(groups["Y"]), "not_towed_pct": pct(groups["N"])}


# --------------------------------------------------------------- the seeded world


@pytest.fixture(scope="module")
def world(db):
    """Seed once, read-only afterwards: every test below only GETs. Emptied again
    on the way out, so a later module's `clean_db` finds what it expects."""
    tables = ("service_requests, custom_fields, census_areas, load_progress, layer_state, "
              "sync_runs, sync_anomalies, layer_versions, layer_version_samples, list_snapshots, "
              "doorway_list_history, block_list_history, triage_decisions, type_figures")
    db.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    db.commit()
    with db.cursor() as cur:
        for n, (dauid, ring, dwellings, _lat) in enumerate(AREAS.values(), start=1):
            xs = [p[0] for r in ring for p in r]
            ys = [p[1] for r in ring for p in r]
            cur.execute(
                "INSERT INTO census_areas (object_id, dauid, population, dwellings, rings, "
                "min_lon, min_lat, max_lon, max_lat) VALUES (%s,%s,400,%s,%s,%s,%s,%s,%s)",
                (n, dauid, dwellings, json.dumps(ring), min(xs), min(ys), max(xs), max(ys)))
        for c in LEDGER:
            lat = AREAS[c.area][3]
            closed = c.when + datetime.timedelta(minutes=c.closed_after_min) if c.closed_after_min else None
            cur.execute(
                "INSERT INTO service_requests (object_id, request_id, date_initiated, date_closed, "
                "address, community, district, latitude, longitude, initiated_by) "
                "VALUES (%s,%s,%s,%s,%s,'HALIFAX',%s,%s,%s,'INTERNAL')",
                (c.rid, c.rid, c.when, closed, c.address, c.district, lat, lat))
            for offset, (name, value) in enumerate([
                    ("Alleged Violation", c.label), ("Vehicle Was Towed", c.towed),
                    ("Vehicle Make", "FORD"), ("Vehicle Model", "F150"), ("Vehicle Colour", c.colour)]):
                cur.execute(
                    "INSERT INTO custom_fields (object_id, request_id, custom_field_name, "
                    "custom_field_value) VALUES (%s,%s,%s,%s)", (c.rid * 10 + offset, c.rid, name, value))
        for layer in ("service_requests", "custom_fields"):
            cur.execute(
                "INSERT INTO layer_state (layer, source_last_edit, last_attempt_at, last_attempt_ok, "
                "last_success_at, next_due_at, full_load_completed_at) "
                "VALUES (%s,%s,%s,true,%s,%s,%s)",
                (layer, SYNC - DAY, SYNC, SYNC, SYNC + DAY, SYNC))
            cur.execute(
                "INSERT INTO sync_runs (kind, layer, started_at, finished_at, ok, source_last_edit) "
                "VALUES ('reload',%s,%s,%s,true,%s)", (layer, SYNC, SYNC, SYNC - DAY))
        # a completed sync has loaded the census layer too, which is what makes the
        # mirror ready: an unready one says nothing has been loaded, not "no doorways"
        cur.execute("INSERT INTO layer_state (layer, full_load_completed_at) "
                    "VALUES ('census_areas', %s)", (SYNC,))
    db.commit()
    stored = mirror_type_figures.compute_and_store(
        db, types=[CANONICAL[BD], CANONICAL[NPS], CANONICAL[METER], CANONICAL[PP]], log=lambda m: None)
    assert [o["ok"] for o in stored] == [True] * 4   # HYDRANT is left unstored on purpose
    yield db
    db.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    db.commit()


@pytest.fixture(scope="module")
def client(world):
    from app import server
    return server.create_app().test_client()


# --------------------------------------------------------------------- parsers


def parse_csv(text):
    """`(preamble dict, doorway rows, block rows)`; rows are {column label: cell}."""
    rows = list(csv.reader(io.StringIO(text)))
    meta = {}
    for row in rows:
        if not row:
            continue
        if len(row) == 1:
            break
        meta.setdefault(row[0], row[1])

    def section(marker):
        start = next(i for i, r in enumerate(rows) if r == [marker]) + 1
        header, out = rows[start], []
        for r in rows[start + 1:]:
            if not r:
                break
            out.append(dict(zip(header, r)))
        return header, out

    return meta, section("DOORWAYS"), section("BLOCKS")


class _Brief(html.parser.HTMLParser):
    """What a reader of the brief sees, as data (entities decoded, markup dropped)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1, self.h2, self.li, self.p, self.tables = [], [], [], [], []
        self._tag = self._buf = self._table = self._row = None
        self._th = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = {"caption": "", "header": None, "rows": []}
        elif tag == "tr":
            self._row, self._th = [], False
        if tag in ("h1", "h2", "li", "p", "caption", "td", "th"):
            self._tag, self._buf = tag, []
            self._th = self._th or tag == "th"

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == self._tag and self._buf is not None:
            text = " ".join("".join(self._buf).split())
            if tag in ("h1", "h2", "li", "p"):
                getattr(self, tag).append(text)
            elif tag == "caption":
                self._table["caption"] = text
            elif tag in ("td", "th") and self._row is not None:
                self._row.append(text)
            self._tag = self._buf = None
        elif tag == "tr" and self._row is not None:
            if self._th:
                self._table["header"] = self._row
            else:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table":
            self.tables.append(self._table)
            self._table = None

    def rows(self, noun):
        for t in self.tables:
            if t["caption"].endswith(noun):
                return [dict(zip(t["header"], r)) for r in t["rows"]]
        return []

    def items(self):
        return dict(t.split(": ", 1) for t in self.li if ": " in t)


def parse_brief(body):
    parsed = _Brief()
    parsed.feed(body)
    parsed.tow = html.unescape(m.group(1)) if (m := re.search(
        r"<h2>Tow effect</h2>\s*<p>(.*?)</p>", body, flags=re.S)) else None
    return parsed


def parse_tow_sentence(text):
    """The numbers a reader takes from a tow-effect sentence, by explicit pattern."""
    out = {"text": text}
    if m := re.search(r"\((\d+\.\d) per cent against (\d+\.\d) per cent\)", text):
        out["towed_pct"], out["not_towed_pct"] = float(m.group(1)), float(m.group(2))
    if m := re.search(r"95% CI \[([+-]\d+\.\d), ([+-]\d+\.\d)\]", text):
        out["ci"] = (float(m.group(1)), float(m.group(2)))
    if m := re.search(r"rules out a reduction larger than (\d+\.\d) points", text):
        out["rules_out"] = float(m.group(1))
    if m := re.search(r"towed=(\d+), not_towed=(\d+), need >= (\d+) each", text):
        out["sample"] = tuple(int(g) for g in m.groups())
    out["caveat"] = "observational, not a randomized comparison" in text.lower()
    out["more_often"] = "more often than not-towed" in text
    out["less_often"] = "less often than not-towed" in text
    out["no_difference"] = "no detectable difference" in text
    return out


# ------------------------------------------- the page's own JS, run in node

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

_NODE = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const els = {};
const document = {title: "HRM", getElementById: id => els[id] || (els[id] = {id, textContent: ""})};
const sandbox = {
  document, SLUG: input.slug, doorPayload: input.door, blockPayload: input.blocks, TOW_KEY: null,
  pageTypeName: () => input.door.type || input.blocks.type || "", console: {error() {}, log() {}},
  fetch: async () => ({ok: input.figures_status === 200, status: input.figures_status,
                       json: async () => input.figures}),
};
vm.createContext(sandbox);
(async () => {
  vm.runInContext(input.code, sandbox);                // the tow/vehicle script and the header script
  await new Promise(resolve => setTimeout(resolve, 50));   // let the async one's stubbed fetch settle
  process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(els).map(([k, v]) => [k, v.textContent]))));
})().catch(e => { console.error(e); process.exit(1); });
"""


def _page_figures_script():
    """The stretch of the served page's script that fills the header figures and the
    tow sentence: from the helper functions the tow script uses (when the page has
    them) through the end of the header-figures script. Cut out by the lines that
    open and close it, so it is the page's own code, not a copy."""
    tow = PAGE.index('const towThesis = document.getElementById("tow-thesis");')
    start = PAGE.find("function vehicleThesis(")
    if not 0 <= start < tow:
        start = PAGE.rfind("(async () => {", 0, tow)
    header = PAGE.index("const s = doorPayload.summary || {};")
    return PAGE[start : PAGE.index("\n})();", header) + len("\n})();")]


def run_page(door, blocks, figures, figures_status=200):
    """What the page writes into its header and tow sentence, given the JSON the
    server returned for this type: the page's own code, executed in node."""
    payload = {
        "slug": door.get("slug"), "door": door, "blocks": blocks, "figures": figures,
        "figures_status": figures_status,
        "code": _page_figures_script(),
    }
    done = subprocess.run([NODE, "-e", _NODE], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ------------------------------------------------------------------- gathering

FILTER_SETS = {
    "default": {},
    "district=8": {"district": "8"},
    "min_calls=1": {"min_calls": 1},
    "recency_days=30": {"recency_days": 30},
    "recency_days=5": {"recency_days": 5},            # a type with calls but none listed
    "recency_days=10": {"recency_days": 10},          # one doorway, no block
    "recur_days=10": {"recur_days": 10},              # A's two calls are exactly 10 days apart
    "recur_days=9": {"recur_days": 9},
    "min_doorways=9": {"min_doorways": 9},            # block B (8 doorways) drops out, A (36) stays
    "all_five": {"min_calls": 1, "district": "7", "min_doorways": 3, "recency_days": 45,
                 "recur_days": 15},
}
DEFAULTS = {"district": None, "min_calls": 2, "min_doorways": 2, "recur_days": 365, "recency_days": 365}

CASES = (
    [(BD, name) for name in FILTER_SETS]
    + [(PP, "default"), (NPS, "default"), (NPS, "min_calls=1"), (NPS, "district=8"),
       (METER, "default"), (METER, "district=8"),
       (HYDRANT, "default"), (HYDRANT, "recency_days=5")]
)
CASE_IDS = [f"{slug}|{name}" for slug, name in CASES]
_CACHE = {}


def gather(client, slug, name):
    key = (slug, name)
    if key not in _CACHE:
        qs = ("?" + urllib.parse.urlencode(FILTER_SETS[name])) if FILTER_SETS[name] else ""
        get = lambda path: client.get(path + (qs if "freshness" not in path else ""))   # noqa: E731
        g = {
            "slug": slug, "name": name, "filters": {**DEFAULTS, **FILTER_SETS[name]},
            "doors": get(f"/api/types/{slug}/doorways").get_json(),
            "blocks": get(f"/api/types/{slug}/blocks").get_json(),
            "figures": client.get(f"/api/types/{slug}/figures").get_json(),
            "fresh": client.get("/api/freshness").get_json(),
        }
        g["csv"] = parse_csv(get(f"/api/types/{slug}/export.csv").get_data(as_text=True))
        g["brief"] = parse_brief(get(f"/types/{slug}/export").get_data(as_text=True))
        g["oracle"] = oracle(slug, **{k: v for k, v in g["filters"].items()})
        g["page"] = run_page(g["doors"], g["blocks"], g["figures"]) if NODE else None
        _CACHE[key] = g
    return _CACHE[key]


@pytest.fixture(params=CASES, ids=CASE_IDS)
def g(request, client):
    return gather(client, *request.param)


DOOR_LABELS = {  # view key -> the export column that carries it
    "a": "Address", "d": "District", "c": "Community", "st": "Street", "bk": "Block",
    "nb": "Doorways Calling On Block", "m": "Calls (12mo)", "t": "Calls (Total)",
    "rp": "Repeat Calls", "w": "Tows", "vd": "Vehicles Distinct", "vs": "Vehicles Seen",
    "g": "Median Gap Days", "l": "Last Call", "o": "Owner", "lat": "Latitude", "lon": "Longitude"}
BLOCK_LABELS = {
    "bk": "Block", "s": "Streets", "n": "Doorways", "m": "Calls (12mo)", "t": "Calls (Total)",
    "w": "Tows", "dw": "Dwellings", "r": "Calls per 1,000 Dwellings", "d": "District",
    "worst": "Worst Doorway", "addrs": "Addresses"}


def as_csv(value, key):
    return "; ".join(value) if key == "addrs" else ("" if value is None else str(value))


def as_brief(value, key):
    return "; ".join(value) if key == "addrs" else ("–" if value is None else str(value))


def number(cell):
    return None if cell in ("", "–") else float(cell) if "." in cell else int(cell)


# ================================================================= the tests


def test_seed_ledger_is_what_the_comments_say():
    """Guard the fixtures: hand-counted totals, and a midnight case whose UTC date
    differs from its Halifax date (else a UTC-vs-Halifax slip could not show)."""
    bd = ledger_for(BD)
    assert (len(bd), sum(c.towed == "Y" for c in bd), sum(not c.address for c in bd)) == (109, 31, 1)
    assert {c.label for c in bd} == {"Blocking Driveway (DISPATCH)", "DRIVEWAY"}
    newest = max(bd, key=lambda c: c.when)
    assert newest.address.startswith("1 MIDNIGHT") and newest.when.date() != local(newest.when).date()
    assert local(newest.when).strftime("%Y-%m-%d %H:%M") == "2026-05-31 23:30"
    winter = next(c for c in bd if c.address.startswith("3 MIDNIGHT") and c.towed == "N")
    assert local(winter.when).utcoffset() == datetime.timedelta(hours=-4)          # AST
    assert local(winter.when).strftime("%Y-%m-%d %H:%M") == "2026-01-14 23:30"
    assert len(ledger_for(NPS)) == 6 and not ledger_for(METER) and len(ledger_for(HYDRANT)) == 4
    assert len(ledger_for(PP)) == 64 and oracle_tow(PP) == {
        "towed": 32, "not_towed": 32, "towed_pct": 50.0, "not_towed_pct": 50.0}


def test_the_oracle_lists_what_the_seed_was_built_to_list():
    """Hand-worked expectations, so the oracle is itself pinned."""
    default = oracle(BD)
    assert len(default["doors"]) == 44 and set(default["blocks"]) == {"12090999", "12091111"}
    assert default["blocks"]["12091111"]["n"] == 8 and default["blocks"]["12090999"]["n"] == 36
    assert len(oracle(BD, min_calls=1)["doors"]) == 64
    assert len(oracle(BD, recency_days=10)["doors"]) == 1 and oracle(BD, recency_days=10)["blocks"] == {}
    assert "3 Midnight St" in default["doors"] and "3 Midnight St" not in oracle(BD, recency_days=30)["doors"]
    assert round(default["tow_pct"], 1) == 28.4              # 31 towed of all 109 calls
    assert oracle_tow(BD) == {"towed": 31, "not_towed": 77, "towed_pct": 67.7, "not_towed_pct": 29.9}


# ---- lists: view, CSV and brief each against the oracle, and against each other


def test_the_view_lists_the_oracles_doorways_and_blocks(g):
    o, doors, blocks = g["oracle"], g["doors"], g["blocks"]
    assert doors["type"] == blocks["type"] == CANONICAL[g["slug"]]
    assert {r["a"]: r for r in doors["rows"]}.keys() == o["doors"].keys()
    for r in doors["rows"]:
        want = o["doors"][r["a"]]
        got = {k: r[k] for k in ("m", "t", "w", "vs", "vd", "rp", "g", "l", "nb", "d")}
        assert got == {k: want[k] for k in got}, r["a"]
    assert {b["bk"]: {"n": b["n"], "m": b["m"], "t": b["t"], "w": b["w"]}
            for b in blocks["blocks"]} == o["blocks"]
    assert doors["count"] == len(o["doors"]) and blocks["count"] == len(o["blocks"])
    assert doors["latest"] == o["latest"]


def _exported_rows(g, which):
    meta, (_, csv_doors), (_, csv_blocks) = g["csv"]
    csv_rows = csv_doors if which == "doors" else csv_blocks
    brief_rows = g["brief"].rows("doorways" if which == "doors" else "blocks")
    return csv_rows, brief_rows


@pytest.mark.parametrize("surface", ["csv", "brief"])
def test_each_export_lists_the_oracles_rows_and_the_views_cells(g, surface):
    render = as_csv if surface == "csv" else as_brief
    for which, key_of, view_rows, want, labels in (
            ("doors", "a", g["doors"]["rows"], g["oracle"]["doors"], DOOR_LABELS),
            ("blocks", "bk", g["blocks"]["blocks"], g["oracle"]["blocks"], BLOCK_LABELS)):
        exported = _exported_rows(g, which)[0 if surface == "csv" else 1]
        assert len(exported) == len(view_rows) == len(want), which
        for view_row, row in zip(view_rows, exported):              # same rows, same order
            for key, label in labels.items():
                assert row[label] == render(view_row[key], key), (which, view_row[key_of], label)
            if surface == "csv":
                assert row["Violation Type"] == CANONICAL[g["slug"]]
        # ... and straight against the oracle, not via the view:
        if which == "doors":
            for row in exported:
                w = want[row["Address"]]
                assert (int(row["Calls (12mo)"]), int(row["Calls (Total)"]), int(row["Tows"]),
                        int(row["Repeat Calls"]), int(row["Vehicles Seen"]),
                        int(row["Vehicles Distinct"]), row["Last Call"]) == (
                    w["m"], w["t"], w["w"], w["rp"], w["vs"], w["vd"], w["l"])
        else:
            for row in exported:
                w = want[row["Block"]]
                assert (int(row["Doorways"]), int(row["Calls (12mo)"]), int(row["Calls (Total)"]),
                        int(row["Tows"])) == (w["n"], w["m"], w["t"], w["w"])


def test_every_count_is_the_same_on_every_surface(g):
    """Doorway and block counts: the view, the page's header, the CSV's stated
    counts and its rows, the brief's headings and its rows."""
    o = g["oracle"]
    nd, nb = len(o["doors"]), len(o["blocks"])
    meta, (_, csv_doors), (_, csv_blocks) = g["csv"]
    assert (g["doors"]["count"], g["blocks"]["count"]) == (nd, nb)
    assert (int(meta["doorway_count"]), int(meta["block_count"])) == (nd, nb)
    assert (len(csv_doors), len(csv_blocks)) == (nd, nb)
    headings = {m.group(1): int(m.group(2)) for h in g["brief"].h2
                if (m := re.fullmatch(r"(Doorways|Blocks) \((\d+)\)", h))}
    assert headings == {"Doorways": nd, "Blocks": nb}
    assert (len(g["brief"].rows("doorways")), len(g["brief"].rows("blocks"))) == (nd, nb)
    for noun, n in (("doorways", nd), ("blocks", nb)):
        if n == 0:                                     # an empty list is stated, not a missing table
            assert any(p.startswith(f"No {noun} match") for p in g["brief"].p), noun
    if g["page"] is not None:
        assert (g["page"]["n-doorways-stat"], g["page"]["n-blocks-stat"]) == (str(nd), str(nb))
        assert g["page"]["n-doorways-thesis"] == str(nd)


@needs_node
def test_header_figures_are_the_same_on_the_page_the_view_and_the_exports(g):
    """The header sentence's figures. The page (its own JS) shows distinct, seen,
    with-neighbour, unique % and tow %. The exports carry, per listed row, the
    vehicles-distinct / vehicles-seen / doorways-on-block columns those are sums of;
    they carry no total, no unique %, and no tow % (its denominator is every call
    of the type, listed or not, which no row of an export can reconstruct). So:
    the three sums are checked export-to-page; unique % is checked against the
    exports' own sums; tow % against the ledger."""
    o, s, page = g["oracle"], g["doors"]["summary"], g["page"]
    csv_rows, brief_rows = _exported_rows(g, "doors")
    for rows in (csv_rows, brief_rows):
        assert sum(int(r["Vehicles Distinct"]) for r in rows) == s["distinct"] == o["distinct"]
        assert sum(int(r["Vehicles Seen"]) for r in rows) == s["seen"] == o["seen"]
        assert sum(1 for r in rows if int(r["Doorways Calling On Block"]) >= 2) == s["with_neighbour"] \
            == o["with_neighbour"]
    assert (page["distinct-count"], page["seen-count"], page["with-neighbour-count"]) == (
        str(s["distinct"]), str(s["seen"]), str(s["with_neighbour"]))
    if o["seen"]:
        assert abs(s["unique_pct"] - o["unique_pct"]) <= 0.5
        assert page["unique-pct-stat"] == f"{s['unique_pct']}%"
    else:
        assert s["unique_pct"] is None and page["unique-pct-stat"] == "-"
    if o["tow_pct"] is None:
        assert s["tow_pct"] is None and page["tow-pct-stat"] == "-"
    else:
        assert abs(s["tow_pct"] - o["tow_pct"]) <= 0.05 + 1e-9
        assert page["tow-pct-stat"] == f"{s['tow_pct']:.1f}%"
    assert page["type-name"] == CANONICAL[g["slug"]]
    assert page["latest-date"] == (o["latest"] or "unknown")


def test_the_filters_in_effect_are_the_same_on_the_view_and_both_exports(g):
    f, meta = g["filters"], g["csv"][0]
    assert g["doors"]["filters"] == g["blocks"]["filters"] == f
    assert meta["filter: district"] == (f["district"] or "")
    for k in ("min_calls", "min_doorways", "recur_days", "recency_days"):
        assert meta[f"filter: {k}"] == str(f[k])
    bits = []
    if f["district"]:
        bits.append(f"district {f['district']}")
    if f["min_calls"] != 2:
        bits.append(f"min. recent calls ≥ {f['min_calls']}")
    if f["min_doorways"] != 2:
        bits.append(f"min. calling doorways/block ≥ {f['min_doorways']}")
    if f["recency_days"] != 365:
        bits.append(f"recency window ≤ {f['recency_days']} days")
    if f["recur_days"] != 365:
        bits.append(f"recurrence window ≤ {f['recur_days']} days")
    statement = f"Filtered by {', '.join(bits)}." if bits else "Default filters -- none active."
    items = g["brief"].items()
    assert meta["filters_in_effect"] == statement and statement in g["brief"].p
    assert items["District"] == (f["district"] or "every district")
    assert items["Minimum recent calls per doorway"] == str(f["min_calls"])
    assert items["Minimum still-calling doorways per block"] == str(f["min_doorways"])
    assert items["Recurrence window"] == f"{f['recur_days']} days"
    assert items["Recency window"] == f"{f['recency_days']} days"


def test_the_mirror_state_is_named_identically_and_dates_are_halifax_not_utc(g):
    """The clocks are instants: compared parsed. The data date is a Halifax
    calendar date: compared with the date zoneinfo gives for the newest call --
    for Blocking Driveway that is 2026-05-31 although its UTC date is 06-01."""
    meta, items, fresh = g["csv"][0], g["brief"].items(), g["fresh"]
    for clock, label in (("last_success_at", "Last successful sync"),
                         ("most_recent_call_date", "Most recent call in the data"),
                         ("next_due_at", "Next update due")):
        assert datetime.datetime.fromisoformat(meta[clock]) == datetime.datetime.fromisoformat(fresh[clock]), clock
        assert datetime.datetime.fromisoformat(items[label]) == datetime.datetime.fromisoformat(fresh[clock]), clock
    assert datetime.datetime.fromisoformat(fresh["last_success_at"]) == SYNC
    assert datetime.datetime.fromisoformat(meta["last_attempt_at"]) == SYNC
    latest = g["oracle"]["latest"]
    assert g["doors"]["latest"] == latest                      # None when the type has no calls
    assert meta["latest_call_date"] == (latest or "")          # ... which the CSV writes as a blank
    if latest:
        note = f"Showing calls from the last {g['filters']['recency_days']} days, since {latest}."
        assert meta["recency_note"] == note and note in g["brief"].p
        assert not any(re.search(r"since \d{4}-\d\d-\d\d", p) for p in g["brief"].p if p != note)
    else:
        assert meta["recency_note"] == "" and not any("Showing calls from" in p for p in g["brief"].p)
    if g["slug"] == BD:                     # holds the mirror's newest call, so the clock is its instant
        newest = datetime.datetime.fromisoformat(fresh["most_recent_call_date"])
        assert newest == at("2026-06-01T02:30:00")
        assert newest.astimezone(HALIFAX).date().isoformat() == g["doors"]["latest"] == "2026-05-31"
        assert newest.astimezone(UTC).date().isoformat() == "2026-06-01" != g["doors"]["latest"]
    stale = fresh["stale_call_warning"]["stale"]           # a function of today's clock, not of the seed
    assert (meta["stale_call_warning"] != "the most recent call is not stale") == stale
    assert (any(x.startswith("Stale-call warning:") for x in g["brief"].li)) == stale


def test_a_call_within_an_hour_of_local_midnight_keeps_its_halifax_date_everywhere(client):
    """Deliberate boundary cases, default and recency=30 (which drops the January
    one): the last-call date of each midnight doorway on the view, the CSV and the
    brief is the Halifax date, not the UTC one, in summer (ADT) and winter (AST)."""
    expected = {  # doorway: (Halifax date, the UTC date it must not be)
        "1 Midnight St": ("2026-05-31", "2026-06-01"),   # 23:30 ADT, before midnight
        "2 Midnight St": ("2026-05-29", "2026-05-30"),
        "3 Midnight St": ("2026-01-14", "2026-01-15"),   # 23:30 AST
        "4 Midnight St": ("2026-05-29", "2026-05-29"),   # 00:30 ADT, after midnight: same either way
    }
    for name in ("default", "recency_days=30", "min_calls=1"):
        g = gather(client, BD, name)
        csv_rows, brief_rows = _exported_rows(g, "doors")
        for surface, rows in (("view", {r["a"]: r["l"] for r in g["doors"]["rows"]}),
                              ("csv", {r["Address"]: r["Last Call"] for r in csv_rows}),
                              ("brief", {r["Address"]: r["Last Call"] for r in brief_rows})):
            for door, (halifax, utc) in expected.items():
                if door in rows:
                    assert rows[door] == halifax, (name, surface, door)
                    assert (rows[door] == utc) == (halifax == utc)
        assert ("3 Midnight St" in {r["a"] for r in g["doors"]["rows"]}) == (name != "recency_days=30")


# ---- the tow comparison and the provenance figures


def _tow_sentences(g):
    """The tow-effect sentence as the CSV, the brief and the page each state it."""
    out = {"csv": g["csv"][0]["tow_effect"], "brief": g["brief"].tow}
    if g["page"] is not None:
        out["page"] = g["page"]["tow-thesis"]
    return out


@pytest.mark.parametrize("slug", [BD, PP, NPS, METER, HYDRANT])
def test_the_tow_sentence_is_the_same_on_both_exports_and_the_page_and_matches_the_figures(client, slug):
    g = gather(client, slug, "default")
    sentences = _tow_sentences(g)
    assert len(set(sentences.values())) == 1, sentences
    said = parse_tow_sentence(sentences["csv"])
    figures = g["figures"]
    if slug == HYDRANT:              # calls exist, nothing stored: stated, with the server's reason
        assert figures["available"] is False and figures["reason"]
        assert sentences["csv"] == f"Tow-effect comparison for this type is not yet available ({figures['reason']})."
        return
    assert figures["available"] is True
    tow, want = figures["figures"]["tow"], oracle_tow(slug)
    assert (tow["towed"]["calls"], tow["not_towed"]["calls"]) == (want["towed"], want["not_towed"])
    if slug in (BD, PP):                               # big enough to conclude
        assert (said["towed_pct"], said["not_towed_pct"]) == (
            tow["towed"]["recurrence_pct"], tow["not_towed"]["recurrence_pct"]) == (
            want["towed_pct"], want["not_towed_pct"])
        if slug == BD:
            assert (want["towed_pct"], want["not_towed_pct"]) == (67.7, 29.9)    # 21/31 and 23/77, by hand
        cb = tow["effect_bound"]["cluster_bootstrap"]
        lo, hi = cb["ci_95_pct_points"]
        assert said["ci"] == (round(lo, 1), round(hi, 1))
        assert said["more_often"] == (lo > 0) and said["less_often"] == (hi < 0)
        assert said["no_difference"] == (lo <= 0 <= hi)
        assert said["no_difference"] == (slug == PP) and said["more_often"] == (slug == BD)
        if said["no_difference"]:      # the bound: what the comparison can rule out, stated, not just "no difference"
            assert said["rules_out"] == round(cb["rules_out_reduction_larger_than_pct_points"], 1)
        else:
            assert "rules_out" not in said
        assert said["caveat"], "the observational caveat travels with the figures"
    else:                                              # too small: counts and the floor, no percentages
        assert said["sample"] == (want["towed"], want["not_towed"], 30)
        assert not {"towed_pct", "ci", "rules_out"} & said.keys() and not said["caveat"]
        assert sentences["csv"] == (f"The tow sample is too small to compare recurrence "
                                    f"(towed={want['towed']}, not_towed={want['not_towed']}, need >= 30 each).")


def test_the_tow_figures_do_not_move_with_the_filters_on_any_surface(client):
    """They are filter-independent by design (M11/5.7): the same sentence on the
    view's page, the CSV and the brief for every filter set."""
    seen = {name: tuple(_tow_sentences(gather(client, BD, name)).values()) for name in FILTER_SETS}
    assert len(set(seen.values())) == 1, seen
    assert len(next(iter(seen.values()))) == (3 if NODE else 2)


def test_the_provenance_figures_match_the_ledger_and_the_views_header(client):
    """4.13/4.14: what `/figures` states about the populations behind the tow
    comparison and the response time, against the ledger and against the view's
    own header. (Neither the page nor either export shows these; only `/figures`
    does -- reported, not a disagreement.)"""
    g = gather(client, BD, "default")
    fig = g["figures"]["figures"]
    ledger = ledger_for(BD)
    want = oracle_tow(BD)
    assert fig["calls_raw"] == len(ledger) == 109
    # the populations, named: every call / the calls with a usable address / the known-status cohort
    pops = fig["call_denominators"]["populations"]
    assert pops["raw_selection_total"]["count"] == 109
    assert pops["raw_label:Blocking Driveway (DISPATCH)"]["count"] == sum(
        c.label == "Blocking Driveway (DISPATCH)" for c in ledger) == 99
    assert pops["raw_label:DRIVEWAY"]["count"] == 10
    assert pops["tow_comparison_population"]["count"] == fig["population"] == 108
    assert pops["tow_comparison_known_status"]["count"] == want["towed"] + want["not_towed"] == 108
    assert pops["excluded_no_usable_address_or_date"]["count"] == fig["excluded_no_usable_address_or_date"] == 1
    assert 108 + 1 == 109
    for pop in pops.values():
        assert pop["definition"], "every published figure names the population it is drawn from"
    # the response time: 40 A calls closed after 120 min and 10 C calls after 30
    rt = fig["response_time"]
    assert (rt["population"]["count"], rt["closed_calls"], rt["never_closed_calls"]) == (109, 50, 59)
    assert rt["median_elapsed_minutes"] == statistics.median([120] * 40 + [30] * 10) == 120
    # the header's tow % is over ALL 109 calls; the comparison cohort is 108: both are stated, and reconcile
    header = g["doors"]["summary"]["tow_pct"]
    assert header == round(100 * fig["tow"]["towed"]["calls"] / pops["raw_selection_total"]["count"], 1) == 28.4
    # the vehicle figure /figures stores (derive_all) equals the view's (derive_recency) at default filters
    assert (fig["vehicles"]["seen"], fig["vehicles"]["distinct"]) == (
        g["doors"]["summary"]["seen"], g["doors"]["summary"]["distinct"])


def test_a_tracked_type_with_no_calls_is_empty_and_says_so_on_every_surface(client):
    g = gather(client, METER, "default")
    assert (g["doors"]["count"], g["blocks"]["count"], g["doors"]["latest"]) == (0, 0, None)
    assert g["doors"]["summary"]["tow_pct"] is None and g["doors"]["summary"]["unique_pct"] is None
    meta = g["csv"][0]
    assert (meta["doorway_count"], meta["block_count"], meta["latest_call_date"]) == ("0", "0", "")
    assert meta["violation_type"] == "Meter Violation"
    assert g["brief"].h1 == ["Meter Violation"] and "Doorways (0)" in g["brief"].h2
    assert g["figures"]["figures"]["population"] == 0
    if g["page"] is not None:
        assert g["page"]["latest-date"] == "unknown" and g["page"]["tow-pct-stat"] == "-"


def test_an_untracked_type_is_a_404_on_every_route_and_says_it_covers_no_type(client):
    for path in ("/api/types/no-such-type/doorways", "/api/types/no-such-type/blocks",
                 "/api/types/no-such-type/figures", "/api/types/no-such-type/export.csv"):
        resp = client.get(path)
        assert resp.status_code == 404 and resp.get_json() == {"error": "untracked", "slug": "no-such-type"}
    for path in ("/types/no-such-type", "/types/no-such-type/export"):
        resp = client.get(path)
        assert resp.status_code == 404
        assert "This page covers no violation type." in html.unescape(resp.get_data(as_text=True))


def test_two_types_read_from_one_mirror_do_not_leak_into_each_others_exports(client):
    """Same mirror state, different types: each export lists only its own
    doorways and names its own type, and their tow sentences differ."""
    bd, nps = gather(client, BD, "default"), gather(client, NPS, "default")
    assert {r["Address"] for r in bd["csv"][1][1]}.isdisjoint({r["Address"] for r in nps["csv"][1][1]})
    assert {r["Violation Type"] for r in bd["csv"][1][1] + bd["csv"][2][1]} == {"Blocking Driveway"}
    assert {r["Violation Type"] for r in nps["csv"][1][1] + nps["csv"][2][1]} == {"No Parking Sign"}
    assert bd["csv"][0]["tow_effect"] != nps["csv"][0]["tow_effect"]
    assert bd["brief"].tow != nps["brief"].tow
