"""Figure-level reconciliation (4.9): the diff logic offline, against fixture
output directories -- never a real pipeline run, so this file needs neither the
network (blocked by `no_network` in conftest.py) nor Postgres.

Two things this file has to prove distinctly: the CSV/brief diff logic notices
every kind of discrepancy 4.9 cares about (a changed cell, a missing row, a
reordered row, a changed brief line), and the freshness normalisation is narrow
enough that it never hides one of those on its way to being narrow enough not to
flag a documented, expected difference as a defect.
"""

import subprocess

import pytest

from mirror import reconcile_figures as rf


# --------------------------------------------------------------- CSV diff: pure


def watchlist_row(address, calls_12mo="3", tows="0", **overrides):
    row = {
        "address": address,
        "calls_12mo": calls_12mo,
        "calls_total": "5",
        "district": "7",
        "tows": tows,
        "vehicles_distinct": "3",
    }
    row.update(overrides)
    return row


def with_freshness(row, derived_at="2026-09-16T00:00:00+00:00",
                    last_sync="2026-09-15T23:00:00+00:00",
                    most_recent="2026-09-15T00:00:00"):
    return dict(
        row,
        derived_at=derived_at,
        last_sync_success_at=last_sync,
        most_recent_call_date=most_recent,
    )


def test_identical_rows_reconcile():
    live = [watchlist_row("1 QUEEN ST"), watchlist_row("2 QUEEN ST")]
    mirror = [with_freshness(watchlist_row("1 QUEEN ST")),
              with_freshness(watchlist_row("2 QUEEN ST"))]

    stripped, present = rf.strip_freshness_columns(mirror)
    assert present == set(rf.MIRROR_CSV_FRESHNESS_COLUMNS)

    result = rf.diff_csv(live, stripped, "address")
    assert result["missing_from_mirror"] == []
    assert result["missing_from_live"] == []
    assert result["reordered"] is False
    assert result["field_mismatches"] == []


def test_induced_cell_change_is_reported():
    live = [watchlist_row("1 QUEEN ST", calls_12mo="3")]
    mirror = [with_freshness(watchlist_row("1 QUEEN ST", calls_12mo="4"))]  # induced

    stripped, _ = rf.strip_freshness_columns(mirror)
    result = rf.diff_csv(live, stripped, "address")

    assert result["missing_from_mirror"] == []
    assert result["missing_from_live"] == []
    assert result["reordered"] is False
    assert result["field_mismatches"] == [
        {"key": "1 QUEEN ST", "field": "calls_12mo", "live": "3", "mirror": "4"}
    ]


def test_missing_row_is_reported():
    live = [watchlist_row("1 QUEEN ST"), watchlist_row("2 QUEEN ST")]
    mirror = [with_freshness(watchlist_row("1 QUEEN ST"))]  # "2 QUEEN ST" dropped

    stripped, _ = rf.strip_freshness_columns(mirror)
    result = rf.diff_csv(live, stripped, "address")

    assert result["missing_from_mirror"] == ["2 QUEEN ST"]
    assert result["missing_from_live"] == []
    assert result["reordered"] is False
    assert result["field_mismatches"] == []


def test_extra_row_is_reported_as_missing_from_live():
    live = [watchlist_row("1 QUEEN ST")]
    mirror = [with_freshness(watchlist_row("1 QUEEN ST")),
              with_freshness(watchlist_row("9 KING ST"))]  # not on the live side

    stripped, _ = rf.strip_freshness_columns(mirror)
    result = rf.diff_csv(live, stripped, "address")

    assert result["missing_from_live"] == ["9 KING ST"]
    assert result["missing_from_mirror"] == []


def test_reordered_row_is_reported_even_with_identical_cells():
    live = [watchlist_row("1 QUEEN ST"), watchlist_row("2 QUEEN ST")]
    mirror = [with_freshness(watchlist_row("2 QUEEN ST")),
              with_freshness(watchlist_row("1 QUEEN ST"))]  # swapped

    stripped, _ = rf.strip_freshness_columns(mirror)
    result = rf.diff_csv(live, stripped, "address")

    assert result["missing_from_mirror"] == []
    assert result["missing_from_live"] == []
    assert result["field_mismatches"] == []  # every cell agrees ...
    assert result["reordered"] is True  # ... but rank differs, and that's a discrepancy
    assert result["live_order"] == ["1 QUEEN ST", "2 QUEEN ST"]
    assert result["mirror_order"] == ["2 QUEEN ST", "1 QUEEN ST"]


def test_freshness_columns_absent_when_not_a_mirror_csv():
    """strip_freshness_columns on rows that never had the columns reports nothing
    present -- so a CSV pair with no freshness columns at all (neither side is a
    mirror export) is not silently treated as already normalised."""
    rows = [watchlist_row("1 QUEEN ST")]
    stripped, present = rf.strip_freshness_columns(rows)
    assert present == set()
    assert stripped == rows


# ------------------------------------------------------------- brief diff: pure


LIVE_BRIEF = "\n".join([
    "# Doorways enforcement cannot fix: alleged violation matching 'Driveway'",
    "",
    "Source: HRM open data.",
    "Most recent call in the data: 2026-09-15.",
    "",
    "- Calls in scope: 100.",
    "",
])

MIRROR_BRIEF_IDENTICAL = "\n".join([
    "# Doorways enforcement cannot fix: alleged violation matching 'Driveway'",
    "",  # the blank line _write_freshness_note inserts ahead of the note
    "Derived 2026-09-16T00:00:00+00:00 from the mirror | last successful sync "
    "2026-09-15T23:00:00+00:00 | most recent call in the data 2026-09-15T00:00:00.",
    "",  # the brief's own original blank line, now pushed one further down
    "Source: HRM open data.",
    "Most recent call in the data: 2026-09-15.",
    "",
    "- Calls in scope: 100.",
    "",
])


def test_identical_briefs_reconcile_after_normalisation():
    result = rf.diff_brief(LIVE_BRIEF, MIRROR_BRIEF_IDENTICAL)
    assert result["freshness_note_found"] is True
    assert result["line_mismatches"] == []
    assert result["live_line_count"] == result["mirror_line_count_normalized"]


def test_changed_brief_line_is_reported():
    mirror_changed = MIRROR_BRIEF_IDENTICAL.replace("Calls in scope: 100.", "Calls in scope: 99.")

    result = rf.diff_brief(LIVE_BRIEF, mirror_changed)

    assert result["freshness_note_found"] is True  # normalisation still applied ...
    assert result["line_mismatches"] != []  # ... but the real change is still caught
    mismatch = result["line_mismatches"][0]
    assert "100" in mismatch["live"]
    assert "99" in mismatch["mirror"]


def test_freshness_normalisation_does_not_hide_a_real_difference():
    """The freshness note sits right next to genuine content in a brief. Proves the
    normalisation removes only the note (and its one leading blank line), not
    anything else nearby, by changing a line adjacent to the note and confirming it
    still surfaces as a mismatch."""
    mirror_changed = MIRROR_BRIEF_IDENTICAL.replace(
        "Source: HRM open data.", "Source: HRM open data (mirrored)."
    )

    result = rf.diff_brief(LIVE_BRIEF, mirror_changed)

    assert result["freshness_note_found"] is True
    assert any("mirrored" in (m["mirror"] or "") for m in result["line_mismatches"])


def test_normalize_brief_without_freshness_note_is_unchanged():
    lines, found = rf.normalize_brief(LIVE_BRIEF)
    assert found is False
    assert lines == LIVE_BRIEF.splitlines()


def test_missing_brief_line_at_end_is_reported():
    live = LIVE_BRIEF
    mirror = "\n".join(MIRROR_BRIEF_IDENTICAL.splitlines()[:-2])  # brief cut short

    result = rf.diff_brief(live, mirror)
    assert result["line_mismatches"] != []
    assert result["live_line_count"] != result["mirror_line_count_normalized"]


# ------------------------------------------------------------- compare_outputs


def csv_text(rows, fieldnames):
    lines = [",".join(fieldnames)]
    for row in rows:
        lines.append(",".join(str(row[f]) for f in fieldnames))
    return "\n".join(lines) + "\n"


def test_compare_outputs_identical_directories_is_clean(tmp_path):
    fieldnames = ["address", "calls_12mo", "calls_total", "district", "tows", "vehicles_distinct"]
    mirror_fieldnames = fieldnames + list(rf.MIRROR_CSV_FRESHNESS_COLUMNS)
    live_rows = [watchlist_row("1 QUEEN ST")]
    mirror_rows = [with_freshness(watchlist_row("1 QUEEN ST"))]

    live_dir = tmp_path / "live"
    mirror_dir = tmp_path / "mirror"
    live_dir.mkdir()
    mirror_dir.mkdir()
    (live_dir / "watchlist.csv").write_text(csv_text(live_rows, fieldnames))
    (live_dir / "blocks.csv").write_text(csv_text([], ["block"]))
    (live_dir / "watchlist.md").write_text(LIVE_BRIEF)
    (live_dir / "blocks.md").write_text(LIVE_BRIEF)
    (mirror_dir / "watchlist.csv").write_text(csv_text(mirror_rows, mirror_fieldnames))
    (mirror_dir / "blocks.csv").write_text(csv_text([], ["block"]))
    (mirror_dir / "watchlist.md").write_text(MIRROR_BRIEF_IDENTICAL)
    (mirror_dir / "blocks.md").write_text(MIRROR_BRIEF_IDENTICAL)

    result = rf.compare_outputs(live_dir, mirror_dir)

    assert result["csv"]["watchlist.csv"]["field_mismatches"] == []
    assert result["csv"]["blocks.csv"]["missing_from_mirror"] == []
    assert result["brief"]["watchlist.md"]["line_mismatches"] == []
    assert any("freshness columns" in n for n in result["normalizations"])
    assert any("freshness note" in n for n in result["normalizations"])
    assert result["board_status"] == rf.BOARD_STATUS

    # Wrap in the shape is_clean() expects (aborted/returncodes come from
    # run_reconciliation, not compare_outputs).
    fake = dict(result, aborted=False, live_returncode=0, mirror_returncode=0)
    assert rf.is_clean(fake) is True


def test_compare_outputs_missing_file_is_reported_not_raised(tmp_path):
    live_dir = tmp_path / "live"
    mirror_dir = tmp_path / "mirror"
    live_dir.mkdir()
    mirror_dir.mkdir()
    (live_dir / "watchlist.csv").write_text(csv_text([watchlist_row("1 QUEEN ST")], ["address"]))
    # mirror side never writes watchlist.csv at all (e.g. an empty-result run)
    (live_dir / "blocks.csv").write_text(csv_text([], ["block"]))
    (mirror_dir / "blocks.csv").write_text(csv_text([], ["block"]))
    (live_dir / "watchlist.md").write_text(LIVE_BRIEF)
    (mirror_dir / "watchlist.md").write_text(MIRROR_BRIEF_IDENTICAL)
    (live_dir / "blocks.md").write_text(LIVE_BRIEF)
    (mirror_dir / "blocks.md").write_text(MIRROR_BRIEF_IDENTICAL)

    result = rf.compare_outputs(live_dir, mirror_dir)
    assert "error" in result["csv"]["watchlist.csv"]

    fake = dict(result, aborted=False, live_returncode=0, mirror_returncode=0)
    assert rf.is_clean(fake) is False


# ----------------------------------------------------------- republish_warning


def fresh_snapshot(service_requests_edit=1, custom_fields_edit=1):
    return {
        "service_requests": {
            "mirror_last_edit": 1, "live_last_edit": service_requests_edit, "advanced": False,
        },
        "custom_fields": {
            "mirror_last_edit": 1, "live_last_edit": custom_fields_edit, "advanced": False,
        },
    }


def test_republish_warning_none_when_unchanged():
    before = fresh_snapshot()
    after = fresh_snapshot()

    changed, warning = rf.republish_warning(before, after)

    assert changed == []
    assert warning is None


def test_republish_warning_present_when_a_layer_advanced_during_the_run():
    before = fresh_snapshot(service_requests_edit=1)
    after = fresh_snapshot(service_requests_edit=2)  # published mid-run

    changed, warning = rf.republish_warning(before, after)

    assert changed == [{"layer": "service_requests", "before": 1, "after": 2}]
    assert warning is not None
    assert "service_requests" in warning
    assert "1" in warning and "2" in warning
    assert "re-run" in warning


def test_republish_warning_reports_every_changed_layer():
    before = fresh_snapshot(service_requests_edit=1, custom_fields_edit=1)
    after = fresh_snapshot(service_requests_edit=2, custom_fields_edit=3)

    changed, warning = rf.republish_warning(before, after)

    assert {c["layer"] for c in changed} == {"service_requests", "custom_fields"}
    assert "service_requests" in warning and "custom_fields" in warning


# --------------------------------------------------------- run_reconciliation


def fake_completed(returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout="", stderr=stderr)


def test_run_reconciliation_aborts_when_source_has_advanced():
    calls = {"live": 0, "mirror": 0}

    def freshness_fn(conn):
        return {
            "service_requests": {"mirror_last_edit": 1, "live_last_edit": 2, "advanced": True},
            "custom_fields": {"mirror_last_edit": 1, "live_last_edit": 1, "advanced": False},
        }

    def run_live_fn(*a, **k):
        calls["live"] += 1
        return fake_completed()

    def run_mirror_fn(*a, **k):
        calls["mirror"] += 1
        return fake_completed()

    result = rf.run_reconciliation(
        conn=None, violation="Driveway",
        freshness_fn=freshness_fn, run_live_fn=run_live_fn, run_mirror_fn=run_mirror_fn,
    )

    assert result["aborted"] is True
    assert "service_requests" in result["reason"]
    assert calls == {"live": 0, "mirror": 0}  # neither pipeline was run
    assert rf.is_clean(result) is False


def test_run_reconciliation_runs_both_pipelines_when_fresh(tmp_path):
    def freshness_fn(conn):
        return {
            "service_requests": {"mirror_last_edit": 1, "live_last_edit": 1, "advanced": False},
            "custom_fields": {"mirror_last_edit": 1, "live_last_edit": 1, "advanced": False},
        }

    captured = {}

    def run_live_fn(out_dir, violation):
        captured["live_dir"] = out_dir
        captured["live_violation"] = violation
        return fake_completed()

    def run_mirror_fn(out_dir, violation):
        captured["mirror_dir"] = out_dir
        captured["mirror_violation"] = violation
        return fake_completed()

    def compare_fn(live_dir, mirror_dir):
        captured["compared"] = (live_dir, mirror_dir)
        return {"csv": {}, "brief": {}, "normalizations": [], "board_status": rf.BOARD_STATUS}

    result = rf.run_reconciliation(
        conn=None, violation="Driveway", out_dir=tmp_path,
        freshness_fn=freshness_fn, run_live_fn=run_live_fn, run_mirror_fn=run_mirror_fn,
        compare_fn=compare_fn,
    )

    assert result["aborted"] is False
    assert captured["live_violation"] == "Driveway"
    assert captured["mirror_violation"] == "Driveway"
    assert captured["live_dir"] == tmp_path / "live"
    assert captured["mirror_dir"] == tmp_path / "mirror"
    assert captured["compared"] == (tmp_path / "live", tmp_path / "mirror")
    assert rf.is_clean(result) is True
    assert result["republish_warning"] is None  # HRM's publish didn't move
    assert result["republish_check"] == []


def test_run_reconciliation_warns_when_hrm_publishes_during_the_run(tmp_path):
    """The scenario this task adds: freshness held before the run started, but HRM
    published in between the pre-run and post-run checks. The result must carry a
    warning naming the layer and both timestamps, and -- just as important -- must
    still report the real discrepancies compare_fn found rather than hiding them
    behind the warning."""
    calls = {"freshness": 0}

    def freshness_fn(conn):
        calls["freshness"] += 1
        # First call (pre-run): current. Second call (post-run): service_requests
        # advanced while the ~3.5-minute live run and derivation were in flight.
        live_edit = 1 if calls["freshness"] == 1 else 2
        return {
            "service_requests": {
                "mirror_last_edit": 1, "live_last_edit": live_edit, "advanced": False,
            },
            "custom_fields": {"mirror_last_edit": 1, "live_last_edit": 1, "advanced": False},
        }

    def run_live_fn(out_dir, violation):
        return fake_completed()

    def run_mirror_fn(out_dir, violation):
        return fake_completed()

    def compare_fn(live_dir, mirror_dir):
        # A real discrepancy, standing in for one the moving target could produce.
        return {
            "csv": {"watchlist.csv": {
                "live_count": 2, "mirror_count": 1,
                "missing_from_mirror": ["9 KING ST"], "missing_from_live": [],
                "reordered": False, "live_order": None, "mirror_order": None,
                "field_mismatches": [],
            }},
            "brief": {},
            "normalizations": [],
            "board_status": rf.BOARD_STATUS,
        }

    result = rf.run_reconciliation(
        conn=None, violation="Driveway", out_dir=tmp_path,
        freshness_fn=freshness_fn, run_live_fn=run_live_fn, run_mirror_fn=run_mirror_fn,
        compare_fn=compare_fn,
    )

    assert calls["freshness"] == 2  # checked once before the run, once after
    assert result["aborted"] is False
    assert result["republish_check"] == [
        {"layer": "service_requests", "before": 1, "after": 2}
    ]
    assert result["republish_warning"] is not None
    assert "service_requests" in result["republish_warning"]
    assert "1" in result["republish_warning"] and "2" in result["republish_warning"]

    # The discrepancy is still reported, not suppressed by the warning.
    assert result["csv"]["watchlist.csv"]["missing_from_mirror"] == ["9 KING ST"]
    assert rf.is_clean(result) is False

    report = rf.format_report(result)
    assert "WARNING" in report
    assert "service_requests" in report
    assert "DIVERGED" in report


# ------------------------------------------------------ command construction (no exec)


def test_run_live_builds_expected_command_without_executing_it(monkeypatch, tmp_path):
    """Proves the CLI wiring is correct without spawning a process: `subprocess.run`
    is replaced, so nothing here can reach the network even indirectly."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return fake_completed()

    monkeypatch.setattr(rf.subprocess, "run", fake_run)

    rf.run_live(tmp_path, violation="No Parking Sign")

    cmd = captured["cmd"]
    assert str(rf.HOTSPOTS) in cmd
    assert "--violation" in cmd and "No Parking Sign" in cmd
    assert str(tmp_path / "watchlist.csv") in cmd
    # hotspots.py no longer takes a board, so the command must not pass one.
    assert "--board" not in cmd
    assert not any("triage-board" in part for part in cmd)
    assert captured["kwargs"]["cwd"] == rf.REPO_ROOT


def test_run_mirror_builds_expected_command_without_executing_it(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return fake_completed()

    monkeypatch.setattr(rf.subprocess, "run", fake_run)

    rf.run_mirror(tmp_path, violation="Driveway")

    cmd = captured["cmd"]
    assert str(rf.DERIVE) in cmd
    assert str(tmp_path) in cmd
    assert "--violation" in cmd and "Driveway" in cmd
