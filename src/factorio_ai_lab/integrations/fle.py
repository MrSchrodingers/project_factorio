from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


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


class TransactionalFLEExecutor:
    def __init__(
        self,
        environment: FactorioEnvironment,
        *,
        agent_idx: int = 0,
        action_factory: ActionFactory = default_action_factory,
    ) -> None:
        self.environment = environment
        self.agent_idx = agent_idx
        self.action_factory = action_factory
        self.game_state: Any | None = None

    def reset(self, *, seed: int | None = None, game_state: Any | None = None) -> Any:
        self.game_state = game_state
        return self.environment.reset(
            options={'game_state': game_state},
            seed=seed,
        )

    def execute(
        self,
        code: str,
        *,
        accept: AcceptancePredicate,
        use_checkpoint_for_action: bool = True,
    ) -> FLEStep:
        checkpoint = self.game_state
        action_state = checkpoint if use_checkpoint_for_action else None
        action = self.action_factory(self.agent_idx, code, action_state)
        observation, reward, terminated, truncated, info = self.environment.step(action)
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
            self.game_state = candidate_state
        else:
            # Restore the exact pre-action checkpoint. Passing None restores
            # the environment's initial task state on the first rejected step.
            self.environment.reset(options={'game_state': checkpoint})
            self.game_state = checkpoint

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
