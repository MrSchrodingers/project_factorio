"""Parity adapters between typed Cortex actions and legacy repair handlers.

F2-B starts by proving that Cortex can prepare the exact operation the validated
legacy runner would execute.  Preparation is intentionally non-mutating: it
returns code/purpose/measurement contracts or a named legacy refusal.

Only after parity is established will a later checkpoint route a prepared
operation through TransactionalFLEExecutor under EXECUTE authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionFamily,
    ActionRequest,
    Refusal,
)
from factorio_ai_lab.experiments import curriculum_runner as legacy
from factorio_ai_lab.learning.repair_loop import (
    INTENT_ATTACH_TO_LIVE_NETWORK,
    INTENT_EXTEND_POWER_SUPPLY,
    INTENT_INSERT_FUEL,
)


@dataclass(frozen=True)
class PreparedLegacyAction:
    """Exact legacy-compatible operation prepared without executing it."""

    action_id: str
    family: ActionFamily
    intent: str
    binding: str
    legacy_handler: str
    code: str
    purpose: str
    measurement_keys: tuple[str, ...]
    preflight: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "preflight", dict(self.preflight))
        if not self.code.strip():
            raise ValueError("prepared code must be non-empty")
        if self.purpose not in {"operation", "infrastructure"}:
            raise ValueError(f"unknown intervention purpose {self.purpose!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "family": self.family.value,
            "intent": self.intent,
            "binding": self.binding,
            "legacy_handler": self.legacy_handler,
            "purpose": self.purpose,
            "measurement_keys": list(self.measurement_keys),
            "preflight": dict(self.preflight),
            "code": self.code,
        }


@dataclass(frozen=True)
class PreparationResult:
    """Prepared operation or the named reason preparation was impossible."""

    request: ActionRequest
    prepared: PreparedLegacyAction | None = None
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.refusal is None):
            raise ValueError("preparation must contain exactly one of prepared/refusal")

    @property
    def ready(self) -> bool:
        return self.prepared is not None


class LegacyRepairParityAdapter:
    """Compile typed repair requests exactly as the legacy runner does."""

    _POWER_INTENTS = frozenset(
        {INTENT_EXTEND_POWER_SUPPLY, INTENT_ATTACH_TO_LIVE_NETWORK}
    )

    def prepare(
        self,
        request: ActionRequest,
        *,
        observation: Any,
        env: Any,
    ) -> PreparationResult:
        if (
            request.family is ActionFamily.RESUPPLY
            and request.intent == INTENT_INSERT_FUEL
        ):
            return self._prepare_refuel(request, observation=observation, env=env)

        if (
            request.family is ActionFamily.PLACEMENT
            and request.intent in self._POWER_INTENTS
        ):
            return self._prepare_power_tap(request, observation=observation)

        return PreparationResult(
            request=request,
            refusal=Refusal(
                code=legacy.REPAIR_NO_BINDING,
                detail=(
                    "legacy parity adapter has no handler for "
                    f"{request.family.value}:{request.intent}"
                ),
            ),
        )

    @staticmethod
    def _prepare_refuel(
        request: ActionRequest,
        *,
        observation: Any,
        env: Any,
    ) -> PreparationResult:
        targets = [
            row
            for row in legacy.repair_entity_targets(observation, request.targets)
            if row[1] is not None
        ]
        if not targets:
            return PreparationResult(
                request=request,
                refusal=Refusal(
                    code=legacy.REPAIR_NO_TARGET,
                    detail="action named no repairable fuel target",
                ),
            )

        instance = getattr(getattr(env, "unwrapped", env), "instance", None)
        carried = legacy._carried_item_count(instance, legacy.MINING_CELL_FUEL_ITEM)
        sources = legacy.repair_fuel_sources(
            observation,
            instance,
            anchor=(targets[0][2], targets[0][3]),
        )
        if not sources and carried == 0:
            return PreparationResult(
                request=request,
                refusal=Refusal(
                    code=legacy.REPAIR_NO_FUEL,
                    detail="world and agent hold no measured spare fuel",
                    retriable=True,
                ),
            )

        code = legacy.repair_refuel_code(
            sources,
            targets,
            dose=legacy.REPAIR_FUEL_DOSE,
        )
        return PreparationResult(
            request=request,
            prepared=PreparedLegacyAction(
                action_id=request.action_id,
                family=request.family,
                intent=request.intent,
                binding="legacy.repair.insert_fuel",
                legacy_handler="_repair_insert_fuel",
                code=code,
                purpose="operation",
                measurement_keys=("inserted", "drawn", "note"),
                preflight={
                    "targets": len(targets),
                    "sources": len(sources),
                    "carried_coal": carried,
                    "dose": legacy.REPAIR_FUEL_DOSE,
                },
            ),
        )

    @staticmethod
    def _prepare_power_tap(
        request: ActionRequest,
        *,
        observation: Any,
    ) -> PreparationResult:
        targets = legacy.repair_entity_targets(observation, request.targets)
        if not targets:
            return PreparationResult(
                request=request,
                refusal=Refusal(
                    code=legacy.REPAIR_NO_TARGET,
                    detail="action named no power target",
                ),
            )

        code = legacy.repair_power_tap_code(targets)
        return PreparationResult(
            request=request,
            prepared=PreparedLegacyAction(
                action_id=request.action_id,
                family=request.family,
                intent=request.intent,
                binding="legacy.repair.power_tap",
                legacy_handler="_repair_power_tap",
                code=code,
                purpose="infrastructure",
                measurement_keys=("poles", "pole_stock", "note"),
                preflight={"targets": len(targets)},
            ),
        )
