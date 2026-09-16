"""The retained list history (task 8.6): a snapshot per sync that actually reloaded
a layer, and the queries that answer "when did doorway X leave the list" and
"which doorways left between snapshot A and B" from the store alone.

`sync.py`'s own tests (`tests/test_sync.py`, "8.6 retained list history") cover the
wiring -- when a sync takes a snapshot and when it doesn't, and that a derivation
failure doesn't undo a reload. This file is about `history.snapshot()` and the
query functions on their own: given an already-derived result (the same shape
`derive.derive()` returns), what gets retained and what can be asked of it.
"""

import datetime

import pytest

from mirror import db as mirror_db
from mirror import history

pytestmark = pytest.mark.db

T0 = datetime.datetime(2026, 9, 1, 3, 0, tzinfo=datetime.UTC)
T1 = T0 + datetime.timedelta(days=7)
T2 = T0 + datetime.timedelta(days=14)


def _doorway(address, calls=3, block="12345678"):
    """A row shaped like `hotspots.build()` produces it -- the fixed columns
    `history.snapshot()` lifts out, plus extras that belong in `detail`."""
    return {
        "address": address, "calls_12mo": calls, "calls_total": calls + 1,
        "district": "7", "community": "HALIFAX", "owner": "PRIVATE",
        "tows": 1, "tow_rate": round(1 / (calls + 1), 3), "vehicles_seen": 2,
        "vehicles_distinct": 2, "repeat_calls": 1, "median_gap_days": 30,
        "last_call": "2026-08-30", "street": "QUEEN ST", "block": block,
        "block_dwellings": 40, "lat": 44.65, "lon": -63.57,
        "block_doorways_calling": 1,
    }


def _block(block_id="12345678", doorways=1, addresses="12 QUEEN ST"):
    return {
        "block": block_id, "streets": "QUEEN ST", "doorways": doorways,
        "calls_12mo": 3, "calls_total": 4, "tows": 1, "dwellings": 40,
        "calls_per_1k_dwellings": 75.0, "district": "7",
        "worst_doorway": addresses.split(";")[0].strip(), "addresses": addresses,
    }


def _result(addresses, blocks=(), latest=T0):
    return {
        "rows": [_doorway(a) for a in addresses],
        "blocks": list(blocks),
        "calls": [], "fields": {}, "latest": latest,
    }


def snapshot_row(conn, snapshot_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT violation_type, parameters, mirror_version, "
            "mirror_last_success_at, latest_call_date, derived_at "
            "FROM list_snapshots WHERE id = %s", (snapshot_id,))
        return cur.fetchone()


def doorway_rows(conn, snapshot_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT address, rank, calls_recent, calls_total, tows, detail "
            "FROM doorway_list_history WHERE snapshot_id = %s ORDER BY rank",
            (snapshot_id,))
        return cur.fetchall()


def block_rows(conn, snapshot_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT block, rank, doorways, calls_recent, detail "
            "FROM block_list_history WHERE snapshot_id = %s ORDER BY rank",
            (snapshot_id,))
        return cur.fetchall()


# --------------------------------------------------------------- writing a snapshot


def test_a_snapshot_retains_parameters_and_the_mirror_version(clean_db):
    with clean_db.cursor() as cur:
        cur.execute(
            "INSERT INTO layer_state (layer, source_last_edit) VALUES "
            "('service_requests', %s), ('custom_fields', %s)", (T0, T0))
    clean_db.commit()
    with clean_db.cursor() as cur:
        # Read the value back through the driver -- Postgres reports a
        # timestamptz in the connection's own offset, not the one it was
        # inserted with, so the expected string has to come from the same
        # round trip `history._mirror_version` makes, not from `T0.isoformat()`.
        cur.execute("SELECT source_last_edit FROM layer_state "
                    "WHERE layer = 'service_requests'")
        stored_isoformat = cur.fetchone()[0].isoformat()

    snap_id = history.snapshot(
        clean_db, None, "Driveway", _result(["12 QUEEN ST"]),
        parameters={"district": "7"}, mirror_last_success_at=T0, derived_at=T0,
    )

    violation_type, parameters, mirror_version, last_success, latest, derived_at = \
        snapshot_row(clean_db, snap_id)
    assert violation_type == "Driveway"
    # the given override survives, and it is merged onto the defaults, not
    # replacing them wholesale
    assert parameters == {"min_calls": 2, "recur_days": 365, "min_doorways": 2,
                          "district": "7"}
    assert mirror_version == {
        "service_requests": stored_isoformat, "custom_fields": stored_isoformat,
        "census_areas": None,        # no layer_state row for it in this test
    }
    assert last_success == T0
    assert latest == T0.date()
    assert derived_at == T0


def test_a_snapshot_retains_every_doorway_and_block_row(clean_db):
    result = _result(
        ["12 QUEEN ST", "14 QUEEN ST"],
        blocks=[_block(doorways=2, addresses="12 QUEEN ST; 14 QUEEN ST")],
    )

    snap_id = history.snapshot(clean_db, None, "Driveway", result, derived_at=T0)

    doorways = doorway_rows(clean_db, snap_id)
    assert [d[0] for d in doorways] == ["12 QUEEN ST", "14 QUEEN ST"]
    assert [d[1] for d in doorways] == [1, 2]              # rank is list order
    address, rank, calls_recent, calls_total, tows, detail = doorways[0]
    assert (calls_recent, calls_total, tows) == (3, 4, 1)
    assert detail["owner"] == "PRIVATE" and detail["street"] == "QUEEN ST"

    blocks = block_rows(clean_db, snap_id)
    assert len(blocks) == 1
    block, rank, doorway_count, calls_recent, detail = blocks[0]
    assert (block, rank, doorway_count, calls_recent) == ("12345678", 1, 2, 3)
    assert detail["worst_doorway"] == "12 QUEEN ST"


def test_a_snapshot_with_no_addresses_still_retains_the_attempt(clean_db):
    """A quiet type -- nothing met the threshold -- is still a fact worth keeping:
    an empty list at a given time is different from no snapshot at all."""
    snap_id = history.snapshot(clean_db, None, "Driveway", _result([]), derived_at=T0)

    assert doorway_rows(clean_db, snap_id) == []
    assert history.snapshots_for(clean_db, "Driveway") == [{
        "snapshot_id": snap_id, "derived_at": T0,
        "parameters": {"min_calls": 2, "recur_days": 365, "min_doorways": 2,
                       "district": None},
        "mirror_version": {"service_requests": None, "custom_fields": None,
                           "census_areas": None},
        "mirror_last_success_at": None, "latest_call_date": T0.date(),
    }]


# ------------------------------------------------------- a doorway leaves the list


@pytest.fixture
def two_snapshots(clean_db):
    """12 QUEEN ST calls throughout; 14 QUEEN ST is listed at T0 and gone by T1 --
    the spec scenario "A doorway leaves the list" -- literally."""
    snap_a = history.snapshot(
        clean_db, None, "Driveway",
        _result(["12 QUEEN ST", "14 QUEEN ST"]), derived_at=T0)
    snap_b = history.snapshot(
        clean_db, None, "Driveway", _result(["12 QUEEN ST"]), derived_at=T1)
    return clean_db, snap_a, snap_b


def test_a_doorway_that_drops_out_is_identifiable(two_snapshots):
    """Spec scenario 'A doorway leaves the list': both states are retained, and the
    sync at which it left can be identified."""
    conn, snap_a, snap_b = two_snapshots

    left = history.departed_between(conn, snap_a, snap_b)
    assert [row["address"] for row in left] == ["14 QUEEN ST"]
    assert left[0]["calls_recent_when_last_listed"] == 3

    assert history.departed_between(conn, snap_b, snap_a) == []  # nothing re-appeared

    report = history.when_did_doorway_leave(conn, "Driveway", "14 QUEEN ST")
    assert report["status"] == "left"
    assert report["last_seen_snapshot_id"] == snap_a
    assert report["left_snapshot_id"] == snap_b
    assert report["left_at"] == T1


def test_a_doorway_still_on_the_list_has_no_departure(two_snapshots):
    conn, snap_a, snap_b = two_snapshots

    report = history.when_did_doorway_leave(conn, "Driveway", "12 QUEEN ST")

    assert report == {
        "violation_type": "Driveway", "address": "12 QUEEN ST",
        "status": "still_listed", "last_seen_snapshot_id": snap_b,
        "last_seen_at": T1,
    }


def test_a_doorway_never_listed_is_distinguished_from_one_that_left(two_snapshots):
    conn, _snap_a, _snap_b = two_snapshots

    report = history.when_did_doorway_leave(conn, "Driveway", "99 UNSEEN AVE")

    assert report == {"violation_type": "Driveway", "address": "99 UNSEEN AVE",
                      "status": "never_listed"}


def test_when_no_snapshot_has_ever_been_taken(clean_db):
    report = history.when_did_doorway_leave(clean_db, "Driveway", "12 QUEEN ST")
    assert report == {"violation_type": "Driveway", "address": "12 QUEEN ST",
                      "status": "no_snapshots_retained"}


def test_doorway_presence_reports_every_snapshot_in_order(two_snapshots):
    conn, snap_a, snap_b = two_snapshots

    presence = history.doorway_presence(conn, "Driveway", "14 QUEEN ST")

    assert [(p["snapshot_id"], p["present"]) for p in presence] == [
        (snap_a, True), (snap_b, False)]


def test_a_reappearing_doorway_is_visible_in_the_full_history(clean_db):
    """The full presence sequence carries a doorway that left and came back, even
    though `when_did_doorway_leave` -- deliberately -- reports only the most recent
    departure."""
    snap_a = history.snapshot(clean_db, None, "Driveway",
                              _result(["12 QUEEN ST"]), derived_at=T0)
    snap_b = history.snapshot(clean_db, None, "Driveway",
                              _result([]), derived_at=T1)
    snap_c = history.snapshot(clean_db, None, "Driveway",
                              _result(["12 QUEEN ST"]), derived_at=T2)

    presence = history.doorway_presence(clean_db, "Driveway", "12 QUEEN ST")
    assert [(p["snapshot_id"], p["present"]) for p in presence] == [
        (snap_a, True), (snap_b, False), (snap_c, True)]

    report = history.when_did_doorway_leave(clean_db, "Driveway", "12 QUEEN ST")
    assert report["status"] == "still_listed"       # back on the list as of T2
    assert report["last_seen_snapshot_id"] == snap_c


def test_blocks_departed_between_mirrors_the_doorway_query(clean_db):
    snap_a = history.snapshot(
        clean_db, None, "Driveway",
        _result([], blocks=[_block("A"), _block("B")]), derived_at=T0)
    snap_b = history.snapshot(
        clean_db, None, "Driveway",
        _result([], blocks=[_block("A")]), derived_at=T1)

    left = history.blocks_departed_between(clean_db, snap_a, snap_b)
    assert [row["block"] for row in left] == ["B"]


# ---------------------------------------------------- 8.6: survives without git


def test_history_outlives_the_repository(clean_db):
    """Spec scenario 'History outlives the repository': queried from the store,
    via a brand-new connection, with nothing here reading version control or the
    filesystem the scheduled job used to write `out/` to."""
    history.snapshot(clean_db, None, "Driveway",
                     _result(["12 QUEEN ST", "14 QUEEN ST"]), derived_at=T0)
    history.snapshot(clean_db, None, "Driveway",
                     _result(["12 QUEEN ST"]), derived_at=T1)

    fresh = mirror_db.connect()   # a second, independent connection
    try:
        snaps = history.snapshots_for(fresh, "Driveway")
        left = history.departed_between(fresh, snaps[0]["snapshot_id"],
                                        snaps[1]["snapshot_id"])
    finally:
        fresh.close()

    assert len(snaps) == 2
    assert [row["address"] for row in left] == ["14 QUEEN ST"]


def test_history_module_never_touches_version_control_or_the_filesystem():
    """Pinning the claim structurally, the way `tests/test_sync.py`'s credential
    tests pin theirs: this module's only persistence is Postgres -- no subprocess
    (so no `git ...`), and no file I/O (so no writing back to `out/`)."""
    import inspect

    text = inspect.getsource(history)
    for banned in ("subprocess", "os.system(", "open(", "import pathlib"):
        assert banned not in text
