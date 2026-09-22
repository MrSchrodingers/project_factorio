from __future__ import annotations

import json
from pathlib import Path

import pytest

from factorio_ai_lab.runtime import (
    ActionRuntimeRecorder,
    FactorioWorldLease,
    WorldBusyError,
    classify_action,
    runtime_status,
)


def test_classify_action_prefers_semantic_control() -> None:
    assert classify_action("sleep(10)\nprint('ok')") == "wait"
    assert classify_action("set_research('automation')\nsleep(10)") == "research"
    assert classify_action("craft_item(Prototype.TransportBelt, quantity=2)") == "craft"
    assert classify_action("insert_item(Prototype.Coal, boiler, quantity=2)") == "logistics"
    assert classify_action("move_to(target.position)") == "move"
    assert classify_action("connect_entities(a,b,Prototype.TransportBelt)") == "build"


def test_world_lease_rejects_concurrent_writer(tmp_path: Path) -> None:
    lock = tmp_path / "world.lock"
    state = tmp_path / "lease.json"
    first = FactorioWorldLease(
        run_id="run-a",
        arena="lab",
        path=lock,
        state_path=state,
    ).acquire()
    try:
        with pytest.raises(WorldBusyError):
            FactorioWorldLease(
                run_id="run-b",
                arena="open_play",
                path=lock,
                state_path=state,
            ).acquire()
    finally:
        first.release()

    payload = json.loads(state.read_text())
    assert payload["status"] == "released"
    assert payload["run_id"] == "run-a"


def test_action_runtime_writes_causal_trace(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    trace = tmp_path / "trace.jsonl"
    recorder = ActionRuntimeRecorder(
        context_provider=lambda: {
            "run_id": "run-1",
            "arena": "open_play",
            "stage": "Electric mining transition",
        },
        heartbeat_path=heartbeat,
        trace_path=trace,
        heartbeat_interval_s=0.01,
    )
    token = recorder.begin("craft_item(Prototype.TransportBelt, quantity=2)")
    recorder.finish(
        token,
        accepted=True,
        reward=3.5,
        terminated=False,
        truncated=False,
        info={"ticks": 42, "policy_execution_time": 0.2},
    )

    status = json.loads(heartbeat.read_text())
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert status["state"] == "completed"
    assert status["action_kind"] == "craft"
    assert status["accepted"] is True
    assert rows[0]["event"] == "started"
    assert rows[-1]["event"] == "completed"
    assert rows[-1]["action_id"] == rows[0]["action_id"]


def test_runtime_status_reports_live_heartbeat(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    lease = tmp_path / "lease.json"
    recorder = ActionRuntimeRecorder(
        context_provider=lambda: {"run_id": "run-live"},
        heartbeat_path=heartbeat,
        trace_path=tmp_path / "trace.jsonl",
    )
    token = recorder.begin("sleep(1)")
    try:
        status = runtime_status(
            heartbeat_path=heartbeat,
            lease_path=lease,
            heartbeat_stale_s=60,
        )
        assert status["active"] is True
        assert status["action_active"] is True
        assert status["action"]["run_id"] == "run-live"
    finally:
        recorder.finish(
            token,
            accepted=True,
            reward=0.0,
            terminated=False,
            truncated=False,
            info={},
        )
