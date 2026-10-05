"""Typed F5-D trajectory records.

The dataset is append-only and deliberately separates state, candidate surface,
selection, authority, execution, postconditions and reward.  Policy output is
never interpreted as authority.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION="cortex_f5_trajectory_schema_v1"
RECORD_TYPE="typed_transition"
REQUIRED_FIELDS=(
    "state",
    "candidate_options",
    "memory_retrieval",
    "selected_option",
    "expected_effect",
    "authority_level",
    "execution_trace",
    "postconditions",
    "capability_delta",
    "resource_cost",
    "rollback",
    "reward_components",
    "next_state",
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
        default=str,
    )


@dataclass(frozen=True)
class F5TypedTransition:
    transition_id: str
    state: Mapping[str,Any]
    candidate_options: tuple[Mapping[str,Any],...]
    memory_retrieval: Mapping[str,Any]
    selected_option: Mapping[str,Any] | None
    expected_effect: Mapping[str,Any] | None
    authority_level: str
    execution_trace: Mapping[str,Any]
    postconditions: Mapping[str,Any]
    capability_delta: Mapping[str,Any]
    resource_cost: Mapping[str,Any]
    rollback: Mapping[str,Any]
    reward_components: Mapping[str,Any]
    next_state: Mapping[str,Any]
    metadata: Mapping[str,Any]

    def __post_init__(self) -> None:
        if not self.transition_id.strip():
            raise ValueError("transition_id must be non-empty")
        if self.authority_level not in {"A0","A1","A2","A3","A4","A5"}:
            raise ValueError("unsupported F5 authority level")
        object.__setattr__(
            self,
            "candidate_options",
            tuple(dict(row) for row in self.candidate_options),
        )

    def to_dict(self) -> dict[str,Any]:
        payload={
            "schema_version":SCHEMA_VERSION,
            "record_type":RECORD_TYPE,
            "transition_id":self.transition_id,
            "state":dict(self.state),
            "candidate_options":[dict(row) for row in self.candidate_options],
            "memory_retrieval":dict(self.memory_retrieval),
            "selected_option":(
                None if self.selected_option is None else dict(self.selected_option)
            ),
            "expected_effect":(
                None if self.expected_effect is None else dict(self.expected_effect)
            ),
            "authority_level":self.authority_level,
            "execution_trace":dict(self.execution_trace),
            "postconditions":dict(self.postconditions),
            "capability_delta":dict(self.capability_delta),
            "resource_cost":dict(self.resource_cost),
            "rollback":dict(self.rollback),
            "reward_components":dict(self.reward_components),
            "next_state":dict(self.next_state),
            "metadata":dict(self.metadata),
            "authority_is_observation_not_policy_output":True,
            "training_runtime_decoupled":True,
        }
        missing=[name for name in REQUIRED_FIELDS if name not in payload]
        if missing:
            raise ValueError("typed transition missing fields: "+", ".join(missing))
        return payload

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode()).hexdigest()


def append_transition(path: Path,transition: F5TypedTransition) -> str:
    path.parent.mkdir(parents=True,exist_ok=True)
    payload=transition.to_dict()
    payload["payload_sha256"]=transition.payload_sha256
    with path.open("a",encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        try:
            handle.write(_canonical(payload)+"\n")
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    return payload["payload_sha256"]
