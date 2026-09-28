from itertools import product

import pytest

from factorio_ai_lab.cortex.causal_evaluation import (
    evaluation_arm_order,
    evaluation_preflight_tasks,
    evaluation_task_from_manifest,
)
from factorio_ai_lab.cortex.causal_harness import (
    HarnessValidationError,
)
from factorio_ai_lab.cortex.causal_inference import (
    exact_one_sided_sign_flip_p,
    exact_shift_confidence_set,
    exact_two_sided_shift_p,
    paired_cohens_dz,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    build_protocol_manifest,
)


def _bruteforce_one_sided(values):
    observed=sum(values)
    stats=[
        sum(sign*value for sign,value in zip(signs,values,strict=True))
        for signs in product((-1,1),repeat=len(values))
    ]
    return sum(value >= observed-1e-12 for value in stats)/len(stats)


def test_exact_one_sided_matches_bruteforce():
    values=[0.3,0.1,-0.02,0.2,0.05,0.09]
    assert exact_one_sided_sign_flip_p(values)==pytest.approx(
        _bruteforce_one_sided(values)
    )


def test_exact_shift_ci_inverts_two_sided_test():
    values=[0.20,0.17,0.15,0.11,0.09,0.13,0.16,0.18]
    confidence=exact_shift_confidence_set(values,alpha=0.05)
    assert confidence.connected is True
    assert confidence.lower < confidence.upper
    assert exact_two_sided_shift_p(values,confidence.lower)>0.05
    assert exact_two_sided_shift_p(values,confidence.upper)>0.05
    assert paired_cohens_dz(values)>0


def test_evaluation_mapping_uses_frozen_schedule_only():
    manifest=build_protocol_manifest()
    tasks=manifest["task_design"]["evaluation_tasks"]
    orders=[]
    for frozen in tasks:
        task=evaluation_task_from_manifest(manifest,frozen["seed"])
        orders.append(evaluation_arm_order(manifest,task))
    assert len(orders)==20
    assert orders.count((MEMORY_ON,MEMORY_ABLATED))==10
    assert orders.count((MEMORY_ABLATED,MEMORY_ON))==10
    with pytest.raises(HarnessValidationError,match="not in the frozen evaluation"):
        evaluation_task_from_manifest(manifest,20261201)
    with pytest.raises(HarnessValidationError,match="not in the frozen evaluation"):
        evaluation_task_from_manifest(manifest,20261101)


def test_evaluation_adapter_runs_only_independent_preflight(tmp_path):
    # Importing the adapter itself must not require a held-out evaluation spec.
    tasks=evaluation_preflight_tasks()
    assert len(tasks)==4
    assert all(task["partition"]=="evaluation_preflight" for task in tasks)
    assert all(task["seed"] is None for task in tasks)
