from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


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
    ) -> FLEStep:
        checkpoint = self.game_state
        action = self.action_factory(self.agent_idx, code, checkpoint)
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
