"""A layer that fails does not stop the others (change automate-mirror-bootstrap-and-sync,
tasks 3.1 to 3.3, design D7).

Before this change `sync()` re-raised the first layer's exception, so a census outage
kept the two Cityworks layers from being polled at all, and skipped the retained lists
and per-type figures. Now every layer is attempted, the run reports its failures, and
the derived products are withheld only when the Cityworks pair may be at two versions.
"""

import datetime
import sys

import pytest

from mirror import db as mirror_db
from mirror import source, sync
from test_sync import (  # noqa: F401  (fixtures and helpers reused from the sync tests)
    BASE_DATE, field_feature, mirrored, request_feature, runs, service, snapshot_rows,
    type_figures_rows,
)

NOW = datetime.datetime(2026, 9, 22, 12, 0, tzinfo=datetime.UTC)


def fail_layer(monkeypatch, layer_key, where="poll"):
    """Make one layer's poll (or reload) fail, the way a network error would."""
    if where == "poll":
        real = source.last_edit_date

        def poll(layer):
            if layer.base_key == layer_key:
                raise OSError("connection reset by peer")
            return real(layer)

        monkeypatch.setattr(source, "last_edit_date", poll)
    else:
        real = sync.full_reload

        def reload(conn, layer, *a, **k):
            if layer.base_key == layer_key:
                raise OSError("connection reset by peer")
            return real(conn, layer, *a, **k)

        monkeypatch.setattr(sync, "full_reload", reload)


def test_a_census_failure_does_not_stop_the_cityworks_layers(mirrored, service,
                                                             monkeypatch):
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()
    fail_layer(monkeypatch, "census_areas")

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert set(outcome["pulled"]) == {"service_requests", "custom_fields"}
    assert list(outcome["errors"]) == ["census_areas"]
    assert outcome["errors"]["census_areas"].startswith("OSError")
    assert outcome["ok"] is False
    assert mirrored.execute("SELECT count(*) FROM service_requests").fetchone()[0] == 11


def test_every_layer_is_attempted_even_when_the_first_fails(mirrored, service,
                                                            monkeypatch):
    fail_layer(monkeypatch, "service_requests")

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert set(outcome["layers"]) == {"service_requests", "custom_fields",
                                      "census_areas"}
    assert outcome["layers"]["custom_fields"].get("reconcile")     # it was polled too
    assert outcome["layers"]["census_areas"].get("reconcile")


def test_a_failed_layer_shows_a_failed_attempt_and_keeps_its_last_success(
        mirrored, service, monkeypatch):
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    fail_layer(monkeypatch, "census_areas")

    sync.sync(mirrored, now=NOW + datetime.timedelta(days=1), log=lambda m: None)

    row = mirrored.execute(
        "SELECT last_attempt_ok, last_success_at FROM layer_state "
        "WHERE layer = 'census_areas'").fetchone()
    assert row[0] is False and row[1] == NOW


def test_a_failed_reconcile_is_recorded_as_a_failed_attempt(mirrored, service,
                                                            monkeypatch):
    """The poll and the reload record their own failures; a failed count request
    (the quiet-night reconcile) did not, and would have left the layer looking fine."""
    def no_count(layer, where="1=1"):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(source, "count", no_count)

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert set(outcome["errors"]) == {"service_requests", "custom_fields",
                                      "census_areas"}
    ok = dict(mirrored.execute(
        "SELECT layer, last_attempt_ok FROM layer_state").fetchall())
    assert ok == {k: False for k in outcome["errors"]}


def test_an_incomplete_staged_fill_is_a_failure_not_a_quiet_success(mirrored, service,
                                                                    monkeypatch):
    """`full_reload` returns rather than raises when the fill did not finish. The held
    version stands, but the run is not a success."""
    service.publish()
    real = sync.full_reload

    def one_page(conn, layer, *a, **k):
        return real(conn, layer, *a, page_size=3, limit_pages=1, **k)

    monkeypatch.setattr(sync, "full_reload", one_page)

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["ok"] is False
    assert "staging fill incomplete" in outcome["errors"]["service_requests"]


def test_retained_lists_and_figures_are_withheld_when_the_pair_may_be_mixed(
        mirrored, service, monkeypatch):
    """3.2: service_requests reloads and custom_fields then fails, so what is held is
    two versions of one publish event. Nothing derived from it may be produced."""
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()
    fail_layer(monkeypatch, "custom_fields", where="reload")
    logged = []

    outcome = sync.sync(mirrored, now=NOW, log=logged.append)

    assert outcome["pulled"] == ["service_requests"]
    assert outcome["ok"] is False and list(outcome["errors"]) == ["custom_fields"]
    assert outcome["snapshots"] == [] and outcome["type_figures"] == []
    assert snapshot_rows(mirrored) == []
    assert type_figures_rows(mirrored) == []
    assert not runs(mirrored, kind="derive")
    assert not runs(mirrored, kind="type_figures")
    assert any("different published versions" in line for line in logged)


def test_a_cityworks_poll_failure_withholds_them_too(mirrored, service, monkeypatch):
    service.publish()
    fail_layer(monkeypatch, "service_requests", where="poll")

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["snapshots"] == [] and outcome["type_figures"] == []


def test_a_census_failure_alone_does_not_withhold_them(mirrored, service, monkeypatch):
    """The gate is narrow: the census layer is not part of the publish pair, so its
    failure leaves the Cityworks pair consistent and the products are still right."""
    from mirror import violation_types

    service.publish()
    fail_layer(monkeypatch, "census_areas")

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert len(outcome["snapshots"]) == len(violation_types.CANONICAL_TYPES)
    assert len(outcome["type_figures"]) == len(violation_types.CANONICAL_TYPES)


def test_a_clean_run_reports_ok_and_no_errors(mirrored, service):
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["ok"] is True and outcome["errors"] == {}


def test_the_next_run_recovers_after_the_pair_was_left_mixed(mirrored, service,
                                                             monkeypatch):
    """The failure is temporary by construction: the next run reloads what did not
    complete, and only then are the derived products produced."""
    service.publish()
    fail_layer(monkeypatch, "custom_fields", where="reload")
    first = sync.sync(mirrored, now=NOW, log=lambda m: None)
    assert first["ok"] is False and first["snapshots"] == []
    monkeypatch.undo()
    service.install(monkeypatch)

    second = sync.sync(mirrored, now=NOW + datetime.timedelta(hours=1),
                       log=lambda m: None)

    assert second["ok"] is True
    assert "custom_fields" in second["pulled"]
    assert second["snapshots"]


# ------------------------------------------------------------------ 3.3 exit status


def run_main(monkeypatch, capsys, *flags):
    monkeypatch.setattr(sys, "argv", ["sync.py", *flags])
    status = sync.main()
    return status, capsys.readouterr()


def test_main_exits_zero_on_a_clean_run(mirrored, service, monkeypatch, capsys):
    service.install(monkeypatch)

    status, out = run_main(monkeypatch, capsys)

    assert status == 0
    assert "FAILED" not in out.out


def test_main_exits_non_zero_and_names_each_failed_layer(mirrored, service,
                                                         monkeypatch, capsys):
    service.publish()
    fail_layer(monkeypatch, "census_areas")

    status, out = run_main(monkeypatch, capsys)

    assert status == 1
    assert "FAILED census_areas: OSError: connection reset by peer" in out.out


def test_main_exits_non_zero_when_every_layer_fails(mirrored, service, monkeypatch,
                                                    capsys):
    def explode(layer):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(source, "last_edit_date", explode)

    status, out = run_main(monkeypatch, capsys)

    assert status == 1
    for key in ("service_requests", "custom_fields", "census_areas"):
        assert f"FAILED {key}:" in out.out
