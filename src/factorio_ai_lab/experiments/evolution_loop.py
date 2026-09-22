from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.agents.evolution_advisor import (
    EvolutionAdvice,
    propose_evolution_advice,
)
from factorio_ai_lab.experiments.curriculum_runner import (
    EVOLUTION_CHAMPION,
    read_json_object,
    run_curriculum,
)
from factorio_ai_lab.experiments.open_play_runner import (
    OPEN_PLAY_ROBUSTNESS_STATE,
    run_open_play_validation,
)
from factorio_ai_lab.learning.evolution import apply_advice, challenger_genome
from factorio_ai_lab.learning.experience import (
    CounterexampleRecord,
    ExperienceBuffer,
    counterexample_signature,
)
from factorio_ai_lab.learning.robustness import OpenPlayRobustnessGate
from factorio_ai_lab.planning.factorio_catalog import (
    EARLY_GAME_PRODUCTION_PLANNER,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
LOOP_STATE = RUNS_DIR / "evolution_loop_state.json"
LOOP_HISTORY = RUNS_DIR / "evolution_loop_history.jsonl"
LOOP_LOCK = RUNS_DIR / "evolution_loop.lock"
VALIDATED_CHAMPION = RUNS_DIR / "open_play_validated_champion.json"
OPEN_PLAY_STRATEGY = RUNS_DIR / "open_play_strategy.json"
COUNTEREXAMPLE_MEMORY = RUNS_DIR / "counterexamples.jsonl"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def _persistent_iteration_offset() -> int:
    if not LOOP_HISTORY.exists():
        return 0
    count = 0
    try:
        for raw in LOOP_HISTORY.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and "loop_index" in row:
                count += 1
    except OSError:
        return 0
    return count


def _train_world_models(seed: int) -> dict[str, Any]:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    env.setdefault("OMP_NUM_THREADS", "8")
    env.setdefault("MKL_NUM_THREADS", "8")
    command = [
        str(python),
        "-m",
        "factorio_ai_lab.experiments.train_recurrent_world_model",
        "--seed",
        str(seed),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=360,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "usable": False,
            "status": "training_error",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    if completed.returncode != 0:
        return {
            "usable": False,
            "status": "training_error",
            "reason": completed.stderr[-4000:],
            "returncode": completed.returncode,
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "usable": False,
            "status": "training_error",
            "reason": f"invalid trainer JSON: {exc}",
            "stdout_tail": completed.stdout[-4000:],
        }
    return payload if isinstance(payload, dict) else {
        "usable": False,
        "status": "training_error",
        "reason": "trainer returned non-object JSON",
    }


def _normalized_open_play_configuration(
    configuration: dict[str, Any] | None,
) -> dict[str, Any]:
    return challenger_genome(
        attempt=1,
        champion_configuration=configuration or {},
    ).to_dict()


def _open_play_strategy(champion: dict[str, Any]) -> dict[str, Any]:
    strategy = read_json_object(OPEN_PLAY_STRATEGY)
    if strategy.get("experimental_champion") == champion.get("run_id"):
        configuration = strategy.get("configuration", {})
        if isinstance(configuration, dict):
            return _normalized_open_play_configuration(configuration)
    champion_configuration = champion.get("configuration", {})
    return _normalized_open_play_configuration(
        champion_configuration
        if isinstance(champion_configuration, dict)
        else {}
    )


def _apply_deterministic_open_play_repairs(
    *,
    current: dict[str, Any],
    configuration: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    adjusted = _normalized_open_play_configuration(configuration)
    repairs: list[dict[str, Any]] = []

    bootstrap = current.get("metrics", {}).get("open_play_bootstrap", {})
    if isinstance(bootstrap, dict):
        deficits = bootstrap.get("deficits", {})
        requested = bootstrap.get("requested", {})
        if isinstance(deficits, dict) and isinstance(requested, dict):
            parameter_map = {
                "wood": "open_play_wood_target",
                "stone": "open_play_stone_target",
                "coal": "open_play_coal_target",
                "iron": "open_play_iron_target",
                "copper": "open_play_copper_target",
            }
            for resource, parameter in parameter_map.items():
                deficit = float(deficits.get(resource, 0.0) or 0.0)
                if deficit <= 0:
                    continue
                current_target = int(
                    requested.get(resource, adjusted.get(parameter, 0)) or 0
                )
                adjusted[parameter] = current_target
                repairs.append(
                    {
                        "reason": "bootstrap_collection_retry",
                        "resource": resource,
                        "parameter": parameter,
                        "deficit": deficit,
                        "target": current_target,
                        "increment": 0,
                    }
                )
            if float(deficits.get("wood", 0.0) or 0.0) > 0:
                old_radius = int(adjusted.get("open_play_wood_radius", 24))
                adjusted["open_play_wood_radius"] = min(48, old_radius + 4)
                repairs.append(
                    {
                        "reason": "wood_search_radius",
                        "parameter": "open_play_wood_radius",
                        "increment": adjusted["open_play_wood_radius"] - old_radius,
                    }
                )

    stage = str(current.get("stage") or "")
    if stage == "Lab bootstrap":
        lab_plan = EARLY_GAME_PRODUCTION_PLANNER.plan("lab", 1.0)
        raw = dict(lab_plan.raw_requirements_per_s)
        resource_map = {
            "iron-plate": ("open_play_iron_target", 520),
            "copper-plate": ("open_play_copper_target", 300),
        }
        for item, (parameter, upper) in resource_map.items():
            required = float(raw.get(item, 0.0) or 0.0)
            if required <= 0:
                continue
            current_target = int(adjusted.get(parameter, 0) or 0)
            increment = max(1, int(required * 1.25 + 0.999))
            adjusted[parameter] = min(upper, current_target + increment)
            repairs.append(
                {
                    "reason": "stage_material_budget",
                    "stage": stage,
                    "target_item": "lab",
                    "raw_item": item,
                    "parameter": parameter,
                    "stoichiometric_requirement": required,
                    "safety_factor": 1.25,
                    "increment": adjusted[parameter] - current_target,
                }
            )

    if stage == "Powered red science":
        raw: dict[str, float] = {}
        for target_item, amount in (
            ("assembling-machine-1", 1.0),
            ("automation-science-pack", 8.0),
            ("small-electric-pole", 4.0),
        ):
            plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
                target_item,
                amount,
            )
            for item, value in plan.raw_requirements_per_s.items():
                raw[item] = raw.get(item, 0.0) + float(value)

        resource_map = {
            "iron-plate": ("open_play_iron_target", 700),
            "copper-plate": ("open_play_copper_target", 420),
            "wood": ("open_play_wood_target", 180),
        }
        for item, (parameter, upper) in resource_map.items():
            required = float(raw.get(item, 0.0) or 0.0)
            if required <= 0:
                continue
            current_target = int(adjusted.get(parameter, 0) or 0)
            increment = max(3, int(required * 1.25 + 0.999))
            adjusted[parameter] = min(
                upper,
                current_target + increment,
            )
            repairs.append(
                {
                    "reason": "powered_red_material_budget",
                    "stage": stage,
                    "raw_item": item,
                    "parameter": parameter,
                    "stoichiometric_requirement": required,
                    "increment": adjusted[parameter] - current_target,
                }
            )

    if stage == "Logistic-science unlock":
        metrics = current.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        diagnostics = metrics.get(
            "logistic_science_unlock_diagnostics",
            {},
        )
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        rounds = metrics.get(
            "logistic_science_production_rounds",
            diagnostics.get("rounds", []),
        )
        if not isinstance(rounds, list):
            rounds = []

        power_starved = False
        observed_states: set[str] = set()
        for row in rounds:
            if not isinstance(row, dict):
                continue
            for key in (
                "boiler_status",
                "engine_status",
                "assembler_status",
            ):
                status = str(row.get(key) or "")
                if status:
                    observed_states.add(status)
                if any(
                    marker in status
                    for marker in ("NO_FUEL", "NO_POWER", "NOT_CONNECTED")
                ):
                    power_starved = True

        required_red = float(diagnostics.get("required_red", 0.0) or 0.0)
        scaled_red = float(
            diagnostics.get(
                "scaled_red",
                metrics.get("open_play_scaled_red_science", 0.0),
            )
            or 0.0
        )
        if power_starved and required_red > scaled_red:
            parameter = "open_play_coal_target"
            current_target = int(adjusted.get(parameter, 100) or 100)
            # The stage may consume up to 40 coal while scaling red science
            # and another 20 while researching the unlock. Reserve that full
            # bounded energy budget after an observed fuel/power starvation.
            increment = 60
            adjusted[parameter] = min(220, current_target + increment)
            repairs.append(
                {
                    "reason": "logistic_science_power_budget",
                    "stage": stage,
                    "parameter": parameter,
                    "required_red": required_red,
                    "scaled_red": scaled_red,
                    "red_shortfall": max(0.0, required_red - scaled_red),
                    "observed_states": sorted(observed_states),
                    "production_coal_budget": 40,
                    "research_coal_budget": 20,
                    "increment": adjusted[parameter] - current_target,
                }
            )

    if stage == "Electric mining transition":
        raw: dict[str, float] = {}
        for target_item, amount in (
            ("electric-mining-drill", 5.0),
            ("inserter", 7.0),
        ):
            plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
                target_item,
                amount,
            )
            for item, value in plan.raw_requirements_per_s.items():
                raw[item] = raw.get(item, 0.0) + float(value)

        metrics = current.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        transition = metrics.get(
            "electric_mining_transition",
            {},
        )
        if not isinstance(transition, dict):
            transition = {}

        research_preparation = metrics.get(
            "electric_mining_research_preparation",
            {},
        )
        if not isinstance(research_preparation, dict):
            research_preparation = {}
        research_rounds = metrics.get(
            "electric_mining_research_rounds",
            transition.get("research_rounds", []),
        )
        if not isinstance(research_rounds, list):
            research_rounds = []

        observed_power_states: set[str] = set()
        power_starved = False
        for row in research_rounds:
            if not isinstance(row, dict):
                continue
            for key in ("boiler_status", "engine_status", "lab_status"):
                status = str(row.get(key) or "")
                if status:
                    observed_power_states.add(status)
                if any(
                    marker in status
                    for marker in ("NO_FUEL", "NO_POWER", "NOT_CONNECTED")
                ):
                    power_starved = True

        for key in ("boiler_status", "engine_status"):
            status = str(research_preparation.get(key) or "")
            if status:
                observed_power_states.add(status)
            if any(
                marker in status
                for marker in ("NO_FUEL", "NO_POWER", "NOT_CONNECTED")
            ):
                power_starved = True

        # A physical-backbone routing failure is a structural counterexample,
        # not a reason to inflate raw-material targets. Explore the next finite
        # layout variant deterministically so repeated validation performs a
        # bounded design-of-experiments sweep over the four topologies.
        transition_phase = str(transition.get("phase") or "")
        transition_result = str(transition.get("result") or "")
        routing_failure_markers = (
            "Cannot connect",
            "Failed to connect",
            "Failed to find a path",
            "object is not subscriptable",
        )
        if (
            transition_phase == "physical_backbone"
            and bool(transition.get("error_occurred"))
            and any(
                marker in transition_result
                for marker in routing_failure_markers
            )
        ):
            parameter = "autonomy_layout_variant"
            current_variant = int(adjusted.get(parameter, 0) or 0) % 4
            next_variant = (current_variant + 1) % 4
            adjusted[parameter] = next_variant
            repairs.append(
                {
                    "reason": "electric_backbone_layout_counterexample",
                    "stage": stage,
                    "parameter": parameter,
                    "previous_variant": current_variant,
                    "next_variant": next_variant,
                    "failure": transition_result[:800],
                }
            )

        coal_loaded = float(
            research_preparation.get("coal_loaded", 0.0) or 0.0
        )
        preparation_lab_energy = float(
            research_preparation.get("lab_energy", 0.0) or 0.0
        )
        if (
            power_starved
            or (coal_loaded <= 0.0 and preparation_lab_energy <= 0.0)
        ):
            parameter = "open_play_coal_target"
            current_target = int(adjusted.get(parameter, 100) or 100)
            increment = 40
            adjusted[parameter] = min(220, current_target + increment)
            repairs.append(
                {
                    "reason": "electric_research_power_budget",
                    "stage": stage,
                    "parameter": parameter,
                    "coal_loaded": coal_loaded,
                    "reclaimed_coal": float(
                        research_preparation.get(
                            "reclaimed_coal",
                            0.0,
                        )
                        or 0.0
                    ),
                    "observed_states": sorted(observed_power_states),
                    "increment": adjusted[parameter] - current_target,
                }
            )
        route_phase = str(transition.get("phase") or "")
        route_stop_reason = str(
            transition.get("route_stop_reason") or ""
        )
        route_iron_observed = float(
            transition.get("route_iron", 0.0) or 0.0
        )
        route_iron_target = float(
            transition.get("route_iron_target", 0.0) or 0.0
        )
        route_shortfall_ratio = (
            max(0.0, route_iron_target - route_iron_observed)
            / route_iron_target
            if route_iron_target > 0
            else 0.0
        )
        prior_route_failures = sum(
            1
            for row in (history or [])
            if row.get("stage") == stage
            and row.get("phase") == "route_buffer"
        )
        structural_route_retry = False
        if (
            route_phase == "route_buffer"
            and 0.0 < route_shortfall_ratio <= 0.20
        ):
            old_detour = int(
                adjusted.get("autonomy_route_detour_margin", 8) or 8
            )
            old_belt_margin = int(
                adjusted.get("autonomy_belt_margin", 6) or 6
            )
            detour_step = 4 if prior_route_failures < 2 else 6
            belt_step = 2 if prior_route_failures < 2 else 4
            new_detour = max(2, old_detour - detour_step)
            new_belt_margin = max(2, old_belt_margin - belt_step)
            if new_detour != old_detour or new_belt_margin != old_belt_margin:
                adjusted["autonomy_route_detour_margin"] = new_detour
                adjusted["autonomy_belt_margin"] = new_belt_margin
                structural_route_retry = True
                repairs.append(
                    {
                        "reason": "route_capex_counterexample",
                        "stage": stage,
                        "phase": route_phase,
                        "stop_reason": route_stop_reason,
                        "shortfall_ratio": route_shortfall_ratio,
                        "prior_route_failures": prior_route_failures,
                        "route_iron": route_iron_observed,
                        "route_iron_target": route_iron_target,
                        "detour_margin": {
                            "before": old_detour,
                            "after": new_detour,
                        },
                        "belt_margin": {
                            "before": old_belt_margin,
                            "after": new_belt_margin,
                        },
                        "policy": (
                            "reduce structural CAPEX before increasing "
                            "bootstrap material targets"
                        ),
                    }
                )

        belt_required = float(
            transition.get("belt_required", 0.0) or 0.0
        )
        pole_required = float(
            transition.get("pole_required", 0.0) or 0.0
        )
        if belt_required > 0:
            belt_plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
                "transport-belt",
                belt_required + 6.0,
            )
            for item, value in belt_plan.raw_requirements_per_s.items():
                raw[item] = raw.get(item, 0.0) + float(value)
        if pole_required > 0:
            pole_plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
                "small-electric-pole",
                pole_required + 10.0,
            )
            for item, value in pole_plan.raw_requirements_per_s.items():
                raw[item] = raw.get(item, 0.0) + float(value)

        # Prefer measured deficits from the exact route buffer when available.
        route_values = {
            "iron": [
                float(transition.get("route_iron", 0.0) or 0.0),
                float(transition.get("route_iron_target", 0.0) or 0.0),
            ],
            "copper": [
                float(transition.get("route_copper", 0.0) or 0.0),
                float(transition.get("route_copper_target", 0.0) or 0.0),
            ],
            "wood": [
                float(transition.get("route_wood", 0.0) or 0.0),
                float(transition.get("route_wood_target", 0.0) or 0.0),
            ],
        }
        transition_result = str(transition.get("result", "") or "")
        for resource, observed, target in re.findall(
            r"(iron|copper|wood)=([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)",
            transition_result,
        ):
            if route_values[resource][1] <= 0.0:
                route_values[resource] = [float(observed), float(target)]
        for resource, observed, target in re.findall(
            r"route (iron|copper|wood) buffer deficit "
            r"([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)",
            transition_result,
        ):
            if route_values[resource][1] <= 0.0:
                route_values[resource] = [float(observed), float(target)]

        measured_requirements = {
            "iron-plate": max(
                0.0,
                route_values["iron"][1] - route_values["iron"][0],
            ),
            "copper-plate": max(
                0.0,
                route_values["copper"][1] - route_values["copper"][0],
            ),
            "wood": max(
                0.0,
                route_values["wood"][1] - route_values["wood"][0],
            ),
        }
        for item, deficit in measured_requirements.items():
            if deficit > 0:
                raw[item] = max(raw.get(item, 0.0), deficit)

        resource_map = {
            "iron-plate": ("open_play_iron_target", 1000),
            "copper-plate": ("open_play_copper_target", 600),
            "wood": ("open_play_wood_target", 240),
        }
        for item, (parameter, upper) in resource_map.items():
            if structural_route_retry and item == "iron-plate":
                continue
            required = float(raw.get(item, 0.0) or 0.0)
            if required <= 0:
                continue
            current_target = int(adjusted.get(parameter, 0) or 0)
            increment = max(5, int(required * 1.25 + 0.999))
            adjusted[parameter] = min(
                upper,
                current_target + increment,
            )
            repairs.append(
                {
                    "reason": "electric_transition_material_budget",
                    "stage": stage,
                    "raw_item": item,
                    "parameter": parameter,
                    "stoichiometric_requirement": required,
                    "belt_required": belt_required,
                    "pole_required": pole_required,
                    "increment": adjusted[parameter] - current_target,
                }
            )

    if stage == "Autonomy soak":
        failed_gates = [
            str(value)
            for value in (
                current.get("events", [])[-1].get(
                    "failed_gates",
                    [],
                )
                if current.get("events")
                and isinstance(current.get("events", [])[-1], dict)
                else []
            )
        ]
        old_variant = int(adjusted.get("autonomy_layout_variant", 0) or 0)
        adjusted["autonomy_layout_variant"] = (old_variant + 1) % 4
        adjusted["autonomy_commissioning_coal"] = min(
            32,
            int(adjusted.get("autonomy_commissioning_coal", 14) or 14) + 4,
        )
        adjusted["autonomy_belt_margin"] = min(
            24,
            int(adjusted.get("autonomy_belt_margin", 6) or 6) + 2,
        )
        adjusted["autonomy_pole_margin"] = min(
            36,
            int(adjusted.get("autonomy_pole_margin", 10) or 10) + 4,
        )
        repairs.append(
            {
                "reason": "structural_autonomy_counterexample",
                "stage": stage,
                "failed_gates": failed_gates,
                "layout_variant": {
                    "before": old_variant,
                    "after": adjusted["autonomy_layout_variant"],
                },
                "commissioning_coal": adjusted[
                    "autonomy_commissioning_coal"
                ],
                "belt_margin": adjusted["autonomy_belt_margin"],
                "pole_margin": adjusted["autonomy_pole_margin"],
            }
        )

    return adjusted, repairs


def _repair_open_play_strategy(
    *,
    champion: dict[str, Any],
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    stage = str(current.get("stage") or "unknown")
    detail = str(current.get("detail") or "")
    metrics = current.get("metrics", {})
    metrics = metrics if isinstance(metrics, dict) else {}
    transition = metrics.get("electric_mining_transition", {})
    transition = transition if isinstance(transition, dict) else {}
    phase = str(transition.get("phase") or "")
    signature = counterexample_signature(
        stage=stage,
        phase=phase,
        detail=detail,
        diagnostics=transition,
    )
    experience = ExperienceBuffer(COUNTEREXAMPLE_MEMORY)
    recent_experience = experience.recent(stage=stage, limit=8)

    persisted = read_json_object(OPEN_PLAY_STRATEGY)
    if (
        persisted.get("experimental_champion") == champion.get("run_id")
        and persisted.get("counterexample_run") == current.get("run_id")
    ):
        persisted_configuration = persisted.get("configuration", {})
        if isinstance(persisted_configuration, dict):
            return _normalized_open_play_configuration(
                persisted_configuration
            )

    deterministic_seed, deterministic_repairs = (
        _apply_deterministic_open_play_repairs(
            current=current,
            configuration=previous,
            history=recent_experience,
        )
    )

    if deterministic_repairs:
        advice = EvolutionAdvice(
            hypothesis=(
                "Deterministic production/material repair available; "
                "LLM mutation skipped for this counterexample."
            ),
            adjustments=(),
            provider={"mode": "deterministic_repair"},
        )
        adjusted = deterministic_seed
    else:
        advice = propose_evolution_advice(
            {
                "arena": "open_play",
                "champion": champion,
                "previous_validation_strategy": previous,
                "counterexample": {
                    "stage": current.get("stage"),
                    "detail": current.get("detail"),
                    "metrics": current.get("metrics", {}),
                    "events": current.get("events", [])[-8:],
                    "signature": signature,
                },
                "recent_counterexamples": recent_experience,
            }
        )
        base = challenger_genome(
            attempt=1,
            champion_configuration=previous,
        )
        adjusted = apply_advice(base, advice.adjustments).to_dict()
        adjusted, deterministic_repairs = (
            _apply_deterministic_open_play_repairs(
                current=current,
                configuration=adjusted,
                history=recent_experience,
            )
        )

    experience.append(
        CounterexampleRecord(
            run_id=str(current.get("run_id") or "unknown"),
            stage=stage,
            phase=phase,
            detail=detail,
            signature=signature,
            diagnostics=transition,
            configuration=previous,
            repair={
                "deterministic_repairs": deterministic_repairs,
                "advice": advice.to_dict(),
                "next_configuration": adjusted,
            },
        )
    )
    repeat_count = experience.signature_count(signature)

    atomic_json(
        OPEN_PLAY_STRATEGY,
        {
            "updated_at": utc_now(),
            "experimental_champion": champion.get("run_id"),
            "configuration": adjusted,
            "advice": advice.to_dict(),
            "deterministic_repairs": deterministic_repairs,
            "counterexample_run": current.get("run_id"),
            "counterexample_stage": current.get("stage"),
            "counterexample_signature": signature,
            "counterexample_repeat_count": repeat_count,
        },
    )
    return adjusted


def run_loop(
    *,
    generations: int,
    seed: int,
    stop_on_validated: bool,
) -> dict[str, Any]:
    if generations < 1:
        raise ValueError("generations must be >= 1")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with LOOP_LOCK.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        iteration_offset = _persistent_iteration_offset()
        state: dict[str, Any] = {
            "status": "running",
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "requested_generations": generations,
            "completed_generations": 0,
            "seed": seed,
            "iteration_offset": iteration_offset,
            "stop_on_validated": stop_on_validated,
            "last_lab_run": None,
            "last_open_play_run": None,
            "world_model": None,
            "next_open_play_strategy": None,
        }
        atomic_json(LOOP_STATE, state)

        for index in range(generations):
            iteration_seed = seed + iteration_offset + index
            state["current_iteration_seed"] = iteration_seed
            state["updated_at"] = utc_now()
            atomic_json(LOOP_STATE, state)
            existing_champion = read_json_object(EVOLUTION_CHAMPION)
            pending_configuration = (
                _open_play_strategy(existing_champion)
                if existing_champion
                else {}
            )
            robustness_gate = OpenPlayRobustnessGate(
                OPEN_PLAY_ROBUSTNESS_STATE,
                required_passes=3,
            )
            robustness_pending = bool(
                existing_champion
                and robustness_gate.pending_for(
                    champion_run_id=str(
                        existing_champion.get("run_id") or "unknown"
                    ),
                    configuration=pending_configuration,
                )
            )

            if robustness_pending:
                lab = {
                    "run_id": existing_champion.get("run_id"),
                    "status": "skipped_for_robustness_validation",
                    "evolution": {
                        "generation": existing_champion.get("generation"),
                        "promotion": {
                            "promoted": False,
                            "reason": (
                                "freeze incumbent while multi-seed open-play "
                                "qualification is pending"
                            ),
                        },
                    },
                }
            else:
                lab = run_curriculum(
                    seed=iteration_seed,
                    placement_episodes=8,
                    baseline_settle=16,
                    trial_settle=8,
                    scale_settle=14,
                    smelt_settle=24,
                    logistics_settle=30,
                    belt_smelt_settle=32,
                    coal_mine_settle=24,
                    copper_mine_settle=16,
                    copper_smelt_settle=24,
                    exploration=2.0,
                )

            state["completed_generations"] = index + 1
            state["last_lab_run"] = {
                "run_id": lab.get("run_id"),
                "status": lab.get("status"),
                "generation": lab.get("evolution", {}).get("generation"),
                "promotion": lab.get("evolution", {}).get("promotion"),
                "robustness_freeze": robustness_pending,
            }

            model = _train_world_models(iteration_seed)
            state["world_model"] = model

            champion = read_json_object(EVOLUTION_CHAMPION)
            validated_champion = read_json_object(VALIDATED_CHAMPION)
            open_play: dict[str, Any] | None = None
            should_validate = bool(champion) and (
                champion.get("run_id")
                != validated_champion.get("run_id")
            )
            if should_validate:
                validation_configuration = _open_play_strategy(champion)
                open_play = run_open_play_validation(
                    seed=iteration_seed,
                    smelt_seconds=150,
                    coal_seconds=28,
                    power_seconds=16,
                    research_seconds=120,
                    assembler_seconds=50,
                    configuration_override=validation_configuration,
                )
                state["last_open_play_run"] = {
                    "run_id": open_play.get("run_id"),
                    "status": open_play.get("status"),
                    "stage": open_play.get("stage"),
                    "detail": open_play.get("detail"),
                    "configuration": validation_configuration,
                }
                if open_play.get("status") == "validation_pass":
                    state["next_open_play_strategy"] = validation_configuration
                elif open_play.get("status") != "generation_complete":
                    state["next_open_play_strategy"] = _repair_open_play_strategy(
                        champion=champion,
                        previous=validation_configuration,
                        current=open_play,
                    )

            record = {
                "at": utc_now(),
                "loop_index": index,
                "persistent_iteration": iteration_offset + index,
                "seed": iteration_seed,
                "lab": state["last_lab_run"],
                "open_play": state["last_open_play_run"] if should_validate else None,
                "world_model": model,
                "next_open_play_strategy": state["next_open_play_strategy"],
            }
            append_jsonl(LOOP_HISTORY, record)
            state["updated_at"] = utc_now()
            atomic_json(LOOP_STATE, state)

            if stop_on_validated and VALIDATED_CHAMPION.exists():
                state["status"] = "validated_champion_reached"
                break

        if state["status"] == "running":
            state["status"] = "completed_generation_budget"
        state["finished_at"] = utc_now()
        state["updated_at"] = utc_now()
        atomic_json(LOOP_STATE, state)
        return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument(
        "--continue-after-validated",
        action="store_true",
    )
    args = parser.parse_args()
    try:
        result = run_loop(
            generations=args.generations,
            seed=args.seed,
            stop_on_validated=not args.continue_after_validated,
        )
    except Exception as exc:
        state = {}
        if LOOP_STATE.exists():
            try:
                loaded = json.loads(LOOP_STATE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
            except (OSError, json.JSONDecodeError):
                state = {}
        state.update(
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        atomic_json(LOOP_STATE, state)
        append_jsonl(
            LOOP_HISTORY,
            {
                "at": utc_now(),
                "type": "loop_error",
                "error": state["error"],
                "last_lab_run": state.get("last_lab_run"),
                "last_open_play_run": state.get("last_open_play_run"),
            },
        )
        raise
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
