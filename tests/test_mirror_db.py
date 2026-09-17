"""The store itself: the load's resumability, and the derived parking view.

These run against a throwaway `mirror_test_<pid>` schema and skip when no database is
reachable. The source is a fixture in every case — nothing here goes to HRM.
"""

import datetime

import pytest

from mirror import load, source

pytestmark = pytest.mark.db

SOURCE_LAST_EDIT = datetime.datetime(2026, 9, 13, 10, 34, tzinfo=datetime.UTC)


def fake_request(object_id):
    return {
        "attributes": {
            "ObjectId": object_id,
            "REQUEST_ID": 2000000 + object_id,
            "DATE_INITIATED": 1701960122000,
            "DATE_CLOSED": None,
            "DESCRIPTION": "Parking",
            "INITIATED_BY": "INTERNAL",
            "PRIORITY": "4",
            "ADDRESS": f"{object_id} QUEEN ST,  HALIFAX",
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
    }


class FakeSource:
    """A source of `total` rows served in pages, optionally failing at one page."""

    def __init__(self, total, fail_at_offset=None):
        self.total = total
        self.fail_at_offset = fail_at_offset
        self.pages_served = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(source, "count", lambda layer, where="1=1": self.total)
        monkeypatch.setattr(source, "last_edit_date", lambda layer: SOURCE_LAST_EDIT)
        monkeypatch.setattr(source, "pages", self.pages)
        return self

    def pages(self, layer, start_offset=0, page_size=None, where="1=1"):
        size = page_size or layer.page_size
        offset = start_offset
        while True:
            if offset == self.fail_at_offset:
                raise OSError("connection reset by peer")
            batch = [
                fake_request(i + 1)
                for i in range(offset, min(offset + size, self.total))
            ]
            self.pages_served += 1
            yield offset, batch
            if len(batch) < size:
                return
            offset += size


@pytest.fixture
def layer():
    return source.LAYERS["service_requests"]


def test_a_full_load_stores_every_page_and_reconciles(clean_db, layer, monkeypatch):
    FakeSource(250).install(monkeypatch)

    result = load.load_layer(clean_db, layer, page_size=100, log=lambda m: None)

    assert result["pages"] == 3
    assert result["stored"] == result["source"] == 250
    assert result["reconciled"] and result["complete"]
    assert result["watermark"] == 250


def test_the_load_resumes_at_the_last_completed_page(clean_db, layer, monkeypatch):
    fake = FakeSource(250).install(monkeypatch)

    first = load.load_layer(clean_db, layer, page_size=100, limit_pages=2,
                            log=lambda m: None)
    assert first["pages"] == 2 and first["stored"] == 200 and not first["complete"]

    served_before = fake.pages_served
    second = load.load_layer(clean_db, layer, page_size=100, log=lambda m: None)

    assert second["pages"] == 1               # only the page that was missing
    assert fake.pages_served == served_before + 1
    assert second["stored"] == 250 and second["complete"]


def test_a_page_that_fails_does_not_advance_the_offset(clean_db, layer, monkeypatch):
    FakeSource(250, fail_at_offset=200).install(monkeypatch)

    with pytest.raises(OSError):
        load.load_layer(clean_db, layer, page_size=100, log=lambda m: None)

    with clean_db.cursor() as cur:
        cur.execute("SELECT next_offset, rows_loaded FROM load_progress")
        assert cur.fetchone() == (200, 200)
        cur.execute("SELECT ok, error FROM sync_runs ORDER BY id DESC LIMIT 1")
        ok, error = cur.fetchone()
        assert ok is False and "connection reset" in error

    FakeSource(250).install(monkeypatch)
    resumed = load.load_layer(clean_db, layer, page_size=100, log=lambda m: None)
    assert resumed["stored"] == 250 and resumed["complete"]


def test_restart_pages_from_zero_again(clean_db, layer, monkeypatch):
    fake = FakeSource(250).install(monkeypatch)
    load.load_layer(clean_db, layer, page_size=100, log=lambda m: None)

    again = load.load_layer(clean_db, layer, page_size=100, restart=True,
                            log=lambda m: None)

    assert again["pages"] == 3 and fake.pages_served == 6
    assert again["stored"] == 250          # re-reading a page upserts, never duplicates


def test_a_completed_load_records_its_watermark_and_counts(clean_db, layer, monkeypatch):
    FakeSource(250).install(monkeypatch)
    load.load_layer(clean_db, layer, page_size=100, log=lambda m: None)

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT watermark, source_count, stored_count, full_load_completed_at "
            "IS NOT NULL, last_success_at IS NOT NULL FROM layer_state WHERE layer = %s",
            (layer.key,),
        )
        assert cur.fetchone() == (250, 250, 250, True, True)


def test_a_load_records_the_source_timestamp_it_observed(clean_db, layer, monkeypatch):
    """The cadence accumulates as measurement, starting with the initial load."""
    FakeSource(250).install(monkeypatch)
    load.load_layer(clean_db, layer, page_size=100, log=lambda m: None)

    with clean_db.cursor() as cur:
        cur.execute("SELECT source_last_edit FROM layer_state WHERE layer = %s",
                    (layer.key,))
        assert cur.fetchone()[0] == SOURCE_LAST_EDIT
        cur.execute("SELECT source_last_edit FROM sync_runs ORDER BY id DESC LIMIT 1")
        assert cur.fetchone()[0] == SOURCE_LAST_EDIT


# What each downstream section reads, so a missing column fails here rather than
# as a migration halfway through section 5.
REQUIRED_COLUMNS = {
    "service_requests": [
        "object_id", "request_id", "date_initiated", "date_closed", "address",
        "community", "district", "resolution", "status", "latitude", "longitude",
        "initiated_by",
    ],
    "custom_fields": [
        "object_id", "request_id", "custom_field_id", "custom_field_name",
        "custom_field_value",
    ],
    "census_areas": ["dauid", "dwellings", "population", "rings", "min_lon", "max_lat"],
    "layer_state": [
        "watermark", "source_count", "stored_count", "source_last_edit",
        "last_attempt_at", "last_attempt_ok", "last_success_at", "next_due_at",
    ],
    "load_progress": ["next_offset", "pages_completed", "rows_loaded", "completed_at"],
    "sync_runs": [
        "kind", "layer", "started_at", "finished_at", "ok", "watermark_reached",
        "rows_inserted", "rows_updated", "source_last_edit", "source_count",
        "stored_count", "reconciled", "error",
    ],
    "sync_anomalies": ["sync_run_id", "layer", "kind", "detail"],
    "list_snapshots": ["violation_type", "parameters", "derived_at", "latest_call_date"],
    "doorway_list_history": ["snapshot_id", "address", "rank", "block", "calls_recent"],
    "block_list_history": ["snapshot_id", "block", "doorways", "dwellings"],
    "triage_decisions": [
        "violation_type", "scope", "item_key", "decision", "note", "role", "updated_at",
    ],
}


@pytest.mark.parametrize("table,columns", sorted(REQUIRED_COLUMNS.items()))
def test_the_schema_supports_every_downstream_read(db, table, columns):
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        present = {row[0] for row in cur.fetchall()}
    assert present, f"{table} is missing"
    assert set(columns) <= present, set(columns) - present


def test_containment_needs_no_postgis(db):
    """Census geometry is JSONB and the ray cast stays in Python."""
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_extension WHERE extname = 'postgis'")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'census_areas' "
            "AND column_name = 'rings'"
        )
        assert cur.fetchone()[0] == "jsonb"


def insert_custom_fields(conn, features):
    with conn.cursor() as cur:
        cur.executemany(
            load.CUSTOM_FIELD_SQL.format(table="custom_fields"),
            [load.custom_field_row(f) for f in features],
        )
    conn.commit()


def test_the_parking_view_gives_one_row_per_request(clean_db, parking_features):
    insert_custom_fields(clean_db, parking_features)

    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM custom_fields")
        stored = cur.fetchone()[0]
        cur.execute("SELECT count(*), count(DISTINCT request_id) "
                    "FROM parking_call_attributes")
        rows, requests = cur.fetchone()

    assert stored == len(parking_features)      # key-value, as published
    assert rows == requests == 2                # pivoted, one row per call


def test_the_parking_view_carries_the_seven_attributes(clean_db, parking_features):
    insert_custom_fields(clean_db, parking_features)

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT alleged_violation, property_ownership, vehicle_was_towed, "
            "vehicle_make, vehicle_model, vehicle_colour, vehicle_province "
            "FROM parking_call_attributes WHERE request_id = 2104114"
        )
        assert cur.fetchone() == (
            "No Parking Sign", "HRM", "N", "ford", "transit 250", "white", "NS"
        )


def test_a_call_with_no_alleged_violation_is_not_in_the_view(clean_db):
    with clean_db.cursor() as cur:
        cur.execute(
            "INSERT INTO custom_fields "
            "(object_id, request_id, custom_field_name, custom_field_value) "
            "VALUES (900001, 9000001, 'Vehicle Make', 'ford'), "
            "(900002, 9000002, 'Building', 'Alderney Gate')"
        )
    clean_db.commit()

    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM parking_call_attributes")
        assert cur.fetchone()[0] == 0


def test_the_store_keeps_field_names_beyond_the_parking_seven(clean_db,
                                                              custom_field_features):
    insert_custom_fields(clean_db, custom_field_features)

    with clean_db.cursor() as cur:
        cur.execute("SELECT count(DISTINCT custom_field_name) FROM custom_fields")
        assert cur.fetchone()[0] > 1
