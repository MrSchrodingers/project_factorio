from __future__ import annotations

from factorio_ai_lab.cortex.spatial_validation import (
    VALIDATOR_VERSION,
    validate_observed_route,
)


def _snapshot(*, water_runs=None, natural=None):
    return {
        "connected": True,
        "bounds": {
            "left_top": {"x": 0, "y": 0},
            "right_bottom": {"x": 5, "y": 3},
        },
        "terrain_runs": list(water_runs or []),
        "natural": list(natural or []),
    }


def test_observed_astar_routes_around_water_natural_and_entities() -> None:
    snapshot=_snapshot(
        water_runs=[{"name":"water","y":1,"x1":2,"x2":2}],
        natural=[
            {
                "name":"tree-01",
                "position":{"x":1.5,"y":0.5},
            }
        ],
    )
    world=[
        {
            "name":"wooden-chest",
            "position":{"x":3.5,"y":0.5},
            "direction":0,
        }
    ]

    result=validate_observed_route(
        start=(0.5,1.5),
        goal=(4.5,1.5),
        map_snapshot=snapshot,
        world_entities=world,
    )

    assert result is not None
    payload=result.to_dict()
    assert payload["validator"]==VALIDATOR_VERSION
    assert payload["path_length_tiles"]>4
    assert payload["water_blocked"]==1
    assert payload["natural_blocked"]==1
    assert payload["entity_blocked"]==1


def test_observed_astar_refuses_complete_water_barrier() -> None:
    snapshot=_snapshot(
        water_runs=[
            {"name":"deepwater","y":0,"x1":2,"x2":2},
            {"name":"deepwater","y":1,"x1":2,"x2":2},
            {"name":"deepwater","y":2,"x1":2,"x2":2},
        ]
    )

    result=validate_observed_route(
        start=(0.5,1.5),
        goal=(4.5,1.5),
        map_snapshot=snapshot,
        world_entities=[],
    )

    assert result is None


def test_observed_astar_rejects_goal_outside_observed_bounds() -> None:
    snapshot=_snapshot()

    try:
        validate_observed_route(
            start=(0.5,1.5),
            goal=(8.5,1.5),
            map_snapshot=snapshot,
            world_entities=[],
        )
    except ValueError as exc:
        assert "outside observed bounds" in str(exc)
    else:
        raise AssertionError("goal outside observed bounds must fail closed")
