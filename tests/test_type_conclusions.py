"""Task 9.8: no tracked violation type presents blocked driveway's conclusion
without its own effectiveness evidence supporting it (design.md D19, restated
in the mirror change's own Risks section because the change that first stated
it is archived).

Three layers, because a per-type conclusion can reach a viewer three ways:

1. **What the server stores and serves** -- `/api/types/<slug>/figures`, the
   brief and the CSV. Seeded with three types whose own numbers differ (one
   shaped like blocked driveway, one the opposite way, one too small to
   conclude anything), then each type's payload, brief and CSV are checked to
   state that type's own conclusion and never another's.
2. **What the page's script does with that payload** -- the tow-thesis IIFE,
   `vehicleThesis`, `neighbourThesis` and `buildAskPrompt` are lifted out of
   the served `index.html` and run in node against the real payloads from (1).
   No browser exists here, so this is the closest the suite gets to "the text a
   viewer reads"; it does not check layout, and it does not run the rest of the
   page. Skipped where node is absent.
3. **What the served file says on its own** -- it is one static file for every
   type, so any driveway-only wording left in it would appear on all 30 pages.

Every seeded call sits in 2024 with no census area, so the doorway/block lists
are not what is under test here: only figures, briefs, CSV and page text are.
"""

import datetime
import html
import json
import re
import shutil
import subprocess

import pytest

from app import server as app_server
from mirror import type_figures as mirror_type_figures
from test_per_type import seed_call, seed_driveway_pattern, seed_opposite_pattern

pytestmark = pytest.mark.db

DRIVEWAY = "Blocking Driveway"       # own figures: no detectable tow difference, mostly distinct
SIGNS = "No Parking Sign"            # own figures: towed calls recur MORE, one vehicle repeats
HYDRANT = "Within 5M of Hydrant"     # own figures: too few calls to conclude anything
ALL_TYPES = (DRIVEWAY, SIGNS, HYDRANT)

_NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture
def client(clean_db):
    app = app_server.create_app()
    app.testing = True
    return app.test_client()


@pytest.fixture
def three_types(clean_db):
    """Blocking Driveway (driveway-shaped), No Parking Sign (opposite-shaped),
    Within 5M of Hydrant (two calls at one address), all stored through the
    same `compute_and_store` the mirror's own reload hook calls."""
    next_oid = seed_driveway_pattern(clean_db, "DRIVEWAY", object_id_start=1)
    next_oid = seed_opposite_pattern(clean_db, "No Parking Sign", object_id_start=next_oid)
    base = datetime.datetime(2024, 1, 1, 12, tzinfo=datetime.UTC)
    seed_call(clean_db, next_oid * 10 + 1, next_oid, "1 TINY ST, HALIFAX", base,
              "Within 5M of Hydrant", towed="Y")
    seed_call(clean_db, (next_oid + 1) * 10 + 1, next_oid + 1, "1 TINY ST, HALIFAX",
              base + datetime.timedelta(days=5), "Within 5M of Hydrant", towed="N")
    outcomes = mirror_type_figures.compute_and_store(
        clean_db, types=list(ALL_TYPES), log=lambda m: None,
    )
    assert len(outcomes) == 3 and all(o["ok"] for o in outcomes)
    return clean_db


def _figures(client, name):
    data = client.get(f"/api/types/{app_server._slug(name)}/figures").get_json()
    assert data["available"] is True, data
    return data


# ----------------------------------------------------- 1. server payloads


def test_each_type_states_its_own_tow_and_vehicle_conclusion(client, three_types):
    """The three seeded types land on three different stored conclusions, and
    each type's sentence names only itself."""
    fig = {name: _figures(client, name)["figures"] for name in ALL_TYPES}

    assert fig[DRIVEWAY]["tow"]["conclusion_key"] == "no_difference"
    assert fig[DRIVEWAY]["vehicles"]["conclusion_key"] == "mostly_distinct"
    assert fig[SIGNS]["tow"]["conclusion_key"] == "tow_higher"
    assert fig[SIGNS]["vehicles"]["conclusion_key"] == "substantial_repeat"
    assert fig[HYDRANT]["tow"]["conclusion_key"] == "sample_too_small"
    assert fig[HYDRANT]["vehicles"]["conclusion_key"] == "sample_too_small"

    for name in ALL_TYPES:
        assert fig[name]["overall_conclusion"].startswith(f"For {name},")
        for other in ALL_TYPES:
            if other != name:
                assert other not in fig[name]["overall_conclusion"]

    # The driveway sentence is not reproduced by any other type ...
    driveway_tow = fig[DRIVEWAY]["tow"]["conclusion"]
    assert "no detectable difference" in driveway_tow
    for name in (SIGNS, HYDRANT):
        assert "no detectable difference" not in fig[name]["tow"]["conclusion"]
        assert fig[name]["tow"]["conclusion"] != driveway_tow
        assert "mostly distinct" not in fig[name]["vehicles"]["conclusion"]
    # ... the opposite type says the opposite ...
    assert "more often" in fig[SIGNS]["tow"]["conclusion"]
    assert "substantially repeat" in fig[SIGNS]["vehicles"]["conclusion"]
    # ... and a type too small to conclude says so rather than borrowing.
    assert "too small" in fig[HYDRANT]["tow"]["conclusion"]
    assert "too small" in fig[HYDRANT]["vehicles"]["conclusion"]
    assert fig[HYDRANT]["tow"]["sample_sufficient"] is False


def test_brief_and_csv_carry_each_types_own_tow_sentence_only(client, three_types):
    sentences = {}
    for name in ALL_TYPES:
        sentences[name] = app_server._tow_thesis_sentence(_figures(client, name))
    assert len(set(sentences.values())) == 3

    for name in ALL_TYPES:
        slug = app_server._slug(name)
        brief = html.unescape(client.get(f"/types/{slug}/export").get_data(as_text=True))
        csv_text = client.get(f"/api/types/{slug}/export.csv").get_data(as_text=True)
        assert sentences[name] in brief
        assert sentences[name] in csv_text or sentences[name].replace('"', '""') in csv_text
        for other in ALL_TYPES:
            if other != name:
                assert sentences[other] not in brief
                assert sentences[other] not in csv_text

    # The too-small type says it cannot conclude, in the brief a reader gets.
    hydrant_brief = html.unescape(
        client.get(f"/types/{app_server._slug(HYDRANT)}/export").get_data(as_text=True))
    assert "too small" in hydrant_brief
    assert "no detectable difference" not in hydrant_brief
    assert "per cent against" not in sentences[HYDRANT]


def test_a_type_with_nothing_stored_states_that_not_borrowed_figures(client, three_types):
    """A type whose figures are not stored says "not yet available" (with the
    server's reason) -- it does not fall back to blocked driveway's."""
    slug = app_server._slug("On Sidewalk")
    data = client.get(f"/api/types/{slug}/figures").get_json()
    assert data["available"] is False
    sentence = app_server._tow_thesis_sentence(data)
    assert sentence.startswith("Tow-effect comparison for this type is not yet available")
    assert "no detectable difference" not in sentence


def test_no_non_driveway_payload_or_export_mentions_driveway(client, three_types):
    """Nothing the server builds for another type carries the word."""
    for name in (SIGNS, HYDRANT):
        slug = app_server._slug(name)
        for url in (f"/api/types/{slug}/doorways", f"/api/types/{slug}/blocks",
                    f"/api/types/{slug}/figures", f"/types/{slug}/export",
                    f"/api/types/{slug}/export.csv"):
            text = client.get(url).get_data(as_text=True)
            assert "driveway" not in text.lower(), f"{url} mentions driveway"


# ------------------------------------------------------ 3. the served file


def _served(client, url):
    resp = client.get(url)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_every_type_page_is_the_same_file_and_carries_no_driveway_only_claim(client, three_types):
    """`GET /types/<slug>` and `GET /` are one static file, so nothing in it
    can differ by type -- every type-specific word must come from the payload.
    """
    root = _served(client, "/")
    for name in ALL_TYPES:
        body = _served(client, f"/types/{app_server._slug(name)}")
        assert body == root
        for text in ("7,651", "9,791", "driveway calls", "blocked-driveway",
                     "Blocked-driveway", "bollard", "driveway marking",
                     "Three coarser groupings", "<h3>Why blocks</h3>",
                     "just say HALIFAX"):
            assert text not in body, f"served page still says {text!r}"
    # The enforcement sentence survives only as the branch of a condition on
    # this type's own stored tow conclusion, never as fixed prompt text.
    assert root.count("Enforcement has already been tried") == 1
    assert re.search(r'towKey === "no_difference"\s*\?\s*"Enforcement has already been tried', root)


def _strip_comments(body):
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", body)


def test_only_non_comment_mention_of_driveway_is_the_default_route_slug(client):
    """`DEFAULT_SLUG = "blocking-driveway"` is what `/` serves, and is the one
    place the served page may name the type. It reaches another type's page
    nowhere: SLUG falls back to it only when the path has no /types/<slug>."""
    body = _strip_comments(_served(client, "/"))
    hits = [m.group(0) for m in re.finditer(r"(?im)^.*driveway.*$", body)]
    assert [h.strip() for h in hits] == ['const DEFAULT_SLUG = "blocking-driveway";'], hits


def test_the_page_keeps_every_lim_target_and_the_read_carefully_anchor(client):
    body = _served(client, "/")
    targets = set(re.findall(r'href="#(lim-[a-z-]+)"', body))
    ids = set(re.findall(r'id="(lim-[a-z-]+)"', body))
    assert targets and targets <= ids
    assert ids == {"lim-intake", "lim-tow", "lim-vehicle", "lim-address",
                   "lim-block-label", "lim-dwelling"}
    assert 'id="read-carefully"' in body
    # The footer's remaining sections survive the removal of the driveway text.
    assert "<h3>The map</h3>" in body and "10,599 street segments" in body


# ------------------------------------------- 2. page script, run in node


def _js_function(body, name):
    m = re.search(rf"^function {name}\([^)]*\)\{{.*?^\}}", body, re.M | re.S)
    assert m, f"function {name}() not found in the served page"
    return m.group(0)


def _node(script):
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _run_page_fills(body, payload):
    """The page's own vehicleThesis/neighbourThesis + tow-thesis IIFE, run
    against `payload` (what /figures served) with stubbed `fetch`/`document`.
    Returns the text each filled element ended up with and the TOW_KEY the
    Ask-Claude prompt will read."""
    start = body.index("function vehicleThesis(")
    end = body.index("\n})();", start) + len("\n})();")
    source = body[start:end]
    harness = f"""
const SLUG = "x";
let TOW_KEY = null;
const nodes = {{"tow-thesis": {{textContent: ""}}, "vehicle-thesis": {{textContent: ""}}}};
const document = {{getElementById: id => nodes[id] || null}};
const payload = {json.dumps(payload)};
const fetch = async () => ({{ok: true, status: 200, json: async () => payload}});
{source}
(async () => {{
  await new Promise(r => setTimeout(r, 20));
  console.log(JSON.stringify({{tow: nodes["tow-thesis"].textContent,
    vehicle: nodes["vehicle-thesis"].textContent, towKey: TOW_KEY}}));
}})();
"""
    return _node(harness)


@_NEEDS_NODE
def test_page_text_states_each_types_own_conclusion(client, three_types):
    body = _served(client, "/")
    texts = {name: _run_page_fills(body, _figures(client, name)) for name in ALL_TYPES}

    d, s, h = texts[DRIVEWAY], texts[SIGNS], texts[HYDRANT]
    assert d["towKey"] == "no_difference" and s["towKey"] == "tow_higher"
    assert h["towKey"] == "sample_too_small"

    assert "no detectable difference" in d["tow"]
    assert d["vehicle"] == ("There is no repeat offender to deter, "
                            "so the street is producing the violation.")

    assert "more often" in s["tow"] and "no detectable difference" not in s["tow"]
    assert "substantially repeat" in s["vehicle"]
    assert "no repeat offender" not in s["vehicle"] and "street is producing" not in s["vehicle"]

    assert "too small" in h["tow"] and "too small" in h["vehicle"]
    assert "no detectable difference" not in h["tow"]
    assert "no repeat offender" not in h["vehicle"]
    # The stored fragments carry no type name, so nothing on the page can name
    # another type; and no rendered sentence is shared between the types.
    assert len({t["tow"] for t in texts.values()}) == 3
    assert len({t["vehicle"] for t in texts.values()}) == 3


@_NEEDS_NODE
def test_page_says_not_yet_available_rather_than_a_conclusion_when_figures_are_absent(client):
    body = _served(client, "/")
    got = _run_page_fills(body, {"available": False, "reason": "nothing stored"})
    assert got["towKey"] is None
    assert got["vehicle"] == "The vehicle-uniqueness conclusion for this type is not yet available."
    assert "not yet available (nothing stored)" in got["tow"]
    assert "repeat offender" not in got["vehicle"]


@_NEEDS_NODE
def test_neighbour_sentence_claims_most_only_when_most_have_a_neighbour(client):
    body = _served(client, "/")
    cases = [(6, 10), (5, 10), (4, 10), (0, 3), (1, 3), (2, 3), (1, 1), (0, 0)]
    harness = f"""
{_js_function(body, "neighbourThesis")}
console.log(JSON.stringify({json.dumps(cases)}.map(([w, c]) => neighbourThesis(w, c))));
"""
    out = dict(zip(map(tuple, cases), _node(harness)))

    for case in [(6, 10), (1, 1), (2, 3)]:            # strictly more than half
        assert "one block-wide problem counted many times" in out[case], case
    for case in [(4, 10), (0, 3), (1, 3)]:            # strictly fewer than half
        assert "on their own" in out[case] and "block-wide problem counted" not in out[case], case
    assert "split evenly" in out[(5, 10)]              # exactly half claims neither
    assert "block-wide problem counted" not in out[(5, 10)]
    assert out[(0, 0)] == ""                          # no doorways: no claim at all


def _prompts(body, type_name, key):
    """Both prompts (doorway, block) the page would build for `type_name`."""
    door = {"a": "1 A ST", "c": "HALIFAX", "d": "7", "m": 3, "t": 9, "vd": 2, "vs": 3,
            "w": 1, "g": 40, "l": "2026-01-01", "nb": 3}
    block = {"s": "A ST", "d": "7", "n": 4, "m": 12, "t": 30, "dw": 200, "w": 2}
    harness = f"""
{_js_function(body, "buildAskPrompt")}
const door = {json.dumps(door)}, block = {json.dumps(block)};
console.log(JSON.stringify([
  buildAskPrompt({json.dumps(type_name)}, door, false, {json.dumps(key)}),
  buildAskPrompt({json.dumps(type_name)}, block, true, {json.dumps(key)}),
]));
"""
    return _node(harness)


ENFORCEMENT = "Enforcement has already been tried and towing does not change the recurrence rate."


@_NEEDS_NODE
def test_ask_prompt_for_driveway_keeps_the_tow_sentence_because_its_own_figures_support_it(
        client, three_types):
    body = _served(client, "/")
    key = _run_page_fills(body, _figures(client, DRIVEWAY))["towKey"]
    assert key == "no_difference"
    for prompt in _prompts(body, DRIVEWAY, key):
        assert f'"{DRIVEWAY}"' in prompt
        assert ENFORCEMENT in prompt
        assert "Blocked-driveway" not in prompt and "blocked-driveway" not in prompt
        assert "bollard" not in prompt and "driveway marking" not in prompt
        assert "separate driveways" not in prompt


@_NEEDS_NODE
def test_ask_prompt_for_another_type_names_it_and_asserts_no_driveway_conclusion(
        client, three_types):
    body = _served(client, "/")
    for name in (SIGNS, HYDRANT):
        key = _run_page_fills(body, _figures(client, name))["towKey"]
        assert key != "no_difference"
        for prompt in _prompts(body, name, key):
            assert f'"{name}"' in prompt
            assert "driveway" not in prompt.lower()
            assert ENFORCEMENT not in prompt and "towing does not change" not in prompt
            assert "Enforcement has already been tried" not in prompt
            assert "bollard" not in prompt and "curb paint" not in prompt
            # The rest of the structure the user approved is kept.
            assert "say what" in prompt and "single biggest reason your suggestion could be wrong" in prompt
            assert "Three or four sentences." in prompt


@_NEEDS_NODE
def test_ask_prompt_states_the_tow_sentence_for_exactly_one_conclusion_key(client):
    body = _served(client, "/")
    for key in ("no_difference", "tow_lower", "tow_higher", "sample_too_small", None):
        for prompt in _prompts(body, "Some Type", key):
            assert (ENFORCEMENT in prompt) == (key == "no_difference"), key
    # No type name known: still neutral, never falls back to driveway.
    for prompt in _prompts(body, "", None):
        assert "complaints of this violation type" in prompt
        assert "driveway" not in prompt.lower()
