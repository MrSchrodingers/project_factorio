from __future__ import annotations

from dataclasses import dataclass

from factorio_ai_lab.cortex.actions import ActionProvenance
from factorio_ai_lab.cortex.executor import request_from_repair_action
from factorio_ai_lab.cortex.legacy_parity import LegacyRepairParityAdapter
from factorio_ai_lab.experiments import curriculum_runner as legacy
from factorio_ai_lab.learning.repair_loop import (
    DIRECTION_DECREASE,
    INTENT_EXTEND_POWER_SUPPLY,
    INTENT_INSERT_FUEL,
    TOOL_PLACEMENT,
    TOOL_RESUPPLY,
    Prediction,
    RepairAction,
)


def entity(name: str, x: float, y: float, *, unit: int, status: str) -> dict:
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "direction": 0,
        "unit_number": unit,
        "status": status,
    }


STARVED_WORLD = [
    entity("burner-mining-drill", 10.0, 10.0, unit=1, status="no_fuel"),
    entity("wooden-chest", 10.0, 11.5, unit=2, status="working"),
]
POWERLESS_WORLD = [
    entity("assembling-machine-2", 20.0, 20.0, unit=1, status="no_power"),
    entity("medium-electric-pole", 28.0, 20.0, unit=2, status="working"),
]


class Namespace:
    def __init__(self, world):
        self.world = list(world)

    def _save_entity_state(self, **_kwargs):
        return list(self.world)


class Rcon:
    def __init__(self, answer: str = "200") -> None:
        self.answer = answer

    def send_command(self, _command: str) -> str:
        return self.answer


class Instance:
    def __init__(self, world, *, rcon: Rcon | None = None) -> None:
        self.namespace = Namespace(world)
        self.rcon_client = rcon or Rcon()


class Env:
    def __init__(self, world, *, rcon: Rcon | None = None) -> None:
        self.instance = Instance(world, rcon=rcon)
        self.unwrapped = self


class Journal:
    def __init__(self) -> None:
        self.run_id = "parity-test"
        self.state = {"metrics": {}, "evolution": {"generation": 1}}


@dataclass
class Step:
    info: dict
    candidate_game_state: str | None
    accepted: bool = False


class CaptureExecutor:
    def __init__(self, env: Env, *, accepts: bool = True) -> None:
        self.env = env
        self.accepts = accepts
        self.codes: list[str] = []
        self.purposes: list[str] = []

    def execute(
        self,
        code: str,
        *,
        accept,
        use_checkpoint_for_action: bool = True,
        purpose: str = "operation",
    ) -> Step:
        del use_checkpoint_for_action
        self.codes.append(code)
        self.purposes.append(purpose)
        namespace = self.env.instance.namespace
        if "repair_inserted" in code:
            namespace.repair_inserted = 16.0 if self.accepts else 0.0
            namespace.repair_drawn = 16.0
            namespace.repair_note = ""
        if "repair_poles" in code:
            namespace.repair_poles = 1.0 if self.accepts else 0.0
            namespace.repair_pole_stock = 4.0
            namespace.repair_note = ""
        step = Step(
            info={"error_occurred": False},
            candidate_game_state="state-1",
        )
        step.accepted = bool(accept(step))
        return step


def provenance() -> ActionProvenance:
    return ActionProvenance(
        requested_by="parity-test",
        source_component="tests.test_cortex_legacy_parity",
        code_revision="test-sha",
    )


def repair_request(
    *,
    tool: str,
    intent: str,
    target: str,
) -> tuple[RepairAction, object]:
    action = RepairAction(
        tool=tool,
        intent=intent,
        prediction=Prediction("target_metric", DIRECTION_DECREASE),
        targets=(target,),
    )
    return action, request_from_repair_action(
        action,
        action_id=f"action-{intent}",
        provenance=provenance(),
    )


def test_resupply_preparation_matches_legacy_handler_code_and_purpose() -> None:
    env = Env(STARVED_WORLD)
    observation = legacy.repair_observation(env, Journal())
    action, request = repair_request(
        tool=TOOL_RESUPPLY,
        intent=INTENT_INSERT_FUEL,
        target="u1",
    )

    prepared = LegacyRepairParityAdapter().prepare(
        request,
        observation=observation,
        env=env,
    )
    assert prepared.ready
    assert prepared.prepared is not None

    capture = CaptureExecutor(env)
    changed, refusal, measurements = legacy._repair_insert_fuel(
        action,
        observation=observation,
        executor=capture,
        env=env,
    )

    assert changed is True
    assert refusal is None
    assert measurements["inserted"] == 16.0
    assert capture.codes == [prepared.prepared.code]
    assert capture.purposes == [prepared.prepared.purpose]
    assert prepared.prepared.purpose == "operation"
    assert prepared.prepared.measurement_keys == ("inserted", "drawn", "note")


def test_power_tap_preparation_matches_legacy_handler_code_and_purpose() -> None:
    env = Env(POWERLESS_WORLD)
    observation = legacy.repair_observation(env, Journal())
    action, request = repair_request(
        tool=TOOL_PLACEMENT,
        intent=INTENT_EXTEND_POWER_SUPPLY,
        target="u1",
    )

    prepared = LegacyRepairParityAdapter().prepare(
        request,
        observation=observation,
        env=env,
    )
    assert prepared.ready
    assert prepared.prepared is not None

    capture = CaptureExecutor(env)
    changed, refusal, measurements = legacy._repair_power_tap(
        action,
        observation=observation,
        executor=capture,
        env=env,
    )

    assert changed is True
    assert refusal is None
    assert measurements["poles"] == 1.0
    assert capture.codes == [prepared.prepared.code]
    assert capture.purposes == [prepared.prepared.purpose]
    assert prepared.prepared.purpose == "infrastructure"
    assert prepared.prepared.measurement_keys == ("poles", "pole_stock", "note")


def test_parity_adapter_preserves_no_target_refusal() -> None:
    env = Env(STARVED_WORLD)
    observation = legacy.repair_observation(env, Journal())
    _, request = repair_request(
        tool=TOOL_RESUPPLY,
        intent=INTENT_INSERT_FUEL,
        target="u999",
    )

    result = LegacyRepairParityAdapter().prepare(
        request,
        observation=observation,
        env=env,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == legacy.REPAIR_NO_TARGET


def test_parity_adapter_preserves_no_fuel_refusal() -> None:
    env = Env(STARVED_WORLD, rcon=Rcon("0"))
    observation = legacy.repair_observation(env, Journal())
    _, request = repair_request(
        tool=TOOL_RESUPPLY,
        intent=INTENT_INSERT_FUEL,
        target="u1",
    )

    result = LegacyRepairParityAdapter().prepare(
        request,
        observation=observation,
        env=env,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == legacy.REPAIR_NO_FUEL


def test_structural_f1_counterexample_remains_named_unbound_in_f2b() -> None:
    env = Env(STARVED_WORLD)
    observation = legacy.repair_observation(env, Journal())
    action = RepairAction(
        tool=TOOL_PLACEMENT,
        intent="place_processing_for_buffered_output",
        prediction=Prediction("producers_reaching_processor", "increase"),
        targets=("u1",),
    )
    request = request_from_repair_action(
        action,
        action_id="structural-gap",
        provenance=provenance(),
    )

    result = LegacyRepairParityAdapter().prepare(
        request,
        observation=observation,
        env=env,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == legacy.REPAIR_NO_BINDING
