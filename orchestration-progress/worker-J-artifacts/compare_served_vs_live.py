"""Task 9.4: served (Flask test client, real mirror, GET only) vs the live pipeline's
kept outputs. Usage: python3 compare_served_vs_live.py <live_dir> <out_dir> [query]

Every normalisation is listed in NORMALISATIONS and printed in the report.
"""
import csv
import html.parser
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, "/home/ross/work/hfx-parking/src")
from app import server  # noqa: E402

live_dir = pathlib.Path(sys.argv[1])
out_dir = pathlib.Path(sys.argv[2])
query = sys.argv[3] if len(sys.argv) > 3 else ""
out_dir.mkdir(parents=True, exist_ok=True)

NORMALISATIONS = [
    "address, street, community, block streets/worst doorway/addresses: live upper-case text compared to served text after str.title() (the served rule)",
    "district: live value compared to str(value or '?') (the served rule; live None -> '?')",
    "missing owner/block/community: live '' compared to served '' (JSON) / '' (CSV) / en dash never applies to these text cells (checked as observed)",
    "median gap: served value is round(live median) whole days (live is a float such as 9.0 or 9.5); compared as int(served) == round(float(live)); half-day cases counted",
    "lat/lon: served is round(x, 6); compared as served == round(float(live), 6); max absolute difference reported",
    "exports: header row labels replaced by the JSON keys they carry (Address->a ...); 'Violation Type' column removed after checking every row carries the type name; preamble/filter rows/clocks/notes/limits/tow sentence not compared (only the doorway and block tables, the counts and latest date)",
    "HTML export: missing value shown as en dash compared to JSON null; thousands separators not present in the served cells (none to strip)",
    "live tow_rate is a float ratio the app does not serve: compared as tows/calls_total == float(tow_rate) exactly",
    "live brief cells: '12 of 15' split to vd/vs; 'N d' and '-' gap to int/None; 'N,NNN' dwellings to int; '-' rate to None-or-0",
    "live block brief/CSV 'Calls in scope' and tow count are not served as such: compared through figures.calls_raw and summary.tow_pct (1 dp) with a range check on the tow count",
]


class Cmp:
    def __init__(self):
        self.checks = []

    def add(self, name, n, mismatches):
        self.checks.append((name, n, mismatches))

    def report(self):
        lines = []
        bad = 0
        for name, n, mm in self.checks:
            lines.append(f"{'OK  ' if not mm else 'DIFF'} {name}: {n} compared, {len(mm)} mismatches")
            for m in mm[:25]:
                lines.append(f"       {m}")
            if len(mm) > 25:
                lines.append(f"       ... {len(mm) - 25} more")
            bad += len(mm)
        lines.append(f"TOTAL mismatches: {bad}")
        return "\n".join(lines), bad


cmp = Cmp()

# ------------------------------------------------------------------ served side
app = server.create_app()
app.testing = True
client = app.test_client()

types = client.get("/api/types").get_json()
slug = next(t["slug"] for t in types["types"] if t["type"] == "Blocking Driveway")
print("slug:", slug, "| default_slug:", types["default_slug"])
qs = ("?" + query) if query else ""


def get(path):
    r = client.get(path + qs)
    assert r.status_code == 200, (path, r.status_code)
    return r


door = get(f"/api/types/{slug}/doorways").get_json()
blk = get(f"/api/types/{slug}/blocks").get_json()
fig = client.get(f"/api/types/{slug}/figures").get_json()  # filter independent
csv_resp = get(f"/api/types/{slug}/export.csv")
html_resp = get(f"/types/{slug}/export")
csv_text = csv_resp.get_data(as_text=True)
html_text = html_resp.get_data(as_text=True)
(out_dir / "doorways.json").write_text(json.dumps(door, indent=1))
(out_dir / "blocks.json").write_text(json.dumps(blk, indent=1))
(out_dir / "figures.json").write_text(json.dumps(fig, indent=1))
(out_dir / "export.csv").write_text(csv_text)
(out_dir / "export.html").write_text(html_text)
print("filters served:", door["filters"])

# ------------------------------------------------------------------- live side
def read_csv(p):
    with open(p, newline="") as fh:
        return list(csv.DictReader(fh))


live_d = read_csv(live_dir / "watchlist.csv")
live_b = read_csv(live_dir / "blocks.csv")
brief_d = (live_dir / "watchlist.md").read_text()
brief_b = (live_dir / "blocks.md").read_text()
print(f"live doorways {len(live_d)}, live blocks {len(live_b)}; served doorways {door['count']}, blocks {blk['count']}")


def opt_int(s):
    return None if s == "" else int(s)


def opt_float(s):
    return None if s == "" else float(s)


def expected_door(r):
    """Live CSV row -> the JSON payload shape the app serves, by the served rules."""
    gap = opt_float(r["median_gap_days"])
    lat = opt_float(r["lat"])
    lon = opt_float(r["lon"])
    return {
        "a": r["address"].title(), "d": str(r["district"] or "?"),
        "c": (r["community"] or "").title(), "st": (r["street"] or "").title(),
        "bk": r["block"] or "", "nb": int(r["block_doorways_calling"]),
        "m": int(r["calls_12mo"]), "t": int(r["calls_total"]), "w": int(r["tows"]),
        "vd": int(r["vehicles_distinct"]), "vs": int(r["vehicles_seen"]),
        "rp": int(r["repeat_calls"]),
        "g": None if gap is None else round(gap),
        "l": r["last_call"], "o": r["owner"] or "",
        "lat": None if lat is None else round(lat, 6),
        "lon": None if lon is None else round(lon, 6),
    }


def expected_block(r):
    rate = opt_float(r["calls_per_1k_dwellings"])
    return {
        "bk": r["block"], "s": r["streets"].title(), "n": int(r["doorways"]),
        "m": int(r["calls_12mo"]), "t": int(r["calls_total"]), "w": int(r["tows"]),
        "dw": int(r["dwellings"]), "r": rate, "d": str(r["district"] or "?"),
        "worst": r["worst_doorway"].title(),
        "addrs": [a.strip().title() for a in r["addresses"].split(";") if a.strip()],
    }


# ---------------------------------------------------- 1. JSON doorways vs live CSV
def compare_rows(name, live_rows, exp_fn, key, served_rows, keyfield, fields):
    """Keyed comparison + order comparison + per-field comparison."""
    exp = [(r[key], exp_fn(r), r) for r in live_rows]
    exp_keys = [e[1][keyfield] for e in exp]
    if len(set(exp_keys)) != len(exp_keys):
        cmp.add(f"{name}: title-cased keys unique", len(exp_keys), ["title-case collision in live keys"])
    served_by = {s[keyfield]: s for s in served_rows}
    served_keys = [s[keyfield] for s in served_rows]
    mm = []
    for k in exp_keys:
        if k not in served_by:
            mm.append(f"missing from served: {k}")
    for k in served_keys:
        if k not in set(exp_keys):
            mm.append(f"extra in served: {k}")
    cmp.add(f"{name}: same key set ({len(exp_keys)} live / {len(served_keys)} served)", len(exp_keys), mm)
    cmp.add(f"{name}: same order", len(exp_keys),
            [] if [k for k in exp_keys if k in served_by] == [k for k in served_keys if k in set(exp_keys)]
            else ["order differs"])
    fm = []
    n = 0
    for _k, e, raw in exp:
        k = e[keyfield]
        s = served_by.get(k)
        if not s:
            continue
        for f in fields:
            n += 1
            if e[f] != s.get(f):
                fm.append(f"{k} [{f}] live={e[f]!r} served={s.get(f)!r}")
    cmp.add(f"{name}: every listed field ({len(fields)} fields)", n, fm)
    return exp


door_fields = ["a", "d", "c", "st", "bk", "nb", "m", "t", "w", "vd", "vs", "rp", "g", "l", "o", "lat", "lon"]
block_fields = ["bk", "s", "n", "m", "t", "w", "dw", "r", "d", "worst", "addrs"]

exp_d = compare_rows("JSON /doorways vs live watchlist.csv", live_d, expected_door, "address",
                     door["rows"], "a", door_fields)
exp_b = compare_rows("JSON /blocks vs live blocks.csv", live_b, expected_block, "block",
                     blk["blocks"], "bk", block_fields)

# figures the app does not serve directly: tow_rate identity, half-day gap cases, lat/lon precision
served_d_by = {s["a"]: s for s in door["rows"]}
mm = []
for r in live_d:
    s = served_d_by.get(r["address"].title())
    if s and s["w"] / s["t"] != float(r["tow_rate"]):
        mm.append(f"{r['address']}: tows/calls_total={s['w'] / s['t']!r} live tow_rate={r['tow_rate']}")
cmp.add("served tows/calls_total == live tow_rate (exact float)", len(live_d), mm)
half = [r["address"] for r in live_d if r["median_gap_days"] and float(r["median_gap_days"]) % 1 == 0.5]
print("half-day median gaps in live doorways:", len(half))
maxdiff = 0.0
for r in live_d:
    s = served_d_by.get(r["address"].title())
    if s and r["lat"]:
        maxdiff = max(maxdiff, abs(float(r["lat"]) - s["lat"]), abs(float(r["lon"]) - s["lon"]))
print("max |live - served| for lat/lon (rounding to 6 dp):", maxdiff)
# a served block dwelling count against the doorway's own live block_dwellings
blk_dw = {b["bk"]: b["dw"] for b in blk["blocks"]}
mm = []
n = 0
for r in live_d:
    if r["block"] and r["block"] in blk_dw and r["block_dwellings"]:
        n += 1
        if int(r["block_dwellings"]) != blk_dw[r["block"]]:
            mm.append(f"{r['address']} block {r['block']}: live doorway block_dwellings={r['block_dwellings']} served block dw={blk_dw[r['block']]}")
cmp.add("live doorway block_dwellings == served block dwellings (blocks that are listed)", n, mm)

# --------------------------------------------------- 2. export.csv tables
rows = list(csv.reader(io.StringIO(csv_text)))


def table(marker, columns):
    i = next(k for k, r in enumerate(rows) if r == [marker])
    header = rows[i + 1]
    j = i + 2
    body = []
    while j < len(rows) and rows[j]:
        body.append(rows[j])
        j += 1
    return header, body


dh, db_rows = table("DOORWAYS", None)
bh, bb_rows = table("BLOCKS", None)
d_keys = [k for k, _ in server._DOORWAY_CSV_COLUMNS]
b_keys = [k for k, _ in server._BLOCK_CSV_COLUMNS]
assert dh == [l for _, l in server._DOORWAY_CSV_COLUMNS] + ["Violation Type"], dh
assert bh == [l for _, l in server._BLOCK_CSV_COLUMNS] + ["Violation Type"], bh
cmp.add("export.csv: 'Violation Type' column is 'Blocking Driveway' on every row",
        len(db_rows) + len(bb_rows),
        [r for r in db_rows + bb_rows if r[-1] != "Blocking Driveway"])
pre = {r[0]: r[1] for r in rows if len(r) == 2}
cmp.add("export.csv preamble doorway_count/block_count/latest_call_date",
        3, [f"{k}: csv={pre.get(k)} json={v}" for k, v in
            (("doorway_count", str(door["count"])), ("block_count", str(blk["count"])),
             ("latest_call_date", door["latest"])) if pre.get(k) != v])


def cell(v, dash=False):
    if isinstance(v, list):
        return "; ".join(v)
    if v is None:
        return "–" if dash else ""
    return str(v)


def compare_table(name, body, keys, expected, dash):
    mm = []
    if len(body) != len(expected):
        mm.append(f"row count {len(body)} vs expected {len(expected)}")
    n = 0
    for pos, (r, e) in enumerate(zip(body, expected)):
        got = r[:-1] if not dash else r
        for c, k in enumerate(keys):
            n += 1
            want = cell(e[k], dash)
            if got[c] != want:
                mm.append(f"row {pos} [{k}] live-expected={want!r} export={got[c]!r}")
    cmp.add(name, n, mm)


compare_table("export.csv DOORWAYS vs live (position + every column)", db_rows, d_keys, [e for _, e, _ in exp_d], False)
compare_table("export.csv BLOCKS vs live (position + every column)", bb_rows, b_keys, [e for _, e, _ in exp_b], False)


# ---------------------------------------------------- 3. export HTML tables
class T(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.cur, self.row, self.cell, self.in_cell = [], None, None, None, False
        self.caps = []
        self.in_cap = False

    def handle_starttag(self, tag, a):
        if tag == "table":
            self.cur = []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell, self.in_cell = "", True
        elif tag == "caption":
            self.in_cap = True

    def handle_data(self, d):
        if self.in_cell:
            self.cell += d
        if self.in_cap:
            self.caps.append(d)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.row.append(self.cell)
            self.in_cell = False
        elif tag == "tr":
            self.cur.append(self.row)
        elif tag == "table":
            self.tables.append(self.cur)
        elif tag == "caption":
            self.in_cap = False


tp = T()
tp.feed(html_text)
assert len(tp.tables) == 2, len(tp.tables)
hd, hb = tp.tables
assert hd[0] == [l for _, l in server._DOORWAY_CSV_COLUMNS], hd[0]
assert hb[0] == [l for _, l in server._BLOCK_CSV_COLUMNS], hb[0]
compare_table("export HTML doorway table vs live (position + every column, en dash = missing)", hd[1:], d_keys, [e for _, e, _ in exp_d], True)
compare_table("export HTML block table vs live (position + every column, en dash = missing)", hb[1:], b_keys, [e for _, e, _ in exp_b], True)
m = re.search(r"<h2>Doorways \((\d+)\)</h2>", html_text)
m2 = re.search(r"<h2>Blocks \((\d+)\)</h2>", html_text)
cmp.add("export HTML headline counts", 2,
        [x for x in ((f"doorways {m and m.group(1)} vs {len(live_d)}") if not m or int(m.group(1)) != len(live_d) else None,
                     (f"blocks {m2 and m2.group(1)} vs {len(live_b)}") if not m2 or int(m2.group(1)) != len(live_b) else None) if x])

# ------------------------------------------------------- 4. briefs
def grab(pattern, text):
    m = re.search(pattern, text, re.M)
    assert m, pattern
    return m.groups()


s = door["summary"]
figs = (fig.get("figures") or {}) if fig.get("available") else {}
mm = []
(latest_live,) = grab(r"^Most recent call in the data: (\d{4}-\d\d-\d\d)\.", brief_d)
if latest_live != door["latest"]:
    mm.append(f"latest live={latest_live} served={door['latest']}")
(in_scope,) = grab(r"^- Calls in scope: ([\d,]+)\.", brief_d)
in_scope = int(in_scope.replace(",", ""))
if figs.get("calls_raw") != in_scope:
    mm.append(f"calls in scope live={in_scope} served figures.calls_raw={figs.get('calls_raw')}")
tows_live, pct_live = grab(r"^- Calls that ended in a tow: ([\d,]+) \(([\d.]+) per cent\)\.", brief_d)
tows_live = int(tows_live.replace(",", ""))
if float(pct_live) != s["tow_pct"]:
    mm.append(f"tow pct live={pct_live} served summary.tow_pct={s['tow_pct']}")
if round(100 * tows_live / in_scope, 1) != float(pct_live):
    mm.append("live brief internally inconsistent tow pct")
(n_addr,) = grab(r"^- Addresses below, each still calling in the last 12 months: ([\d,]+)\.", brief_d)
if int(n_addr.replace(",", "")) != door["count"]:
    mm.append(f"addresses live={n_addr} served count={door['count']}")
dv, sn, up = grab(r"^- Distinct vehicles at those addresses: ([\d,]+) across ([\d,]+) calls \((\d+) per cent unique\)\.", brief_d)
for nm, lv, sv in (("distinct", int(dv.replace(",", "")), s["distinct"]),
                   ("seen", int(sn.replace(",", "")), s["seen"]),
                   ("unique_pct", int(up), s["unique_pct"])):
    if lv != sv:
        mm.append(f"{nm} live={lv} served summary={sv}")
(rd,) = grab(r"^Repeat means another call at the same address within (\d+) days\.", brief_d)
if int(rd) != door["filters"]["recur_days"]:
    mm.append(f"recur_days live={rd} served={door['filters']['recur_days']}")
cmp.add("watchlist.md header figures vs served summary/figures/latest/filters (9 figures)", 9, mm)

# tow count range check (served only serves the percentage and the tow cohort)
if figs:
    t = figs["tow"]
    lo = t["towed"]["calls"]
    hi = lo + figs["excluded_no_usable_address_or_date"]
    cmp.add("watchlist.md tow count within served towed calls .. + excluded calls", 1,
            [] if lo <= tows_live <= hi else [f"live {tows_live} not in [{lo}, {hi}]"])

# watchlist table
tab = [l for l in brief_d.splitlines() if re.match(r"^\| \d+ \|", l)]
mm = []
for l in tab:
    c = [x.strip() for x in l.strip().strip("|").split("|")]
    rank = int(c[0])
    sv = door["rows"][rank - 1]
    veh_d, veh_s = re.match(r"(\d+) of (\d+)", c[5]).groups()
    gap = None if c[6] == "-" else int(re.match(r"(\d+) d", c[6]).group(1))
    exp = {"a": c[1].title(), "m": int(c[2]), "t": int(c[3]), "w": int(c[4]), "vd": int(veh_d),
           "vs": int(veh_s), "g": gap, "l": c[7], "nb": int(c[8]), "d": c[9] or "?"}
    for k, v in exp.items():
        if sv[k] != v:
            mm.append(f"rank {rank} [{k}] brief={v!r} served={sv[k]!r}")
cmp.add(f"watchlist.md table rows (top {len(tab)}) vs served rows by rank, 10 cells each", len(tab) * 10, mm)

# block brief
mm = []
(nblocks,) = grab(r"^- Blocks with two or more doorways still calling: ([\d,]+)\.", brief_b)
if int(nblocks.replace(",", "")) != blk["count"]:
    mm.append(f"blocks live={nblocks} served={blk['count']}")
held, listed = grab(r"^- Doorways they hold: ([\d,]+) of the ([\d,]+) on the doorway list\.", brief_b)
if int(held.replace(",", "")) != sum(b["n"] for b in blk["blocks"]):
    mm.append(f"doorways held live={held} served sum(n)={sum(b['n'] for b in blk['blocks'])}")
if int(listed.replace(",", "")) != door["count"]:
    mm.append(f"of the N on doorway list live={listed} served={door['count']}")
cmp.add("blocks.md header figures vs served blocks/doorways", 3, mm)
tab = [l for l in brief_b.splitlines() if re.match(r"^\| \d+ \|", l)]
mm = []
for l in tab:
    c = [x.strip() for x in l.strip().strip("|").split("|")]
    rank = int(c[0])
    sv = blk["blocks"][rank - 1]
    rate = None if c[7] == "-" else float(c[7])
    exp = {"s": c[1].title(), "n": int(c[2]), "m": int(c[3]), "t": int(c[4]), "w": int(c[5]),
           "dw": int(c[6].replace(",", "")), "d": c[8] or "?"}
    for k, v in exp.items():
        if sv[k] != v:
            mm.append(f"rank {rank} [{k}] brief={v!r} served={sv[k]!r}")
    if (rate is None and sv["r"]) or (rate is not None and sv["r"] != rate):
        mm.append(f"rank {rank} [r] brief={rate!r} served={sv['r']!r}")
cmp.add(f"blocks.md table rows (top {len(tab)}) vs served rows by rank, 8 cells each", len(tab) * 8, mm)

# ------------------------------------------------------- 5. figures cross-checks
mm = []
if figs:
    v = figs["vehicles"]
    if v["distinct"] != s["distinct"] or v["seen"] != s["seen"] or v["doorways"] != door["count"]:
        mm.append(f"figures.vehicles {v} vs summary {s} count {door['count']}")
    live_pct = int(up)
    m = re.search(r"\((\d+)% of (\d+) seen\)", v["conclusion"])
    if m and int(m.group(1)) != live_pct:
        mm.append(f"figures.vehicles.conclusion says {m.group(1)}% of {m.group(2)} seen; live brief says {live_pct} per cent unique "
                  f"({v['distinct']}/{v['seen']} = {100 * v['distinct'] / v['seen']:.2f}%)")
cmp.add("figures.vehicles (distinct/seen/doorways and the % in the stored sentence) vs live brief", 4, mm)

text, bad = cmp.report()
print(text)
(out_dir / "report.txt").write_text(text + "\n\nNormalisations:\n" + "\n".join(f"- {n}" for n in NORMALISATIONS) + "\n")
sys.exit(1 if bad else 0)
