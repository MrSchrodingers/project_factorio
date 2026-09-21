from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from math import log, sqrt
from typing import TypeVar

Arm = TypeVar("Arm", bound=Hashable)


@dataclass(frozen=True)
class ArmStats:
    pulls: int
    mean_reward: float
    ucb_score: float | None


class UCB1Bandit:
    """Deterministic UCB1 baseline for discrete hyperparameter selection."""

    def __init__(self, arms: Iterable[Arm], *, exploration: float = 2.0) -> None:
        self.arms = tuple(arms)
        if not self.arms:
            raise ValueError("arms must not be empty")
        if len(set(self.arms)) != len(self.arms):
            raise ValueError("arms must be unique")
        if exploration < 0:
            raise ValueError("exploration must be non-negative")

        self.exploration = float(exploration)
        self._pulls = {arm: 0 for arm in self.arms}
        self._mean = {arm: 0.0 for arm in self.arms}
        self.total_pulls = 0

    def select(self) -> Arm:
        for arm in self.arms:
            if self._pulls[arm] == 0:
                return arm

        log_total = log(self.total_pulls)
        return max(
            self.arms,
            key=lambda arm: (
                self._mean[arm]
                + sqrt(self.exploration * log_total / self._pulls[arm]),
                -self.arms.index(arm),
            ),
        )

    def update(self, arm: Arm, reward: float) -> None:
        if arm not in self._pulls:
            raise KeyError(f"unknown arm: {arm!r}")

        pulls = self._pulls[arm] + 1
        old_mean = self._mean[arm]
        self._pulls[arm] = pulls
        self._mean[arm] = old_mean + (float(reward) - old_mean) / pulls
        self.total_pulls += 1

    def stats(self) -> dict[Arm, ArmStats]:
        log_total = log(self.total_pulls) if self.total_pulls else 0.0
        result: dict[Arm, ArmStats] = {}
        for arm in self.arms:
            pulls = self._pulls[arm]
            score = None
            if pulls and self.total_pulls:
                score = self._mean[arm] + sqrt(
                    self.exploration * log_total / pulls
                )
            result[arm] = ArmStats(
                pulls=pulls,
                mean_reward=self._mean[arm],
                ucb_score=score,
            )
        return result

    def best_observed(self) -> Arm:
        observed = [arm for arm in self.arms if self._pulls[arm] > 0]
        if not observed:
            raise RuntimeError("no arm has been observed")
        return max(
            observed,
            key=lambda arm: (self._mean[arm], -self.arms.index(arm)),
        )
