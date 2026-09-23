"""Tests for the runtime prototype catalog.

The last block reads ``runs/game_knowledge_graph.json``. That file is written
by a live run, so its ``enabled`` and ``researched`` fields move between runs.
Those tests therefore assert recipe topology, which does not move, and derive
every numeric expectation from the payload they just read.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    PROBE_UNKNOWN,
    RuntimeFactorioCatalog,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
KNOWLEDGE_GRAPH = REPO_ROOT / "runs" / "game_knowledge_graph.json"

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


# ---------------------------------------------------------------------------
# Every product of a recipe, not just the one that was asked for
# ---------------------------------------------------------------------------

OIL_PAYLOAD = {
    "connected": True,
    "factorio_version": "2.0.73",
    "recipes": [
        {
            "name": "advanced-oil-processing",
            "energy": 5,
            "categories": ["oil-processing"],
            "ingredients": [
                {"name": "water", "type": "fluid", "amount": 50},
                {"name": "crude-oil", "type": "fluid", "amount": 100},
            ],
            "products": [
                {"name": "heavy-oil", "type": "fluid", "amount": 25},
                {"name": "light-oil", "type": "fluid", "amount": 45},
                {"name": "petroleum-gas", "type": "fluid", "amount": 55},
            ],
            "enabled": False,
        },
        {
            "name": "basic-oil-processing",
            "energy": 5,
            "categories": ["oil-processing"],
            "ingredients": [{"name": "crude-oil", "type": "fluid", "amount": 100}],
            "products": [
                {"name": "petroleum-gas", "type": "fluid", "amount": 45}
            ],
            "enabled": False,
        },
    ],
}


def test_multi_product_recipe_records_every_product_with_its_amount():
    catalog = RuntimeFactorioCatalog(OIL_PAYLOAD)
    choice = catalog.recipe_choice("heavy-oil")
    assert choice is not None
    assert choice.recipe_name == "advanced-oil-processing"
    assert {product.item: product.count for product in choice.products} == {
        "heavy-oil": 25.0,
        "light-oil": 45.0,
        "petroleum-gas": 55.0,
    }


def test_byproducts_exclude_the_product_that_was_asked_for():
    catalog = RuntimeFactorioCatalog(OIL_PAYLOAD)
    choice = catalog.recipe_choice("light-oil")
    assert choice is not None
    assert {product.item: product.count for product in choice.byproducts} == {
        "heavy-oil": 25.0,
        "petroleum-gas": 55.0,
    }
    assert choice.product_amount == 45.0


def test_single_product_recipe_lists_exactly_its_own_product():
    catalog = RuntimeFactorioCatalog(OIL_PAYLOAD)
    choice = catalog.recipe_choice("petroleum-gas")
    assert choice is not None
    # The one-ingredient recipe still wins; what changes is that the chosen
    # recipe now carries the full product list instead of a single amount.
    assert choice.recipe_name == "basic-oil-processing"
    assert [(product.item, product.count) for product in choice.products] == [
        ("petroleum-gas", 45.0)
    ]
    assert choice.byproducts == ()


def test_recipe_choice_keeps_the_fields_the_dashboard_reads():
    catalog = RuntimeFactorioCatalog(PAYLOAD)
    choice = catalog.recipe_choice("iron-gear-wheel")
    assert choice is not None
    assert choice.recipe_name == "iron-gear-wheel"
    assert choice.product_name == "iron-gear-wheel"
    assert choice.product_amount == 1.0
    assert choice.enabled is True
    assert choice.spec.item == "iron-gear-wheel"


def test_dependency_subgraph_reports_the_products_of_each_recipe():
    catalog = RuntimeFactorioCatalog(OIL_PAYLOAD)
    graph = catalog.dependency_subgraph("heavy-oil")
    node = next(entry for entry in graph["nodes"] if entry["id"] == "heavy-oil")
    assert node["products"] == [
        {"item": "heavy-oil", "count": 25.0},
        {"item": "light-oil", "count": 45.0},
        {"item": "petroleum-gas", "count": 55.0},
    ]


# ---------------------------------------------------------------------------
# Crafting time carries the status that qualifies it
# ---------------------------------------------------------------------------


def _recipe_row(name, **overrides):
    row = {
        "name": name,
        "categories": ["crafting"],
        "ingredients": [{"name": "iron-plate", "type": "item", "amount": 1}],
        "products": [{"name": name, "type": "item", "amount": 1}],
        "enabled": True,
    }
    row.update(overrides)
    return row


def test_measured_energy_becomes_the_crafting_time():
    catalog = RuntimeFactorioCatalog(
        {"recipes": [_recipe_row("widget", energy=3.2)]}
    )
    spec = catalog.recipe_provider("widget")
    assert spec is not None
    assert spec.crafting_time_s == 3.2
    assert spec.crafting_time_status == PROBE_MEASURED
    assert spec.crafting_time_measured is True


def test_recipe_without_energy_has_no_crafting_time_instead_of_half_a_second():
    catalog = RuntimeFactorioCatalog({"recipes": [_recipe_row("timeless-widget")]})
    spec = catalog.recipe_provider("timeless-widget")
    assert spec is not None
    assert spec.crafting_time_s is None
    assert spec.crafting_time_status == PROBE_UNKNOWN
    assert spec.crafting_time_measured is False


def test_zero_energy_is_not_a_crafting_time():
    catalog = RuntimeFactorioCatalog(
        {"recipes": [_recipe_row("instant-widget", energy=0)]}
    )
    spec = catalog.recipe_provider("instant-widget")
    assert spec is not None
    assert spec.crafting_time_s is None
    assert spec.crafting_time_status == PROBE_UNKNOWN


def test_a_declared_absent_energy_stays_absent():
    catalog = RuntimeFactorioCatalog(
        {
            "recipes": [
                _recipe_row("fieldless-widget", energy_status=PROBE_ABSENT)
            ]
        }
    )
    spec = catalog.recipe_provider("fieldless-widget")
    assert spec is not None
    assert spec.crafting_time_s is None
    assert spec.crafting_time_status == PROBE_ABSENT


def test_dependency_subgraph_qualifies_the_crafting_time_it_reports():
    catalog = RuntimeFactorioCatalog(
        {
            "recipes": [
                _recipe_row("timed-widget", energy=1.5),
                _recipe_row("timeless-widget"),
            ]
        }
    )
    by_id = {
        node["id"]: node
        for node in catalog.dependency_subgraph("timed-widget")["nodes"]
    }
    assert by_id["timed-widget"]["crafting_time_s"] == 1.5
    assert by_id["timed-widget"]["crafting_time_status"] == PROBE_MEASURED
    by_id = {
        node["id"]: node
        for node in catalog.dependency_subgraph("timeless-widget")["nodes"]
    }
    assert by_id["timeless-widget"]["crafting_time_s"] is None
    assert by_id["timeless-widget"]["crafting_time_status"] == PROBE_UNKNOWN


def test_summary_counts_measured_crafting_times():
    catalog = RuntimeFactorioCatalog(
        {
            "recipes": [
                _recipe_row("timed-widget", energy=1.5),
                _recipe_row("timeless-widget"),
            ]
        }
    )
    assert catalog.summary()["measured_crafting_time_count"] == 1


# ---------------------------------------------------------------------------
# The real catalog on disk
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_payload() -> dict:
    if not KNOWLEDGE_GRAPH.exists():
        pytest.skip("runs/game_knowledge_graph.json is not present")
    try:
        return json.loads(KNOWLEDGE_GRAPH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:  # pragma: no cover - live file, partial write
        pytest.skip("runs/game_knowledge_graph.json was mid-write")


def test_real_catalog_keeps_every_product_of_an_oil_recipe(real_payload) -> None:
    catalog = RuntimeFactorioCatalog(real_payload)
    # Recipe topology, not the live ``enabled`` flags: advanced oil processing
    # yields three fluids per execution in every base-game payload.
    choice = next(
        entry
        for entry in catalog.recipe_choices("heavy-oil")
        if entry.recipe_name == "advanced-oil-processing"
    )
    assert {product.item for product in choice.products} == {
        "heavy-oil",
        "light-oil",
        "petroleum-gas",
    }
    row = next(
        entry
        for entry in real_payload["recipes"]
        if entry["name"] == choice.recipe_name
    )
    assert {product.item: product.count for product in choice.products} == {
        str(entry["name"]): float(entry["amount"]) for entry in row["products"]
    }
    assert choice.product_name == "heavy-oil"
    assert {product.item for product in choice.byproducts} == {
        "light-oil",
        "petroleum-gas",
    }


def test_real_catalog_lists_every_recipe_that_yields_a_product(
    real_payload,
) -> None:
    catalog = RuntimeFactorioCatalog(real_payload)
    expected = {
        str(row["name"])
        for row in real_payload["recipes"]
        for product in (row.get("products") or [])
        if str(product.get("name")) == "petroleum-gas"
    }
    assert expected  # the base game has more than one way to make it
    assert {
        choice.recipe_name for choice in catalog.recipe_choices("petroleum-gas")
    } == expected
    assert catalog.recipe_choice("petroleum-gas").recipe_name in expected


def test_real_catalog_time_status_matches_the_rows_it_came_from(
    real_payload,
) -> None:
    catalog = RuntimeFactorioCatalog(real_payload)
    measured_rows = 0
    for row in real_payload["recipes"]:
        products = row.get("products") or []
        energy = row.get("energy")
        usable = isinstance(energy, (int, float)) and not isinstance(
            energy, bool
        )
        usable = usable and float(energy) > 0.0
        if usable:
            measured_rows += 1
        for product in products:
            choice = catalog.recipe_choice(str(product["name"]))
            assert choice is not None
            if choice.recipe_name != row["name"]:
                continue
            spec = choice.spec
            if usable:
                assert spec.crafting_time_s == float(energy)
                assert spec.crafting_time_status == PROBE_MEASURED
            else:
                assert spec.crafting_time_s is None
                assert spec.crafting_time_status != PROBE_MEASURED
    assert catalog.summary()["measured_crafting_time_count"] == measured_rows


def test_a_real_row_stripped_of_energy_reports_absence_not_a_default(
    real_payload,
) -> None:
    row = next(
        entry
        for entry in real_payload["recipes"]
        if isinstance(entry.get("energy"), (int, float))
        and float(entry["energy"]) > 0.0
        and (entry.get("products") or [])
    )
    stripped = {key: value for key, value in row.items() if key != "energy"}
    product = str(stripped["products"][0]["name"])
    spec = RuntimeFactorioCatalog({"recipes": [stripped]}).recipe_provider(product)
    assert spec is not None
    assert spec.crafting_time_s is None
    assert spec.crafting_time_s != 0.5
    assert spec.crafting_time_status == PROBE_UNKNOWN
