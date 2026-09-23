from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from factorio_ai_lab.runtime import ActionRuntimeRecorder


class FactorioEnvironment(Protocol):
    def reset(
        self,
        *,
        options: Mapping[str, Any] | None = None,
        seed: int | None = None,
    ) -> Any: ...

    def step(self, action: Any) -> tuple[Any, float, bool, bool, Mapping[str, Any]]: ...

    def close(self) -> None: ...


ActionFactory = Callable[[int, str, Any | None], Any]
AcceptancePredicate = Callable[['FLEStep'], bool]


@dataclass(frozen=True)
class FLEStep:
    observation: Any
    reward: float
    terminated: bool
    truncated: bool
    info: Mapping[str, Any]
    checkpoint_before: Any | None
    candidate_game_state: Any | None
    accepted: bool

    @property
    def done(self) -> bool:
        return self.terminated or self.truncated


def default_action_factory(agent_idx: int, code: str, game_state: Any | None) -> Any:
    # Lazy import keeps the deterministic core usable without FLE installed.
    from fle.env.gym_env.action import Action

    return Action(agent_idx=agent_idx, code=code, game_state=game_state)


def list_environments() -> list[str]:
    from fle.env.gym_env.registry import list_available_environments

    return list(list_available_environments())


def enforce_minimum_eval_timeout(
    environment: Any,
    *,
    minimum_seconds: int,
) -> int:
    """Install a local compatibility floor for FLE action evaluation.

    FLE 0.4.3 hard-codes timeout=120 in FactorioGymEnv.step. Complex but
    bounded physical construction actions can legitimately exceed that
    wall-clock budget even while Factorio is progressing. This shim raises
    only the lower bound passed to FactorioInstance.eval; callers asking for
    a larger timeout keep their larger value.

    The patch is instance-local, idempotent, and does not modify site-packages.
    """
    minimum = int(minimum_seconds)
    if minimum <= 0:
        raise ValueError("minimum_seconds must be positive")

    unwrapped = getattr(environment, "unwrapped", environment)
    instance = getattr(unwrapped, "instance", None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")

    current_floor = int(
        getattr(instance, "_factorio_ai_eval_timeout_floor_s", 0) or 0
    )
    if current_floor >= minimum:
        return current_floor

    original_eval = getattr(
        instance,
        "_factorio_ai_original_eval",
        None,
    )
    if original_eval is None:
        original_eval = instance.eval
        instance._factorio_ai_original_eval = original_eval

    def eval_with_timeout_floor(
        expr: Any,
        agent_idx: int = 0,
        timeout: int = 60,
    ) -> Any:
        return original_eval(
            expr,
            agent_idx=agent_idx,
            timeout=max(int(timeout), minimum),
        )

    instance.eval = eval_with_timeout_floor
    instance._factorio_ai_eval_timeout_floor_s = minimum
    return minimum


_INTERVENTION_CALLS = {
    "harvest_resource": "manual_harvest_calls",
    "insert_item": "manual_insert_calls",
    "extract_item": "manual_extract_calls",
    "craft_item": "manual_craft_calls",
}


def intervention_counts_from_code(code: str) -> dict[str, int]:
    counts = {
        metric: 0
        for metric in _INTERVENTION_CALLS.values()
    }
    for call_name, metric in _INTERVENTION_CALLS.items():
        counts[metric] = len(
            re.findall(
                rf"\b{re.escape(call_name)}\s*\(",
                code,
            )
        )
    counts["manual_transfer_calls"] = (
        counts["manual_insert_calls"] + counts["manual_extract_calls"]
    )
    counts["manual_logistics_calls"] = (
        counts["manual_harvest_calls"] + counts["manual_transfer_calls"]
    )
    return counts


def intervention_delta(
    current: Mapping[str, int],
    baseline: Mapping[str, int],
) -> dict[str, int]:
    keys = set(current) | set(baseline)
    return {
        key: max(
            0,
            int(current.get(key, 0)) - int(baseline.get(key, 0)),
        )
        for key in sorted(keys)
    }


class TransactionalFLEExecutor:
    def __init__(
        self,
        environment: FactorioEnvironment,
        *,
        agent_idx: int = 0,
        action_factory: ActionFactory = default_action_factory,
        runtime_context: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.environment = environment
        self.agent_idx = agent_idx
        self.action_factory = action_factory
        self.game_state: Any | None = None
        self._attempted_interventions: dict[str, int] = {}
        self._committed_interventions: dict[str, int] = {}
        # Counted apart from the above: see execute(purpose=...).
        self._attempted_infrastructure: dict[str, int] = {}
        self._committed_infrastructure: dict[str, int] = {}
        self._action_runtime = (
            ActionRuntimeRecorder(context_provider=runtime_context)
            if runtime_context is not None
            else None
        )

    def reset(self, *, seed: int | None = None, game_state: Any | None = None) -> Any:
        self.game_state = game_state
        self._attempted_interventions = {}
        self._committed_interventions = {}
        self._attempted_infrastructure = {}
        self._committed_infrastructure = {}
        return self.environment.reset(
            options={'game_state': game_state},
            seed=seed,
        )

    @staticmethod
    def _accumulate(
        target: dict[str, int],
        counts: Mapping[str, int],
    ) -> None:
        for key, value in counts.items():
            target[key] = int(target.get(key, 0)) + int(value)

    def intervention_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            "attempted": dict(self._attempted_interventions),
            "committed": dict(self._committed_interventions),
            "attempted_infrastructure": dict(self._attempted_infrastructure),
            "committed_infrastructure": dict(self._committed_infrastructure),
        }

    def execute(
        self,
        code: str,
        *,
        accept: AcceptancePredicate,
        use_checkpoint_for_action: bool = True,
        purpose: str = "operation",
    ) -> FLEStep:
        """Run one transactional step.

        `purpose` separates operating the factory from building it. The
        intervention counters exist to measure how much the agent has to carry
        by hand because the factory cannot carry it itself. Loading the chest
        that feeds an automatic inserter is the opposite of that: it is the
        investment that removes future carrying. Counting it as manual
        logistics would make building automation look like a regression, so
        infrastructure steps are counted separately and reported separately -
        reclassified, never hidden.
        """
        if purpose not in {"operation", "infrastructure"}:
            raise ValueError(
                f"purpose must be operation or infrastructure, got {purpose!r}"
            )
        checkpoint = self.game_state
        counts = intervention_counts_from_code(code)
        if purpose == "infrastructure":
            self._accumulate(self._attempted_infrastructure, counts)
        else:
            self._accumulate(self._attempted_interventions, counts)
        action_state = checkpoint if use_checkpoint_for_action else None
        action = self.action_factory(self.agent_idx, code, action_state)
        runtime_token = (
            self._action_runtime.begin(code)
            if self._action_runtime is not None
            else None
        )
        try:
            observation, reward, terminated, truncated, info = (
                self.environment.step(action)
            )
        except Exception as exc:
            if self._action_runtime is not None and runtime_token is not None:
                self._action_runtime.finish(
                    runtime_token,
                    accepted=False,
                    reward=0.0,
                    terminated=False,
                    truncated=False,
                    info={},
                    error=exc,
                )
            raise
        candidate_state = info.get('output_game_state')

        provisional = FLEStep(
            observation=observation,
            reward=float(reward),
            terminated=bool(terminated),
            truncated=bool(truncated),
            info=info,
            checkpoint_before=checkpoint,
            candidate_game_state=candidate_state,
            accepted=False,
        )
        accepted = bool(accept(provisional))

        result = FLEStep(
            observation=provisional.observation,
            reward=provisional.reward,
            terminated=provisional.terminated,
            truncated=provisional.truncated,
            info=provisional.info,
            checkpoint_before=checkpoint,
            candidate_game_state=candidate_state,
            accepted=accepted,
        )

        if accepted:
            if purpose == "infrastructure":
                self._accumulate(self._committed_infrastructure, counts)
            else:
                self._accumulate(self._committed_interventions, counts)
            self.game_state = candidate_state
        else:
            # Restore the exact pre-action checkpoint. Passing None restores
            # the environment's initial task state on the first rejected step.
            self.environment.reset(options={'game_state': checkpoint})
            self.game_state = checkpoint

        if self._action_runtime is not None and runtime_token is not None:
            self._action_runtime.finish(
                runtime_token,
                accepted=accepted,
                reward=result.reward,
                terminated=result.terminated,
                truncated=result.truncated,
                info=result.info,
            )

        return result

    def close(self) -> None:
        self.environment.close()


@dataclass(frozen=True)
class FastRepositionResult:
    x: float
    y: float
    agent_idx: int
    raw_response: str


def fast_reposition(
    environment: Any,
    *,
    x: float,
    y: float,
    agent_idx: int = 0,
) -> FastRepositionResult:
    """
    Compatibility shim for FLE 0.4.3 fast-mode movement.

    FLE's move_to currently depends on request_path/get_path, which can time out
    on Factorio 2.0.73 even for short paths. The FLE runtime itself represents
    agents as storage.agent_characters[N], so in fast experimental mode we
    reposition that synthetic character directly and keep namespace state in sync.

    This does not validate path feasibility. Spatial feasibility remains a
    planner/validator responsibility and the reposition distance must be treated
    as an experimental execution cost, not as free movement.
    """
    import math

    target_x = float(x)
    target_y = float(y)
    if not math.isfinite(target_x) or not math.isfinite(target_y):
        raise ValueError("reposition coordinates must be finite")
    if agent_idx < 0:
        raise ValueError("agent_idx must be non-negative")

    unwrapped = getattr(environment, "unwrapped", environment)
    instance = getattr(unwrapped, "instance", None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")

    character_index = agent_idx + 1
    command = (
        "/c "
        f"local p=storage.agent_characters[{character_index}]; "
        "if not p then error('agent character unavailable') end; "
        f"p.teleport({{x={target_x},y={target_y}}}); "
        "rcon.print(p.position.x .. ',' .. p.position.y)"
    )
    response = instance.rcon_client.send_command(command)
    if response is None:
        raise RuntimeError("fast reposition returned no position")

    parts = str(response).strip().split(",")
    if len(parts) != 2:
        raise RuntimeError(f"unexpected reposition response: {response!r}")
    actual_x, actual_y = (float(parts[0]), float(parts[1]))

    namespace = instance.namespaces[agent_idx]
    current = getattr(namespace, "player_location", None)
    if current is not None:
        namespace.player_location = type(current)(x=actual_x, y=actual_y)

    return FastRepositionResult(
        x=actual_x,
        y=actual_y,
        agent_idx=agent_idx,
        raw_response=str(response),
    )
