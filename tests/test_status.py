"""The queryable freshness surface (design.md M4; tasks 3.2, 3.3, 3.4).

Reuses `test_sync`'s fake service and `mirrored` fixture rather than duplicating
them: the surface under test here reads exactly the `layer_state` rows `sync.py`
writes, so the same fixtures that prove the writes are correct prove the reads are
too. No HTTP and no live service anywhere in this file — see `no_network` in
conftest.py.
"""

import datetime

import pytest

from mirror import status, sync

from test_sync import (  # noqa: F401  (imported for reuse as fixtures/helpers)
    BASE_DATE, DAY, EDITED, NOW, mirrored, service,
)

pytestmark = pytest.mark.db


# --------------------------------------------------------- 3.2 attempt vs success


def test_attempt_and_success_are_separately_available(mirrored, service):
    """A consumer of the status surface, not the raw table: this is the read 3.2
    asks be possible, exercised through `layer_freshness` rather than SQL by hand."""
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    result = status.layer_freshness(mirrored, "service_requests")

    assert result["known"] is True
    assert result["last_attempt_at"] == NOW
    assert result["last_attempt_ok"] is True
    assert result["last_success_at"] == NOW


def test_a_repeatedly_failing_sync_reports_a_recent_attempt_and_a_stale_success(
        mirrored, service, monkeypatch):
    sync.sync(mirrored, now=NOW, log=lambda m: None)          # one success

    def explode(layer):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(sync.source, "last_edit_date", explode)
    for day in (1, 2):
        with pytest.raises(OSError):
            sync.sync(mirrored, now=NOW + day * DAY, log=lambda m: None)

    result = status.layer_freshness(mirrored, "service_requests")

    assert result["last_attempt_at"] == NOW + 2 * DAY
    assert result["last_attempt_ok"] is False
    assert result["last_success_at"] == NOW           # unmoved since the one success


def test_an_unattempted_layer_reports_known_false_rather_than_an_error(clean_db):
    """A store that has never synced a layer is a state of knowledge (design.md
    M2a's "not yet answerable" pattern), not an exception."""
    result = status.layer_freshness(clean_db, "service_requests")

    assert result["known"] is False
    assert result["behind_source"] is False
    assert "reason" in result


# ----------------------------------------------------- 3.3 the four clocks


def test_four_clocks_are_distinguishable(mirrored, service):
    """The source clock, the data clock, the sync clock and the due clock each
    answer a different question and none of them stand in for another."""
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    result = status.layer_freshness(mirrored, "service_requests")
    data_clock = status.most_recent_call_date(mirrored)

    assert result["source_last_edit"] == EDITED + DAY            # HRM's clock
    assert data_clock == BASE_DATE + datetime.timedelta(hours=10)  # the data itself
    assert result["last_success_at"] == NOW                       # this sync
    assert result["next_due_at"] == NOW + sync.POLL_INTERVAL      # when it's due again
    clocks = {result["source_last_edit"], data_clock, result["last_success_at"],
             result["next_due_at"]}
    assert len(clocks) == 4                                       # none coincide


def test_syncs_succeed_while_the_source_stops_advancing(mirrored, service):
    """3.3's named edge case: a healthy sync against a quiet source. All four
    clocks stay legible, and none of them is mistaken for another."""
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    sync.sync(mirrored, now=NOW + DAY, log=lambda m: None)         # HRM published nothing

    result = status.layer_freshness(mirrored, "service_requests")

    assert result["source_last_edit"] == EDITED                    # unchanged
    assert result["last_success_at"] == NOW + DAY                  # still syncing fine
    assert result["behind_source"] is False                        # nothing to catch up on


def test_mirror_freshness_reports_one_picture_across_layers(mirrored, service):
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    result = status.mirror_freshness(mirrored)

    assert set(result["layers"]) == set(status.TRACKED_LAYERS)
    assert result["most_recent_call_date"] is not None
    assert result["last_success_at"] == NOW
    assert result["behind_source"] is False


# --------------------------------------------------- 3.4 behind the source, or not


def test_behind_the_source_when_hrm_has_published_since_the_last_pull(mirrored,
                                                                       service,
                                                                       monkeypatch):
    """HRM publishing more recently than the last successful pull, distinguished
    from HRM simply not having published: the poll sees the advance, but the pull
    itself fails, so the mirror is genuinely behind rather than merely quiet."""
    service.publish()

    def explode_reload(*a, **k):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(sync, "full_reload", explode_reload)
    with pytest.raises(OSError):
        sync.sync(mirrored, now=NOW, log=lambda m: None)

    result = status.layer_freshness(mirrored, "service_requests")

    assert result["source_last_edit"] == EDITED + DAY     # the poll saw the advance
    assert result["pulled_source_edit"] == EDITED          # but nothing was pulled
    assert result["behind_source"] is True
    assert "published" in result["behind_reason"]


def test_not_behind_when_hrm_simply_has_not_published(mirrored, service):
    """The other half of 3.4's distinction: syncs succeed, and there is nothing to
    be behind on, because the source has not moved."""
    for day in range(5):
        sync.sync(mirrored, now=NOW + day * DAY, log=lambda m: None)

    result = status.layer_freshness(mirrored, "service_requests")

    assert result["source_last_edit"] == result["pulled_source_edit"] == EDITED
    assert result["behind_source"] is False
    assert "has not published" in result["behind_reason"]


def test_a_completed_pull_is_never_reported_as_behind(mirrored, service):
    """The ordinary case: the poll sees the advance and the reload lands it in the
    same run. There is no window here in which the mirror reports itself behind."""
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    result = status.layer_freshness(mirrored, "service_requests")

    assert result["behind_source"] is False


def test_mirror_freshness_is_behind_if_any_currency_layer_is(mirrored, service,
                                                              monkeypatch):
    service.publish()  # advances both service_requests and custom_fields

    calls = {"n": 0}
    real_reload = sync.full_reload

    def reload_unless_custom_fields(conn, layer, *a, **k):
        if layer.base_key == "custom_fields":
            raise OSError("connection reset by peer")
        return real_reload(conn, layer, *a, **k)

    monkeypatch.setattr(sync, "full_reload", reload_unless_custom_fields)
    with pytest.raises(OSError):
        sync.sync(mirrored, now=NOW, log=lambda m: None)

    result = status.mirror_freshness(mirrored)

    assert result["layers"]["service_requests"]["behind_source"] is False
    assert result["layers"]["custom_fields"]["behind_source"] is True
    assert result["behind_source"] is True             # one behind layer is enough
