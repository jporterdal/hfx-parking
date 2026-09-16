"""Find the Halifax doorways where parking enforcement cannot win.

The script joins three HRM open datasets that are published apart:
  1. Cityworks Service Requests (the call).
  2. Cityworks Service Requests Custom Fields (the violation, the tow, the vehicle).
  3. The same custom fields table again, for the vehicle make, model and colour.

For each address it counts the calls, counts the tows, and counts how many
distinct vehicles were involved. An address with many calls, almost no tows,
and a different vehicle nearly every time is an address where enforcement has
already been tried and has not worked.

Run:  python3 src/hotspots.py --violation Driveway

The violation is matched as a substring, because HRM records the same problem
under more than one label. A blocked driveway is filed as both
"Blocking Driveway (DISPATCH)" and "DRIVEWAY".
"""

import argparse
import collections
import csv
import datetime
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
import zoneinfo

BASE = "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services"
SR = BASE + "/Cityworks_Service_Requests/FeatureServer/0/query"
CF = BASE + "/Cityworks_Service_Requests_Custom_Fields/FeatureServer/0/query"
DA = BASE + "/Census_2021_Dissemination_Areas/FeatureServer/0/query"
PAGE = 1000
# Halifax observes AST (UTC-4) and ADT (UTC-3), switching on the same schedule as
# the rest of North America. A fixed offset was wrong for roughly half the year;
# zoneinfo carries the actual transition dates. Timestamps arrive in UTC.
HALIFAX = zoneinfo.ZoneInfo("America/Halifax")
VEHICLE_FIELDS = ("Vehicle Make", "Vehicle Model", "Vehicle Colour")
STREET_NUMBER = re.compile(r"^\s*\d+[A-Z]?\s+")


def query(url, where, out_fields, order_by):
    """Page through an ArcGIS feature service and return all attribute rows."""
    offset, rows = 0, []
    while True:
        body = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": str(PAGE),
            "orderByFields": order_by,
        }
        req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode())
        for attempt in range(4):
            try:
                payload = json.load(urllib.request.urlopen(req, timeout=120))
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(3)
        if "error" in payload:
            raise RuntimeError(payload["error"])
        features = payload.get("features", [])
        rows += [f["attributes"] for f in features]
        if len(features) < PAGE:
            return rows
        offset += PAGE


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def to_local(epoch_ms):
    if epoch_ms in (None, ""):
        return None
    utc = datetime.datetime.fromtimestamp(epoch_ms / 1000, datetime.UTC)
    return utc.astimezone(HALIFAX)


def clean_address(raw):
    """Strip the city and postal code so one doorway is one key."""
    if not raw:
        return None
    head = raw.upper().split(",")[0].strip()
    return re.sub(r"\s+", " ", head) or None


def fetch_blocks():
    """HRM's Census 2021 Dissemination Areas, with a bounding box for each."""
    print("fetching census blocks ...", file=sys.stderr)
    offset, blocks = 0, []
    while True:
        body = {
            "where": "1=1",
            "outFields": "DAUID,DAPOP2021,DATDWELL20",
            "returnGeometry": "true",
            "outSR": "4326",
            "geometryPrecision": "6",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": "200",
        }
        req = urllib.request.Request(DA, data=urllib.parse.urlencode(body).encode())
        payload = json.load(urllib.request.urlopen(req, timeout=180))
        if "error" in payload:
            raise RuntimeError(payload["error"])
        features = payload.get("features", [])
        for f in features:
            rings = f.get("geometry", {}).get("rings") or []
            if not rings:
                continue
            xs = [p[0] for r in rings for p in r]
            ys = [p[1] for r in rings for p in r]
            blocks.append(
                {
                    "id": f["attributes"]["DAUID"],
                    "dwellings": f["attributes"].get("DATDWELL20") or 0,
                    "people": f["attributes"].get("DAPOP2021") or 0,
                    "rings": rings,
                    "box": (min(xs), min(ys), max(xs), max(ys)),
                }
            )
        if len(features) < 200:
            break
        offset += 200
    print(f"  {len(blocks)} blocks", file=sys.stderr)
    return blocks


def in_block(lon, lat, block):
    """Even-odd ray cast across every ring, so interior holes exclude correctly."""
    x0, y0, x1, y1 = block["box"]
    if not (x0 <= lon <= x1 and y0 <= lat <= y1):
        return False
    hits = False
    for ring in block["rings"]:
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            if (ay > lat) != (by > lat):
                if lon < ax + (lat - ay) / (by - ay) * (bx - ax):
                    hits = not hits
    return hits


def find_block(lon, lat, blocks):
    for b in blocks:
        if in_block(lon, lat, b):
            return b
    return None


def street_of(address):
    """The street an address sits on, so a block can be named the way people name it."""
    s = STREET_NUMBER.sub("", address).strip()
    s = re.sub(r"\s*&\s*.*$", "", s).strip()   # an intersection takes its first street
    return s or None


def load(violation):
    """Fetch every call matching a violation label, with its outcome fields."""
    print(f"fetching request ids matching {violation!r} ...", file=sys.stderr)
    tagged = query(
        CF,
        "CUSTOM_FIELD_NAME='Alleged Violation' "
        f"AND UPPER(CUSTOM_FIELD_VALUE) LIKE '%{violation.upper()}%'",
        "REQUESTID,CUSTOM_FIELD_VALUE",
        "ObjectId",
    )
    labels = sorted({r["CUSTOM_FIELD_VALUE"] for r in tagged})
    print(f"  labels matched: {labels}", file=sys.stderr)
    ids = sorted({str(r["REQUESTID"]) for r in tagged})
    print(f"  {len(ids)} calls", file=sys.stderr)

    wanted = "','".join(("Vehicle Was Towed", "Property Ownership") + VEHICLE_FIELDS)
    calls, extras = [], []
    for i, chunk in enumerate(chunked(ids, 300)):
        joined = ",".join(chunk)
        calls += query(
            SR,
            f"REQUEST_ID IN ({joined})",
            "REQUEST_ID,DATE_INITIATED,DATE_CLOSED,ADDRESS,COMMUNITY,DISTRICT,"
            "RESOLUTION,LATITUDE,LONGITUDE,INITIATED_BY",
            "REQUEST_ID",
        )
        extras += query(
            CF,
            f"REQUESTID IN ({joined}) AND CUSTOM_FIELD_NAME IN ('{wanted}')",
            "REQUESTID,CUSTOM_FIELD_NAME,CUSTOM_FIELD_VALUE",
            "ObjectId",
        )
        print(f"  batch {i + 1} done", file=sys.stderr)

    fields = collections.defaultdict(dict)
    for row in extras:
        fields[row["REQUESTID"]][row["CUSTOM_FIELD_NAME"]] = row["CUSTOM_FIELD_VALUE"]
    return calls, fields


def vehicle_key(field_map):
    """A rough identity for one vehicle. Returns None when nothing was recorded."""
    parts = tuple((field_map.get(f) or "").strip().upper() for f in VEHICLE_FIELDS)
    return parts if any(parts) else None


def build(calls, fields, min_calls, recur_days, latest, blocks):
    """Group calls by address, and place each address in its census block."""
    recent_from = latest - datetime.timedelta(days=365)
    by_address = collections.defaultdict(list)
    for call in calls:
        started = to_local(call["DATE_INITIATED"])
        address = clean_address(call["ADDRESS"])
        if started and address:
            by_address[address].append((started, call))

    rows = []
    for address, entries in by_address.items():
        entries.sort(key=lambda e: e[0])
        times = [e[0] for e in entries]
        ids = [e[1]["REQUEST_ID"] for e in entries]

        recent = sum(1 for t in times if t >= recent_from)
        if recent == 0:
            continue  # A doorway that stopped calling is not Monday's problem.

        tows = sum(1 for i in ids if fields[i].get("Vehicle Was Towed") == "Y")
        seen = [vehicle_key(fields[i]) for i in ids]
        seen = [s for s in seen if s]
        repeats = sum(
            1
            for k, t in enumerate(times)
            if any((times[j] - t).days <= recur_days for j in range(k + 1, len(times)))
        )
        gaps = [(times[i + 1] - times[i]).days for i in range(len(times) - 1)]

        # A median coordinate shrugs off one mistyped call.
        lats = [c["LATITUDE"] for _, c in entries if c["LATITUDE"]]
        lons = [c["LONGITUDE"] for _, c in entries if c["LONGITUDE"]]
        lat = statistics.median(lats) if lats else None
        lon = statistics.median(lons) if lons else None
        block = find_block(lon, lat, blocks) if (lat and lon) else None

        rows.append(
            {
                "address": address,
                "calls_12mo": recent,
                "calls_total": len(times),
                "district": entries[-1][1]["DISTRICT"],
                "community": entries[-1][1]["COMMUNITY"],
                "owner": collections.Counter(
                    fields[i].get("Property Ownership") for i in ids
                ).most_common(1)[0][0],
                "tows": tows,
                "tow_rate": tows / len(times),
                "vehicles_seen": len(seen),
                "vehicles_distinct": len(set(seen)),
                "repeat_calls": repeats,
                "median_gap_days": statistics.median(gaps) if gaps else None,
                "last_call": max(times).strftime("%Y-%m-%d"),
                "street": street_of(address),
                "block": block["id"] if block else None,
                "block_dwellings": block["dwellings"] if block else None,
                "lat": lat,
                "lon": lon,
            }
        )

    rows = [r for r in rows if r["calls_12mo"] >= min_calls]
    # Rank by calls still arriving, weighted by how little enforcement achieved
    # there. An address that already gets towed is already being handled.
    rows.sort(key=lambda r: -(r["calls_12mo"] * (1 - r["tow_rate"])))
    return rows


def roll_blocks(rows, min_doorways):
    """Group the listed doorways into their census blocks.

    One doorway calling is a doorway problem. A block where ten doorways are all
    still calling is not ten doorway problems, and ten signs is the wrong answer.
    """
    groups = collections.defaultdict(
        lambda: {"doorways": [], "streets": collections.Counter(), "dwellings": 0}
    )
    for r in rows:
        if not r["block"]:
            continue
        g = groups[r["block"]]
        g["doorways"].append(r)
        g["dwellings"] = r["block_dwellings"] or 0
        if r["street"]:
            g["streets"][r["street"]] += r["calls_12mo"]

    out = []
    for block_id, g in groups.items():
        doors = g["doorways"]
        recent = sum(d["calls_12mo"] for d in doors)
        dwell = g["dwellings"]
        out.append(
            {
                "block": block_id,
                "streets": " / ".join(s for s, _ in g["streets"].most_common(2)),
                "doorways": len(doors),
                "calls_12mo": recent,
                "calls_total": sum(d["calls_total"] for d in doors),
                "tows": sum(d["tows"] for d in doors),
                "dwellings": dwell,
                "calls_per_1k_dwellings": round(1000 * recent / dwell, 1) if dwell else None,
                "district": collections.Counter(
                    d["district"] for d in doors
                ).most_common(1)[0][0],
                "worst_doorway": max(doors, key=lambda d: d["calls_12mo"])["address"],
                "addresses": "; ".join(sorted(d["address"] for d in doors)),
            }
        )
    out = [b for b in out if b["doorways"] >= min_doorways]
    # A block earns attention by how many separate doorways are still calling,
    # then by the load per dwelling rather than the raw count, so a dense block
    # does not outrank a genuinely worse one just for having more front doors.
    out.sort(key=lambda b: (-b["doorways"], -(b["calls_per_1k_dwellings"] or 0)))
    return out


def write_csv(rows, path):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_brief(rows, calls, fields, violation, path, recur_days, latest):
    total = len(calls)
    tows = sum(1 for c in calls if fields[c["REQUEST_ID"]].get("Vehicle Was Towed") == "Y")
    seen = sum(r["vehicles_seen"] for r in rows)
    distinct = sum(r["vehicles_distinct"] for r in rows)

    lines = [
        f"# Doorways enforcement cannot fix: alleged violation matching {violation!r}",
        "",
        "Source: HRM open data. Cityworks Service Requests joined to Cityworks Service "
        "Requests Custom Fields.",
        f"Most recent call in the data: {latest.strftime('%Y-%m-%d')}.",
        "",
        f"- Calls in scope: {total:,}.",
        f"- Calls that ended in a tow: {tows:,} ({100 * tows / total:.1f} per cent).",
        f"- Addresses below, each still calling in the last 12 months: {len(rows):,}.",
        f"- Distinct vehicles at those addresses: {distinct:,} across {seen:,} calls "
        f"({100 * distinct / seen:.0f} per cent unique).",
        "",
        "Each row is a doorway that keeps generating calls.",
        "Nearly every call is a different vehicle, so there is no repeat offender to deter.",
        "Send these to whoever owns signs, bollards and curb paint, not to an officer.",
        "",
        "Read the \"On this block\" column before you order a sign. Where it says 1, the",
        "doorway is alone and a marking or bollard fixes it. Where it says 5 or 10, the",
        "block is the problem and out/blocks.md is the list to work from instead.",
        "",
        "| # | Address | Calls 12mo | Calls all time | Tows | Distinct vehicles | Median gap | Last call | On this block | District |",
        "|---|---------|-----------:|---------------:|-----:|------------------:|-----------:|-----------|--------------:|----------|",
    ]
    for i, r in enumerate(rows[:40], 1):
        gap = f"{r['median_gap_days']:.0f} d" if r["median_gap_days"] is not None else "-"
        veh = f"{r['vehicles_distinct']} of {r['vehicles_seen']}"
        nb = r.get("block_doorways_calling", 1)
        lines.append(
            f"| {i} | {r['address']} | {r['calls_12mo']} | {r['calls_total']} | {r['tows']} "
            f"| {veh} | {gap} | {r['last_call']} | {nb} | {r['district']} |"
        )
    lines += [
        "",
        f"Repeat means another call at the same address within {recur_days} days.",
        "A tow is the outcome the city recorded. HRM publishes no ticketing field, so a",
        "call with no tow is a call with no recorded outcome, not proof that nothing was done.",
        "Vehicle identity is make, model and colour. It is not a plate, so two identical cars",
        "count as one. That makes the distinct count a floor, not a ceiling.",
        "",
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(lines))


def write_board(rows, blocks, calls, fields, latest, template, network, path):
    """Fill the standalone board template with today's numbers.

    The board is regenerated from the same run that writes the CSVs, so the page
    a person opens can never disagree with the briefs sitting beside it.
    """
    keep_row = ("address", "calls_12mo", "calls_total", "district", "community", "owner",
                "tows", "vehicles_seen", "vehicles_distinct", "median_gap_days",
                "last_call", "street", "block", "block_doorways_calling", "lat", "lon")
    page_rows = []
    for i, r in enumerate(rows):
        page_rows.append({
            "i": i, "a": r["address"].title(), "d": str(r["district"] or "?"),
            "c": (r["community"] or "").title(), "st": (r["street"] or "").title(),
            "bk": r["block"] or "", "nb": r.get("block_doorways_calling", 1),
            "m": r["calls_12mo"], "t": r["calls_total"], "w": r["tows"],
            "vd": r["vehicles_distinct"], "vs": r["vehicles_seen"],
            "g": None if r["median_gap_days"] is None else round(r["median_gap_days"]),
            "l": r["last_call"], "o": r["owner"] or "",
            "lat": round(r["lat"], 6), "lon": round(r["lon"], 6),
        })
    page_blocks = []
    for i, b in enumerate(blocks):
        page_blocks.append({
            "i": i, "bk": b["block"], "s": b["streets"].title(), "n": b["doorways"],
            "m": b["calls_12mo"], "t": b["calls_total"], "w": b["tows"],
            "dw": b["dwellings"], "r": b["calls_per_1k_dwellings"],
            "d": str(b["district"] or "?"), "worst": b["worst_doorway"].title(),
            "addrs": [a.strip().title() for a in b["addresses"].split(";") if a.strip()],
        })
    with open(template) as fh:
        page = fh.read()
    with open(network) as fh:
        net = fh.read()
    # Header numbers come from the same run as the rows, never from the template.
    total = len(calls)
    tows = sum(1 for c in calls if fields[c["REQUEST_ID"]].get("Vehicle Was Towed") == "Y")
    seen = sum(r["vehicles_seen"] for r in rows)
    distinct = sum(r["vehicles_distinct"] for r in rows)
    with_neighbour = sum(1 for r in rows if r.get("block_doorways_calling", 1) >= 2)
    fills = {
        "__LATEST__": latest.strftime("%Y-%m-%d"),
        "__N_DOORWAYS__": f"{len(rows):,}",
        "__N_BLOCKS__": f"{len(blocks):,}",
        "__TOW_PCT__": f"{100 * tows / total:.1f}%" if total else "-",
        "__UNIQUE_PCT__": f"{100 * distinct / seen:.0f}%" if seen else "-",
        "__DISTINCT__": f"{distinct:,}",
        "__SEEN__": f"{seen:,}",
        "__WITH_NEIGHBOUR__": f"{with_neighbour:,}",
        "__MAP__": net,
        "__DATA__": json.dumps({"rows": page_rows, "blocks": page_blocks}, separators=(",", ":")),
    }
    for key, val in fills.items():
        page = page.replace(key, val)
    with open(path, "w") as fh:
        fh.write(page)
    return len(page)


def write_block_brief(blocks, rows, path):
    listed = sum(b["doorways"] for b in blocks)
    lines = [
        "# Blocks, not doorways",
        "",
        "A doorway calling on its own wants a bollard or a driveway marking.",
        "A block where several doorways are all still calling is one problem, not several,",
        "and putting up a sign at each address is the wrong answer to it.",
        "",
        "Blocks are HRM's Census 2021 Dissemination Areas, which carry a dwelling count,",
        "so the load is shown per 1,000 dwellings as well as raw. Dense blocks call more;",
        "the rate is what separates a real problem from a lot of front doors.",
        "",
        f"- Blocks with two or more doorways still calling: {len(blocks):,}.",
        f"- Doorways they hold: {listed:,} of the {len(rows):,} on the doorway list.",
        "",
        "| # | Streets | Doorways | Calls 12mo | All time | Tows | Dwellings | Per 1k dwellings | District |",
        "|---|---------|---------:|-----------:|---------:|-----:|----------:|-----------------:|----------|",
    ]
    for i, b in enumerate(blocks[:30], 1):
        rate = f"{b['calls_per_1k_dwellings']:.1f}" if b["calls_per_1k_dwellings"] else "-"
        lines.append(
            f"| {i} | {b['streets']} | {b['doorways']} | {b['calls_12mo']} | {b['calls_total']} "
            f"| {b['tows']} | {b['dwellings']:,} | {rate} | {b['district']} |"
        )
    lines += [
        "",
        "Streets name the block by where its calls come from, not by a census label.",
        "The block id is in the CSV if you need to join back to the census layer.",
        "",
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--violation", default="Driveway")
    ap.add_argument("--min-calls", type=int, default=2,
                    help="minimum calls in the last 12 months")
    ap.add_argument("--recur-days", type=int, default=365)
    ap.add_argument("--district", help="limit the brief to one district")
    ap.add_argument("--min-doorways", type=int, default=2,
                    help="minimum still-calling doorways for a block to be listed")
    ap.add_argument("--csv", default="out/watchlist.csv")
    ap.add_argument("--brief", default="out/watchlist.md")
    ap.add_argument("--block-csv", default="out/blocks.csv")
    ap.add_argument("--block-brief", default="out/blocks.md")
    ap.add_argument("--board", default="out/triage-board.html",
                    help="standalone page anyone can open in a browser")
    ap.add_argument("--template", default="web/template.html")
    ap.add_argument("--network", default="web/map-network.json")
    args = ap.parse_args()

    calls, fields = load(args.violation)
    latest = max(to_local(c["DATE_INITIATED"]) for c in calls if c["DATE_INITIATED"])
    blocks_geo = fetch_blocks()
    rows = build(calls, fields, args.min_calls, args.recur_days, latest, blocks_geo)
    if args.district:
        rows = [r for r in rows if str(r["district"]) == args.district]
    if not rows:
        print("no addresses met the threshold", file=sys.stderr)
        return 1

    blocks = roll_blocks(rows, args.min_doorways)
    # Tell each doorway how many of its neighbours are also still calling. That
    # single number is what decides a bollard against a block-wide measure.
    neighbours = {b["block"]: b["doorways"] for b in blocks}
    for r in rows:
        r["block_doorways_calling"] = neighbours.get(r["block"], 1)

    write_csv(rows, args.csv)
    write_brief(rows, calls, fields, args.violation, args.brief, args.recur_days, latest)
    if blocks:
        write_csv(blocks, args.block_csv)
        write_block_brief(blocks, rows, args.block_brief)

    try:
        size = write_board(rows, blocks, calls, fields, latest, args.template, args.network, args.board)
        print(f"wrote {args.board} ({size // 1024} KB)")
    except FileNotFoundError as e:
        print(f"skipped the board: {e.filename} is missing", file=sys.stderr)

    unmatched = sum(1 for r in rows if not r["block"])
    print(f"wrote {args.csv} and {args.brief} ({len(rows)} addresses)")
    print(f"wrote {args.block_csv} and {args.block_brief} ({len(blocks)} blocks)")
    if unmatched:
        print(f"note: {unmatched} addresses fell in no census block", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
