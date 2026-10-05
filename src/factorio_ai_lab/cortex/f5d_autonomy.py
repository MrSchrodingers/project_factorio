"""F5-D autonomous objective generation and online shadow learning.

This layer has no Factorio mutation surface.  It converts the live factory
graph into deficits, diagnoses them, exposes the complete repair candidate
surface, and ranks candidates from persistent measured rewards.  Authority is
supplied elsewhere and never by this policy.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.learning.repair_loop import (
    RepairAction,
    RepairObservation,
    detect_deficits,
    diagnose,
    propose_actions,
    symptom_key,
)

POLICY_SCHEMA_VERSION="cortex_f5d_online_policy_v1"


@dataclass(frozen=True)
class AutonomyCandidate:
    symptom: str
    severity: float
    action: RepairAction
    score: float
    score_basis: str

    def to_dict(self) -> dict[str,Any]:
        return {
            "symptom":self.symptom,
            "severity":self.severity,
            "action":self.action.to_dict(),
            "score":self.score,
            "score_basis":self.score_basis,
        }


@dataclass(frozen=True)
class AutonomyDecision:
    graph: Mapping[str,Any]
    candidates: tuple[AutonomyCandidate,...]
    refusals: tuple[Mapping[str,Any],...]
    selected: AutonomyCandidate | None
    policy_snapshot: Mapping[str,Any]

    def to_dict(self) -> dict[str,Any]:
        return {
            "candidates":[row.to_dict() for row in self.candidates],
            "refusals":[dict(row) for row in self.refusals],
            "selected":None if self.selected is None else self.selected.to_dict(),
            "policy_snapshot":dict(self.policy_snapshot),
        }


class PersistentUCBPolicy:
    def __init__(self,path: Path,*,exploration: float=2.0) -> None:
        self.path=Path(path)
        self.exploration=float(exploration)
        self.state=self._load()

    def _load(self) -> dict[str,Any]:
        try:
            raw=json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            raw={}
        if not isinstance(raw,dict):
            raw={}
        return {
            "schema_version":POLICY_SCHEMA_VERSION,
            "total_updates":int(raw.get("total_updates") or 0),
            "arms":dict(raw.get("arms") or {}),
        }

    def snapshot(self) -> dict[str,Any]:
        return json.loads(json.dumps(self.state,sort_keys=True))

    def _row(self,symptom: str,arm: str) -> dict[str,Any]:
        key=f"{symptom}|{arm}"
        rows=self.state.setdefault("arms",{})
        row=rows.setdefault(key,{"pulls":0,"mean_reward":0.0})
        return row

    def score(self,symptom: str,arm: str,*,severity: float) -> tuple[float,str]:
        row=self._row(symptom,arm)
        pulls=int(row.get("pulls") or 0)
        total=max(1,int(self.state.get("total_updates") or 0))
        if pulls==0:
            return math.inf,"ucb_unexplored"
        mean=float(row.get("mean_reward") or 0.0)
        bonus=math.sqrt(self.exploration*math.log(max(2,total))/pulls)
        return mean+bonus+0.05*float(severity),"ucb_measured"

    def update(self,symptom: str,arm: str,reward: float) -> None:
        row=self._row(symptom,arm)
        pulls=int(row.get("pulls") or 0)+1
        mean=float(row.get("mean_reward") or 0.0)
        row["pulls"]=pulls
        row["mean_reward"]=mean+(float(reward)-mean)/pulls
        self.state["total_updates"]=int(self.state.get("total_updates") or 0)+1
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix(self.path.suffix+".tmp")
        temp.write_text(
            json.dumps(self.state,indent=2,sort_keys=True)+"\n",
            encoding="utf-8",
        )
        temp.replace(self.path)


def compact_state(snapshot: Mapping[str,Any],graph: Mapping[str,Any]) -> dict[str,Any]:
    metrics=graph.get("metrics") if isinstance(graph,Mapping) else {}
    if not isinstance(metrics,Mapping):
        metrics={}
    produced=snapshot.get("production",{}).get("produced",{})
    consumed=snapshot.get("production",{}).get("consumed",{})
    return {
        "tick":snapshot.get("tick"),
        "entity_count":snapshot.get("entity_count"),
        "factory_metrics":{
            key:metrics.get(key)
            for key in (
                "producer_count",
                "producers_reaching_processor",
                "isolated_producers",
                "fuel_starved_entities",
                "power_starved_entities",
                "steam_path_live",
            )
        },
        "production":{
            "coal":float((produced or {}).get("coal",0) or 0),
            "iron_plate":float((produced or {}).get("iron-plate",0) or 0),
            "copper_plate":float((produced or {}).get("copper-plate",0) or 0),
            "logistic_science":float(
                (produced or {}).get("logistic-science-pack",0) or 0
            ),
            "coal_consumed":float((consumed or {}).get("coal",0) or 0),
        },
    }


def reward_components(
    before: Mapping[str,Any],
    after: Mapping[str,Any],
    *,
    manual_logistics_actions: int=0,
) -> dict[str,float]:
    bm=before.get("factory_metrics",{})
    am=after.get("factory_metrics",{})
    bp=before.get("production",{})
    ap=after.get("production",{})

    def n(mapping: Mapping[str,Any],key: str) -> float:
        raw=mapping.get(key)
        return float(raw) if isinstance(raw,(int,float)) and not isinstance(raw,bool) else 0.0

    fuel_recovery=n(bm,"fuel_starved_entities")-n(am,"fuel_starved_entities")
    power_recovery=n(bm,"power_starved_entities")-n(am,"power_starved_entities")
    processing_gain=(
        n(am,"producers_reaching_processor")-n(bm,"producers_reaching_processor")
    )
    isolated_reduction=n(bm,"isolated_producers")-n(am,"isolated_producers")
    science_gain=max(0.0,n(ap,"logistic_science")-n(bp,"logistic_science"))
    manual_cost=-0.25*max(0,int(manual_logistics_actions))
    total=(
        2.0*fuel_recovery
        +2.0*power_recovery
        +3.0*processing_gain
        +1.5*isolated_reduction
        +0.25*science_gain
        +manual_cost
    )
    return {
        "fuel_recovery":fuel_recovery,
        "power_recovery":power_recovery,
        "processing_gain":processing_gain,
        "isolated_reduction":isolated_reduction,
        "logistic_science_gain":science_gain,
        "manual_logistics_cost":manual_cost,
        "total":total,
    }


def select_supported_candidate(
    decision: AutonomyDecision,
    supported_action_keys: Sequence[str],
) -> AutonomyCandidate | None:
    supported=set(map(str,supported_action_keys))
    eligible=[
        row for row in decision.candidates
        if row.action.key in supported
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row:(row.score,row.severity,row.action.key),
    )


def decide(
    snapshot: Mapping[str,Any],
    policy: PersistentUCBPolicy,
) -> AutonomyDecision:
    entities=snapshot.get("entities")
    if not isinstance(entities,Sequence) or isinstance(entities,(str,bytes)):
        raise TypeError("snapshot entities unavailable")
    graph=build_factory_graph(
        [row for row in entities if isinstance(row,Mapping)]
    )
    observation=RepairObservation(graph=graph)
    candidates: list[AutonomyCandidate]=[]
    refusals: list[Mapping[str,Any]]=[]
    for deficit in detect_deficits(observation):
        diagnosis=diagnose(deficit,observation)
        proposal=propose_actions(diagnosis,observation)
        symptom=symptom_key(diagnosis)
        if proposal.refusal or not proposal.candidates:
            refusals.append({
                "symptom":symptom,
                "deficit":deficit.to_dict(),
                "diagnosis":diagnosis.to_dict(),
                "refusal":proposal.refusal or "no_candidates",
            })
            continue
        for action in proposal.candidates:
            score,basis=policy.score(
                symptom,
                action.key,
                severity=float(deficit.severity),
            )
            candidates.append(
                AutonomyCandidate(
                    symptom=symptom,
                    severity=float(deficit.severity),
                    action=action,
                    score=score,
                    score_basis=basis,
                )
            )
    selected=(
        None
        if not candidates
        else max(
            candidates,
            key=lambda row:(
                row.score,
                row.severity,
                row.action.key,
            ),
        )
    )
    return AutonomyDecision(
        graph=graph,
        candidates=tuple(candidates),
        refusals=tuple(refusals),
        selected=selected,
        policy_snapshot=policy.snapshot(),
    )
