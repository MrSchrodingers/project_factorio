from __future__ import annotations

import pytest

from factorio_ai_lab.cortex.actions import (
    REFUSAL_AUTHORITY_SHADOW,
    REFUSAL_NO_BINDING,
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
)
from factorio_ai_lab.cortex.executor import (
    LEGACY_SHADOW_BINDINGS,
    ActionBinding,
    UniversalExecutor,
    request_from_repair_action,
)
from factorio_ai_lab.evidence import EvidenceStatus
from factorio_ai_lab.learning.repair_loop import (
    INTENT_PLACE_PROCESSING,
    TOOL_PLACEMENT,
    Prediction,
    RepairAction,
)


def provenance() -> ActionProvenance:
    return ActionProvenance(
        requested_by="cortex-test",
        source_component="tests.test_cortex_actions",
        code_revision="abc123",
        run_id="run-1",
    )


def request(
    family: ActionFamily = ActionFamily.PLACEMENT,
    *,
    intent: str = "place_processing_for_buffered_output",
    preconditions: tuple[ActionCondition, ...] = (),
) -> ActionRequest:
    return ActionRequest(
        action_id="a-1",
        family=family,
        intent=intent,
        provenance=provenance(),
        arguments={"count": 1},
        targets=("producer-1",),
        postconditions=(
            ActionCondition(
                name="producers_reaching_processor",
                operator=ConditionOperator.INCREASE,
            ),
        ),
        preconditions=preconditions,
    )


def test_missing_evidence_reference_requires_reason() -> None:
    with pytest.raises(ValueError):
        EvidenceRef(
            source="graph",
            path="nodes.u1.status",
            status=EvidenceStatus.MISSING,
        )


def test_action_request_serializes_epistemic_and_provenance_contract() -> None:
    evidence = EvidenceRef(
        source="factory_graph",
        path="metrics.physical_processing_coverage",
        status=EvidenceStatus.OBSERVED,
    )
    action = ActionRequest(
        action_id="a-2",
        family=ActionFamily.REBUILD,
        intent="reroute_producer_logistics",
        provenance=provenance(),
        evidence=(evidence,),
        arguments={"producer": "u1"},
        targets=("u1",),
        requires=("material",),
        provides=("machine_output",),
    )

    payload = action.to_dict()

    assert payload["family"] == "rebuild"
    assert payload["provenance"]["code_revision"] == "abc123"
    assert payload["evidence"][0]["status"] == "observed"
    assert payload["arguments"] == {"producer": "u1"}


def test_shadow_facade_covers_all_f2_action_families_without_mutation() -> None:
    executor = UniversalExecutor.shadow_legacy()
    covered = {binding.family for binding in LEGACY_SHADOW_BINDINGS}

    assert {
        ActionFamily.PLACEMENT,
        ActionFamily.DELIVERY,
        ActionFamily.RESUPPLY,
        ActionFamily.REBUILD,
        ActionFamily.CRAFT,
        ActionFamily.RESEARCH,
    }.issubset(covered)

    for family in covered:
        result = executor.execute(
            request(family, intent=f"shadow_{family.value}")
        )
        assert result.status is ActionStatus.SHADOWED
        assert result.authority is ActionAuthority.SHADOW
        assert result.changed_world is False
        assert result.refusal is not None
        assert result.refusal.code == REFUSAL_AUTHORITY_SHADOW


def test_hard_unknown_precondition_blocks_before_shadow_or_execution() -> None:
    executor = UniversalExecutor.shadow_legacy()
    precondition = ActionCondition(
        name="target_tiles_free",
        operator=ConditionOperator.EQUALS,
        state=ConditionState.UNKNOWN,
        expected=True,
        hard=True,
    )

    result = executor.execute(request(preconditions=(precondition,)))

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_PRECONDITION
    assert result.changed_world is False


def test_unbound_family_intent_is_named_refusal() -> None:
    executor = UniversalExecutor(authority=ActionAuthority.SHADOW)

    result = executor.execute(request())

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_NO_BINDING


def test_execute_handler_requires_verified_hard_postconditions() -> None:
    observed = ActionCondition(
        name="output_count",
        operator=ConditionOperator.INCREASE,
        state=ConditionState.SATISFIED,
        hard=True,
    )

    def handler(action: ActionRequest) -> ActionResult:
        return ActionResult(
            action_id=action.action_id,
            family=action.family,
            intent=action.intent,
            status=ActionStatus.ACCEPTED,
            authority=ActionAuthority.EXECUTE,
            changed_world=True,
            binding="test.handler",
            postconditions=(observed,),
        )

    executor = UniversalExecutor(
        authority=ActionAuthority.EXECUTE,
        bindings=(
            ActionBinding(
                family=ActionFamily.PLACEMENT,
                intent="place_processing_for_buffered_output",
                name="test.handler",
                source_component="test",
                handler=handler,
            ),
        ),
    )

    result = executor.execute(request())

    assert result.status is ActionStatus.ACCEPTED
    assert result.changed_world is True


def test_accepted_result_rejects_unknown_hard_postcondition() -> None:
    with pytest.raises(ValueError, match="satisfied hard postconditions"):
        ActionResult(
            action_id="a-3",
            family=ActionFamily.PLACEMENT,
            intent="place",
            status=ActionStatus.ACCEPTED,
            authority=ActionAuthority.EXECUTE,
            changed_world=True,
            postconditions=(
                ActionCondition(
                    name="entity_exists",
                    operator=ConditionOperator.EXISTS,
                    state=ConditionState.UNKNOWN,
                ),
            ),
        )


def test_repair_action_lifts_structural_baseline_gap_into_universal_request() -> None:
    repair = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        prediction=Prediction(
            "physical_factory_graph.producers_reaching_processor",
            "increase",
        ),
        provides=("material",),
        targets=("u836", "u896"),
        arguments={"producers": ["u836", "u896"]},
    )

    action = request_from_repair_action(
        repair,
        action_id="repair-1",
        provenance=provenance(),
    )
    result = UniversalExecutor.shadow_legacy().execute(action)

    assert action.family is ActionFamily.PLACEMENT
    assert action.intent == INTENT_PLACE_PROCESSING
    assert action.targets == ("u836", "u896")
    assert action.postconditions[0].operator is ConditionOperator.INCREASE
    assert result.status is ActionStatus.SHADOWED
    assert result.changed_world is False
