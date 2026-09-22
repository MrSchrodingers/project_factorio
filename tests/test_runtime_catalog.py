from factorio_ai_lab.planning.runtime_catalog import (
    RuntimeFactorioCatalog,
)

PAYLOAD = {
    "connected": True,
    "factorio_version": "2.0.73",
    "recipes": [
        {
            "name": "iron-gear-wheel",
            "energy": 0.5,
            "categories": ["crafting"],
            "ingredients": [{"name": "iron-plate", "type": "item", "amount": 2}],
            "products": [{"name": "iron-gear-wheel", "type": "item", "amount": 1}],
            "enabled": True,
        },
        {
            "name": "automation-science-pack",
            "energy": 5.0,
            "categories": ["crafting"],
            "ingredients": [
                {"name": "iron-gear-wheel", "type": "item", "amount": 1},
                {"name": "copper-plate", "type": "item", "amount": 1},
            ],
            "products": [
                {"name": "automation-science-pack", "type": "item", "amount": 1}
            ],
            "enabled": True,
        },
    ],
    "technologies": [
        {
            "name": "automation",
            "unlocks": ["assembling-machine-1"],
            "researched": False,
        }
    ],
    "machines": [{"name": "assembling-machine-1"}],
}


def test_runtime_catalog_builds_recipe_dag():
    catalog = RuntimeFactorioCatalog(PAYLOAD)
    dag = catalog.planner().plan("automation-science-pack", 0.2)
    assert dag.target_item == "automation-science-pack"
    assert dag.raw_requirements_per_s["iron-plate"] == 0.4
    assert dag.raw_requirements_per_s["copper-plate"] == 0.2


def test_runtime_catalog_dependency_graph_marks_raw_inputs():
    catalog = RuntimeFactorioCatalog(PAYLOAD)
    graph = catalog.dependency_subgraph("automation-science-pack")
    by_id = {node["id"]: node for node in graph["nodes"]}
    assert by_id["iron-plate"]["kind"] == "raw_or_unresolved"
    assert by_id["iron-gear-wheel"]["kind"] == "recipe"
    assert any(
        edge == {
            "source": "iron-gear-wheel",
            "target": "automation-science-pack",
            "relation": "ingredient",
        }
        for edge in graph["edges"]
    )


def test_runtime_catalog_summary_counts_game_facts():
    summary = RuntimeFactorioCatalog(PAYLOAD).summary()
    assert summary["recipe_count"] == 2
    assert summary["technology_count"] == 1
    assert summary["machine_count"] == 1
