"""Row-level reconciliation (3.6): the diff logic offline, and an end-to-end run
against the throwaway `mirror_test_<pid>` schema with a deliberately induced mismatch.

The source is always a fixture here — see `no_network` in conftest.py. Tests that
touch Postgres are marked `db` and skip when it cannot be reached, same as
test_mirror_db.py.
"""

import datetime
import re

import pytest

from mirror import reconcile, source


# ----------------------------------------------------------- diff logic (pure)


def test_diff_ids_reports_both_directions():
    result = reconcile.diff_ids({1, 2, 3}, {2, 3, 4})

    assert result["live_count"] == 3
    assert result["mirror_count"] == 3
    assert result["missing_from_mirror"] == [1]
    assert result["missing_from_live"] == [4]


def test_diff_ids_identical_reports_nothing_missing():
    result = reconcile.diff_ids({1, 2}, {1, 2})

    assert result["missing_from_mirror"] == []
    assert result["missing_from_live"] == []


def live_request(request_id, **overrides):
    attrs = {
        "REQUEST_ID": request_id,
        "DATE_INITIATED": 1701960122000,
        "DATE_CLOSED": None,
        "DESCRIPTION": "Parking",
        "INITIATED_BY": "INTERNAL",
        "PRIORITY": "4",
        "ADDRESS": "12 QUEEN ST,  HALIFAX",
        "COMMUNITY": "HALIFAX",
        "DISTRICT": "7",
        "REQUEST_CATEGORY": "PARKING",
        "RESOLUTION": None,
        "LATITUDE": 44.65,
        "LONGITUDE": -63.57,
        "STATUS": "OPEN",
        "DEPT_RESPONSIBILITY": "TPW",
        "WORK_ORDER": "N",
    }
    attrs.update(overrides)
    return attrs


def mirror_request(request_id, **overrides):
    row = {
        "date_initiated": source.epoch_to_utc(1701960122000),
        "date_closed": None,
        "description": "Parking",
        "initiated_by": "INTERNAL",
        "priority": "4",
        "address": "12 QUEEN ST,  HALIFAX",
        "community": "HALIFAX",
        "district": "7",
        "request_category": "PARKING",
        "resolution": None,
        "latitude": 44.65,
        "longitude": -63.57,
        "status": "OPEN",
        "dept_responsibility": "TPW",
        "work_order": "N",
    }
    row.update(overrides)
    return row


def test_diff_service_requests_agrees_when_rows_match():
    live = {2101842: live_request(2101842)}
    mirror = {2101842: mirror_request(2101842)}

    result = reconcile.diff_service_requests(live, mirror)

    assert result["field_mismatches"] == []
    assert result["missing_from_mirror"] == []
    assert result["missing_from_live"] == []


def test_diff_service_requests_reports_an_induced_field_mismatch():
    live = {2101842: live_request(2101842, ADDRESS="12 QUEEN ST,  HALIFAX")}
    mirror = {2101842: mirror_request(2101842, address="99 DIFFERENT ST,  HALIFAX")}

    result = reconcile.diff_service_requests(live, mirror)

    assert len(result["field_mismatches"]) == 1
    mismatch = result["field_mismatches"][0]
    assert mismatch["request_id"] == 2101842
    assert mismatch["field"] == "address"
    assert mismatch["live"] == "12 QUEEN ST,  HALIFAX"
    assert mismatch["mirror"] == "99 DIFFERENT ST,  HALIFAX"


def test_diff_service_requests_converts_epoch_dates_before_comparing():
    live = {1: live_request(1, DATE_INITIATED=1701960122000)}
    mirror = {1: mirror_request(1, date_initiated=source.epoch_to_utc(1701960122000))}

    result = reconcile.diff_service_requests(live, mirror)

    assert result["field_mismatches"] == []


def test_diff_service_requests_reports_rows_missing_either_side():
    live = {1: live_request(1), 2: live_request(2)}
    mirror = {2: mirror_request(2)}

    result = reconcile.diff_service_requests(live, mirror)

    assert result["missing_from_mirror"] == [1]
    assert result["missing_from_live"] == []


def test_diff_custom_fields_reports_a_value_mismatch():
    live = {
        (2101842, 60050): {
            "REQUESTID": 2101842,
            "CUSTOM_FIELD_ID": 60050,
            "CUSTOM_FIELD_NAME": "Alleged Violation",
            "CUSTOM_FIELD_VALUE": "DRIVEWAY",
        }
    }
    mirror = {(2101842, 60050): {"name": "Alleged Violation", "value": "SOMETHING ELSE"}}

    result = reconcile.diff_custom_fields(live, mirror)

    assert len(result["field_mismatches"]) == 1
    mismatch = result["field_mismatches"][0]
    assert mismatch["key"] == (2101842, 60050)
    assert mismatch["field"] == "custom_field_value"
    assert mismatch["live"] == "DRIVEWAY"
    assert mismatch["mirror"] == "SOMETHING ELSE"


def test_diff_custom_fields_reports_a_row_missing_from_mirror():
    live = {
        (2101842, 60050): {
            "REQUESTID": 2101842,
            "CUSTOM_FIELD_ID": 60050,
            "CUSTOM_FIELD_NAME": "Alleged Violation",
            "CUSTOM_FIELD_VALUE": "DRIVEWAY",
        }
    }

    result = reconcile.diff_custom_fields(live, {})

    assert result["missing_from_mirror"] == [(2101842, 60050)]
    assert result["field_mismatches"] == []


def test_is_clean_true_with_no_diffs():
    result = {
        "ids": {"missing_from_mirror": [], "missing_from_live": []},
        "service_requests": {
            "missing_from_mirror": [], "missing_from_live": [], "field_mismatches": []
        },
        "custom_fields": {
            "missing_from_mirror": [], "missing_from_live": [], "field_mismatches": []
        },
    }
    assert reconcile.is_clean(result) is True


def test_is_clean_false_on_an_induced_mismatch():
    result = {
        "ids": {"missing_from_mirror": [], "missing_from_live": []},
        "service_requests": {
            "missing_from_mirror": [], "missing_from_live": [],
            "field_mismatches": [{"request_id": 1, "field": "address"}],
        },
        "custom_fields": {
            "missing_from_mirror": [], "missing_from_live": [], "field_mismatches": []
        },
    }
    assert reconcile.is_clean(result) is False


def test_format_report_states_reconciled_or_diverged():
    clean = {
        "violation": "Driveway",
        "freshness": {},
        "ids": {"live_count": 1, "mirror_count": 1,
                "missing_from_mirror": [], "missing_from_live": []},
        "service_requests": {
            "live_count": 1, "mirror_count": 1,
            "missing_from_mirror": [], "missing_from_live": [], "field_mismatches": [],
        },
        "custom_fields": {
            "live_count": 1, "mirror_count": 1,
            "missing_from_mirror": [], "missing_from_live": [], "field_mismatches": [],
        },
    }
    assert "RECONCILED" in reconcile.format_report(clean)

    dirty = dict(clean)
    dirty["ids"] = {**clean["ids"], "missing_from_mirror": [42]}
    assert "DIVERGED" in reconcile.format_report(dirty)
    assert "42" in reconcile.format_report(dirty)


# ----------------------------------------------------- live reads (fixture source)


class FakeArcGIS:
    """Filters in-memory feature lists the way the live service would for the
    `where` clauses reconcile.py builds: an `Alleged Violation` substring match on
    custom_fields, and chunked `REQUEST_ID(S)? IN (...)` on either layer.
    """

    def __init__(self, service_requests=(), custom_fields=()):
        self.service_requests = list(service_requests)
        self.custom_fields = list(custom_fields)
        self.where_clauses = []

    def install(self, monkeypatch):
        monkeypatch.setattr(source, "pages", self.pages)
        # reconcile() checks freshness before anything else; these tests are not
        # about freshness, so stand in with "unchanged" rather than reach the network.
        monkeypatch.setattr(source, "last_edit_date", lambda layer: None)
        return self

    def pages(self, layer, start_offset=0, page_size=None, where="1=1"):
        self.where_clauses.append(where)
        rows = (
            self.service_requests
            if layer.base_key == "service_requests"
            else self.custom_fields
        )
        matched = [r for r in rows if self._matches(r["attributes"], where)]
        yield 0, matched

    @staticmethod
    def _matches(attrs, where):
        violation = re.match(
            r"CUSTOM_FIELD_NAME='([^']*)' AND UPPER\(CUSTOM_FIELD_VALUE\) "
            r"LIKE '%([^%]*)%'",
            where,
        )
        if violation:
            name, needle = violation.groups()
            value = attrs.get("CUSTOM_FIELD_VALUE") or ""
            return attrs.get("CUSTOM_FIELD_NAME") == name and needle in value.upper()
        in_clause = re.match(r"(REQUEST_ID|REQUESTID) IN \(([^)]*)\)", where)
        if in_clause:
            field, ids_str = in_clause.groups()
            wanted = {int(x) for x in ids_str.split(",")}
            return attrs.get(field) in wanted
        return True


def cf_feature(request_id, field_id, name, value):
    return {
        "attributes": {
            "REQUESTID": request_id,
            "CUSTOM_FIELD_ID": field_id,
            "CUSTOM_FIELD_NAME": name,
            "CUSTOM_FIELD_VALUE": value,
        }
    }


def sr_feature(request_id, **overrides):
    return {"attributes": live_request(request_id, **overrides)}


def test_live_driveway_selection_matches_by_substring_case_insensitively(monkeypatch):
    fake = FakeArcGIS(
        custom_fields=[
            cf_feature(1, 100, "Alleged Violation", "DRIVEWAY"),
            cf_feature(2, 101, "Alleged Violation", "Blocking Driveway (DISPATCH)"),
            cf_feature(3, 102, "Alleged Violation", "No Parking Sign"),
            cf_feature(1, 103, "Vehicle Make", "HONDA"),  # not an Alleged Violation row
        ]
    ).install(monkeypatch)

    tagged = reconcile.live_driveway_selection("Driveway")

    assert set(rid for rid, _ in tagged) == {1, 2}
    assert (1, 100) in tagged and (2, 101) in tagged
    assert "Alleged Violation" in fake.where_clauses[0]


def test_live_service_requests_chunks_the_id_list(monkeypatch):
    fake = FakeArcGIS(
        service_requests=[sr_feature(i) for i in range(1, 8)]
    ).install(monkeypatch)

    rows = reconcile.live_service_requests(set(range(1, 8)), chunk_size=3)

    assert set(rows) == set(range(1, 8))
    assert len(fake.where_clauses) == 3  # 7 ids at chunk size 3: 3, 3, 1


def test_live_custom_fields_returns_every_field_not_only_the_parking_seven(
    monkeypatch,
):
    FakeArcGIS(
        custom_fields=[
            cf_feature(1, 100, "Alleged Violation", "DRIVEWAY"),
            cf_feature(1, 101, "Outcome", None),
            cf_feature(1, 102, "Quality Checked", None),
        ]
    ).install(monkeypatch)

    rows = reconcile.live_custom_fields({1})

    assert set(rows) == {(1, 100), (1, 101), (1, 102)}


# --------------------------------------------------------- end-to-end (needs db)


@pytest.fixture
def seeded(clean_db):
    """A mirror holding one Driveway call, matching `live_request`/`live_cf_row`
    below exactly — the baseline a test then perturbs to induce a mismatch.
    """
    clean_db.execute(
        "INSERT INTO service_requests (object_id, request_id, date_initiated, "
        "date_closed, description, initiated_by, priority, address, community, "
        "district, request_category, resolution, latitude, longitude, status, "
        "dept_responsibility, work_order) VALUES "
        "(1, 2101842, %s, NULL, 'Parking', 'INTERNAL', '4', "
        "'12 QUEEN ST,  HALIFAX', 'HALIFAX', '7', 'PARKING', NULL, 44.65, -63.57, "
        "'OPEN', 'TPW', 'N')",
        (source.epoch_to_utc(1701960122000),),
    )
    clean_db.execute(
        "INSERT INTO custom_fields (object_id, request_id, custom_field_id, "
        "custom_field_name, custom_field_value) VALUES "
        "(1, 2101842, 60050, 'Alleged Violation', 'DRIVEWAY'), "
        "(2, 2101842, 60051, 'Vehicle Make', 'HONDA')"
    )
    clean_db.commit()
    return clean_db


@pytest.mark.db
def test_reconcile_reports_clean_when_mirror_matches_the_live_service(
    seeded, monkeypatch
):
    FakeArcGIS(
        service_requests=[sr_feature(2101842)],
        custom_fields=[
            cf_feature(2101842, 60050, "Alleged Violation", "DRIVEWAY"),
            cf_feature(2101842, 60051, "Vehicle Make", "HONDA"),
        ],
    ).install(monkeypatch)

    result = reconcile.reconcile(seeded, "Driveway")

    assert reconcile.is_clean(result)
    assert result["ids"]["live_count"] == 1
    assert result["service_requests"]["field_mismatches"] == []
    assert result["custom_fields"]["field_mismatches"] == []
    assert "RECONCILED" in reconcile.format_report(result)


@pytest.mark.db
def test_reconcile_reports_an_induced_service_request_mismatch(seeded, monkeypatch):
    # The live address disagrees with what the mirror holds for the same call.
    FakeArcGIS(
        service_requests=[sr_feature(2101842, ADDRESS="99 DIFFERENT ST,  HALIFAX")],
        custom_fields=[
            cf_feature(2101842, 60050, "Alleged Violation", "DRIVEWAY"),
            cf_feature(2101842, 60051, "Vehicle Make", "HONDA"),
        ],
    ).install(monkeypatch)

    result = reconcile.reconcile(seeded, "Driveway")

    assert not reconcile.is_clean(result)
    mismatches = result["service_requests"]["field_mismatches"]
    assert any(m["field"] == "address" for m in mismatches)
    assert "DIVERGED" in reconcile.format_report(result)


@pytest.mark.db
def test_reconcile_reports_an_induced_custom_field_value_mismatch(seeded, monkeypatch):
    # The live service disagrees on the vehicle make the mirror stored.
    FakeArcGIS(
        service_requests=[sr_feature(2101842)],
        custom_fields=[
            cf_feature(2101842, 60050, "Alleged Violation", "DRIVEWAY"),
            cf_feature(2101842, 60051, "Vehicle Make", "TOYOTA"),
        ],
    ).install(monkeypatch)

    result = reconcile.reconcile(seeded, "Driveway")

    assert not reconcile.is_clean(result)
    mismatches = result["custom_fields"]["field_mismatches"]
    assert any(
        m["key"] == (2101842, 60051) and m["live"] == "TOYOTA" and m["mirror"] == "HONDA"
        for m in mismatches
    )


@pytest.mark.db
def test_reconcile_reports_a_call_missing_from_the_mirror(seeded, monkeypatch):
    # A second Driveway call the live service has but the mirror does not.
    FakeArcGIS(
        service_requests=[sr_feature(2101842), sr_feature(2222222)],
        custom_fields=[
            cf_feature(2101842, 60050, "Alleged Violation", "DRIVEWAY"),
            cf_feature(2101842, 60051, "Vehicle Make", "HONDA"),
            cf_feature(2222222, 60060, "Alleged Violation", "DRIVEWAY"),
        ],
    ).install(monkeypatch)

    result = reconcile.reconcile(seeded, "Driveway")

    assert not reconcile.is_clean(result)
    assert result["ids"]["missing_from_mirror"] == [2222222]


@pytest.mark.db
def test_freshness_reports_current_when_timestamps_agree(seeded, monkeypatch):
    when = datetime.datetime(2026, 9, 13, 10, 34, tzinfo=datetime.UTC)
    seeded.execute(
        "INSERT INTO layer_state (layer, source_last_edit) VALUES "
        "('service_requests', %s), ('custom_fields', %s)",
        (when, when),
    )
    seeded.commit()
    monkeypatch.setattr(source, "last_edit_date", lambda layer: when)

    result = reconcile.freshness(seeded)

    assert result["service_requests"]["advanced"] is False
    assert result["custom_fields"]["advanced"] is False


@pytest.mark.db
def test_freshness_reports_advanced_when_the_source_has_published_since(
    seeded, monkeypatch
):
    loaded = datetime.datetime(2026, 9, 13, 10, 34, tzinfo=datetime.UTC)
    published = datetime.datetime(2026, 9, 16, 8, 0, tzinfo=datetime.UTC)
    seeded.execute(
        "INSERT INTO layer_state (layer, source_last_edit) VALUES "
        "('service_requests', %s), ('custom_fields', %s)",
        (loaded, loaded),
    )
    seeded.commit()
    monkeypatch.setattr(source, "last_edit_date", lambda layer: published)

    result = reconcile.freshness(seeded)

    assert result["service_requests"]["advanced"] is True
    assert result["service_requests"]["live_last_edit"] == published
    assert result["service_requests"]["mirror_last_edit"] == loaded
