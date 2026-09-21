"""The sync: poll the published clock, and replace the version wholly when it moves.

Every test here runs against a fake source and a throwaway schema. The suite blocks
outbound HTTP, so nothing below can quietly become a live-service test.

The fake source is a published snapshot, like the real one: it answers `1=1` and
nothing else, and a publish may renumber every row. Anything that tried to fetch
"the rows above N" would fail here rather than quietly appear to work.
"""

import datetime
import pathlib
import random
import re
import urllib.request

import pytest

from mirror import db, load, source, sync

pytestmark = pytest.mark.db

DAY = datetime.timedelta(days=1)
NOW = datetime.datetime(2026, 9, 16, 3, 0, tzinfo=datetime.UTC)
EDITED = datetime.datetime(2026, 9, 13, 10, 34, tzinfo=datetime.UTC)
CENSUS_EDITED = datetime.datetime(2024, 3, 19, 13, 16, tzinfo=datetime.UTC)

REPO = pathlib.Path(__file__).resolve().parents[1]


def ms(when):
    return int(when.timestamp() * 1000)


def request_feature(object_id, initiated, closed=None, status="OPEN",
                    resolution=None, request_id=None):
    return {"attributes": {
        "ObjectId": object_id,
        "REQUEST_ID": request_id if request_id is not None else 2000000 + object_id,
        "DATE_INITIATED": ms(initiated),
        "DATE_CLOSED": ms(closed) if closed else None,
        "DESCRIPTION": "Parking",
        "INITIATED_BY": "311 Online",
        "PRIORITY": "4",
        "ADDRESS": f"{object_id} QUEEN ST,  HALIFAX",
        "COMMUNITY": "HALIFAX",
        "DISTRICT": "7",
        "REQUEST_CATEGORY": "PARKING",
        "RESOLUTION": resolution,
        "LATITUDE": 44.65,
        "LONGITUDE": -63.57,
        "STATUS": status,
        "DEPT_RESPONSIBILITY": "TPW",
        "WORK_ORDER": "N",
    }}


def field_feature(object_id, request_id, name, value, field_id=None):
    """The custom-fields layer publishes five columns and no date at all."""
    return {"attributes": {
        "ObjectId": object_id,
        "REQUESTID": request_id,
        "CUSTOM_FIELD_ID": field_id if field_id is not None else object_id,
        "CUSTOM_FIELD_NAME": name,
        "CUSTOM_FIELD_VALUE": value,
    }}


class FakeService:
    """The three layers, their clocks, and the one query shape the sync issues.

    A layer here behaves as the real ones do: it serves whole pages, it carries a
    published edit timestamp, and a publish is free to reassign every `ObjectId`.
    """

    def __init__(self, requests=(), fields=(), areas=()):
        self.rows = {
            "service_requests": list(requests),
            "custom_fields": list(fields),
            "census_areas": list(areas),
        }
        self.last_edit = {
            "service_requests": EDITED,
            "custom_fields": EDITED + datetime.timedelta(minutes=4),
            "census_areas": CENSUS_EDITED,
        }
        self.queries = []       # (layer key, where) per HTTP request issued
        self.metadata_reads = []
        self.count_reads = []   # layer keys asked for a count-only query (3.5)
        self.pages_served = 0
        self.fail_after_pages = None   # the network dies once this many pages are out
        self.between_pages = None      # a hook, to inspect the mirror mid-fill

    def install(self, monkeypatch):
        monkeypatch.setattr(source, "pages", self.pages)
        monkeypatch.setattr(source, "count", self.count)
        monkeypatch.setattr(source, "last_edit_date", self.last_edit_date)
        return self

    # -- the service's own answers

    def last_edit_date(self, layer):
        self.metadata_reads.append(layer.base_key)
        return self.last_edit[layer.base_key]

    def count(self, layer, where="1=1"):
        self.count_reads.append(layer.base_key)
        return len(self.matching(layer, where))

    def matching(self, layer, where):
        rows = self.rows[layer.base_key]
        if where in (None, "1=1"):
            return list(rows)
        # There is no other query shape. A published snapshot has no "since" to ask
        # for, and design.md M2 removed the only code that pretended otherwise.
        raise AssertionError(
            f"the source is queried whole; {where!r} filters within a version"
        )

    def pages(self, layer, start_offset=0, page_size=None, where="1=1"):
        size = page_size or layer.page_size
        matched = sorted(self.matching(layer, where),
                         key=lambda r: r["attributes"][layer.oid_field])
        offset = start_offset
        while True:
            batch = matched[offset:offset + size]
            self.queries.append((layer.base_key, where))
            self.pages_served += 1
            yield offset, batch
            if self.between_pages:
                self.between_pages()
            if (self.fail_after_pages is not None
                    and self.pages_served >= self.fail_after_pages):
                raise OSError("connection reset by peer")
            if len(batch) < size:
                return
            offset += size

    # -- what HRM does between syncs

    def publish(self, at=None):
        """Move both Cityworks clocks, four minutes apart, the way HRM does."""
        at = at or (self.last_edit["service_requests"] + DAY)
        self.last_edit["service_requests"] = at
        self.last_edit["custom_fields"] = at + datetime.timedelta(minutes=4)

    def renumber(self, layer_key, seed=0):
        """Republish the layer with fresh object ids, dense from 1 to N.

        This is what the stored id space says HRM does: 478,458 rows numbered 1 to N
        with not one gap, in an order that has nothing to do with date. The order is
        seeded so a test is repeatable, and it is a derangement so that every
        identifier genuinely moves rather than some landing back where they were.
        """
        rows = self.rows[layer_key]
        previous = [f["attributes"].get("ObjectId") for f in rows]
        ids = list(range(1, len(rows) + 1))
        rnd = random.Random(seed)
        for _ in range(200):
            rnd.shuffle(ids)
            if all(new != old for new, old in zip(ids, previous)):
                break
        else:
            raise AssertionError("no renumbering found that moves every identifier")
        for feature, new_id in zip(rows, ids):
            feature["attributes"]["ObjectId"] = new_id

    def add(self, layer_key, *features):
        self.rows[layer_key].extend(features)

    def replace(self, layer_key, feature):
        oid = feature["attributes"]["ObjectId"]
        rows = self.rows[layer_key]
        for i, existing in enumerate(rows):
            if existing["attributes"]["ObjectId"] == oid:
                rows[i] = feature
                return
        raise AssertionError(f"no row {oid} in {layer_key}")


# ------------------------------------------------------------------ fixtures


BASE_DATE = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def service():
    """Ten requests, one open, each with a tow flag and an alleged violation."""
    requests, fields, oid = [], [], 0
    for i in range(1, 11):
        initiated = BASE_DATE + datetime.timedelta(hours=i)
        closed = None if i == 10 else initiated + datetime.timedelta(hours=2)
        requests.append(request_feature(i, initiated, closed,
                                        status="OPEN" if closed is None else "CLOSED",
                                        resolution=None if closed is None else "DONE"))
        oid += 1
        fields.append(field_feature(oid, 2000000 + i, "Alleged Violation",
                                    "DRIVEWAY", field_id=1))
        oid += 1
        fields.append(field_feature(oid, 2000000 + i, "Vehicle Was Towed", "N",
                                    field_id=2))
    return FakeService(requests, fields)


@pytest.fixture
def mirrored(clean_db, service, monkeypatch):
    """A mirror holding one published version of each layer."""
    service.install(monkeypatch)
    for key in sync.CITYWORKS:
        load.load_layer(clean_db, source.LAYERS[key], log=lambda m: None)
    load.load_layer(clean_db, source.LAYERS["census_areas"], log=lambda m: None)
    service.queries.clear()          # what the sync asks for, not what the load did
    service.metadata_reads.clear()
    service.count_reads.clear()      # the initial load's own reconciliation counts
    service.pages_served = 0
    return clean_db


@pytest.fixture
def fine_sample(monkeypatch):
    """Sample every second request rather than every 128th.

    The fixture holds ten requests; the production stride would sample none of them.
    The rule under test is the stride's *fixedness*, not its value.
    """
    monkeypatch.setattr(sync, "SAMPLE_STRIDE", 2)
    return 2


def runs(conn, kind=None, layer=None):
    where, params = [], []
    if kind:
        where.append("kind = %s")
        params.append(kind)
    if layer:
        where.append("layer = %s")
        params.append(layer)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, kind, layer, ok, watermark_reached, rows_inserted, "
            f"rows_updated, source_last_edit, error FROM sync_runs {clause} "
            f"ORDER BY id", params)
        return cur.fetchall()


def anomalies(conn, kind=None):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT layer, kind, detail FROM sync_anomalies "
            + ("WHERE kind = %s " if kind else "") + "ORDER BY id",
            (kind,) if kind else ())
        return cur.fetchall()


def one(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row and len(row) == 1 else row


def held_requests(conn):
    """Object id to request id, as the mirror currently holds it."""
    with conn.cursor() as cur:
        cur.execute("SELECT object_id, request_id FROM service_requests "
                    "ORDER BY object_id")
        return dict(cur.fetchall())


# ------------------------------------ 2.8 a published version advances: reload


def test_an_advanced_clock_reloads_the_layer_and_reconciles(mirrored, service):
    """The path 2.8 specifies, end to end: the clock moved, so take the version whole."""
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)),
                request_feature(12, BASE_DATE + datetime.timedelta(hours=31)))
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    reloaded = outcome["layers"]["service_requests"]["reload"]
    assert outcome["layers"]["service_requests"]["reason"] == "source advanced"
    assert reloaded["swapped"] is True
    assert reloaded["stored"] == reloaded["source"] == 12
    assert reloaded["reconciled"]
    assert load.stored_count(mirrored, source.LAYERS["service_requests"]) == 12
    reload_runs = runs(mirrored, kind="reload", layer="service_requests")
    assert len(reload_runs) == 1 and reload_runs[0][3] is True


def test_the_reload_asks_for_the_whole_layer_and_filters_on_no_identifier(
        mirrored, service):
    """2.10's deletion, observed from the source's side: no `ObjectId > n` is issued.

    The fake service refuses any other query shape, so this pins the absence rather
    than merely asserting it in the code.
    """
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert service.queries                      # something was fetched
    assert {where for _, where in service.queries} == {"1=1"}


def test_an_edit_that_moved_no_identifier_arrives_because_everything_arrives(
        mirrored, service):
    """The tow flag set after filing — the only published enforcement outcome, and
    the case the deleted open-set refetch existed for. A reload needs no special path
    for it: the row is retrieved because every row is."""
    assert one(mirrored, "SELECT custom_field_value FROM custom_fields "
                         "WHERE object_id = 20") == "N"
    service.replace("custom_fields",
                    field_feature(20, 2000010, "Vehicle Was Towed", "Y", field_id=2))
    closure = BASE_DATE + datetime.timedelta(hours=20)
    service.replace("service_requests",
                    request_feature(10, BASE_DATE + datetime.timedelta(hours=10),
                                    closed=closure, status="CLOSED",
                                    resolution="TOWED VEHICLE"))
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert one(mirrored, "SELECT custom_field_value FROM custom_fields "
                         "WHERE object_id = 20") == "Y"
    assert one(mirrored, "SELECT vehicle_was_towed FROM parking_call_attributes "
                         "WHERE request_id = 2000010") == "Y"
    assert one(mirrored, "SELECT date_closed, status, resolution FROM "
                         "service_requests WHERE request_id = 2000010") == (
        closure, "CLOSED", "TOWED VEHICLE")
    assert load.stored_count(mirrored, source.LAYERS["service_requests"]) == 10


def test_a_republish_that_renumbers_every_row_duplicates_nothing(mirrored, service):
    """The failure a watermark could not survive, and the reason for design.md M2.

    Every record keeps its REQUEST_ID and is handed a new ObjectId, and one new call
    lands below the old high-water mark. A reload is correct regardless: the previous
    version is replaced, not merged into.
    """
    before = held_requests(mirrored)
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30),
                                request_id=2000011))
    service.renumber("service_requests")
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    after = held_requests(mirrored)
    assert len(after) == 11                          # not 21: nothing was duplicated
    assert one(mirrored, "SELECT count(*) - count(DISTINCT request_id) "
                         "FROM service_requests") == 0
    assert set(after.values()) == set(before.values()) | {2000011}
    assert sorted(after) == list(range(1, 12))       # dense 1..N, as published
    assert after != before                           # and every identifier moved


def test_a_reload_reconciles_against_the_service_s_own_count(mirrored, service):
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    run = runs(mirrored, kind="reload", layer="service_requests")[-1]
    stored, source_count, reconciled = one(
        mirrored, "SELECT stored_count, source_count, reconciled FROM sync_runs "
                  "WHERE id = %s", (run[0],))
    assert stored == source_count == 10 and reconciled is True
    assert outcome["layers"]["custom_fields"]["reload"]["reconciled"]


# -------------------------------------------------- 2.9 the swap is atomic


def test_an_interrupted_reload_leaves_the_previous_version_intact(mirrored, service):
    """The whole of 2.9: a reload that dies partway changes nothing at all."""
    before = held_requests(mirrored)
    success_before = one(mirrored, "SELECT last_success_at FROM layer_state "
                                   "WHERE layer = 'service_requests'")
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.renumber("service_requests")
    service.publish()
    service.fail_after_pages = 2                 # dies with four pages to fetch

    with pytest.raises(OSError):
        sync.full_reload(mirrored, source.LAYERS["service_requests"],
                         page_size=3, log=lambda m: None)

    assert held_requests(mirrored) == before      # not one row of it moved
    assert one(mirrored, "SELECT count(*) FROM service_requests") == 10
    assert one(mirrored, "SELECT count(*) FROM parking_call_attributes") == 10
    failed = runs(mirrored, kind="reload", layer="service_requests")[-1]
    assert failed[3] is False and "connection reset" in failed[8]
    ok, success = one(mirrored, "SELECT last_attempt_ok, last_success_at "
                                "FROM layer_state WHERE layer = 'service_requests'")
    assert ok is False and success == success_before
    # The partial copy is parked where it can be resumed, not spliced into the mirror.
    assert one(mirrored, "SELECT count(*) FROM service_requests__staging") == 6


def test_the_interrupted_reload_resumes_and_then_replaces(mirrored, service):
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()
    service.fail_after_pages = 2
    with pytest.raises(OSError):
        sync.full_reload(mirrored, source.LAYERS["service_requests"],
                         page_size=3, log=lambda m: None)

    service.fail_after_pages = None
    service.pages_served = 0
    result = sync.full_reload(mirrored, source.LAYERS["service_requests"],
                              page_size=3, log=lambda m: None)

    assert result["resumed"] is True
    assert service.pages_served == 2              # resumed at offset 6, not at zero
    assert result["swapped"] and result["stored"] == 11 and result["reconciled"]


def test_the_live_table_holds_one_whole_version_throughout_the_fill(mirrored,
                                                                     service):
    """Not merely intact at the end: intact at every moment in between."""
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.renumber("service_requests")
    service.publish()
    seen = []

    def look():
        seen.append(one(mirrored, "SELECT count(*), count(DISTINCT request_id) "
                                  "FROM service_requests"))

    service.between_pages = look

    sync.full_reload(mirrored, source.LAYERS["service_requests"], page_size=3,
                     log=lambda m: None)

    assert len(seen) >= 3                         # one look between each pair of pages
    assert set(seen) == {(10, 10)}                # the old version, whole, every time
    assert one(mirrored, "SELECT count(*) FROM service_requests") == 11


def test_a_swap_that_fails_midway_rolls_back_to_the_previous_version(mirrored,
                                                                      service):
    """The emptying and the refilling are one transaction or they are a mixture."""
    before = held_requests(mirrored)
    staging, _ = sync.prepare_staging(mirrored, source.LAYERS["service_requests"],
                                      EDITED + DAY)
    with mirrored.cursor() as cur:
        cur.execute("INSERT INTO service_requests__staging (object_id, request_id) "
                    "VALUES (1, 2000001)")
        # A row the live table will refuse: request_id is NOT NULL there and here,
        # but the refill is what discovers it, halfway through the swap.
        cur.execute("ALTER TABLE service_requests__staging "
                    "ALTER COLUMN request_id DROP NOT NULL")
        cur.execute("INSERT INTO service_requests__staging (object_id) VALUES (2)")
    mirrored.commit()

    with pytest.raises(Exception):
        sync.swap_in(mirrored, source.LAYERS["service_requests"], staging)
    mirrored.rollback()

    assert held_requests(mirrored) == before
    assert one(mirrored, "SELECT count(*) FROM service_requests") == 10


def test_a_staged_copy_of_a_superseded_version_is_not_spliced_onto_a_newer_one(
        mirrored, service):
    """Resumption is only ever within one published version.

    If HRM publishes again while a fill is interrupted, continuing the fill would
    join pages of two different versions. It starts over instead.
    """
    service.publish()
    service.fail_after_pages = 2
    with pytest.raises(OSError):
        sync.full_reload(mirrored, source.LAYERS["service_requests"],
                         page_size=3, log=lambda m: None)
    assert one(mirrored, "SELECT next_offset FROM load_progress "
                         "WHERE layer = 'service_requests__staging'") == 6

    service.fail_after_pages = None
    service.publish()                             # a second publish lands meanwhile
    service.pages_served = 0
    result = sync.full_reload(mirrored, source.LAYERS["service_requests"],
                              page_size=3, log=lambda m: None)

    assert result["resumed"] is False
    assert service.pages_served == 4              # from zero: 3+3+3+1
    assert result["stored"] == 10 and result["reconciled"]


def test_a_partial_fill_is_not_swapped_in(mirrored, service):
    """`limit_pages` stops the fill cleanly rather than by failing. The held version
    still stands, because a staged copy replaces nothing until it is complete."""
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()

    result = sync.full_reload(mirrored, source.LAYERS["service_requests"],
                              page_size=3, limit_pages=2, log=lambda m: None)

    assert result["swapped"] is False
    assert result["stored"] == 10                 # the version already held
    assert one(mirrored, "SELECT count(*) FROM service_requests") == 10
    run = runs(mirrored, kind="reload", layer="service_requests")[-1]
    assert run[3] is False and "not replaced" in run[8]


def test_a_reload_leaves_no_staging_table_behind(mirrored, service):
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    for key in sync.CITYWORKS:
        assert not sync.table_exists(mirrored, f"{key}__staging")
        assert one(mirrored, "SELECT count(*) FROM load_progress WHERE layer = %s",
                   (f"{key}__staging",)) == 0


# ------------------------------- 2.10 the retired machinery, and its absence


RETIRED = [
    "watermark_pull", "check_monotonicity", "check_reassignment", "refetch_open_set",
    "open_request_ids", "fetch_by_id", "pull_is_due", "upsert", "changed_only",
    "MONOTONICITY", "MONOTONICITY_TOLERANCE", "REASSIGNMENT", "UPSERTS",
    "PULL_INTERVAL", "OPEN_SET_CHUNK", "last_successful_pull_at",
]


@pytest.mark.parametrize("name", RETIRED)
def test_the_retired_mechanism_is_gone_not_merely_unused(name):
    """Dead code that looks live is worse than none. design.md M2 says it is gone."""
    assert not hasattr(sync, name), f"sync.{name} survived the reversal of M2"


def test_nothing_reads_the_watermark_as_a_sync_input():
    """`layer_state.watermark` is written as an observation and read by nothing.

    The column stays because section 3 reports the watermark a run reached, but no
    query selects it back and no fetch filters on an object id.
    """
    for path in sorted(pathlib.Path(source.__file__).parent.glob("*.py")):
        text = path.read_text()
        assert "SELECT watermark" not in text, path.name
        assert "watermark FROM layer_state" not in text, path.name
        assert "oid_field} >" not in text, path.name
        assert "ObjectId >" not in text, path.name


def test_the_open_set_index_went_with_the_open_set_refetch():
    """The partial index served the retired open-set refetch, so nothing creates it
    any more. The `DROP INDEX IF EXISTS` stays: a store built before this change still
    carries the index physically, and this is what sheds it on the next schema apply."""
    schema = db.SCHEMA_PATH.read_text()
    assert "CREATE INDEX IF NOT EXISTS service_requests_open_idx" not in schema
    assert "DROP INDEX IF EXISTS service_requests_open_idx" in schema
    assert "identifier_reassigned" not in schema


def test_no_anomaly_kind_from_the_retired_method_can_still_be_raised():
    text = (pathlib.Path(source.__file__).parent / "sync.py").read_text()
    for kind in ("monotonicity_violated", "unresolvable_request",
                 "identifier_reassigned", "missing_at_source"):
        assert f'"{kind}"' not in text


# ------------------------------------------ 2.11 what a version was, retained


def test_the_replaced_version_is_retained_before_it_is_overwritten(
        mirrored, service, fine_sample):
    """Its timestamp, its size, its highest identifier, and a sample of its
    identifiers — all still queryable after the version itself is gone."""
    before = held_requests(mirrored)
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.renumber("service_requests")
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    versions = sync.retained_versions(mirrored, "service_requests")
    replaced = [v for v in versions if v["note"] == "replaced"]
    assert len(replaced) == 1
    assert replaced[0]["source_last_edit"] == EDITED
    assert replaced[0]["row_count"] == 10
    assert replaced[0]["highest_object_id"] == 10
    assert replaced[0]["sample_size"] == 5        # every second request, of ten
    with mirrored.cursor() as cur:
        cur.execute("SELECT business_key, object_id FROM layer_version_samples "
                    "WHERE version_id = %s ORDER BY object_id",
                    (replaced[0]["version_id"],))
        sampled = dict(cur.fetchall())
    assert {int(k): v for k, v in sampled.items()} == {
        request_id: object_id for object_id, request_id in before.items()
        if request_id % 2 == 0
    }


def test_the_incoming_version_is_retained_too_so_the_next_reload_can_compare(
        mirrored, service, fine_sample):
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    versions = sync.retained_versions(mirrored, "service_requests")
    assert [v["note"] for v in versions] == ["replaced", "loaded"]
    assert [v["source_last_edit"] for v in versions] == [EDITED, EDITED + DAY]


def test_a_version_is_recorded_once_however_often_it_is_seen(mirrored, service,
                                                              fine_sample):
    """The version one reload retains as incoming is the one the next finds going
    out. Recording it again is a no-op, not a second version."""
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    service.publish()
    sync.sync(mirrored, now=NOW + DAY, log=lambda m: None)

    versions = sync.retained_versions(mirrored, "service_requests")
    stamps = [v["source_last_edit"] for v in versions]
    assert stamps == [EDITED, EDITED + DAY, EDITED + 2 * DAY]
    assert len(stamps) == len(set(stamps))


def test_the_sample_is_a_sample_and_the_same_records_in_every_version(
        mirrored, service, fine_sample):
    """A fixed residue class, not a random draw: two versions must sample the same
    records or the comparison has nothing to join on."""
    service.renumber("service_requests")
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    versions = sync.retained_versions(mirrored, "service_requests")
    keys = []
    for version in versions:
        with mirrored.cursor() as cur:
            cur.execute("SELECT business_key FROM layer_version_samples "
                        "WHERE version_id = %s", (version["version_id"],))
            keys.append({row[0] for row in cur.fetchall()})
    assert keys[0] == keys[1]                     # same records, both versions
    assert all(0 < len(k) < 10 for k in keys)     # a sample, not the whole mapping
    assert all(int(k) % fine_sample == 0 for k in keys[0])


def test_the_custom_fields_sample_keys_on_the_request_and_the_field(
        mirrored, service, fine_sample):
    """REQUESTID alone is not a record there: a request carries many fields."""
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    version = sync.retained_versions(mirrored, "custom_fields")[0]
    with mirrored.cursor() as cur:
        cur.execute("SELECT business_key FROM layer_version_samples "
                    "WHERE version_id = %s ORDER BY business_key",
                    (version["version_id"],))
        keys = [row[0] for row in cur.fetchall()]
    assert keys == sorted(keys)
    assert all(re.fullmatch(r"\d+:\d+", k) for k in keys)
    assert len(keys) == len(set(keys)) == 10      # five requests, two fields each


def test_the_production_stride_is_fixed_and_sized_against_the_loaded_mirror():
    """128 is 3,721 requests of 478,458 — a few thousand, as design.md M2a asks.

    Fixed rather than derived from the row count: a stride that moved with the layer
    would sample different records in each version.
    """
    assert sync.SAMPLE_STRIDE == 128
    assert 478458 // sync.SAMPLE_STRIDE == 3737
    assert set(sync.VERSION_SAMPLE) == set(source.LAYERS)


# ------------------------------------------------ 2.12 renumbering, detected


def test_a_publish_that_renumbers_is_reported_as_renumbering(mirrored, service,
                                                              fine_sample):
    service.renumber("service_requests")
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    report = sync.renumbering_report(mirrored, "service_requests")

    assert report["comparable"] is True
    assert report["verdict"] == "identifiers reassigned by the publish"
    assert report["shared_keys"] == 5
    assert report["kept_identifier"] == 0
    assert report["moved_identifier"] == 5
    assert report["examples"] and report["examples"][0]["was"] != \
        report["examples"][0]["now"]


def test_a_publish_that_preserves_identifiers_is_reported_as_preserving(
        mirrored, service, fine_sample):
    """The other hypothesis, which would make an incremental sync worth costing."""
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    report = sync.renumbering_report(mirrored, "service_requests")

    assert report["verdict"] == "identifiers preserved across the publish"
    assert report["kept_identifier"] == report["shared_keys"] == 5
    assert report["only_in_newer"] == 0          # request 2000011 is not sampled
    assert report["moved_identifier"] == 0


def test_a_partial_reassignment_is_not_reported_as_either_extreme(mirrored, service,
                                                                   fine_sample):
    service.rows["service_requests"][0]["attributes"]["ObjectId"] = 99
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    # Request 2000002 is sampled and moved; the rest of the sample did not.
    service.rows["service_requests"][1]["attributes"]["ObjectId"] = 98
    service.publish()
    sync.sync(mirrored, now=NOW + DAY, log=lambda m: None)

    report = sync.renumbering_report(mirrored, "service_requests")

    assert report["verdict"] == "identifiers partially reassigned"
    assert report["moved_identifier"] == 1 and report["kept_identifier"] == 4


def test_one_retained_version_reports_an_answer_rather_than_an_error(mirrored,
                                                                      service,
                                                                      fine_sample):
    """"Not yet" is a state of knowledge. It is not an exception."""
    report = sync.renumbering_report(mirrored, "service_requests")

    assert report["versions_retained"] == 0
    assert report["comparable"] is False
    assert report["verdict"] == "not yet answerable"
    assert "two are needed" in report["reason"]

    service.publish()
    sync.full_reload(mirrored, source.LAYERS["service_requests"],
                     log=lambda m: None)
    partial = sync.renumbering_report(mirrored, "service_requests")
    assert partial["versions_retained"] == 2      # the one replaced and the one loaded
    assert partial["comparable"] is True


def test_the_report_names_the_two_versions_it_compared(mirrored, service,
                                                        fine_sample):
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    report = sync.renumbering_report(mirrored, "service_requests")

    assert report["older"]["source_last_edit"] == EDITED
    assert report["newer"]["source_last_edit"] == EDITED + DAY


# --------------------------------- 2.13 answerable after four publishes


def test_four_publishes_answer_the_m2a_question_without_re_instrumenting(
        mirrored, service, fine_sample):
    """design.md M2a defers the watermark question and asks for four observed
    publishes. This is those four, run through the ordinary nightly path — no extra
    collection, no code change, and an answer for every consecutive pair."""
    for night in range(1, 5):
        service.add("service_requests",
                    request_feature(11 + night,
                                    BASE_DATE + datetime.timedelta(hours=30 + night),
                                    request_id=2000011 + night))
        service.renumber("service_requests", seed=night)
        service.publish()
        sync.sync(mirrored, now=NOW + night * DAY, log=lambda m: None)

    versions = sync.retained_versions(mirrored, "service_requests")
    assert len(versions) == 5                      # the original plus four publishes
    report = sync.renumbering_report(mirrored, "service_requests")
    assert report["publishes_observed"] == 4
    assert report["verdict"] == "identifiers reassigned by the publish"

    for older, newer in zip(versions, versions[1:]):
        pair = sync.renumbering_report(mirrored, "service_requests",
                                       older["version_id"], newer["version_id"])
        assert pair["comparable"], (older, newer)
        assert pair["verdict"] == "identifiers reassigned by the publish"


def test_every_retained_version_carries_what_m2a_asks_for(mirrored, service,
                                                           fine_sample):
    for night in range(1, 3):
        service.publish()
        sync.sync(mirrored, now=NOW + night * DAY, log=lambda m: None)

    for version in sync.retained_versions(mirrored, "service_requests"):
        assert version["source_last_edit"] is not None    # published edit timestamp
        assert version["row_count"] == 10                 # row count
        assert version["highest_object_id"] == 10         # highest identifier
        assert version["sample_size"] == 5                # and the sample


def test_the_design_records_when_the_question_is_next_examined():
    text = (REPO / "openspec/changes/mirror-hrm-data-and-host-app/design.md").read_text()
    m2a = text.split("### M2a")[1].split("### M3")[0]
    assert "four publishes" in m2a
    assert "sync.py --versions" in m2a


def test_the_versions_report_reads_without_a_database_query_by_hand(mirrored,
                                                                     service,
                                                                     fine_sample):
    service.renumber("service_requests")
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    text = sync.describe_versions(mirrored, "service_requests")

    assert "identifiers reassigned by the publish" in text
    assert "publishes observed: 1" in text


# ------------------------------------------------------------ 2.5 the no-op


def test_a_sync_finding_nothing_new_is_recorded_as_successful(mirrored, service):
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["pulled"] == []
    assert all(row[3] for row in runs(mirrored))          # nothing failed
    assert [row[8] for row in runs(mirrored, kind="poll")] == [None, None, None]
    assert anomalies(mirrored) == []
    assert not runs(mirrored, kind="reload")


def test_a_reload_that_finds_nothing_changed_still_reconciles(mirrored, service):
    """Forced, with a source that has not moved: the same rows arrive again and the
    mirror ends where it started."""
    before = held_requests(mirrored)

    outcome = sync.sync(mirrored, now=NOW, force_pull=True, log=lambda m: None)

    assert outcome["layers"]["service_requests"]["reason"] == "forced"
    assert held_requests(mirrored) == before
    assert outcome["layers"]["service_requests"]["reload"]["reconciled"]
    assert anomalies(mirrored) == []


def test_a_no_op_sync_records_a_successful_attempt_against_the_layer(mirrored,
                                                                     service):
    sync.sync(mirrored, now=NOW, force_pull=True, log=lambda m: None)

    with mirrored.cursor() as cur:
        cur.execute("SELECT last_attempt_ok, last_attempt_at, last_success_at "
                    "FROM layer_state WHERE layer = 'service_requests'")
        ok, attempt, success = cur.fetchone()
    assert ok is True and attempt == NOW and success == NOW


# --------------------------------------------- 2.6 the poll and the pull


def test_a_poll_against_an_unchanged_source_pulls_nothing(mirrored, service):
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["pulled"] == []
    for layer_key in source.LAYERS:
        polls = runs(mirrored, kind="poll", layer=layer_key)
        assert len(polls) == 1 and polls[0][3] is True
    assert not runs(mirrored, kind="reload")
    assert not service.queries                      # no paging request at all


def test_a_poll_that_finds_the_clock_advanced_pulls(mirrored, service):
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert set(outcome["pulled"]) == set(sync.CITYWORKS)
    assert outcome["layers"]["service_requests"]["reason"] == "source advanced"


def test_a_week_with_one_publication_is_seven_polls_and_one_pull(mirrored, service):
    """The two cadences are separate, and this is what separate means."""
    sync.sync(mirrored, now=NOW, log=lambda m: None)                     # day 0
    for day in range(1, 8):
        if day == 4:
            service.publish()
        sync.sync(mirrored, now=NOW + day * DAY, log=lambda m: None)

    polls = runs(mirrored, kind="poll", layer="service_requests")
    reloads = runs(mirrored, kind="reload", layer="service_requests")
    assert len(polls) == 8                      # day 0 plus seven nights
    assert len(reloads) == 1                    # the one night the source moved
    # Eight polls, plus the one metadata read the reload itself makes to see whether
    # the source published again while it was reading.
    assert service.metadata_reads.count("service_requests") == 9


def test_a_night_with_nothing_published_costs_two_requests_per_layer(mirrored,
                                                                      service):
    """The poll plus the 3.5 reconciliation check are the whole cost of a quiet
    night — both cheap, single-shot requests, neither of them paging."""
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert len(service.metadata_reads) == 3       # one lastEditDate poll per layer
    assert len(service.count_reads) == 3           # one count reconciliation per layer
    assert service.pages_served == 0


def test_poll_only_never_pulls(mirrored, service):
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, poll_only=True, log=lambda m: None)

    assert outcome["pulled"] == []
    assert len(runs(mirrored, kind="poll")) == 3
    assert not runs(mirrored, kind="reload")


def test_the_static_census_layer_is_not_repulled_because_a_schedule_fired(
        mirrored, service):
    """Unedited since 2024-03-19. A timer is not a reason to fetch 610 polygons."""
    for day in range(6):
        sync.sync(mirrored, now=NOW + day * DAY, log=lambda m: None)

    assert len(runs(mirrored, kind="poll", layer="census_areas")) == 6
    assert not runs(mirrored, kind="reload", layer="census_areas")
    assert not [q for q in service.queries if q[0] == "census_areas"]


def test_the_static_layer_is_pulled_when_its_own_clock_moves(mirrored, service):
    service.last_edit["census_areas"] = CENSUS_EDITED + datetime.timedelta(days=400)

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert "census_areas" in outcome["pulled"]
    assert outcome["layers"]["census_areas"]["reason"] == "source advanced"
    assert outcome["layers"]["census_areas"]["reload"]["reconciled"]


def test_every_poll_records_the_timestamp_it_observed(mirrored, service):
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    service.publish()
    sync.sync(mirrored, now=NOW + DAY, log=lambda m: None)

    observed = [row[7] for row in runs(mirrored, kind="poll",
                                       layer="service_requests")]
    assert observed == [EDITED, EDITED + DAY]


def test_the_interval_between_source_updates_is_queryable(mirrored, service):
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    service.publish(EDITED + 3 * DAY)
    sync.sync(mirrored, now=NOW + 3 * DAY, log=lambda m: None)
    service.publish(EDITED + 10 * DAY)
    sync.sync(mirrored, now=NOW + 10 * DAY, log=lambda m: None)

    history = sync.source_edit_history(mirrored, "service_requests")

    assert [entry["source_last_edit"] for entry in history] == [
        EDITED, EDITED + 3 * DAY, EDITED + 10 * DAY]
    assert [entry["interval"] for entry in history] == [None, 3 * DAY, 7 * DAY]


def test_the_cadence_is_not_guessed_from_a_single_observation(mirrored, service):
    sync.sync(mirrored, now=NOW, log=lambda m: None)

    history = sync.source_edit_history(mirrored, "service_requests")

    assert len(history) == 1 and history[0]["interval"] is None


# ------------------------------------------------- 2.6e running unattended


def test_the_sync_sends_no_credential(monkeypatch):
    """The layers are public. Nothing here carries a token, a key or a header."""
    captured = {}

    class Response:
        def read(self):
            return b'{"features": []}'

    def capture(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data.decode()
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    monkeypatch.setattr("json.load", lambda fp: {"features": []})
    source.page(source.LAYERS["service_requests"], 0)

    assert not any(h.lower() in ("authorization", "cookie", "x-api-key")
                   for h in captured["headers"])
    assert "token" not in captured["body"].lower()
    assert "apikey" not in captured["body"].lower().replace("_", "")


def test_the_sync_reads_no_credential_from_the_environment():
    text = (db.SCHEMA_PATH.parent / "sync.py").read_text()
    for secret in ("token", "api_key", "apikey", "password", "secret", "credential"):
        assert f'"{secret}"' not in text.lower()
    # The only environment the mirror reads is where its own database lives: the
    # PG* variables libpq reads, and the schema inside it. Both the direct reads and
    # the REQUIRED / OPTIONAL lists count, so a variable added either way fails this.
    import inspect
    source = inspect.getsource(db)
    read = set(re.findall(r"environ(?:\.get)?[\[(]\s*\"(\w+)\"", source))
    read |= set(re.findall(r"getenv\(\s*\"(\w+)\"", source))
    read |= set(db.REQUIRED) | set(db.OPTIONAL)
    assert read == {"PGHOST", "PGUSER", "PGDATABASE", "PGPASSWORD", "PGPORT",
                    "HFX_MIRROR_SCHEMA"}


def test_a_sync_takes_no_arguments_and_records_its_outcome(mirrored, service):
    """Unattended means the schedule fires and nothing else is needed."""
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["started_at"] == NOW
    assert all(row[3] for row in runs(mirrored))


# ---------------------------------------------------------- 2.7 full reload


def test_a_reload_after_an_induced_divergence_restores_agreement(mirrored, service):
    with mirrored.cursor() as cur:
        cur.execute("DELETE FROM service_requests WHERE object_id IN (3, 4)")
        cur.execute("INSERT INTO service_requests (object_id, request_id) "
                    "VALUES (99, 2999999)")
    mirrored.commit()
    assert load.stored_count(mirrored, source.LAYERS["service_requests"]) == 9
    assert service.count(source.LAYERS["service_requests"]) == 10

    result = sync.full_reload(mirrored, source.LAYERS["service_requests"],
                              log=lambda m: None)

    assert result["stored"] == result["source"] == 10
    assert result["reconciled"]
    assert one(mirrored, "SELECT count(*) FROM service_requests "
                         "WHERE object_id = 99") == 0


def test_a_reload_is_recorded_as_a_run(mirrored, service):
    sync.full_reload(mirrored, source.LAYERS["service_requests"],
                     log=lambda m: None)

    reloads = runs(mirrored, kind="reload", layer="service_requests")
    assert len(reloads) == 1 and reloads[0][3] is True


def test_a_reload_discards_a_record_held_twice_under_two_identifiers(mirrored,
                                                                      service):
    """Replacement rather than merge is what makes this unremarkable."""
    with mirrored.cursor() as cur:
        cur.execute("INSERT INTO service_requests (object_id, request_id, "
                    "date_initiated) SELECT 200, request_id, date_initiated "
                    "FROM service_requests WHERE object_id = 1")
    mirrored.commit()
    assert one(mirrored, "SELECT count(*) FROM service_requests "
                         "WHERE request_id = 2000001") == 2

    sync.full_reload(mirrored, source.LAYERS["service_requests"], log=lambda m: None)

    assert one(mirrored, "SELECT count(*) FROM service_requests "
                         "WHERE request_id = 2000001") == 1


def test_a_count_divergence_is_reported_with_both_figures(mirrored, service,
                                                           monkeypatch):
    """The service's count and what arrived disagree: recorded, never resolved
    silently, and the version that arrived is the one the source is publishing."""
    monkeypatch.setattr(source, "count", lambda layer, where="1=1": 12)

    result = sync.full_reload(mirrored, source.LAYERS["service_requests"],
                              log=lambda m: None)

    assert result["swapped"] and result["reconciled"] is False
    found = anomalies(mirrored, "count_divergence")
    assert found and found[0][2]["stored"] == 10 and found[0][2]["source"] == 12


def test_a_publish_during_a_reload_is_noticed_and_left_for_the_next_poll(
        mirrored, service):
    """The reload is recorded against the version it began reading, so the newer one
    still reads as an advance on the next poll rather than being skipped."""
    service.publish()
    original = service.last_edit["service_requests"]

    def publish_again():
        service.between_pages = None
        service.publish()

    service.between_pages = publish_again
    sync.full_reload(mirrored, source.LAYERS["service_requests"], original, NOW,
                     page_size=3, log=lambda m: None)

    found = anomalies(mirrored, "source_published_during_reload")
    assert found and found[0][2]["version_read"].startswith("2026-09-14")
    assert sync.last_pulled_source_edit(mirrored, "service_requests") == original

    outcome = sync.sync(mirrored, now=NOW + DAY, log=lambda m: None)
    assert outcome["layers"]["service_requests"]["advanced"] is True


# ---------------------------------------------------------- failure honesty


def test_a_failed_poll_is_a_row_and_leaves_the_held_version_alone(mirrored, service,
                                                                   monkeypatch):
    before = held_requests(mirrored)
    success_before = one(mirrored, "SELECT last_success_at FROM layer_state "
                                   "WHERE layer = 'service_requests'")

    def explode(layer):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(source, "last_edit_date", explode)
    with pytest.raises(OSError):
        sync.sync(mirrored, now=NOW, log=lambda m: None)

    failed = runs(mirrored, kind="poll", layer="service_requests")[-1]
    assert failed[3] is False and "connection reset" in failed[8]
    assert held_requests(mirrored) == before
    ok, success = one(mirrored, "SELECT last_attempt_ok, last_success_at "
                                "FROM layer_state WHERE layer = 'service_requests'")
    assert ok is False and success == success_before


def test_a_poll_records_the_highest_identifier_actually_held(mirrored, service):
    """A state row created by a poll reports the version's size, not a default zero.

    The column is an observation about the version held. Nothing resumes from it —
    the source reassigns object ids on publish — and 2.10 removed the code that did.
    """
    with mirrored.cursor() as cur:
        cur.execute("DELETE FROM layer_state WHERE layer = 'service_requests'")
    mirrored.commit()

    sync.poll_layer(mirrored, source.LAYERS["service_requests"], NOW)

    assert one(mirrored, "SELECT watermark FROM layer_state "
                         "WHERE layer = 'service_requests'") == 10


# --------------------------------- 3.1 every field a sync attempt records


def test_a_reload_run_records_every_field_3_1_asks_for(mirrored, service):
    """start, outcome, watermark reached, rows inserted and updated, the observed
    source last-edit, and (on failure) an error — all in the one row, per layer."""
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()

    sync.sync(mirrored, now=NOW, log=lambda m: None)

    with mirrored.cursor() as cur:
        cur.execute(
            "SELECT started_at, finished_at, ok, watermark_reached, rows_inserted, "
            "rows_updated, source_last_edit, error FROM sync_runs "
            "WHERE kind = 'reload' AND layer = 'service_requests'"
        )
        (started, finished, ok, watermark, inserted, updated, edit, error,
         ) = cur.fetchone()
    assert started == NOW and finished is not None and finished >= started
    assert ok is True
    assert watermark == 11                       # the highest id in the new version
    assert inserted == 11                         # every row the reload fetched
    # 3.1: a reload replaces the whole layer, so there is no row in the finished
    # version that was "updated" rather than freshly written into staging — see the
    # note in sync.full_reload. Recorded as 0, not left at the column default.
    assert updated == 0
    assert edit == EDITED + DAY
    assert error is None


def test_a_failed_reload_records_the_fields_it_reached_before_failing(mirrored,
                                                                       service):
    """The interpretation in sync.full_reload applies on the failure path too: what
    was fetched before the failure is still honestly "inserted," and nothing is
    claimed as "updated."""
    service.add("service_requests",
                request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()
    service.fail_after_pages = 2

    with pytest.raises(OSError):
        sync.full_reload(mirrored, source.LAYERS["service_requests"], page_size=3,
                         now=NOW, log=lambda m: None)

    with mirrored.cursor() as cur:
        cur.execute(
            "SELECT started_at, ok, rows_inserted, rows_updated, error "
            "FROM sync_runs WHERE kind = 'reload' AND layer = 'service_requests' "
            "ORDER BY id DESC LIMIT 1"
        )
        started, ok, inserted, updated, error = cur.fetchone()
    assert started == NOW
    assert ok is False
    assert inserted == 6                          # two pages of three, before the break
    assert updated == 0
    assert "connection reset" in error


def test_a_no_op_reload_still_records_watermark_and_source_edit(mirrored, service):
    """A forced reload that changes nothing still fills in every 3.1 field —
    "nothing new" is not the same as "nothing recorded."""
    outcome = sync.sync(mirrored, now=NOW, force_pull=True, log=lambda m: None)

    reload_result = outcome["layers"]["service_requests"]["reload"]
    run = runs(mirrored, kind="reload", layer="service_requests")[-1]
    _id, _kind, _layer, ok, watermark, inserted, updated, edit, error = run
    assert ok is True
    assert watermark == 10
    assert inserted == 10
    assert updated == 0
    assert edit == EDITED
    assert error is None
    assert reload_result["reconciled"]


# --------------------------------------- 3.5 reconciliation on the no-op path


def test_a_no_op_poll_also_reconciles_and_records_agreement(mirrored, service):
    """2.8 already reconciles a reload; this is the other case — a night nothing
    was pulled still compares stored against source, per design.md's "Reconcile
    the mirror against the source", which asks for the check on every sync."""
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    reconciled = outcome["layers"]["service_requests"]["reconcile"]
    assert reconciled == {"layer": "service_requests", "stored": 10, "source": 10,
                          "reconciled": True}
    assert not runs(mirrored, kind="reload")       # nothing was pulled to check this
    run = runs(mirrored, kind="poll", layer="service_requests")[-1]
    with mirrored.cursor() as cur:
        cur.execute(
            "SELECT stored_count, source_count, reconciled FROM sync_runs "
            "WHERE id = %s", (run[0],))
        assert cur.fetchone() == (10, 10, True)
    assert anomalies(mirrored) == []


def test_a_no_op_poll_reports_divergence_without_fixing_it(mirrored, service):
    """Induced without a publish, so the poll finds the version unchanged and never
    pulls — the divergence has to be caught by the reconciliation check alone, and
    it must be reported, not silently repaired."""
    with mirrored.cursor() as cur:
        cur.execute("DELETE FROM service_requests WHERE object_id = 3")
    mirrored.commit()
    assert load.stored_count(mirrored, source.LAYERS["service_requests"]) == 9

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    reconcile = outcome["layers"]["service_requests"]["reconcile"]
    assert reconcile == {"layer": "service_requests", "stored": 9, "source": 10,
                         "reconciled": False}
    assert outcome["pulled"] == []                 # divergence alone triggers no pull
    assert not runs(mirrored, kind="reload")
    # not fixed: the missing row is still missing after the sync that noticed it
    assert load.stored_count(mirrored, source.LAYERS["service_requests"]) == 9
    found = anomalies(mirrored, "count_divergence")
    assert found and found[0][0] == "service_requests"
    assert found[0][2]["stored"] == 9 and found[0][2]["source"] == 10


def test_a_pulled_layer_is_not_separately_reconciled(mirrored, service):
    """A reload already reconciles itself after the swap (2.8); checking again
    before it, against the version about to be replaced, would compare stale
    figures and manufacture a false divergence every time the source publishes."""
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert "reconcile" not in outcome["layers"]["service_requests"]
    assert outcome["layers"]["service_requests"]["reload"]["reconciled"]


def test_the_static_layer_is_reconciled_on_a_quiet_night_too(mirrored, service):
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["layers"]["census_areas"]["reconcile"]["reconciled"] is True


# ------------------------------------------------------- 3.2 attempt vs success


def test_a_run_of_successful_no_op_polls_advances_last_success_every_night(
        mirrored, service):
    """3.2's "last success" only means something if an ordinary quiet success moves
    it. Before this, only a *pull* advanced last_success_at — a mirror that was
    simply confirmed current every night for a month looked exactly like one that
    had not synced since its initial load."""
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    sync.sync(mirrored, now=NOW + DAY, log=lambda m: None)

    assert one(mirrored, "SELECT last_success_at FROM layer_state "
                         "WHERE layer = 'service_requests'") == NOW + DAY


def test_repeated_failure_advances_attempt_but_not_success(mirrored, service,
                                                            monkeypatch):
    """The scenario 3.2 names directly: a sync failing repeatedly reports a recent
    attempt and an unchanged last-success time."""
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    success_after_first = one(mirrored, "SELECT last_success_at FROM layer_state "
                                        "WHERE layer = 'service_requests'")

    def explode(layer):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(source, "last_edit_date", explode)
    for day in (1, 2, 3):
        with pytest.raises(OSError):
            sync.sync(mirrored, now=NOW + day * DAY, log=lambda m: None)

    attempt, success, ok = one(
        mirrored, "SELECT last_attempt_at, last_success_at, last_attempt_ok "
                  "FROM layer_state WHERE layer = 'service_requests'")
    assert attempt == NOW + 3 * DAY                # the most recent try
    assert success == success_after_first          # unmoved since the last one that worked
    assert ok is False


# ------------------------------------------------------- 8.6 retained list history


def snapshot_rows(conn, violation_type="Blocking Driveway"):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, sync_run_id, parameters, mirror_version, latest_call_date "
            "FROM list_snapshots WHERE violation_type = %s ORDER BY id",
            (violation_type,))
        return cur.fetchall()


def test_a_quiet_night_takes_no_snapshot_and_costs_no_extra_request(mirrored, service):
    """design.md M5: the record moves into the store, but a no-op poll changes no
    row in any layer, so derive.derive() would return exactly what the last
    snapshot already holds. Snapshotting it anyway would only duplicate rows for a
    list that could not have moved -- and the request cost (3.5's poll +
    reconcile) has to stay exactly what it was before this feature existed."""
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["pulled"] == []
    assert outcome["snapshots"] == []
    assert snapshot_rows(mirrored) == []
    assert not runs(mirrored, kind="derive")
    assert len(service.metadata_reads) == 3        # unchanged: one poll per layer
    assert len(service.count_reads) == 3            # unchanged: one reconcile per layer
    assert service.pages_served == 0                # unchanged: nothing paged


def test_a_reload_retains_the_lists_it_produced(mirrored, service):
    """The path 8.6 specifies: a sync that actually reloaded a layer retains what
    derive.derive_all() produced from the fresh mirror -- every canonical type from
    one pass, each with the parameters and the mirror version it was derived
    under."""
    from mirror import violation_types

    service.add("service_requests",
               request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["pulled"]                       # at least one layer reloaded
    assert len(outcome["snapshots"]) == len(violation_types.CANONICAL_TYPES)
    assert {s["violation_type"] for s in outcome["snapshots"]} == \
        set(violation_types.CANONICAL_TYPES)
    assert all(s["ok"] for s in outcome["snapshots"])

    by_type = {s["violation_type"]: s for s in outcome["snapshots"]}
    # every fixture request is tagged DRIVEWAY (-> "Blocking Driveway"), and every
    # address is called exactly once, below the default min_calls of 2, so even
    # that type's own list comes back empty
    snap = by_type["Blocking Driveway"]
    assert snap == {"violation_type": "Blocking Driveway", "ok": True,
                    "snapshot_id": snap["snapshot_id"], "doorways": 0, "blocks": 0}
    # a type with no calls at all is still snapshotted -- an empty list is retained,
    # not skipped
    other = by_type["No Parking Sign"]
    assert other == {"violation_type": "No Parking Sign", "ok": True,
                     "snapshot_id": other["snapshot_id"], "doorways": 0, "blocks": 0}

    rows = snapshot_rows(mirrored, "Blocking Driveway")
    assert len(rows) == 1
    snap_id, run_id, parameters, mirror_version, latest_call_date = rows[0]
    assert snap_id == snap["snapshot_id"]
    assert parameters == {"min_calls": 2, "recur_days": 365, "min_doorways": 2,
                          "district": None}
    with mirrored.cursor() as cur:
        cur.execute("SELECT layer, source_last_edit FROM layer_state")
        held = dict(cur.fetchall())
    assert mirror_version == {
        layer: (held[layer].isoformat() if held.get(layer) else None)
        for layer in ("service_requests", "custom_fields", "census_areas")
    }
    derive_run = runs(mirrored, kind="derive", layer="Blocking Driveway")
    assert len(derive_run) == 1 and derive_run[0][0] == run_id and derive_run[0][3]
    # every canonical type got its own sync_runs row, not just this one
    assert len(runs(mirrored, kind="derive")) == len(violation_types.CANONICAL_TYPES)


def test_a_derivation_failure_is_recorded_but_does_not_undo_the_reload(
        mirrored, service, monkeypatch):
    """8.6: deriving must not make a successful reload look failed, and a
    derivation failure must not undo it. The reload has already committed
    (`sync.swap_in`) before `history.snapshot_after_sync` is ever called.

    `derive_all()` is the one call that produces every type's result, so when it
    itself raises, every requested type failed together -- each still gets its own
    `sync_runs` row and its own anomaly (the module docstring's "One pass, one row
    per type")."""
    from mirror import history, violation_types

    def explode(conn, **kwargs):
        raise RuntimeError("derivation blew up")

    monkeypatch.setattr(history.derive, "derive_all", explode)
    service.add("service_requests",
               request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    reloaded = outcome["layers"]["service_requests"]["reload"]
    assert reloaded["swapped"] is True and reloaded["reconciled"]
    assert load.stored_count(mirrored, source.LAYERS["service_requests"]) == 11
    reload_runs = runs(mirrored, kind="reload", layer="service_requests")
    assert reload_runs[-1][3] is True               # the reload's own row: still ok

    assert outcome["snapshots"] == [
        {"violation_type": t, "ok": False, "error": "derivation blew up"}
        for t in violation_types.CANONICAL_TYPES
    ]
    derive_runs = runs(mirrored, kind="derive", layer="Blocking Driveway")
    assert len(derive_runs) == 1 and derive_runs[0][3] is False
    assert derive_runs[0][8] == "derivation blew up"
    assert len(runs(mirrored, kind="derive")) == len(violation_types.CANONICAL_TYPES)
    assert all(r[3] is False for r in runs(mirrored, kind="derive"))
    assert snapshot_rows(mirrored) == []             # nothing partial was retained
    found = anomalies(mirrored, "list_snapshot_failed")
    assert len(found) == len(violation_types.CANONICAL_TYPES)
    assert {f[0] for f in found} == set(violation_types.CANONICAL_TYPES)


def test_a_reload_snapshots_every_requested_type_from_one_derive_all_call(
        mirrored, monkeypatch):
    """4.16's one-pass derivation is the point of switching to `derive_all()`
    here: however many canonical types are requested, the mirror is read once, not
    once per type. Called directly (not through `sync.sync()`) with an explicit,
    small `types` so the call count is easy to pin down."""
    from mirror import history

    calls = []
    real_derive_all = history.derive.derive_all

    def counting_derive_all(conn, **kwargs):
        calls.append(kwargs.get("types"))
        return real_derive_all(conn, **kwargs)

    monkeypatch.setattr(history.derive, "derive_all", counting_derive_all)

    types = ("Blocking Driveway", "No Parking Sign", "Private Property")
    outcomes = history.snapshot_after_sync(
        mirrored, {"pulled": ["service_requests"]}, now=NOW, types=types,
        log=lambda m: None)

    assert len(calls) == 1                          # one derive_all call, not three
    assert calls[0] == list(types)
    assert [o["violation_type"] for o in outcomes] == list(types)
    assert all(o["ok"] for o in outcomes)
    assert len({o["snapshot_id"] for o in outcomes}) == 3   # three distinct snapshots
    assert {r[2] for r in runs(mirrored, kind="derive")} == set(types)


def test_one_types_failing_snapshot_insert_does_not_block_the_others(
        mirrored, monkeypatch):
    """A `derive_all()` failure takes every type down together (the test above
    this section), but a failure *after* `derive_all()` has already returned --
    one type's `snapshot()` insert going bad -- must not: the other types' rows
    already exist and stand."""
    from mirror import history

    real_snapshot = history.snapshot

    def flaky_snapshot(conn, run_id, violation_type, result, **kwargs):
        if violation_type == "No Parking Sign":
            raise RuntimeError("insert blew up")
        return real_snapshot(conn, run_id, violation_type, result, **kwargs)

    monkeypatch.setattr(history, "snapshot", flaky_snapshot)

    types = ("Blocking Driveway", "No Parking Sign", "Private Property")
    outcomes = history.snapshot_after_sync(
        mirrored, {"pulled": ["service_requests"]}, now=NOW, types=types,
        log=lambda m: None)

    by_type = {o["violation_type"]: o for o in outcomes}
    assert by_type["Blocking Driveway"]["ok"] is True
    assert by_type["Private Property"]["ok"] is True
    assert by_type["No Parking Sign"] == {
        "violation_type": "No Parking Sign", "ok": False, "error": "insert blew up"}

    assert snapshot_rows(mirrored, "Blocking Driveway") != []
    assert snapshot_rows(mirrored, "Private Property") != []
    assert snapshot_rows(mirrored, "No Parking Sign") == []

    found = anomalies(mirrored, "list_snapshot_failed")
    assert len(found) == 1 and found[0][0] == "No Parking Sign"

    derive_runs = {r[2]: r for r in runs(mirrored, kind="derive")}
    assert derive_runs["No Parking Sign"][3] is False
    assert derive_runs["Blocking Driveway"][3] is True
    assert derive_runs["Private Property"][3] is True


# --------------------------------------------------------- 5.7 type figures


def type_figures_rows(conn, canonical_type=None):
    q = ("SELECT canonical_type, sync_run_id, figures, parameters, mirror_version "
        "FROM type_figures")
    params = ()
    if canonical_type:
        q += " WHERE canonical_type = %s"
        params = (canonical_type,)
    q += " ORDER BY id"
    with conn.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall()


def test_a_quiet_night_computes_no_type_figures_and_costs_no_extra_request(
        mirrored, service):
    """Mirrors the 8.6 quiet-night test directly: a poll that finds nothing to pull
    computes no figures either, and the request cost stays exactly what it was
    before this feature existed."""
    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["pulled"] == []
    assert outcome["type_figures"] == []
    assert type_figures_rows(mirrored) == []
    assert not runs(mirrored, kind="type_figures")
    assert len(service.metadata_reads) == 3        # unchanged: one poll per layer
    assert len(service.count_reads) == 3            # unchanged: one reconcile per layer
    assert service.pages_served == 0                # unchanged: nothing paged


def test_a_reload_computes_type_figures_for_every_type(mirrored, service):
    """The path 5.7 specifies: a sync that actually reloaded a layer computes and
    stores every canonical type's filter-independent figures, keyed to the mirror
    version they were derived from."""
    from mirror import type_figures, violation_types

    service.add("service_requests",
               request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    assert outcome["pulled"]
    assert len(outcome["type_figures"]) == len(violation_types.CANONICAL_TYPES)
    assert {f["canonical_type"] for f in outcome["type_figures"]} == \
        set(violation_types.CANONICAL_TYPES)
    assert all(f["ok"] for f in outcome["type_figures"])

    rows = type_figures_rows(mirrored)
    assert len(rows) == len(violation_types.CANONICAL_TYPES)

    driveway = type_figures_rows(mirrored, "Blocking Driveway")
    assert len(driveway) == 1
    canonical_type, sync_run_id, figures_json, parameters, mirror_version = driveway[0]
    assert canonical_type == "Blocking Driveway"
    assert set(figures_json) >= {"tow", "vehicles", "overall_conclusion",
                                 "response_time", "call_denominators"}
    assert parameters == type_figures.DEFAULT_PARAMETERS
    with mirrored.cursor() as cur:
        cur.execute("SELECT layer, source_last_edit FROM layer_state")
        held = dict(cur.fetchall())
    assert mirror_version == {
        layer: (held[layer].isoformat() if held.get(layer) else None)
        for layer in ("service_requests", "custom_fields", "census_areas")
    }
    type_figures_run = runs(mirrored, kind="type_figures", layer="Blocking Driveway")
    assert len(type_figures_run) == 1 and type_figures_run[0][0] == sync_run_id
    assert type_figures_run[0][3] is True
    assert len(runs(mirrored, kind="type_figures")) == len(violation_types.CANONICAL_TYPES)


def test_a_type_figures_failure_does_not_undo_the_reload_or_the_snapshots(
        mirrored, service, monkeypatch):
    """5.7: a figures-compute failure must not read as a failed sync and must not
    undo the reload or the list snapshots (8.6) that already committed before this
    step ever runs."""
    from mirror import type_figures, violation_types

    def explode(conn, **kwargs):
        raise RuntimeError("type figures blew up")

    monkeypatch.setattr(type_figures.per_type, "compute_all", explode)
    service.add("service_requests",
               request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()

    outcome = sync.sync(mirrored, now=NOW, log=lambda m: None)

    reloaded = outcome["layers"]["service_requests"]["reload"]
    assert reloaded["swapped"] is True and reloaded["reconciled"]
    assert outcome["snapshots"] and all(s["ok"] for s in outcome["snapshots"])

    assert outcome["type_figures"] == [
        {"canonical_type": t, "ok": False, "error": "type figures blew up"}
        for t in violation_types.CANONICAL_TYPES
    ]
    assert type_figures_rows(mirrored) == []
    found = anomalies(mirrored, "type_figures_failed")
    assert len(found) == len(violation_types.CANONICAL_TYPES)
    assert {f[0] for f in found} == set(violation_types.CANONICAL_TYPES)
    type_figures_runs = runs(mirrored, kind="type_figures")
    assert len(type_figures_runs) == len(violation_types.CANONICAL_TYPES)
    assert all(r[3] is False for r in type_figures_runs)


def test_running_the_compute_command_again_after_a_reload_does_not_duplicate(
        mirrored, service):
    """The exact scenario 5.7 asks to be verified: a second run of the
    compute-and-store command against the version a reload just produced does not
    duplicate what the reload's own hook already stored."""
    from mirror import type_figures

    service.add("service_requests",
               request_feature(11, BASE_DATE + datetime.timedelta(hours=30)))
    service.publish()
    sync.sync(mirrored, now=NOW, log=lambda m: None)
    first_count = len(type_figures_rows(mirrored))
    assert first_count > 0

    outcomes = type_figures.compute_and_store(mirrored, now=NOW, log=lambda m: None)

    assert outcomes == []                        # nothing missing; nothing computed
    assert len(type_figures_rows(mirrored)) == first_count
