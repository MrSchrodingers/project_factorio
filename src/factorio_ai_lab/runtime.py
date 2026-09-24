from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self, TextIO

from factorio_ai_lab.paths import RUNS_DIR

WORLD_LOCK = RUNS_DIR / "factorio_world.lock"
WORLD_LEASE_STATE = RUNS_DIR / "factorio_world_lease.json"
RUNTIME_HEARTBEAT = RUNS_DIR / "runtime_heartbeat.json"
ACTION_TRACE = RUNS_DIR / "action_transitions.jsonl"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def classify_action(code: str) -> str:
    calls = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", code))
    if calls & {"set_research", "get_research_progress"}:
        return "research"
    if calls & {
        "place_entity",
        "place_entity_next_to",
        "pickup_entity",
        "rotate_entity",
        "connect_entities",
    }:
        return "build"
    if calls & {"craft_item"}:
        return "craft"
    if calls & {"insert_item", "extract_item", "harvest_resource"}:
        return "logistics"
    if calls & {"move_to", "nearest", "get_resource_patch"}:
        return "move"
    passive = {
        "sleep",
        "print",
        "str",
        "float",
        "int",
        "max",
        "min",
        "len",
        "get_entity",
        "inspect_inventory",
    }
    if calls <= passive and "sleep" in calls:
        return "wait"
    return "other"


class WorldBusyError(RuntimeError):
    pass


@dataclass
class FactorioWorldLease:
    run_id: str
    arena: str
    owner: str = "factorio_ai_lab"
    path: Path = WORLD_LOCK
    state_path: Path = WORLD_LEASE_STATE
    _handle: TextIO | None = None

    def acquire(self) -> FactorioWorldLease:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            raw = handle.read().strip()
            holder = raw or json.dumps(_read_json(self.state_path), default=str)
            handle.close()
            raise WorldBusyError(
                "Factorio world already has an experimental writer: "
                + holder[:1200]
            ) from exc

        payload = {
            "status": "active",
            "pid": os.getpid(),
            "run_id": self.run_id,
            "arena": self.arena,
            "owner": self.owner,
            "acquired_at": utc_now(),
            "updated_at": utc_now(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(payload, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
        _atomic_json(self.state_path, payload)
        self._handle = handle
        return self

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        released = {
            **_read_json(self.state_path),
            "status": "released",
            "released_at": utc_now(),
            "updated_at": utc_now(),
        }
        _atomic_json(self.state_path, released)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


ContextProvider = Callable[[], Mapping[str, Any]]


@dataclass
class ActionToken:
    action_id: str
    payload: dict[str, Any]
    stop: threading.Event
    thread: threading.Thread | None


class ActionRuntimeRecorder:
    def __init__(
        self,
        *,
        context_provider: ContextProvider,
        heartbeat_path: Path = RUNTIME_HEARTBEAT,
        trace_path: Path = ACTION_TRACE,
        heartbeat_interval_s: float = 2.0,
    ) -> None:
        self.context_provider = context_provider
        self.heartbeat_path = heartbeat_path
        self.trace_path = trace_path
        self.heartbeat_interval_s = max(0.5, float(heartbeat_interval_s))
        self._sequence = 0
        self._lock = threading.Lock()

    def _context(self) -> dict[str, Any]:
        return dict(self.context_provider())

    def _write_heartbeat(self, payload: Mapping[str, Any]) -> None:
        _atomic_json(self.heartbeat_path, payload)

    def begin(self, code: str) -> ActionToken:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence

        context = self._context()
        action_id = (
            f"{context.get('run_id', 'run')}:{sequence}:{uuid.uuid4().hex[:8]}"
        )
        now = time.time()
        payload: dict[str, Any] = {
            **context,
            "pid": os.getpid(),
            "action_id": action_id,
            "action_sequence": sequence,
            "action_kind": classify_action(code),
            "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "state": "running",
            "started_at": datetime.fromtimestamp(now, UTC).isoformat(),
            "heartbeat_at": datetime.fromtimestamp(now, UTC).isoformat(),
            "heartbeat_unix": now,
            "elapsed_s": 0.0,
        }
        _append_jsonl(self.trace_path, {**payload, "event": "started"})
        self._write_heartbeat(payload)

        stop = threading.Event()

        def pulse() -> None:
            while not stop.wait(self.heartbeat_interval_s):
                current = time.time()
                heartbeat = {
                    **payload,
                    "heartbeat_at": datetime.fromtimestamp(current, UTC).isoformat(),
                    "heartbeat_unix": current,
                    "elapsed_s": round(current - now, 3),
                }
                self._write_heartbeat(heartbeat)

        thread = threading.Thread(
            target=pulse,
            name=f"factorio-action-heartbeat-{sequence}",
            daemon=True,
        )
        thread.start()
        return ActionToken(
            action_id=action_id,
            payload=payload,
            stop=stop,
            thread=thread,
        )

    def finish(
        self,
        token: ActionToken,
        *,
        accepted: bool,
        reward: float,
        terminated: bool,
        truncated: bool,
        info: Mapping[str, Any],
        error: BaseException | None = None,
    ) -> None:
        token.stop.set()
        if token.thread is not None:
            token.thread.join(timeout=self.heartbeat_interval_s + 0.5)
        current = time.time()
        started = datetime.fromisoformat(str(token.payload["started_at"])).timestamp()
        result = {
            **token.payload,
            "state": "completed" if error is None else "error",
            "accepted": bool(accepted),
            "reward": float(reward),
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "error_occurred": bool(info.get("error_occurred", False)),
            "policy_execution_time": info.get("policy_execution_time"),
            "ticks": info.get("ticks"),
            "heartbeat_at": datetime.fromtimestamp(current, UTC).isoformat(),
            "heartbeat_unix": current,
            "elapsed_s": round(current - started, 3),
            "finished_at": datetime.fromtimestamp(current, UTC).isoformat(),
        }
        if error is not None:
            result["exception"] = f"{type(error).__name__}: {error}"
        _append_jsonl(self.trace_path, {**result, "event": "completed"})
        self._write_heartbeat(result)


def runtime_status(
    *,
    heartbeat_path: Path = RUNTIME_HEARTBEAT,
    lease_path: Path = WORLD_LEASE_STATE,
    heartbeat_stale_s: float = 12.0,
) -> dict[str, Any]:
    now = time.time()
    heartbeat = _read_json(heartbeat_path)
    lease = _read_json(lease_path)

    heartbeat_pid = int(heartbeat.get("pid", 0) or 0)
    heartbeat_unix = float(heartbeat.get("heartbeat_unix", 0.0) or 0.0)
    heartbeat_age = (
        max(0.0, now - heartbeat_unix)
        if heartbeat_unix > 0
        else None
    )
    heartbeat_live = (
        bool(heartbeat)
        and _pid_alive(heartbeat_pid)
        and heartbeat_age is not None
        and heartbeat_age <= heartbeat_stale_s
    )

    lease_pid = int(lease.get("pid", 0) or 0)
    lease_live = lease.get("status") == "active" and _pid_alive(lease_pid)

    return {
        "active": bool(heartbeat_live or lease_live),
        "writer_active": bool(lease_live),
        "action_active": bool(
            heartbeat_live and heartbeat.get("state") == "running"
        ),
        "heartbeat_age_s": (
            round(float(heartbeat_age), 3)
            if heartbeat_age is not None
            else None
        ),
        "action": heartbeat if heartbeat_live else {},
        "lease": lease if lease_live else {},
    }
