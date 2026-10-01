from __future__ import annotations

import base64
import json
import re
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from factorio_ai_lab.runtime import ActionRuntimeRecorder


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


def _decode_fle_entity_snapshot(raw: Any) -> list[dict[str, Any]]:
    """Decode one FLE save_entity_state payload without importing FLE eagerly."""

    if not isinstance(raw, str) or not raw:
        return []
    try:
        decoded = zlib.decompress(base64.b64decode(raw)).decode("utf-8")
        value = json.loads(decoded)
    except (OSError, ValueError, TypeError, zlib.error):
        return []
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _snapshot_entity_identity(row: Mapping[str, Any]) -> tuple[str, float, float, int]:
    raw_name = row.get("name")
    name = str(raw_name or "").replace('"', "")
    pos = row.get("position")
    if not isinstance(pos, Mapping):
        raise TypeError(f"entity snapshot has no position: {dict(row)!r}")
    x = round(float(pos["x"]), 4)
    y = round(float(pos["y"]), 4)
    direction = int(row.get("direction") or 0)
    return name, x, y, direction


def _capture_rcon_entity_rows(environment: Any) -> list[dict[str, Any]] | None:
    """Capture physical entity identities through RCON if the FLE saver is stale.

    FLE 0.4.3 may return an empty saver payload immediately after a
    game-state reset even though the Factorio surface is live. This fallback
    is observation-only: entity replay still uses the exact checkpoint rows.
    """

    unwrapped = getattr(environment, "unwrapped", environment)
    instance = getattr(unwrapped, "instance", None)
    ensure_connected = getattr(instance, "ensure_connected", None)
    if callable(ensure_connected):
        try:
            ensure_connected()
        except (OSError, RuntimeError, ConnectionError):
            return None
    rcon = getattr(instance, "rcon_client", None)
    send = getattr(rcon, "send_command", None)
    if not callable(send):
        return None

    command = (
        "/c local p=storage.agent_characters and storage.agent_characters[1]; "
        "if p and not p.valid then p=nil end; "
        "local s=(p and p.surface) or game.surfaces[1]; "
        "local f=(p and p.force) or game.forces.player; "
        "if not s or not f then rcon.print('CORTEX_CAPTURE_ERROR') return end; "
        "local area={{-500,-500},{500,500}}; "
        "rcon.print('CORTEX_CAPTURE_BEGIN'); "
        "for _,e in pairs(s.find_entities_filtered{area=area,force=f}) do "
        "if e.valid and e.name~='character' then "
        "rcon.print('CORTEX_ENTITY|'..e.name..'|'..e.position.x..'|'.."
        "e.position.y..'|'..(e.direction or 0)) end end; "
        "for _,e in pairs(s.find_entities_filtered{area=area,name='item-on-ground'}) do "
        "if e.valid then rcon.print('CORTEX_ENTITY|item-on-ground|'.."
        "e.position.x..'|'..e.position.y..'|0') end end; "
        "rcon.print('CORTEX_CAPTURE_END')"
    )
    try:
        response = send(command)
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
        return None
    if response is None:
        return None

    lines = [line.strip() for line in str(response).splitlines()]
    if (
        "CORTEX_CAPTURE_BEGIN" not in lines
        or "CORTEX_CAPTURE_END" not in lines
        or "CORTEX_CAPTURE_ERROR" in lines
    ):
        return None

    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.startswith("CORTEX_ENTITY|"):
            continue
        parts = line.split("|")
        if len(parts) != 5:
            return None
        try:
            x = float(parts[2])
            y = float(parts[3])
            direction = int(float(parts[4]))
        except (TypeError, ValueError):
            return None
        rows.append(
            {
                "name": parts[1],
                "position": {"x": x, "y": y},
                "direction": direction,
            }
        )
    return rows


def _capture_fle_entity_rows(
    environment: Any,
) -> list[dict[str, Any]] | None:
    unwrapped = getattr(environment, "unwrapped", environment)
    instance = getattr(unwrapped, "instance", None)
    namespace = getattr(instance, "first_namespace", None)
    saver = getattr(namespace, "_save_entity_state", None)
    if callable(saver):
        try:
            rows = _decode_fle_entity_snapshot(
                saver(compress=True, encode=True)
            )
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
            rows = []
        if rows:
            return rows
    return _capture_rcon_entity_rows(environment)


def _repair_missing_checkpoint_entities(
    environment: Any,
    checkpoint: Any,
) -> dict[str, Any]:
    """Verify FLE rollback structure and replay only missing checkpoint entities.

    FLE 0.4.3 resets the whole surface before loading a GameState and its Lua
    loader silently ignores create_entity failures. F5 cannot accept a rollback
    that loses an incumbent factory entity. This guard compares the checkpoint
    entity identities with the restored world and, when necessary, asks FLE's
    own loader to replay only the exact missing serialized rows. No new entity
    specification or resource value is synthesized here.
    """

    expected = _decode_fle_entity_snapshot(getattr(checkpoint, "entities", None))
    if not expected:
        return {
            "status": "not_applicable",
            "expected_entities": 0,
            "missing_before_repair": [],
            "missing_after_repair": [],
            "replayed_entities": 0,
        }

    actual = _capture_fle_entity_rows(environment)
    if actual is None:
        raise RuntimeError("rollback integrity check could not capture restored WORLD")

    expected_by_id = {
        _snapshot_entity_identity(row): row
        for row in expected
        if str(row.get("name") or "").replace('"', "") != "character"
    }
    actual_ids = {
        _snapshot_entity_identity(row)
        for row in actual
        if str(row.get("name") or "").replace('"', "") != "character"
    }
    missing = sorted(set(expected_by_id) - actual_ids)
    replayed = 0

    if missing:
        unwrapped = getattr(environment, "unwrapped", environment)
        instance = getattr(unwrapped, "instance", None)
        namespace = getattr(instance, "first_namespace", None)
        loader = getattr(namespace, "_load_entity_state", None)
        if not callable(loader):
            raise RuntimeError(
                "rollback lost checkpoint entities and no FLE loader is available"
            )
        rows = [expected_by_id[key] for key in missing]
        loader(rows, decompress=False)
        replayed = len(rows)
        actual_after = _capture_fle_entity_rows(environment)
        if actual_after is None:
            raise RuntimeError(
                "rollback integrity check could not recapture WORLD after replay"
            )
        actual_after_ids = {
            _snapshot_entity_identity(row)
            for row in actual_after
            if str(row.get("name") or "").replace('"', "") != "character"
        }
        remaining = sorted(set(expected_by_id) - actual_after_ids)
    else:
        remaining = []

    if remaining:
        raise RuntimeError(
            "FLE rollback integrity failure; checkpoint entities remain missing: "
            + repr(remaining)
        )

    return {
        "status": "repaired" if missing else "verified",
        "expected_entities": len(expected_by_id),
        "missing_before_repair": [list(key) for key in missing],
        "missing_after_repair": [list(key) for key in remaining],
        "replayed_entities": replayed,
    }


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


def attach_live_factorio_environment(
    *,
    address: str | None=None,
    tcp_port: int | None=None,
) -> Any:
    """Attach a FactorioGymEnv to the existing WORLD without task setup/reset.

    FLE's registry factory provisions a task by calling TaskABC.setup(), which
    resets the Factorio instance. Continuation experiments must never use that
    factory because the live WORLD is itself the promoted capability state.
    """
    import os

    from fle.env import FactorioInstance
    from fle.env.gym_env.environment import FactorioGymEnv

    resolved_address=(
        address
        or os.getenv("FACTORIO_SERVER_ADDRESS")
        or "127.0.0.1"
    )
    raw_port=(
        tcp_port
        if tcp_port is not None
        else os.getenv("FACTORIO_SERVER_PORT")
    )
    resolved_port=27000 if raw_port is None else int(raw_port)
    instance=FactorioInstance(
        address=resolved_address,
        tcp_port=resolved_port,
        num_agents=1,
        fast=True,
        cache_scripts=True,
        inventory={},
        all_technologies_researched=False,
        clear_entities=False,
        peaceful=False,
    )
    return FactorioGymEnv(
        instance=instance,
        task=None,
        enable_vision=False,
    )


def enforce_minimum_eval_timeout(
    environment: Any,
    *,
    minimum_seconds: int,
) -> int:
    """Install a local compatibility floor for FLE action evaluation.

    FLE 0.4.3 hard-codes timeout=120 in FactorioGymEnv.step. Complex but
    bounded physical construction actions can legitimately exceed that
    wall-clock budget even while Factorio is progressing. This shim raises
    only the lower bound passed to FactorioInstance.eval; callers asking for
    a larger timeout keep their larger value.

    The patch is instance-local, idempotent, and does not modify site-packages.
    """
    minimum = int(minimum_seconds)
    if minimum <= 0:
        raise ValueError("minimum_seconds must be positive")

    unwrapped = getattr(environment, "unwrapped", environment)
    instance = getattr(unwrapped, "instance", None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")

    current_floor = int(
        getattr(instance, "_factorio_ai_eval_timeout_floor_s", 0) or 0
    )
    if current_floor >= minimum:
        return current_floor

    original_eval = getattr(
        instance,
        "_factorio_ai_original_eval",
        None,
    )
    if original_eval is None:
        original_eval = instance.eval
        instance._factorio_ai_original_eval = original_eval

    def eval_with_timeout_floor(
        expr: Any,
        agent_idx: int = 0,
        timeout: int = 60,
    ) -> Any:
        return original_eval(
            expr,
            agent_idx=agent_idx,
            timeout=max(int(timeout), minimum),
        )

    instance.eval = eval_with_timeout_floor
    instance._factorio_ai_eval_timeout_floor_s = minimum
    return minimum


def enforce_pathfinding_retry_floor(
    environment: Any,
    *,
    minimum_attempts: int,
) -> int:
    """Raise FLE move_to path polling attempts without modifying site-packages.

    FLE 0.4.3 hard-codes GetPath(max_attempts=10) inside MoveTo. Large or busy
    worlds can leave a valid path in pending state beyond that short polling
    window. This shim is instance-local and monotonic: it only raises the
    lower bound while preserving callers that explicitly request more attempts.
    """
    minimum=int(minimum_attempts)
    if minimum<=0:
        raise ValueError("minimum_attempts must be positive")

    unwrapped=getattr(environment,"unwrapped",environment)
    instance=getattr(unwrapped,"instance",None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")
    namespaces=getattr(instance,"namespaces",None)
    if not isinstance(namespaces,(list,tuple)):
        namespace=getattr(instance,"namespace",None)
        namespaces=[] if namespace is None else [namespace]

    patched=0
    current_floor=0
    for namespace in namespaces:
        move_to=getattr(namespace,"move_to",None)
        tool=getattr(move_to,"__wrapped__",None)
        original=getattr(tool,"get_path",None) if tool is not None else None
        if tool is None or not callable(original):
            continue
        current=int(
            getattr(tool,"_factorio_ai_path_retry_floor",0) or 0
        )
        current_floor=max(current_floor,current)
        if current>=minimum:
            patched+=1
            continue
        base=getattr(tool,"_factorio_ai_original_get_path",None)
        if base is None:
            base=original
            tool._factorio_ai_original_get_path=base

        def get_path_with_floor(
            path_handle: int,
            max_attempts: int=10,
            *,
            _base: Any=base,
            _minimum: int=minimum,
        ) -> Any:
            return _base(
                path_handle,
                max_attempts=max(int(max_attempts),_minimum),
            )

        tool.get_path=get_path_with_floor
        tool._factorio_ai_path_retry_floor=minimum
        if move_to is not None:
            move_to.get_path=get_path_with_floor
        patched+=1
        current_floor=max(current_floor,minimum)

    if patched==0:
        raise TypeError("environment does not expose patchable move_to tools")
    return max(current_floor,minimum)


_INTERVENTION_CALLS = {
    "harvest_resource": "manual_harvest_calls",
    "insert_item": "manual_insert_calls",
    "extract_item": "manual_extract_calls",
    "craft_item": "manual_craft_calls",
}


def intervention_counts_from_code(code: str) -> dict[str, int]:
    counts = {
        metric: 0
        for metric in _INTERVENTION_CALLS.values()
    }
    for call_name, metric in _INTERVENTION_CALLS.items():
        counts[metric] = len(
            re.findall(
                rf"\b{re.escape(call_name)}\s*\(",
                code,
            )
        )
    counts["manual_transfer_calls"] = (
        counts["manual_insert_calls"] + counts["manual_extract_calls"]
    )
    counts["manual_logistics_calls"] = (
        counts["manual_harvest_calls"] + counts["manual_transfer_calls"]
    )
    return counts


def intervention_delta(
    current: Mapping[str, int],
    baseline: Mapping[str, int],
) -> dict[str, int]:
    keys = set(current) | set(baseline)
    return {
        key: max(
            0,
            int(current.get(key, 0)) - int(baseline.get(key, 0)),
        )
        for key in sorted(keys)
    }


class TransactionalFLEExecutor:
    def __init__(
        self,
        environment: FactorioEnvironment,
        *,
        agent_idx: int = 0,
        action_factory: ActionFactory = default_action_factory,
        runtime_context: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.environment = environment
        self.agent_idx = agent_idx
        self.action_factory = action_factory
        self.game_state: Any | None = None
        self._attempted_interventions: dict[str, int] = {}
        self._committed_interventions: dict[str, int] = {}
        # Counted apart from the above: see execute(purpose=...).
        self._attempted_infrastructure: dict[str, int] = {}
        self._committed_infrastructure: dict[str, int] = {}
        self._attempted_repair: dict[str, int] = {}
        self._committed_repair: dict[str, int] = {}
        self._last_rollback_integrity: dict[str, Any] | None = None
        self._action_runtime = (
            ActionRuntimeRecorder(context_provider=runtime_context)
            if runtime_context is not None
            else None
        )

    def reset(self, *, seed: int | None = None, game_state: Any | None = None) -> Any:
        self.game_state = game_state
        self._attempted_interventions = {}
        self._committed_interventions = {}
        self._attempted_infrastructure = {}
        self._committed_infrastructure = {}
        self._attempted_repair = {}
        self._committed_repair = {}
        self._last_rollback_integrity = None
        return self.environment.reset(
            options={'game_state': game_state},
            seed=seed,
        )

    @staticmethod
    def _accumulate(
        target: dict[str, int],
        counts: Mapping[str, int],
    ) -> None:
        for key, value in counts.items():
            target[key] = int(target.get(key, 0)) + int(value)

    def rollback_integrity_snapshot(self) -> dict[str, Any] | None:
        return (
            None
            if self._last_rollback_integrity is None
            else dict(self._last_rollback_integrity)
        )

    def intervention_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            "attempted": dict(self._attempted_interventions),
            "committed": dict(self._committed_interventions),
            "attempted_infrastructure": dict(self._attempted_infrastructure),
            "committed_infrastructure": dict(self._committed_infrastructure),
            "attempted_repair": dict(self._attempted_repair),
            "committed_repair": dict(self._committed_repair),
        }

    def _counters_for(self, purpose: str) -> tuple[dict[str, int], dict[str, int]]:
        """The (attempted, committed) pair a purpose is booked into."""
        if purpose == "infrastructure":
            return self._attempted_infrastructure, self._committed_infrastructure
        if purpose == "repair":
            return self._attempted_repair, self._committed_repair
        return self._attempted_interventions, self._committed_interventions

    def execute(
        self,
        code: str,
        *,
        accept: AcceptancePredicate,
        use_checkpoint_for_action: bool = True,
        purpose: str = "operation",
    ) -> FLEStep:
        """Run one transactional step.

        `purpose` separates operating the factory from building it. The
        intervention counters exist to measure how much the agent has to carry
        by hand because the factory cannot carry it itself. Loading the chest
        that feeds an automatic inserter is the opposite of that: it is the
        investment that removes future carrying. Counting it as manual
        logistics would make building automation look like a regression, so
        infrastructure steps are counted separately and reported separately -
        reclassified, never hidden.

        `repair` is the third case and it is not infrastructure: the agent
        really is carrying material by hand, and nothing about the step
        removes future carrying. What separates it is when it happens. A
        repair only runs after the graph has measured a machine as broken, it
        is answering that reading, and the repair budget bounds how many may
        run in one generation. Booking it as operation charges the challenger
        for the mechanism that fixed the factory: generation 70 drove
        fuel_starved_entities from 10 to 0 and paid four hand calls for it,
        which was the whole difference in manual logistics against generation
        69. Kept apart it stays measurable as what it is -- the cost of
        repairing -- instead of arriving as an increase in routine hand work.
        """
        if purpose not in {"operation", "infrastructure", "repair"}:
            raise ValueError(
                "purpose must be operation, infrastructure or repair, "
                f"got {purpose!r}"
            )
        checkpoint = self.game_state
        counts = intervention_counts_from_code(code)
        attempted_counter, committed_counter = self._counters_for(purpose)
        self._accumulate(attempted_counter, counts)
        action_state = checkpoint if use_checkpoint_for_action else None
        action = self.action_factory(self.agent_idx, code, action_state)
        runtime_token = (
            self._action_runtime.begin(code)
            if self._action_runtime is not None
            else None
        )
        try:
            observation, reward, terminated, truncated, info = (
                self.environment.step(action)
            )
        except Exception as exc:
            if self._action_runtime is not None and runtime_token is not None:
                self._action_runtime.finish(
                    runtime_token,
                    accepted=False,
                    reward=0.0,
                    terminated=False,
                    truncated=False,
                    info={},
                    error=exc,
                )
            raise
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
            self._accumulate(committed_counter, counts)
            self.game_state = candidate_state
            self._last_rollback_integrity = None
        else:
            # Restore the exact pre-action checkpoint. FLE 0.4.3 clears the
            # surface before replaying GameState entities and silently ignores
            # create_entity failures. Verify the restored structure and replay
            # only exact missing rows from the checkpoint before calling the
            # rollback complete.
            try:
                self.environment.reset(options={'game_state': checkpoint})
                self._last_rollback_integrity = _repair_missing_checkpoint_entities(
                    self.environment,
                    checkpoint,
                )
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                AttributeError,
                ConnectionError,
            ) as rollback_error:
                original_result = result.info.get("result")
                original_error = result.info.get("error_occurred")
                raise RuntimeError(
                    "rollback integrity failure after rejected FLE action; "
                    f"original_error_occurred={original_error!r}; "
                    f"original_result={original_result!r}; "
                    f"rollback_error={rollback_error}"
                ) from rollback_error
            self.game_state = checkpoint

        if self._action_runtime is not None and runtime_token is not None:
            self._action_runtime.finish(
                runtime_token,
                accepted=accepted,
                reward=result.reward,
                terminated=result.terminated,
                truncated=result.truncated,
                info=result.info,
            )

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





@dataclass(frozen=True)
class ExactResourceMineResult:
    resource_name: str
    requested_quantity: int
    inventory_before: int
    inventory_after: int
    inventory_growth: int
    attempts: int
    agent_idx: int
    raw_response: str


def mine_exact_resource(
    environment: Any,
    *,
    x: float,
    y: float,
    resource_name: str,
    quantity: int,
    radius: float=1.5,
    agent_idx: int=0,
) -> ExactResourceMineResult:
    """Mine endogenous resources with Factorio native character mining.

    FLE 0.4.3 fast harvest can report a calculated yield even when its manual
    inventory insertion fails. This helper calls the native mine_entity
    primitive and accepts only the measured main-inventory delta as evidence.
    """
    import math

    target_x=float(x)
    target_y=float(y)
    search_radius=float(radius)
    if not math.isfinite(target_x) or not math.isfinite(target_y):
        raise ValueError("resource mining coordinates must be finite")
    if not math.isfinite(search_radius) or search_radius<=0 or search_radius>3:
        raise ValueError("resource mining radius must be within 0..3")
    if agent_idx<0:
        raise ValueError("agent_idx must be non-negative")
    if (
        not isinstance(quantity,int)
        or isinstance(quantity,bool)
        or quantity<=0
        or quantity>50
    ):
        raise ValueError("resource mining quantity must be within 1..50")
    allowed={"stone","coal","iron-ore","copper-ore"}
    if resource_name not in allowed:
        raise ValueError(f"unsupported exact resource {resource_name!r}")

    unwrapped=getattr(environment,"unwrapped",environment)
    instance=getattr(unwrapped,"instance",None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")

    character_index=agent_idx+1
    quoted=json.dumps(resource_name)
    max_attempts=max(quantity*4,quantity+4)
    command=(
        "/c "
        f"local p=storage.agent_characters[{character_index}]; "
        "if not p or not p.valid then error('agent character unavailable') end; "
        "local inv=p.get_main_inventory(); "
        "if not inv or not inv.valid then error('agent inventory unavailable') end; "
        f"local q={{x={target_x},y={target_y}}}; "
        f"local name={quoted}; "
        "local before=p.get_item_count(name); "
        "local attempts=0; "
        f"local target={quantity}; "
        f"local max_attempts={max_attempts}; "
        "while (p.get_item_count(name)-before)<target and attempts<max_attempts do "
        "local best=nil; local bestd=nil; "
        "for _,e in pairs(p.surface.find_entities_filtered{"
        f"position=q,radius={search_radius},type='resource',name=name"
        "}) do "
        "if e.valid and e.minable then "
        "local dx=e.position.x-q.x; local dy=e.position.y-q.y; "
        "local d=dx*dx+dy*dy; "
        "if bestd==nil or d<bestd then best=e; bestd=d end "
        "end "
        "end; "
        "if not best then break end; "
        "local before_attempt=p.get_item_count(name); "
        "p.mine_entity(best); "
        "attempts=attempts+1; "
        "local after_attempt=p.get_item_count(name); "
        "if after_attempt<=before_attempt then break end; "
        "end; "
        "local after=p.get_item_count(name); "
        "local growth=after-before; "
        "rcon.print(before..','..after..','..growth..','..attempts)"
    )
    response=instance.rcon_client.send_command(command)
    if response is None:
        raise RuntimeError("exact resource mining returned no measurement")
    parts=str(response).strip().split(",")
    if len(parts)!=4:
        raise RuntimeError(f"unexpected exact resource mining response: {response!r}")
    try:
        before,after,growth,attempts=(int(float(value)) for value in parts)
    except ValueError as exc:
        raise RuntimeError(
            f"invalid exact resource mining response: {response!r}"
        ) from exc
    if growth<quantity:
        raise RuntimeError(
            f"native mining produced {growth}/{quantity} {resource_name}"
        )
    return ExactResourceMineResult(
        resource_name=resource_name,
        requested_quantity=quantity,
        inventory_before=before,
        inventory_after=after,
        inventory_growth=growth,
        attempts=attempts,
        agent_idx=agent_idx,
        raw_response=str(response),
    )


def bind_exact_resource_mining_tool(
    environment: Any,
    *,
    tool_name: str="cortex_mine_exact_resource",
) -> str:
    """Expose native endogenous mining inside a transactional FLE Option."""
    if not tool_name.isidentifier() or tool_name.startswith("_"):
        raise ValueError("tool_name must be a public Python identifier")
    unwrapped=getattr(environment,"unwrapped",environment)
    instance=getattr(unwrapped,"instance",None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")
    namespaces=getattr(instance,"namespaces",None)
    if not isinstance(namespaces,(list,tuple)) or not namespaces:
        raise TypeError("environment does not expose FLE namespaces")

    for agent_idx,namespace in enumerate(namespaces):
        def bound(
            position: Any,
            resource_name: str,
            *,
            quantity: int,
            radius: float=1.5,
            _agent_idx: int=agent_idx,
        ) -> int:
            x=getattr(position,"x",None)
            y=getattr(position,"y",None)
            if not isinstance(x,(int,float)) or isinstance(x,bool):
                raise TypeError("exact resource position.x must be numeric")
            if not isinstance(y,(int,float)) or isinstance(y,bool):
                raise TypeError("exact resource position.y must be numeric")
            result=mine_exact_resource(
                environment,
                x=float(x),
                y=float(y),
                resource_name=str(resource_name),
                quantity=quantity,
                radius=radius,
                agent_idx=_agent_idx,
            )
            return result.inventory_growth

        setattr(namespace,tool_name,bound)
    return tool_name



@dataclass(frozen=True)
class ExactItemTransferResult:
    source_name: str
    item_name: str
    requested_quantity: int
    inventory_before: int
    inventory_after: int
    inventory_growth: int
    removed: int
    agent_idx: int
    raw_response: str


def transfer_exact_item(
    environment: Any,
    *,
    x: float,
    y: float,
    source_name: str,
    item_name: str,
    quantity: int,
    agent_idx: int=0,
) -> ExactItemTransferResult:
    """Transfer an exact live-world item quantity into the agent inventory."""
    import math

    target_x=float(x)
    target_y=float(y)
    if not math.isfinite(target_x) or not math.isfinite(target_y):
        raise ValueError("exact transfer coordinates must be finite")
    if agent_idx<0:
        raise ValueError("agent_idx must be non-negative")
    if (
        not isinstance(quantity,int)
        or isinstance(quantity,bool)
        or quantity<=0
        or quantity>100
    ):
        raise ValueError("exact transfer quantity must be within 1..100")
    allowed_sources={"wooden-chest","stone-furnace"}
    allowed_items={
        "iron-plate","iron-ore","coal","copper-ore",
        "copper-plate","automation-science-pack",
    }
    if source_name not in allowed_sources:
        raise ValueError(f"unsupported exact transfer source {source_name!r}")
    if item_name not in allowed_items:
        raise ValueError(f"unsupported exact transfer item {item_name!r}")

    unwrapped=getattr(environment,"unwrapped",environment)
    instance=getattr(unwrapped,"instance",None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")

    character_index=agent_idx+1
    source_q=json.dumps(source_name)
    item_q=json.dumps(item_name)
    command=(
        "/c "
        f"local p=storage.agent_characters[{character_index}]; "
        "if not p or not p.valid then error('agent character unavailable') end; "
        f"local q={{x={target_x},y={target_y}}}; "
        f"local source_name={source_q}; local item_name={item_q}; "
        "local rows=p.surface.find_entities_filtered{"
        "position=q,radius=0.6,name=source_name,force=p.force}; "
        "local source=nil; local bestd=nil; "
        "for _,e in pairs(rows) do "
        "if e.valid then local dx=e.position.x-q.x; local dy=e.position.y-q.y; "
        "local d=dx*dx+dy*dy; "
        "if bestd==nil or d<bestd then source=e;bestd=d end end end; "
        "if not source then error('exact transfer source unavailable') end; "
        f"local requested={quantity}; "
        "local available=source.get_item_count(item_name); "
        "if available<requested then error('exact transfer source quantity insufficient') end; "
        "local before=p.get_item_count(item_name); "
        "local removed=source.remove_item{name=item_name,count=requested}; "
        "if removed~=requested then "
        "if removed>0 then source.insert{name=item_name,count=removed} end; "
        "error('exact transfer remove mismatch') end; "
        "local inserted=p.insert{name=item_name,count=removed}; "
        "if inserted~=removed then "
        "if inserted>0 then p.remove_item{name=item_name,count=inserted} end; "
        "source.insert{name=item_name,count=removed}; "
        "error('exact transfer insert mismatch') end; "
        "local after=p.get_item_count(item_name); local growth=after-before; "
        "rcon.print(before..','..after..','..growth..','..removed)"
    )
    response=instance.rcon_client.send_command(command)
    if response is None:
        raise RuntimeError("exact item transfer returned no measurement")
    parts=str(response).strip().split(",")
    if len(parts)!=4:
        raise RuntimeError(f"unexpected exact item transfer response: {response!r}")
    try:
        before,after,growth,removed=(int(float(value)) for value in parts)
    except ValueError as exc:
        raise RuntimeError(
            f"invalid exact item transfer response: {response!r}"
        ) from exc
    if removed!=quantity or growth!=quantity:
        raise RuntimeError(
            f"exact item transfer mismatch: removed={removed}, growth={growth}, "
            f"requested={quantity}"
        )
    return ExactItemTransferResult(
        source_name=source_name,
        item_name=item_name,
        requested_quantity=quantity,
        inventory_before=before,
        inventory_after=after,
        inventory_growth=growth,
        removed=removed,
        agent_idx=agent_idx,
        raw_response=str(response),
    )


def bind_exact_item_transfer_tool(
    environment: Any,
    *,
    tool_name: str="cortex_transfer_exact_item",
) -> str:
    """Expose exact endogenous item transfer inside a transactional Option."""
    if not tool_name.isidentifier() or tool_name.startswith("_"):
        raise ValueError("tool_name must be a public Python identifier")
    unwrapped=getattr(environment,"unwrapped",environment)
    instance=getattr(unwrapped,"instance",None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")
    namespaces=getattr(instance,"namespaces",None)
    if not isinstance(namespaces,(list,tuple)) or not namespaces:
        raise TypeError("environment does not expose FLE namespaces")

    for agent_idx,namespace in enumerate(namespaces):
        def bound(
            position: Any,
            source_name: str,
            item_name: str,
            *,
            quantity: int,
            _agent_idx: int=agent_idx,
        ) -> int:
            x=getattr(position,"x",None)
            y=getattr(position,"y",None)
            if not isinstance(x,(int,float)) or isinstance(x,bool):
                raise TypeError("exact transfer position.x must be numeric")
            if not isinstance(y,(int,float)) or isinstance(y,bool):
                raise TypeError("exact transfer position.y must be numeric")
            result=transfer_exact_item(
                environment,
                x=float(x),
                y=float(y),
                source_name=str(source_name),
                item_name=str(item_name),
                quantity=quantity,
                agent_idx=_agent_idx,
            )
            return result.inventory_growth

        setattr(namespace,tool_name,bound)
    return tool_name


def bind_fast_reposition_tool(
    environment: Any,
    *,
    tool_name: str="cortex_fast_reposition",
) -> str:
    """Expose fast_reposition inside FLE eval as a transactional tool.

    Binding itself does not mutate Factorio. The bound callable mutates only
    when invoked from an evaluated Option action, where normal checkpoint
    rollback semantics apply.
    """
    if not tool_name.isidentifier() or tool_name.startswith("_"):
        raise ValueError("tool_name must be a public Python identifier")
    unwrapped=getattr(environment,"unwrapped",environment)
    instance=getattr(unwrapped,"instance",None)
    if instance is None:
        raise TypeError("environment does not expose a FactorioInstance")
    namespaces=getattr(instance,"namespaces",None)
    if not isinstance(namespaces,(list,tuple)) or not namespaces:
        raise TypeError("environment does not expose FLE namespaces")

    for agent_idx,namespace in enumerate(namespaces):
        def bound(
            position: Any,
            *,
            _agent_idx: int=agent_idx,
            _namespace: Any=namespace,
        ) -> Any:
            x=getattr(position,"x",None)
            y=getattr(position,"y",None)
            if not isinstance(x,(int,float)) or isinstance(x,bool):
                raise TypeError("fast reposition position.x must be numeric")
            if not isinstance(y,(int,float)) or isinstance(y,bool):
                raise TypeError("fast reposition position.y must be numeric")
            fast_reposition(
                environment,
                x=float(x),
                y=float(y),
                agent_idx=_agent_idx,
            )
            return getattr(_namespace,"player_location",position)

        setattr(namespace,tool_name,bound)
    return tool_name
