"""The mirror's scope: three layers in full, and 311 call details deliberately out."""

import pathlib

from mirror import source

MIRROR_DIR = pathlib.Path(source.__file__).parent


def test_exactly_three_layers_are_mirrored():
    assert set(source.LAYERS) == {"service_requests", "custom_fields", "census_areas"}


def test_each_layer_is_mirrored_in_full():
    # No layer carries a filter: the mirror holds the whole layer, not a slice of it.
    for layer in source.LAYERS.values():
        assert layer.page_size > 0
        assert "Call_Details" not in layer.path


def test_311_call_details_is_excluded_with_a_reason():
    reason = source.EXCLUDED_LAYERS["311_Call_Details"]
    assert "M10" in reason
    assert "join" in reason


def test_311_call_details_appears_nowhere_but_the_exclusion_record():
    """A reader grepping the mirror for the dataset finds the decision, not a use."""
    for path in sorted(MIRROR_DIR.glob("*.py")) + [MIRROR_DIR / "schema.sql"]:
        text = path.read_text()
        if "311_Call_Details" not in text:
            continue
        assert path.name == "source.py"
        # Only in the docstring and in EXCLUDED_LAYERS, never in LAYERS.
        assert text.count("311_Call_Details") == 2


def test_the_seven_parking_fields_are_named_once():
    assert source.PARKING_FIELDS == (
        "Alleged Violation",
        "Property Ownership",
        "Vehicle Was Towed",
        "Vehicle Make",
        "Vehicle Model",
        "Vehicle Colour",
        "Vehicle Province",
    )
