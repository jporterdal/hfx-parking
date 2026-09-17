"""Task 5.7: compute and store each canonical type's filter-independent figures,
keyed to the mirror version they were derived from.

`tests/test_sync.py`'s own "5.7 type figures" section covers the `sync.sync()`
wiring -- when a reload triggers a compute and when it doesn't, and that a compute
failure doesn't undo the reload. This file is about `type_figures.compute_and_store()`
and `type_figures.figures_for()` on their own: given a mirror already holding some
calls, what gets stored, whether re-running duplicates it, what a new mirror version
does to the answer, and how a read reports "nothing stored" rather than guessing.
"""

import datetime

import pytest

from mirror import type_figures

pytestmark = pytest.mark.db

BASE = datetime.datetime(2024, 1, 1, 12, tzinfo=datetime.UTC)
T0 = datetime.datetime(2026, 9, 1, 3, 0, tzinfo=datetime.UTC)
T1 = T0 + datetime.timedelta(days=7)

DRIVEWAY = "Blocking Driveway"
NO_PARKING = "No Parking Sign"

_LAYERS = ("service_requests", "custom_fields", "census_areas")

# Well under per_type.MIN_TOW_GROUP_SAMPLE (30) -- this file is about what gets
# stored and read, not about the tow/vehicle statistics themselves
# (tests/test_per_type.py owns those, with data shaped to actually clear the floor).
CALLS_SEEDED = 5


def insert_service_request(conn, object_id, request_id, address, date_initiated,
                           date_closed=None, district="7"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_requests (object_id, request_id, date_initiated, "
            "date_closed, address, community, district, latitude, longitude, "
            "initiated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (object_id, request_id, date_initiated, date_closed, address, "HALIFAX",
             district, 44.65, -63.57, "INTERNAL"),
        )
    conn.commit()


def insert_custom_field(conn, object_id, request_id, name, value):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO custom_fields (object_id, request_id, custom_field_id, "
            "custom_field_name, custom_field_value) VALUES (%s, %s, NULL, %s, %s)",
            (object_id, request_id, name, value),
        )
    conn.commit()


def seed_driveway(conn, n=CALLS_SEEDED, start_oid=1):
    """A handful of Blocking Driveway calls, some towed, some closed -- enough for
    every figure this module stores (tow, vehicles, response_time,
    call_denominators) to have something other than an empty population to report.
    """
    oid = start_oid
    for i in range(n):
        request_id = 3_000_000 + oid
        initiated = BASE + datetime.timedelta(days=i)
        closed = initiated + datetime.timedelta(hours=1) if i % 2 else None
        insert_service_request(conn, oid, request_id, f"{oid} QUEEN ST, HALIFAX",
                               initiated, closed)
        insert_custom_field(conn, oid * 10, request_id, "Alleged Violation",
                            "DRIVEWAY")
        insert_custom_field(conn, oid * 10 + 1, request_id, "Vehicle Was Towed",
                            "Y" if i % 2 else "N")
        oid += 1
    return oid


def set_mirror_version(conn, edit, layers=_LAYERS):
    with conn.cursor() as cur:
        for layer in layers:
            cur.execute(
                "INSERT INTO layer_state (layer, source_last_edit) VALUES (%s, %s) "
                "ON CONFLICT (layer) DO UPDATE SET source_last_edit = %s",
                (layer, edit, edit),
            )
    conn.commit()


def type_figures_rows(conn, canonical_type=None):
    q = ("SELECT canonical_type, figures, mirror_version, parameters, computed_at "
        "FROM type_figures")
    params = ()
    if canonical_type:
        q += " WHERE canonical_type = %s"
        params = (canonical_type,)
    q += " ORDER BY id"
    with conn.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall()


def sync_runs(conn, kind=None):
    q = "SELECT layer, ok, error FROM sync_runs"
    params = ()
    if kind:
        q += " WHERE kind = %s"
        params = (kind,)
    q += " ORDER BY id"
    with conn.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall()


def anomalies(conn, kind=None):
    q = "SELECT layer, detail FROM sync_anomalies"
    params = ()
    if kind:
        q += " WHERE kind = %s"
        params = (kind,)
    with conn.cursor() as cur:
        cur.execute(q, params)
        return cur.fetchall()


FAST = {"bootstrap_iterations": 50}   # cheap enough to keep the suite quick


# --------------------------------------------------------------- computing and storing


def test_compute_and_store_stores_every_requested_type(clean_db):
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)

    outcomes = type_figures.compute_and_store(
        clean_db, types=[DRIVEWAY, NO_PARKING], now=T0, parameters=FAST,
        log=lambda m: None,
    )

    assert {o["canonical_type"] for o in outcomes} == {DRIVEWAY, NO_PARKING}
    assert all(o["ok"] for o in outcomes)

    rows = type_figures_rows(clean_db)
    assert {r[0] for r in rows} == {DRIVEWAY, NO_PARKING}
    for canonical_type, figures_json, mirror_version, parameters, _computed_at in rows:
        assert set(figures_json) >= {
            "tow", "vehicles", "overall_conclusion", "response_time",
            "call_denominators",
        }
        assert figures_json["overall_conclusion"].startswith(f"For {canonical_type}")
        assert set(mirror_version) == set(_LAYERS)
        assert parameters["bootstrap_iterations"] == 50
        assert parameters["recur_days"] == 365   # merged onto DEFAULT_PARAMETERS

    driveway_run = [r for r in sync_runs(clean_db, kind="type_figures")
                    if r[0] == DRIVEWAY]
    assert len(driveway_run) == 1 and driveway_run[0][1] is True


def test_a_type_with_no_calls_is_still_stored(clean_db):
    """An empty population is itself an observation -- matches history.py's own
    'a snapshot with no addresses still retains the attempt'."""
    set_mirror_version(clean_db, T0)

    outcomes = type_figures.compute_and_store(
        clean_db, types=[NO_PARKING], now=T0, parameters=FAST, log=lambda m: None,
    )

    assert outcomes == [{"canonical_type": NO_PARKING, "ok": True,
                         "id": outcomes[0]["id"], "computed_at": T0}]
    rows = type_figures_rows(clean_db, NO_PARKING)
    assert len(rows) == 1
    figures_json = rows[0][1]
    assert figures_json["population"] == 0
    assert figures_json["tow"]["conclusion_key"] == "sample_too_small"
    assert figures_json["call_denominators"]["populations"][
        "raw_selection_total"]["count"] == 0


def test_a_second_run_against_the_same_version_does_not_duplicate(clean_db):
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)

    first = type_figures.compute_and_store(
        clean_db, types=[DRIVEWAY], now=T0, parameters=FAST, log=lambda m: None,
    )
    second = type_figures.compute_and_store(
        clean_db, types=[DRIVEWAY], now=T0, parameters=FAST, log=lambda m: None,
    )

    assert len(first) == 1 and first[0]["ok"]
    assert second == []                          # nothing missing, nothing computed
    assert len(type_figures_rows(clean_db, DRIVEWAY)) == 1
    # only one sync_runs row too -- the second call never opened one
    assert len(sync_runs(clean_db, kind="type_figures")) == 1


def test_a_second_run_with_different_parameters_is_not_a_duplicate(clean_db):
    """Different parameters describe a different figure, not a repeat of the same
    one -- the uniqueness constraint is (mirror_version, canonical_type,
    parameters) together, per task 5.7."""
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)

    type_figures.compute_and_store(clean_db, types=[DRIVEWAY], now=T0,
                                   parameters=FAST, log=lambda m: None)
    outcomes = type_figures.compute_and_store(
        clean_db, types=[DRIVEWAY], now=T0,
        parameters={"bootstrap_iterations": 51}, log=lambda m: None,
    )

    assert len(outcomes) == 1 and outcomes[0]["ok"]
    assert len(type_figures_rows(clean_db, DRIVEWAY)) == 2


def test_a_new_mirror_version_produces_new_rows(clean_db):
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)
    type_figures.compute_and_store(clean_db, types=[DRIVEWAY], now=T0,
                                   parameters=FAST, log=lambda m: None)

    set_mirror_version(clean_db, T1)
    outcomes = type_figures.compute_and_store(clean_db, types=[DRIVEWAY], now=T1,
                                              parameters=FAST, log=lambda m: None)

    assert len(outcomes) == 1 and outcomes[0]["ok"]
    rows = type_figures_rows(clean_db, DRIVEWAY)
    assert len(rows) == 2                         # both versions retained
    assert rows[0][2] != rows[1][2]                # mirror_version actually differs


def test_a_compute_failure_is_recorded_and_stores_nothing(clean_db, monkeypatch):
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)

    def explode(conn, **kwargs):
        raise RuntimeError("per-type blew up")

    monkeypatch.setattr(type_figures.per_type, "compute_all", explode)

    outcomes = type_figures.compute_and_store(
        clean_db, types=[DRIVEWAY, NO_PARKING], now=T0, log=lambda m: None,
    )

    assert outcomes == [
        {"canonical_type": DRIVEWAY, "ok": False, "error": "per-type blew up"},
        {"canonical_type": NO_PARKING, "ok": False, "error": "per-type blew up"},
    ]
    assert type_figures_rows(clean_db) == []
    runs = sync_runs(clean_db, kind="type_figures")
    assert len(runs) == 2 and all(r[1] is False for r in runs)
    found = anomalies(clean_db, "type_figures_failed")
    assert {f[0] for f in found} == {DRIVEWAY, NO_PARKING}


def test_a_per_type_store_failure_does_not_block_the_others(clean_db, monkeypatch):
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)

    real_store = type_figures._store

    def flaky_store(conn, run_id, canonical_type, *args, **kwargs):
        if canonical_type == NO_PARKING:
            raise RuntimeError("insert blew up")
        return real_store(conn, run_id, canonical_type, *args, **kwargs)

    monkeypatch.setattr(type_figures, "_store", flaky_store)

    outcomes = type_figures.compute_and_store(
        clean_db, types=[DRIVEWAY, NO_PARKING], now=T0, parameters=FAST,
        log=lambda m: None,
    )

    by_type = {o["canonical_type"]: o for o in outcomes}
    assert by_type[DRIVEWAY]["ok"] is True
    assert by_type[NO_PARKING] == {"canonical_type": NO_PARKING, "ok": False,
                                   "error": "insert blew up"}
    assert type_figures_rows(clean_db, DRIVEWAY) != []
    assert type_figures_rows(clean_db, NO_PARKING) == []
    found = anomalies(clean_db, "type_figures_failed")
    assert len(found) == 1 and found[0][0] == NO_PARKING
    runs = {r[0]: r for r in sync_runs(clean_db, kind="type_figures")}
    assert runs[DRIVEWAY][1] is True
    assert runs[NO_PARKING][1] is False


# --------------------------------------------------------------------- reading


def test_read_reports_the_stored_figures(clean_db):
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)
    type_figures.compute_and_store(clean_db, types=[DRIVEWAY], now=T0,
                                   parameters=FAST, log=lambda m: None)

    result = type_figures.figures_for(clean_db, DRIVEWAY, parameters=FAST)

    assert result["available"] is True
    assert result["canonical_type"] == DRIVEWAY
    assert result["figures"]["overall_conclusion"].startswith(f"For {DRIVEWAY}")
    assert "response_time" in result["figures"]
    assert "call_denominators" in result["figures"]
    assert result["computed_at"] == T0


def test_read_reports_none_stored_for_a_version_with_nothing(clean_db):
    set_mirror_version(clean_db, T0)

    result = type_figures.figures_for(clean_db, DRIVEWAY)

    assert result["available"] is False
    assert result["canonical_type"] == DRIVEWAY
    assert set(result["mirror_version"]) == set(_LAYERS)
    assert "no figures stored" in result["reason"]


def test_read_does_not_answer_for_a_different_version(clean_db):
    """A row retained under an earlier version does not silently answer for the
    mirror's current one -- reported as absent, not stale-but-served."""
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)
    type_figures.compute_and_store(clean_db, types=[DRIVEWAY], now=T0,
                                   parameters=FAST, log=lambda m: None)

    set_mirror_version(clean_db, T1)
    result = type_figures.figures_for(clean_db, DRIVEWAY, parameters=FAST)

    assert result["available"] is False


def test_read_does_not_answer_for_different_parameters(clean_db):
    seed_driveway(clean_db)
    set_mirror_version(clean_db, T0)
    type_figures.compute_and_store(clean_db, types=[DRIVEWAY], now=T0,
                                   parameters=FAST, log=lambda m: None)

    result = type_figures.figures_for(
        clean_db, DRIVEWAY, parameters={"bootstrap_iterations": 999},
    )

    assert result["available"] is False
