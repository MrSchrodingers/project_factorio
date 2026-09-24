"""Read-only runtime instrumentation shared by planners and Cortex.

Adapters in this module may depend on the canonical dashboard RCON commands,
but they return plain measured facts and never mutate the Factorio world.
"""

from __future__ import annotations

import json
from typing import Any

from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.planning.footprints import prototype_footprints


def runtime_entity_footprints(instance: Any) -> dict[str, tuple[int, int]]:
    """Read placeable-entity tile footprints from the live runtime.

    Best effort by design. A failed or malformed runtime response returns an
    empty mapping; callers then use snapshot/static fallback semantics from
    factorio_ai_lab.planning.footprints.
    """

    try:
        raw = instance.rcon_client.send_command(
            FactorioObserver._ENTITY_PROTOTYPE_COMMAND
        )
        if not raw:
            return {}
        return prototype_footprints(json.loads(raw))
    except (AttributeError, OSError, TypeError, ValueError):
        return {}
