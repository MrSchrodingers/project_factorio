from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _module():
    path=ROOT/"scripts"/"run_cortex_supervisor.py"
    spec=importlib.util.spec_from_file_location("run_cortex_supervisor",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontier_returns_first_unpromoted_capability() -> None:
    module=_module()
    state={
        "phase5_protocol":{
            "capabilities":["iron_extraction","coal_self_sufficiency"],
            "achieved_capabilities":[],
        },
    }
    assert module.frontier(state)=="iron_extraction"


def test_supervisor_dispatch_is_explicit_and_bounded() -> None:
    module=_module()
    assert module.RUNNERS=={
        "iron_extraction":"run_cortex_f5c_deterministic_baseline.py",
        "coal_self_sufficiency":"run_cortex_f5c_coal_self_sufficiency.py",
    }


def test_preflight_block_is_not_terminal_counterexample() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()

    assert 'last_result in {"failed","rejected"}' in source
    assert '"preflight_blocked"' in source
    assert 'last_result="blocked"' not in source
