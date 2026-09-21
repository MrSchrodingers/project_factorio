from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CurriculumStage:
    name: str
    max_recipe_depth: int
    max_area: int
    allow_underground: bool
    allow_splitters: bool
    allow_biters: bool = False


CURRICULUM: tuple[CurriculumStage, ...] = (
    CurriculumStage("route_single_item", 0, 7 * 7, False, False),
    CurriculumStage("route_obstacles", 0, 11 * 11, True, False),
    CurriculumStage("single_recipe", 1, 11 * 11, True, False),
    CurriculumStage("multi_input_recipe", 1, 15 * 15, True, True),
    CurriculumStage("recipe_tree_depth_2", 2, 21 * 21, True, True),
    CurriculumStage("open_factory_no_biters", 4, 41 * 41, True, True),
)
