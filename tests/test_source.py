"""Offset paging, retry and the query each layer asks for."""

import datetime
import json
import urllib.request

import pytest

from mirror import source


class FakeService:
    """Serves pages out of a row list the way ArcGIS does, short page last."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def post(self, url, body, **kwargs):
        self.calls.append(body)
        if body.get("returnCountOnly") == "true":
            return {"count": len(self.rows)}
        offset = int(body["resultOffset"])
        size = int(body["resultRecordCount"])
        return {"features": self.rows[offset : offset + size]}


def rows(n, start=1):
    return [{"attributes": {"ObjectId": i}} for i in range(start, start + n)]


def test_pages_walks_to_exhaustion(monkeypatch):
    fake = FakeService(rows(2500))
    monkeypatch.setattr(source, "post", fake.post)
    layer = source.LAYERS["service_requests"]

    seen = list(source.pages(layer, page_size=1000))

    assert [offset for offset, _ in seen] == [0, 1000, 2000]
    assert [len(f) for _, f in seen] == [1000, 1000, 500]


def test_an_exact_multiple_ends_on_an_empty_page(monkeypatch):
    fake = FakeService(rows(2000))
    monkeypatch.setattr(source, "post", fake.post)

    seen = list(source.pages(source.LAYERS["service_requests"], page_size=1000))

    assert [len(f) for _, f in seen] == [1000, 1000, 0]


def test_pages_resume_from_an_offset(monkeypatch):
    fake = FakeService(rows(2500))
    monkeypatch.setattr(source, "post", fake.post)

    seen = list(source.pages(source.LAYERS["service_requests"], 2000, page_size=1000))

    assert [offset for offset, _ in seen] == [2000]
    assert len(seen[0][1]) == 500


def test_paging_orders_by_object_id_so_offsets_are_stable(monkeypatch):
    fake = FakeService(rows(10))
    monkeypatch.setattr(source, "post", fake.post)

    list(source.pages(source.LAYERS["custom_fields"], page_size=1000))

    assert fake.calls[0]["orderByFields"] == "ObjectId"
    assert fake.calls[0]["returnGeometry"] == "false"


def test_the_census_layer_asks_for_geometry_in_wgs84(monkeypatch):
    fake = FakeService(rows(10))
    monkeypatch.setattr(source, "post", fake.post)
    layer = source.LAYERS["census_areas"]

    source.page(layer, 0)

    body = fake.calls[0]
    assert body["returnGeometry"] == "true"
    assert body["outSR"] == "4326"
    assert body["orderByFields"] == "OBJECTID"
    # Geometry makes a page heavy, so this layer pages smaller than the others.
    assert int(body["resultRecordCount"]) == 200 < source.LAYERS["custom_fields"].page_size


def test_count_uses_the_services_own_count_query(monkeypatch):
    fake = FakeService(rows(478))
    monkeypatch.setattr(source, "post", fake.post)

    assert source.count(source.LAYERS["service_requests"]) == 478
    assert fake.calls[0]["returnCountOnly"] == "true"


def test_post_retries_then_gives_up(monkeypatch):
    attempts = []

    def failing(*args, **kwargs):
        attempts.append(1)
        raise OSError("connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    with pytest.raises(OSError):
        source.post("https://example.invalid", {}, attempts=4, sleep=lambda s: None)
    assert len(attempts) == 4


def test_post_succeeds_after_a_failure(monkeypatch):
    class Response:
        def read(self):
            return json.dumps({"features": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    state = {"n": 0}

    def flaky(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("timed out")
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    assert source.post("https://example.invalid", {}, sleep=lambda s: None) == {
        "features": []
    }


def test_a_service_error_payload_is_raised(monkeypatch):
    monkeypatch.setattr(source, "post", source.post)

    class Response:
        def read(self):
            return json.dumps({"error": {"code": 400}}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(RuntimeError):
        source.post("https://example.invalid", {}, sleep=lambda s: None)


def test_epoch_milliseconds_become_utc():
    when = source.epoch_to_utc(1701960122000)
    assert when == datetime.datetime(2023, 12, 7, 14, 42, 2, tzinfo=datetime.UTC)
    assert source.epoch_to_utc(None) is None
    assert source.epoch_to_utc("") is None


def test_the_suite_cannot_reach_the_network():
    with pytest.raises(AssertionError):
        urllib.request.urlopen("https://services2.arcgis.com")
