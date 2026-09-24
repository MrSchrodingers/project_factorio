"""Universal action facade with explicit progressive authority.

F2-A intentionally wires no new live-world authority.  The default facade
knows which legacy subsystem corresponds to each action family and returns
shadow/proposal results without invoking a world-mutating handler.

Future F2 blocks add concrete adapters behind the same interface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from factorio_ai_lab.cortex.actions import (
    REFUSAL_AUTHORITY_PROPOSAL,
    REFUSAL_AUTHORITY_SHADOW,
    REFUSAL_HANDLER_EXCEPTION,
    REFUSAL_NO_BINDING,
    REFUSAL_NO_HANDLER,
    REFUSAL_PRECONDITION,
    ActionAuthority,
    ActionCondition,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ConditionOperator,
    ConditionState,
    EvidenceRef,
    Refusal,
)
from factorio_ai_lab.evidence import EvidenceStatus
from factorio_ai_lab.learning.repair_loop import RepairAction

ActionHandler = Callable[[ActionRequest], ActionResult]


@dataclass(frozen=True)
class ActionBinding:
    """One family/intent adapter exposed to the universal facade."""

    family: ActionFamily
    name: str
    source_component: str
    intent: str = "*"
    mutates_world: bool = True
    handler: ActionHandler | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("binding name must be non-empty")
        if not self.source_component.strip():
            raise ValueError("binding source_component must be non-empty")
        if not self.intent.strip():
            raise ValueError("binding intent must be non-empty")


LEGACY_SHADOW_BINDINGS: tuple[ActionBinding, ...] = (
    ActionBinding(
        family=ActionFamily.PLACEMENT,
        name="legacy.planning.placement",
        source_component="factorio_ai_lab.planning.placement",
    ),
    ActionBinding(
        family=ActionFamily.DELIVERY,
        name="legacy.planning.delivery",
        source_component="factorio_ai_lab.planning.delivery",
    ),
    ActionBinding(
        family=ActionFamily.RESUPPLY,
        name="legacy.planning.resupply",
        source_component="factorio_ai_lab.planning.resupply",
    ),
    ActionBinding(
        family=ActionFamily.REBUILD,
        name="legacy.planning.rebuild",
        source_component="factorio_ai_lab.planning.rebuild",
    ),
    ActionBinding(
        family=ActionFamily.CRAFT,
        name="legacy.fle.craft_item",
        source_component="factorio_ai_lab.experiments.open_play_runner",
    ),
    ActionBinding(
        family=ActionFamily.RESEARCH,
        name="legacy.fle.set_research",
        source_component="factorio_ai_lab.experiments.open_play_runner",
    ),
    ActionBinding(
        family=ActionFamily.DEPENDENCY_PLAN,
        name="legacy.planning.dependency_plan",
        source_component="factorio_ai_lab.planning.dependency_plan",
        mutates_world=False,
    ),
)


class UniversalExecutor:
    """Single authority gate over heterogeneous action implementations."""

    def __init__(
        self,
        *,
        authority: ActionAuthority = ActionAuthority.SHADOW,
        bindings: tuple[ActionBinding, ...] = (),
    ) -> None:
        self.authority = authority
        self._bindings: dict[tuple[ActionFamily, str], ActionBinding] = {}
        for binding in bindings:
            self.register(binding)

    @classmethod
    def shadow_legacy(cls) -> UniversalExecutor:
        """Facade over every legacy F2 family with zero mutation authority."""

        return cls(
            authority=ActionAuthority.SHADOW,
            bindings=LEGACY_SHADOW_BINDINGS,
        )

    def register(self, binding: ActionBinding) -> None:
        key = (binding.family, binding.intent)
        if key in self._bindings:
            raise ValueError(
                f"duplicate action binding for {binding.family.value}:{binding.intent}"
            )
        self._bindings[key] = binding

    def binding_for(self, request: ActionRequest) -> ActionBinding | None:
        return self._bindings.get(
            (request.family, request.intent),
            self._bindings.get((request.family, "*")),
        )

    @staticmethod
    def _refused(
        request: ActionRequest,
        *,
        authority: ActionAuthority,
        code: str,
        detail: str,
        binding: ActionBinding | None = None,
        retriable: bool = False,
    ) -> ActionResult:
        return ActionResult(
            action_id=request.action_id,
            family=request.family,
            intent=request.intent,
            status=ActionStatus.REFUSED,
            authority=authority,
            changed_world=False,
            binding=None if binding is None else binding.name,
            refusal=Refusal(
                code=code,
                detail=detail,
                retriable=retriable,
                evidence=request.evidence,
            ),
            evidence=request.evidence,
            postconditions=request.postconditions,
        )

    def execute(self, request: ActionRequest) -> ActionResult:
        """Apply the configured authority gate to one typed request."""

        binding = self.binding_for(request)
        if binding is None:
            return self._refused(
                request,
                authority=self.authority,
                code=REFUSAL_NO_BINDING,
                detail=(
                    f"no binding declared for "
                    f"{request.family.value}:{request.intent}"
                ),
            )

        blockers = request.blocking_preconditions
        if blockers:
            return self._refused(
                request,
                authority=self.authority,
                code=REFUSAL_PRECONDITION,
                detail=(
                    "hard precondition(s) not satisfied: "
                    + ", ".join(condition.name for condition in blockers)
                ),
                binding=binding,
                retriable=any(
                    condition.state is ConditionState.UNKNOWN for condition in blockers
                ),
            )

        if self.authority is ActionAuthority.SHADOW:
            return ActionResult(
                action_id=request.action_id,
                family=request.family,
                intent=request.intent,
                status=ActionStatus.SHADOWED,
                authority=self.authority,
                changed_world=False,
                binding=binding.name,
                refusal=Refusal(
                    code=REFUSAL_AUTHORITY_SHADOW,
                    detail="shadow authority records the candidate and forbids execution",
                ),
                evidence=request.evidence,
                postconditions=request.postconditions,
                measurements={
                    "source_component": binding.source_component,
                    "would_mutate_world": binding.mutates_world,
                },
            )

        if self.authority is ActionAuthority.PROPOSAL:
            return ActionResult(
                action_id=request.action_id,
                family=request.family,
                intent=request.intent,
                status=ActionStatus.PROPOSED,
                authority=self.authority,
                changed_world=False,
                binding=binding.name,
                refusal=Refusal(
                    code=REFUSAL_AUTHORITY_PROPOSAL,
                    detail="proposal authority may rank/emit an action but cannot execute it",
                ),
                evidence=request.evidence,
                postconditions=request.postconditions,
                measurements={
                    "source_component": binding.source_component,
                    "would_mutate_world": binding.mutates_world,
                },
            )

        if binding.handler is None:
            return self._refused(
                request,
                authority=self.authority,
                code=REFUSAL_NO_HANDLER,
                detail=f"binding {binding.name} has no execute handler",
                binding=binding,
            )

        try:
            result = binding.handler(request)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return self._refused(
                request,
                authority=self.authority,
                code=REFUSAL_HANDLER_EXCEPTION,
                detail=f"{type(exc).__name__}: {exc}",
                binding=binding,
                retriable=True,
            )

        if result.action_id != request.action_id:
            raise ValueError("handler returned result for a different action_id")
        if result.family is not request.family or result.intent != request.intent:
            raise ValueError("handler returned result for a different action")
        return result


def request_from_repair_action(
    action: RepairAction,
    *,
    action_id: str,
    provenance: ActionProvenance,
    evidence: tuple[EvidenceRef, ...] = (),
) -> ActionRequest:
    """Lift the existing repair ontology into the universal action schema."""

    try:
        family = ActionFamily(action.tool)
    except ValueError as exc:
        raise ValueError(f"repair tool {action.tool!r} is not in the Cortex ontology") from exc

    operator = {
        "increase": ConditionOperator.INCREASE,
        "decrease": ConditionOperator.DECREASE,
    }.get(action.prediction.direction)
    if operator is None:
        raise ValueError(
            f"unsupported repair prediction direction {action.prediction.direction!r}"
        )

    prediction_evidence = evidence or (
        EvidenceRef(
            source="repair_loop",
            path=action.prediction.metric,
            status=EvidenceStatus.DERIVED,
            reason="repair action prediction",
        ),
    )
    postcondition = ActionCondition(
        name=action.prediction.metric,
        operator=operator,
        state=ConditionState.UNKNOWN,
        hard=True,
        evidence=prediction_evidence,
    )

    return ActionRequest(
        action_id=action_id,
        family=family,
        intent=action.intent,
        provenance=provenance,
        arguments=dict(action.arguments),
        targets=() if action.targets is None else tuple(action.targets),
        requires=tuple(action.requires),
        provides=tuple(action.provides),
        evidence=evidence,
        postconditions=(postcondition,),
    )
