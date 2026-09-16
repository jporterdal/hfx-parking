"""Census containment, including interior holes (task 4.5).

Two halves. `test_synthetic_*` builds small hand-drawn polygons -- a square with a
square hole and a smaller island inside that hole, and a block made of two disjoint
squares -- so the even-odd ray cast in `hotspots.in_block`/`find_block` can be
checked against geometry simple enough to reason about by hand, independent of any
real block shape. `test_six_addresses_*` replays the authoritative check `docs/
parking-hotspots/data-sources.md` records ("Verified against the service's own
esriSpatialRelIntersects query on six addresses: 6 of 6 matched") against a frozen
fixture, so the suite proves agreement with the live spatial service without
reaching it.

The archived `add-parking-hotspot-map` change did not record which six addresses
were originally used -- neither its `design.md` nor `data-sources.md` names them,
and no script survives in git history that ran the check. So this file's six were
freshly chosen from the real Driveway call set (queried live against the mirror and
the ArcGIS service on 2026-09-16, see `tests/fixtures/census_six_addresses.json`'s
`_comment`): the two highest-volume watchlist doorways, the lowest-volume one, two
addresses picked for geographic spread (a different district; east of the harbour),
and one picked specifically because its median coordinate sits close to its block's
boundary (~0.5 m, the closest of a 600-address sample). All six matched the live
service's own `esriSpatialRelIntersects` answer, 6 of 6, reproducing the original
verification's result on a fresh, disclosed sample.

Whether any interior-hole polygon exists in the real census layer, and whether any
of the six addresses sit in or near one, is `test_real_layer_hole_report` below --
it is a report, not a correctness assertion, since it depends on which addresses
happened to be chosen.
"""

import json
import pathlib

import hotspots

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_six_addresses_fixture():
    return json.loads((FIXTURES / "census_six_addresses.json").read_text())


# ------------------------------------------------------- synthetic holes (4.5a)


def square(x0, y0, x1, y1):
    """A closed ring, ArcGIS's `rings` shape: a plain list of [x, y] pairs."""
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def block_from_rings(block_id, rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return {
        "id": block_id,
        "dwellings": 1,
        "people": 1,
        "rings": rings,
        "box": (min(xs), min(ys), max(xs), max(ys)),
    }


# Outer landmass 0..10, a lake-shaped hole 3..7, and an island 4.5..5.5 inside the
# lake. Ring winding order is deliberately not matched to the outer/hole convention
# ArcGIS itself uses (outer clockwise, hole counter-clockwise): the even-odd rule
# `in_block` implements does not depend on winding, only on how many ring
# boundaries a ray crosses, and this fixture exists partly to prove that.
DONUT_BLOCK = block_from_rings(
    "donut",
    [
        square(0, 0, 10, 10),      # outer boundary
        square(3, 3, 7, 7),        # hole (a lake)
        square(4.5, 4.5, 5.5, 5.5),  # island inside the hole
    ],
)


def test_point_in_outer_landmass_is_inside():
    assert hotspots.in_block(1, 1, DONUT_BLOCK) is True
    assert hotspots.in_block(9, 9, DONUT_BLOCK) is True  # inside outer, nowhere near the hole


def test_point_in_the_hole_is_outside():
    # Inside the hole's box (3..7) but outside the island's box (4.5..5.5): open water.
    assert hotspots.in_block(3.5, 3.5, DONUT_BLOCK) is False
    assert hotspots.in_block(6.5, 3.5, DONUT_BLOCK) is False


def test_point_on_the_island_inside_the_hole_is_inside():
    # An island in the middle of a lake in the middle of the landmass is solid
    # ground again -- a second parity flip, not treated as still "in the hole".
    assert hotspots.in_block(5, 5, DONUT_BLOCK) is True


def test_points_adjacent_to_each_boundary_land_on_the_correct_side():
    # Just inside the outer edge: still land.
    assert hotspots.in_block(0.001, 5, DONUT_BLOCK) is True
    # Just outside the outer edge: not this block (also exercises the bbox reject).
    assert hotspots.in_block(-0.001, 5, DONUT_BLOCK) is False
    # Just outside the hole (still landmass) vs. just inside it (open water).
    assert hotspots.in_block(2.999, 5, DONUT_BLOCK) is True
    assert hotspots.in_block(3.001, 5, DONUT_BLOCK) is False
    # Just outside the island (open water) vs. just inside it (solid ground).
    assert hotspots.in_block(4.499, 5, DONUT_BLOCK) is False
    assert hotspots.in_block(4.501, 5, DONUT_BLOCK) is True


def test_bbox_rejects_before_any_ring_walk():
    # Far outside the block's own bounding box -- the cheap reject `in_block`
    # takes before walking a single ring.
    assert hotspots.in_block(1000, 1000, DONUT_BLOCK) is False


# A block made of two disjoint squares (an archipelago, not a hole): both rings
# wind the same way and neither nests inside the other. A DA can be multi-part
# without either part being a hole, and the combined bounding box spans the gap
# between them -- so a point that falls in that gap must still test as outside,
# proving the bbox prefilter alone is not what decides containment.
ARCHIPELAGO_BLOCK = block_from_rings(
    "archipelago",
    [square(0, 0, 4, 4), square(10, 10, 14, 14)],
)


def test_multi_ring_block_each_island_is_inside():
    assert hotspots.in_block(2, 2, ARCHIPELAGO_BLOCK) is True
    assert hotspots.in_block(12, 12, ARCHIPELAGO_BLOCK) is True


def test_multi_ring_block_the_gap_between_islands_is_outside():
    # (6, 6) sits inside the combined bounding box (0,0)-(14,14) but inside
    # neither square -- the bbox prefilter passes it through, and only the ring
    # walk rejects it.
    assert hotspots.in_block(6, 6, ARCHIPELAGO_BLOCK) is False


def test_find_block_returns_none_when_no_block_contains_the_point():
    # (3.5, 3.5) sits in the donut's hole -- checked against the donut alone, since
    # it also happens to fall inside the archipelago's first island square, which
    # would confound this specific assertion rather than test it.
    assert hotspots.find_block(3.5, 3.5, [DONUT_BLOCK]) is None
    assert hotspots.find_block(1000, 1000, [DONUT_BLOCK, ARCHIPELAGO_BLOCK]) is None


def test_find_block_picks_the_containing_block_among_several():
    blocks = [ARCHIPELAGO_BLOCK, DONUT_BLOCK]
    found = hotspots.find_block(5, 5, blocks)  # only the donut's island contains this
    assert found is not None
    assert found["id"] == "donut"


# ------------------------------------------------- six-address authoritative check (4.5b)


def test_six_addresses_match_the_live_service_answer():
    """Reproduces `data-sources.md`'s "6 of 6 matched" against a frozen fixture.

    The fixture holds, for each address, the DAUID the live ArcGIS service's own
    `esriSpatialRelIntersects` query returned on 2026-09-16, and the six blocks (of
    610 in the mirror) those DAUIDs name -- trimmed to just the ones these points
    need, in exactly the shape `mirror.derive.census_blocks()` produces. Running
    `hotspots.find_block` over that frozen set and comparing to the frozen live
    answer is the whole check; no network call is made.
    """
    fixture = load_six_addresses_fixture()
    blocks = fixture["blocks"]
    matched = 0
    for case in fixture["addresses"]:
        found = hotspots.find_block(case["lon"], case["lat"], blocks)
        assert found is not None, f"{case['address']} matched no block at all"
        assert found["id"] == case["expected_dauid"], (
            f"{case['address']}: find_block said {found['id']!r}, "
            f"live service said {case['expected_dauid']!r}"
        )
        matched += 1
    assert matched == 6


def test_real_layer_hole_report(capsys):
    """Not a correctness assertion about the six addresses -- a recorded finding.

    A scan of the full 610-polygon mirror (2026-09-16) found exactly two
    dissemination areas with a genuine interior hole (an inner ring of opposite
    winding whose own vertices sit inside an outer ring of the same block, checked
    by point-in-ring rather than by bounding-box overlap alone): DAUID 12090924
    (a large, sparse eastern-HRM area) and DAUID 12091000 (a smaller area near
    Hammonds Plains Rd). Neither is among the six DAUIDs this fixture's addresses
    resolve to (12090357, 12090854, 12090264, 12090872, 12090139, 12090172), and a
    check of every Driveway address within padded reach of 12091000's bounding box
    found none actually inside that block or its hole -- the nearest real Driveway
    addresses there (10 Nicholas Dr, 2015 Hammonds Plains Rd) belong to neighbouring
    blocks. So none of the six sit in or near a hole; the hole coverage in this file
    is carried entirely by the synthetic `DONUT_BLOCK` tests above.
    """
    print(
        "census containment: 2 of 610 real blocks carry an interior hole "
        "(12090924, 12091000); none of the six authoritative-check addresses "
        "fall in or near either"
    )
