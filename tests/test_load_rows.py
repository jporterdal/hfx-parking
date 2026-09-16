"""Turning source features into mirror rows, against pages recorded from the source."""

import json

from mirror import load


def test_a_service_request_keeps_its_source_identifiers(service_request_features):
    row = load.service_request_row(service_request_features[0])

    assert row[0] == 1           # ObjectId, the mirror's primary key
    assert row[1] == 2381683     # REQUEST_ID, the join key to the custom fields
    assert row[2].isoformat() == "2023-12-07T14:42:02+00:00"
    assert row[7] == "NANTUCKET AVE,  DARTMOUTH"
    assert len(row) == 17


def test_a_request_with_no_closure_date_stores_null(service_request_features):
    feature = {"attributes": dict(service_request_features[0]["attributes"])}
    feature["attributes"]["DATE_CLOSED"] = None

    assert load.service_request_row(feature)[3] is None


def test_custom_fields_are_stored_as_published(custom_field_features):
    row = load.custom_field_row(custom_field_features[0])

    # Key and value, exactly as the layer publishes them. No pivot on ingest.
    assert row == (1, 2031577, 60045, "Outcome", None)


def test_every_custom_field_name_is_kept_not_only_the_parking_seven(
    custom_field_features,
):
    names = {load.custom_field_row(f)[3] for f in custom_field_features}
    from mirror import source

    assert names - set(source.PARKING_FIELDS)


def test_a_census_area_carries_its_rings_and_a_bounding_box(census_features):
    row = load.census_row(census_features[0])

    assert row[1] == "12090103"
    rings = json.loads(row[11])
    assert len(rings) == 1 and len(rings[0]) == 143
    min_lon, min_lat, max_lon, max_lat = row[12:16]
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    assert (min_lon, min_lat, max_lon, max_lat) == (min(xs), min(ys), max(xs), max(ys))


def test_a_census_area_with_no_geometry_still_stores(census_features):
    feature = dict(census_features[0])
    feature.pop("geometry")

    row = load.census_row(feature)

    assert json.loads(row[11]) == []
    assert row[12:16] == (None, None, None, None)
