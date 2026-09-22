"""The not-ready view of the served page (change automate-mirror-bootstrap-and-sync,
tasks 5.5 and 5.6; design D9).

There is no browser here, so the page's own readiness gate is run in node against a stub
page, a fake `fetch` and a fake clock. What is exercised is the text the browser would
load, not a re-implementation of it: the block between the two GATE markers is cut out of
`web/app/index.html` and evaluated.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "app" / "index.html").read_text()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

STATES = ("uninitialised", "loading", "awaiting_first_load")


def gate_block():
    m = re.search(r"/\* MIRROR-READINESS GATE \(begin\).*?/\* MIRROR-READINESS GATE \(end\) \*/",
                  PAGE, re.S)
    assert m, "the readiness gate was not found in the served page"
    return m.group(0)


def banner_script():
    m = re.search(r'\(async \(\) => \{\n  const header = document\.getElementById\("freshness"\);'
                  r".*?\n\}\)\(\);", PAGE, re.S)
    assert m, "the freshness banner script was not found in the served page"
    return m.group(0)


def ok(state, **extra):
    return {"status": 200, "body": {"readiness": state, **extra}}


def unavailable(state):
    return {"status": 503, "body": {"error": "not_ready", "readiness": state}}


NETWORK_ERROR = {"throw": True}
NOT_JSON = {"status": 200, "not_json": True}


def run_gate(responses, ticks=0, with_banner=False):
    """Run the gate, then `ticks` scheduled polls. Returns one snapshot per step.

    `responses` is the queue `fetch` answers from, in order; asking for more than it
    holds is an error, so a page that fetches more than it should cannot pass.
    """
    harness = """
const queue = %(queue)s;
const fetches = [];
const timers = [];
let booted = 0;
const bodyClasses = [];
const nodes = {
  "not-ready": {hidden: true}, "not-ready-title": {textContent: ""},
  "not-ready-body": {textContent: ""}, freshness: {textContent: "Checking update status...",
    hidden: false, classList: {add(){}}}, "latest-date": {textContent: "..."},
  "footer-sync-date": {textContent: ""},
};
const document = {body: {classList: {add: c => bodyClasses.push(c)}},
                  getElementById: id => nodes[id] || null};
const location = {reloads: 0, reload(){ this.reloads++; }};
const setTimeout = (fn, delay) => { timers.push({fn, delay}); return timers.length; };
const fetch = async url => {
  fetches.push(url);
  if (!queue.length) throw new Error("fetch called with no response queued: " + url);
  const r = queue.shift();
  if (r.throw) throw new TypeError("Failed to fetch");
  return {ok: r.status >= 200 && r.status < 300, status: r.status, clone(){ return this; },
          json: async () => { if (r.not_json) throw new SyntaxError("Unexpected token <"); return r.body; }};
};
(async () => {
  const shots = [];
  const snap = label => shots.push({
    label, fetches: fetches.slice(), booted, reloads: location.reloads,
    timers: timers.map(t => t.delay), title: nodes["not-ready-title"].textContent,
    body: nodes["not-ready-body"].textContent, panelHidden: nodes["not-ready"].hidden,
    bodyClasses: bodyClasses.slice(), banner: nodes.freshness.textContent});
  %(block)s
  await gateThenBoot(() => { booted++; });
  %(banner)s
  await new Promise(r => setImmediate(r));
  snap("load");
  for (let i = 0; i < %(ticks)d; i++){
    const t = timers.shift();
    if (!t){ snap("no timer for tick " + i); continue; }
    await t.fn();
    snap("tick " + (i + 1));
  }
  console.log(JSON.stringify(shots));
})().catch(e => { console.error(e); process.exit(1); });
""" % {"queue": json.dumps(responses), "block": gate_block(), "ticks": ticks,
       "banner": banner_script() if with_banner else ""}
    done = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# -------------------------------------------------------------- 5.5 the not-ready view


@pytest.mark.parametrize("state", STATES)
def test_each_state_shows_one_message_in_place_of_the_lists(state):
    shot = run_gate([ok(state)])[0]

    assert shot["booted"] == 0                          # the list page never started
    assert shot["panelHidden"] is False
    assert "mirror-not-ready" in shot["bodyClasses"]    # CSS hides lists, map, filters
    assert shot["title"] and shot["body"]
    assert shot["fetches"] == ["/api/freshness"]        # no list, map or types fetched


def test_the_wording_tells_a_first_load_from_a_fault():
    titles = {s: run_gate([ok(s)])[0]["title"] for s in STATES}

    assert titles["uninitialised"] == "This service is being set up"
    assert titles["loading"] == "Loading HRM's data for the first time"
    assert titles["awaiting_first_load"] == "The first load has not completed"
    assert len(set(titles.values())) == 3


@pytest.mark.parametrize("state", STATES)
def test_the_banner_carries_the_same_state(state):
    shot = run_gate([ok(state)])[0]

    assert shot["banner"].startswith("Mirror: ")
    assert shot["banner"] != "Checking update status..."


@pytest.mark.parametrize("state", STATES)
def test_no_state_says_overdue_or_states_a_duration(state):
    shot = run_gate([ok(state)])[0]
    said = " ".join([shot["title"], shot["body"], shot["banner"]]).lower()

    assert "overdue" not in said and "late" not in said and "behind" not in said
    assert not re.search(r"\d", said), f"a figure appears in the not-ready wording: {said!r}"
    assert not re.search(r"\b(second|minute|hour|day)s?\b", said), said


def test_a_503_from_the_route_is_read_as_the_uninitialised_state():
    shot = run_gate([unavailable("uninitialised")])[0]

    assert shot["title"] == "This service is being set up"
    assert shot["booted"] == 0


def test_the_lists_are_hidden_by_the_page_own_css_while_not_ready():
    css = re.search(r"\.mirror-not-ready \.intro[^{]*\{([^}]*)\}", PAGE)
    assert css and "display:none" in css.group(1)
    rule = re.search(r"\.mirror-not-ready \.intro.*?\{[^}]*\}", PAGE, re.S).group(0)
    for selector in (".intro", ".toolbar", ".workspace", "#eyebrow-latest"):
        assert f".mirror-not-ready {selector}" in rule


def test_the_eyebrow_does_not_read_open_data_to_nothing_while_not_ready():
    """"open data to …" has no date to name before a load, and filling the gap with words
    reads as broken grammar; the whole clause goes instead."""
    assert '<span id="eyebrow-latest"> &middot; open data to <span id="latest-date">' in PAGE


def test_the_panel_is_hidden_until_the_gate_reveals_it():
    panel = re.search(r'<section class="not-ready" id="not-ready"[^>]*>', PAGE)

    assert panel and " hidden" in panel.group(0) and 'role="status"' in panel.group(0)


def test_the_header_stats_are_inside_what_is_hidden():
    """The stats read "0 doorways still calling" on an empty mirror; they live in
    `.intro`, which the not-ready CSS hides."""
    assert re.search(r'<div class="intro">.*?id="n-doorways-stat"', PAGE, re.S)


# ---------------------------------------------------------------- ready, or not gated


def test_a_ready_mirror_boots_the_page_and_never_polls():
    shots = run_gate([ok("ready")], ticks=3)

    assert shots[0]["booted"] == 1
    assert shots[0]["panelHidden"] is True and shots[0]["bodyClasses"] == []
    assert all(s["timers"] == [] for s in shots)
    assert all(s["fetches"] == ["/api/freshness"] for s in shots)
    assert all(s["reloads"] == 0 for s in shots)


def test_a_ready_page_makes_exactly_the_one_freshness_request_it_always_did():
    """The gate and the freshness banner share one request."""
    shot = run_gate([ok("ready", last_success_at=None, next_due_at=None,
                        next_update={"known": False})], with_banner=True)[0]

    assert shot["fetches"] == ["/api/freshness"]
    assert shot["booted"] == 1


@pytest.mark.parametrize("response", [ok("something-new"), {"status": 200, "body": {}},
                                      NETWORK_ERROR],
                         ids=["unknown state", "no readiness field", "network error"])
def test_anything_the_gate_cannot_read_boots_the_page_as_before(response):
    """An older server, or one that cannot be reached, is the page's own error path's
    business, not the gate's: it must not strand the page on a message."""
    shots = run_gate([response], ticks=2)

    assert shots[0]["booted"] == 1
    assert shots[0]["panelHidden"] is True
    assert all(s["timers"] == [] for s in shots)


# ------------------------------------------------------------------- 5.6 the recovery


def test_polling_every_thirty_seconds_while_not_ready():
    shot = run_gate([ok("loading")])[0]

    assert shot["timers"] == [30000]


def test_a_tick_that_is_still_loading_changes_nothing_and_schedules_the_next():
    shots = run_gate([ok("loading"), ok("loading")], ticks=1)
    first, second = shots

    assert second["reloads"] == 0 and second["booted"] == 0
    assert second["title"] == first["title"]
    assert second["timers"] == [30000]
    assert second["fetches"] == ["/api/freshness", "/api/freshness"]


def test_the_tick_that_returns_ready_reloads_exactly_once_and_stops():
    shots = run_gate([ok("loading"), ok("loading"), ok("ready")], ticks=5)

    reloads = [s["reloads"] for s in shots]
    assert reloads[-1] == 1 and max(reloads) == 1
    ready_at = next(i for i, s in enumerate(shots) if s["reloads"] == 1)
    assert shots[ready_at]["timers"] == []              # nothing scheduled after ready
    assert all(s["label"].startswith("no timer") for s in shots[ready_at + 1:])
    assert len(shots[-1]["fetches"]) == 3               # and no request beyond ready


def test_a_network_error_on_a_tick_keeps_the_message_and_tries_again():
    shots = run_gate([ok("loading"), NETWORK_ERROR, ok("loading")], ticks=2)

    after_error = shots[1]
    assert after_error["reloads"] == 0 and after_error["booted"] == 0
    assert after_error["title"] == shots[0]["title"] and after_error["panelHidden"] is False
    assert after_error["timers"] == [30000]
    assert shots[2]["timers"] == [30000]


def test_a_body_that_is_not_json_keeps_the_message_and_tries_again():
    shots = run_gate([ok("loading"), NOT_JSON, ok("loading")], ticks=2)

    assert shots[1]["reloads"] == 0 and shots[1]["title"] == shots[0]["title"]
    assert shots[1]["timers"] == [30000] and shots[2]["timers"] == [30000]


def test_it_recovers_after_errors_and_then_reloads():
    shots = run_gate([ok("loading"), NETWORK_ERROR, NOT_JSON, ok("ready")], ticks=4)

    assert [s["reloads"] for s in shots] == [0, 0, 0, 1, 1][:len(shots)]


def test_the_message_follows_the_state_as_it_changes():
    """Uninitialised, then the schema appears, then a first load starts, then it fails."""
    shots = run_gate([unavailable("uninitialised"), ok("loading"), ok("awaiting_first_load")],
                     ticks=2)

    assert [s["title"] for s in shots] == [
        "This service is being set up",
        "Loading HRM's data for the first time",
        "The first load has not completed",
    ]
    assert all(s["reloads"] == 0 for s in shots)


def test_polling_never_asks_for_anything_but_the_freshness_route():
    shots = run_gate([ok("loading")] * 4, ticks=3)

    assert set(shots[-1]["fetches"]) == {"/api/freshness"}
