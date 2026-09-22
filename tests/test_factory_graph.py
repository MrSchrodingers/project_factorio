from factorio_ai_lab.learning.factory_graph import build_factory_graph


def entity(name, x, y, *, direction=0, unit=1):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "direction": direction,
        "unit_number": unit,
        "status": "working",
    }


def test_drill_to_chest_is_buffered_but_not_processed():
    graph = build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=8, unit=1),
            entity("wooden-chest", 0, 1.5, unit=2),
        ]
    )
    assert graph["metrics"]["producer_count"] == 1
    assert graph["metrics"]["producers_reaching_buffer"] == 1
    assert graph["metrics"]["producers_reaching_processor"] == 0
    assert graph["metrics"]["physical_processing_coverage"] == 0.0


def test_inserter_connects_belt_to_furnace():
    graph = build_factory_graph(
        [
            entity("transport-belt", -1, 0, direction=4, unit=1),
            entity("inserter", 0, 0, direction=4, unit=2),
            entity("stone-furnace", 1, 0, unit=3),
        ]
    )
    relations = {
        (edge["source"], edge["target"], edge["relation"])
        for edge in graph["edges"]
    }
    assert ("u1", "u2", "pickup") in relations
    assert ("u2", "u3", "drop") in relations


def test_power_graph_links_pole_to_consumer():
    graph = build_factory_graph(
        [
            entity("small-electric-pole", 0, 0, unit=1),
            entity("electric-mining-drill", 2, 0, unit=2),
        ]
    )
    assert any(
        edge["relation"] == "power_supply"
        for edge in graph["edges"]
    )


def test_fluid_graph_detects_pump_to_steam_engine_path():
    graph = build_factory_graph(
        [
            entity("offshore-pump", 0, 0, unit=1),
            entity("pipe", 1, 0, unit=2),
            entity("boiler", 2.5, 0, unit=3),
            entity("pipe", 4, 0, unit=4),
            entity("steam-engine", 7, 0, unit=5),
        ]
    )
    assert graph["metrics"]["steam_path_live"] is True
    assert graph["metrics"]["fluid_edge_count"] >= 8
