"""The HRM ArcGIS layers this project mirrors, and how to read them.

Three layers are mirrored, each in full. One is deliberately not: `311_Call_Details`
carries no address, no coordinates and no request identifier, so it joins to nothing
the product reads, and at 4,968,536 rows it is three times everything else combined.
See design.md M10. `EXCLUDED_LAYERS` records the exclusion so it reads as a decision
rather than an oversight.

Rows are retrieved by offset paging, not by identifier chunk. A page of 1,000 rows
costs about half a second; the identifier-chunk queries in `src/hotspots.py` cost
about six seconds a chunk for the same rows.
"""

import copy
import datetime
import json
import time
import urllib.parse
import urllib.request

BASE = "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services"

EXCLUDED_LAYERS = {
    "311_Call_Details": (
        "design.md M10: 4,968,536 rows and ~1.2 GB, with no address, no coordinates "
        "and no request identifier, so no per-record join to a service request exists. "
        "Mirroring a dataset is not the same as un-rejecting it."
    ),
}


class Layer:
    """One mirrored feature layer and everything the loader needs to know about it."""

    def __init__(self, key, path, table, oid_field, page_size, geometry=False,
                 static=False, id_field=None):
        self.key = key
        self.path = path
        self.table = table
        # The mirrored layer this one writes for. A staging variant carries the same
        # base key as the layer it is replacing, so the row builders and the sample
        # rules are found by what the rows *are*, not by which table holds them.
        self.base_key = key
        # The object-id field is per layer and the two spellings are not the same:
        # the Cityworks layers publish `ObjectId`, the census layer `OBJECTID`. It is
        # the primary key the mirror stores rows under; it is *not* a record identity
        # that survives a publish, and nothing pages or filters on it (design.md M2).
        self.oid_field = oid_field
        self.page_size = page_size
        self.geometry = geometry
        # A static layer is not re-pulled because a schedule fired; only an advance
        # of its published last-edit timestamp pulls it. See design.md M3.
        self.static = static
        # The source's name for the request identifier: the business key whose
        # stability across publishes design.md M2a leaves open.
        self.id_field = id_field

    def staging(self):
        """The same layer, writing to its staging table.

        A reload fills this and swaps it in wholesale, so the live table holds one
        published version from the first row to the last. See `sync.full_reload`.
        """
        clone = copy.copy(self)
        clone.key = f"{self.key}__staging"
        clone.table = f"{self.table}__staging"
        clone.base_key = self.base_key
        return clone

    @property
    def url(self):
        return f"{BASE}/{self.path}"

    @property
    def query_url(self):
        return self.url + "/query"

    def __repr__(self):
        return f"Layer({self.key!r})"


LAYERS = {
    "service_requests": Layer(
        key="service_requests",
        path="Cityworks_Service_Requests/FeatureServer/0",
        table="service_requests",
        oid_field="ObjectId",
        page_size=1000,
        id_field="REQUEST_ID",
    ),
    "custom_fields": Layer(
        key="custom_fields",
        path="Cityworks_Service_Requests_Custom_Fields/FeatureServer/0",
        table="custom_fields",
        oid_field="ObjectId",
        page_size=1000,
        id_field="REQUESTID",
    ),
    # Geometry makes the page heavy, so this layer pages at 200 rather than 1,000.
    "census_areas": Layer(
        key="census_areas",
        path="Census_2021_Dissemination_Areas/FeatureServer/0",
        table="census_areas",
        oid_field="OBJECTID",
        page_size=200,
        geometry=True,
        static=True,
    ),
}

CENSUS_FIELDS = (
    "OBJECTID,DAUID,DAPOP2021,DATDWELL20,DAURDWELL2,DAAREA,DAPOPDEN,"
    "DARPLAT,DARPLONG,CSDNAME,CDNAME"
)

# The seven parking attributes the product reads out of the key-value layer. The
# layer publishes 88 field names; these seven are pivoted by a view, never on ingest.
PARKING_FIELDS = (
    "Alleged Violation",
    "Property Ownership",
    "Vehicle Was Towed",
    "Vehicle Make",
    "Vehicle Model",
    "Vehicle Colour",
    "Vehicle Province",
)


def post(url, body, attempts=4, timeout=180, sleep=time.sleep):
    """POST a form-encoded ArcGIS query and return the decoded payload."""
    data = urllib.parse.urlencode(body).encode()
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data)
            payload = json.load(urllib.request.urlopen(req, timeout=timeout))
            break
        except Exception:
            if attempt == attempts - 1:
                raise
            sleep(3)
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload


def count(layer, where="1=1"):
    """The service's own count for a layer, used to check what we stored."""
    payload = post(
        layer.query_url,
        {"where": where, "returnCountOnly": "true", "f": "json"},
    )
    return payload["count"]


def last_edit_date(layer):
    """`editingInfo.lastEditDate` — one small request, no paging."""
    payload = post(layer.url, {"f": "json"})
    editing = payload.get("editingInfo") or {}
    return epoch_to_utc(editing.get("lastEditDate"))


def page(layer, offset, page_size=None, where="1=1"):
    """One page of a layer, ordered by its object id so offsets are stable."""
    size = page_size or layer.page_size
    body = {
        "where": where,
        "outFields": CENSUS_FIELDS if layer.geometry else "*",
        "returnGeometry": "true" if layer.geometry else "false",
        "orderByFields": layer.oid_field,
        "resultOffset": str(offset),
        "resultRecordCount": str(size),
        "f": "json",
    }
    if layer.geometry:
        body["outSR"] = "4326"
        body["geometryPrecision"] = "6"
    return post(layer.query_url, body).get("features", [])


def pages(layer, start_offset=0, page_size=None, where="1=1"):
    """Yield `(offset, features)` from `start_offset` to exhaustion.

    A short page ends the walk, which is how ArcGIS signals the last one. Ordering
    by object id keeps already-read offsets stable while rows are appended at the
    source during a long load.
    """
    size = page_size or layer.page_size
    offset = start_offset
    while True:
        features = page(layer, offset, size, where)
        yield offset, features
        if len(features) < size:
            return
        offset += size


def epoch_to_utc(epoch_ms):
    """ArcGIS publishes epoch milliseconds. Store UTC; convert on the way out."""
    if epoch_ms in (None, ""):
        return None
    return datetime.datetime.fromtimestamp(epoch_ms / 1000, datetime.UTC)
