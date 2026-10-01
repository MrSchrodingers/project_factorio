"""Controlled transactional execution for prepared structural Cortex actions.

F2-E1 deliberately grants no ambient authority. Every mutation attempt must
carry ActionAuthority.EXECUTE, runs through TransactionalFLEExecutor, and is
committed only after the prepared hard postconditions are measured in the
candidate world.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any

from fle.env.game_types import Prototype, Resource

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionCondition,
    ActionResult,
    ActionStatus,
    ConditionOperator,
    ConditionState,
    EvidenceRef,
    Refusal,
)
from factorio_ai_lab.cortex.automation_science_structural_execute import (
    compile_automation_science,
)
from factorio_ai_lab.cortex.coal_structural_execute import (
    compile_coal_self_sufficiency,
)
from factorio_ai_lab.cortex.copper_chain_structural_execute import (
    compile_copper_chain,
)
from factorio_ai_lab.cortex.iron_smelting_structural_execute import (
    compile_iron_smelting,
)
from factorio_ai_lab.cortex.powered_manufacturing_structural_execute import (
    compile_powered_manufacturing,
)
from factorio_ai_lab.cortex.rollback_recovery_structural_execute import (
    compile_rollback_recovery,
)
from factorio_ai_lab.cortex.steam_power_structural_execute import (
    compile_steam_power,
)
from factorio_ai_lab.cortex.structural_prepare import (
    AUTOMATION_SCIENCE_CONTRACT_VERSION,
    COAL_SELF_SUFFICIENCY_CONTRACT_VERSION,
    COPPER_CHAIN_CONTRACT_VERSION,
    IRON_SMELTING_CONTRACT_VERSION,
    POWERED_MANUFACTURING_CONTRACT_VERSION,
    RESOURCE_EXTRACTION_CONTRACT_VERSION,
    ROLLBACK_RECOVERY_CONTRACT_VERSION,
    STEAM_POWER_CONTRACT_VERSION,
    SUPPORTED_CONTRACT_VERSIONS,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.evidence import EvidenceStatus
from factorio_ai_lab.planning.delivery import MODE_INSERTER

REFUSAL_EXECUTE_AUTHORITY_REQUIRED = "structural_execute_authority_required"
REFUSAL_CONTRACT_UNSUPPORTED = "structural_execute_contract_unsupported"
REFUSAL_OPERATION_UNSUPPORTED = "structural_execute_operation_unsupported"
REFUSAL_PROTOTYPE_UNRESOLVED = "structural_execute_prototype_unresolved"
REFUSAL_POSTCONDITION_CONTRACT = "structural_postcondition_contract_invalid"
REFUSAL_POSTCONDITION_FAILED = "structural_postcondition_failed"
REFUSAL_MEASUREMENT_FAILED = "structural_measurement_failed"
REFUSAL_TRANSACTION_FAILED = "structural_transaction_failed"
REFUSAL_WORLD_FUEL_DRAW_UNSUPPORTED = "structural_world_fuel_draw_unsupported"

DEFAULT_SETTLE_SECONDS = 8
MAX_SETTLE_SECONDS = 60
MAX_COAL_SELF_SUFFICIENCY_SECONDS = 180
MAX_IRON_SMELTING_SECONDS = 120
MAX_STEAM_POWER_SECONDS = 360
MAX_COPPER_CHAIN_SECONDS = 240
MAX_AUTOMATION_SCIENCE_SECONDS = 300
MAX_POWERED_MANUFACTURING_SECONDS = 720
MAX_ROLLBACK_RECOVERY_SECONDS = 240

MeasurementProbe = Callable[[PreparedStructuralAction], Mapping[str, Any]]

_FURNACE_PROTOTYPES = frozenset(
    {"stone-furnace", "steel-furnace", "electric-furnace"}
)


@dataclass(frozen=True)
class CompiledStructuralAction:
    action_id: str
    contract_version: str
    purpose: str
    code: str
    operation_names: tuple[str, ...]
    settle_seconds: int

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("compiled structural code must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "contract_version": self.contract_version,
            "purpose": self.purpose,
            "operation_names": list(self.operation_names),
            "settle_seconds": self.settle_seconds,
            "code": self.code,
        }


@dataclass(frozen=True)
class StructuralCompilationResult:
    prepared: PreparedStructuralAction
    compiled: CompiledStructuralAction | None = None
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.compiled is None) == (self.refusal is None):
            raise ValueError("compilation must contain exactly one compiled/refusal")

    @property
    def ready(self) -> bool:
        return self.compiled is not None


def _prototype_factorio_name(member: Any) -> str | None:
    value = getattr(member, "value", None)
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    return value if isinstance(value, str) else None


def prototype_symbol(name: str) -> str:
    """Resolve a Factorio prototype name through FLE's real Prototype enum."""

    matches = [
        member_name
        for member_name, member in Prototype.__members__.items()
        if _prototype_factorio_name(member) == name
    ]
    if not matches:
        raise ValueError(f"no FLE Prototype member for {name!r}")
    return f"Prototype.{matches[0]}"


def resource_symbol(name: str) -> str:
    """Resolve a Factorio resource name through FLE's Resource namespace."""

    matches: list[str]=[]
    for member_name in dir(Resource):
        if member_name.startswith("_"):
            continue
        member=getattr(Resource,member_name,None)
        factorio_name=(
            member[0]
            if isinstance(member,tuple)
            and member
            and isinstance(member[0],str)
            else _prototype_factorio_name(member)
        )
        if factorio_name==name:
            matches.append(member_name)
    if not matches:
        raise ValueError(f"no FLE Resource member for {name!r}")
    return f"Resource.{min(matches)}"


def _position(raw: Mapping[str, Any]) -> str:
    x = raw.get("x")
    y = raw.get("y")
    if not isinstance(x, Real) or not isinstance(y, Real):
        raise TypeError(f"invalid position {dict(raw)!r}")
    return f"Position(x={float(x)!r},y={float(y)!r})"


def _direction(name: object) -> str:
    value = str(name or "").upper()
    if value not in {"UP", "DOWN", "LEFT", "RIGHT"}:
        raise ValueError(f"unsupported FLE direction {name!r}")
    return f"Direction.{value}"


def _compile_ensure_item(operation: StructuralOperation) -> list[str]:
    item = str(operation.parameters.get("item") or "")
    quantity = operation.parameters.get("quantity")
    if not isinstance(quantity, Real) or float(quantity) <= 0:
        raise ValueError("ensure_item quantity must be positive")
    amount = int(quantity)
    if float(quantity) != float(amount):
        raise ValueError("ensure_item quantity must be integral")
    symbol = prototype_symbol(item)
    return [
        f"if inspect_inventory()[{symbol}] < {amount}:",
        f"    craft_item({symbol}, quantity={amount})",
    ]


def _compile_place_processor(operation: StructuralOperation) -> list[str]:
    entity = str(operation.parameters.get("entity") or "")
    position = operation.parameters.get("position")
    if not isinstance(position, Mapping):
        raise TypeError("place_processor requires position")
    return [
        "cortex_processor=place_entity(",
        f"    {prototype_symbol(entity)},",
        f"    position={_position(position)},",
        ")",
    ]


def _compile_adopt_processor(operation: StructuralOperation) -> list[str]:
    entity = str(operation.parameters.get("entity") or "")
    position = operation.parameters.get("position")
    if not isinstance(position, Mapping):
        raise TypeError("adopt_processor requires position")
    return [
        "cortex_processor=get_entity(",
        f"    {prototype_symbol(entity)},",
        f"    {_position(position)},",
        ")",
    ]


def _compile_configure_processing(operation: StructuralOperation) -> list[str]:
    processor = str(operation.parameters.get("processor") or "")
    product = str(operation.parameters.get("product") or "")
    if not processor or not product:
        raise ValueError("configure_processing requires processor and product")
    expected = f"cortex_expected_product={prototype_symbol(product)}"
    if processor in _FURNACE_PROTOTYPES:
        return [expected]
    return [
        "cortex_processor=set_entity_recipe(",
        "    cortex_processor,",
        f"    {prototype_symbol(product)},",
        ")",
        expected,
    ]


class _WorldFuelDrawUnsupported(RuntimeError):
    pass


def _compile_carried_fuel(
    operation: StructuralOperation,
    *,
    target_var: str,
    operation_name: str,
) -> list[str]:
    fuel_item = str(operation.parameters.get("fuel_item") or "")
    quantity = operation.parameters.get("quantity")
    if not isinstance(quantity, Real) or isinstance(quantity, bool):
        raise TypeError(f"{operation_name} quantity must be numeric")
    amount = int(quantity)
    if amount <= 0 or float(quantity) != float(amount):
        raise ValueError(
            f"{operation_name} quantity must be a positive integer"
        )

    supply_plan = operation.parameters.get("supply_plan")
    if not isinstance(supply_plan, Mapping):
        raise TypeError(f"{operation_name} requires supply_plan")
    draws = supply_plan.get("fuel_draws")
    if (
        isinstance(draws, Sequence)
        and not isinstance(draws, (str, bytes))
        and len(draws) > 0
    ):
        raise _WorldFuelDrawUnsupported(
            f"{operation_name} world draws require a provenance-preserving adapter"
        )

    symbol = prototype_symbol(fuel_item)
    return [
        f"{target_var}=insert_item(",
        f"    {symbol},",
        f"    {target_var},",
        f"    quantity={amount},",
        ")",
    ]


def _compile_fuel_processor(operation: StructuralOperation) -> list[str]:
    return _compile_carried_fuel(
        operation,
        target_var="cortex_processor",
        operation_name="fuel_processor",
    )


def _compile_fuel_delivery_actuator(
    operation: StructuralOperation,
) -> list[str]:
    return _compile_carried_fuel(
        operation,
        target_var="cortex_delivery",
        operation_name="fuel_delivery_actuator",
    )


def _compile_delivery(operation: StructuralOperation) -> list[str]:
    mode = str(operation.parameters.get("mode") or "")
    if mode != MODE_INSERTER:
        raise NotImplementedError(
            f"F2-E1 supports direct inserter delivery only, got {mode!r}"
        )
    delivery = operation.parameters.get("delivery")
    if not isinstance(delivery, Mapping):
        raise TypeError("connect_delivery requires delivery payload")
    lift = delivery.get("lift")
    if not isinstance(lift, Mapping):
        raise TypeError("direct inserter delivery requires lift")
    position = lift.get("position")
    if not isinstance(position, Mapping):
        raise TypeError("direct inserter lift requires position")
    entities = operation.parameters.get("entities")
    if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes)):
        raise TypeError("connect_delivery requires entity list")
    inserter = next((str(value) for value in entities if str(value)), "")
    if not inserter:
        raise ValueError("connect_delivery has no inserter prototype")
    symbol = prototype_symbol(inserter)
    return [
        f"if inspect_inventory()[{symbol}] < 1:",
        f"    craft_item({symbol}, quantity=1)",
        "cortex_delivery=place_entity(",
        f"    {symbol},",
        f"    position={_position(position)},",
        f"    direction={_direction(lift.get('direction'))},",
        ")",
    ]


def _compile_harvest_bootstrap_resources(
    operation: StructuralOperation,
) -> list[str]:
    resources = operation.parameters.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise TypeError("harvest_bootstrap_resources requires resources")
    lines: list[str] = []
    for index, raw in enumerate(resources):
        if not isinstance(raw, Mapping):
            raise TypeError("bootstrap resource row must be a mapping")
        resource_name = str(raw.get("resource") or "")
        quantity = raw.get("quantity")
        if (
            not isinstance(quantity, Real)
            or isinstance(quantity, bool)
            or int(quantity) <= 0
            or float(quantity) != float(int(quantity))
        ):
            raise ValueError("bootstrap harvest quantity must be a positive integer")
        amount = int(quantity)
        radius = raw.get("radius")
        radius_clause = ""
        if radius is not None:
            if (
                not isinstance(radius, Real)
                or isinstance(radius, bool)
                or float(radius) <= 0
            ):
                raise ValueError("bootstrap harvest radius must be positive")
            radius_clause = f", radius={float(radius)!r}"
        position_var = f"cortex_bootstrap_resource_{index}"
        lines.extend(
            (
                f"{position_var}=nearest({resource_symbol(resource_name)})",
                f"move_to({position_var})",
                "harvest_resource(",
                f"    {position_var},",
                f"    quantity={amount}{radius_clause},",
                ")",
            )
        )
    return lines


def _compile_bootstrap_smelt_iron(
    operation: StructuralOperation,
) -> list[str]:
    furnace_quantity = operation.parameters.get("furnace_quantity")
    iron_ore_quantity = operation.parameters.get("iron_ore_quantity")
    coal_quantity = operation.parameters.get("coal_quantity")
    settle_seconds = operation.parameters.get("settle_seconds")
    for label, value in (
        ("furnace_quantity", furnace_quantity),
        ("iron_ore_quantity", iron_ore_quantity),
        ("coal_quantity", coal_quantity),
        ("settle_seconds", settle_seconds),
    ):
        if (
            not isinstance(value, Real)
            or isinstance(value, bool)
            or int(value) <= 0
            or float(value) != float(int(value))
        ):
            raise ValueError(f"{label} must be a positive integer")
    furnaces = int(furnace_quantity)
    ore = int(iron_ore_quantity)
    coal = int(coal_quantity)
    settle = int(settle_seconds)
    return [
        f"craft_item({prototype_symbol('stone-furnace')}, quantity={furnaces})",
        "cortex_bootstrap_furnace=place_entity_next_to(",
        f"    {prototype_symbol('stone-furnace')},",
        "    player_location,",
        "    direction=Direction.RIGHT,",
        ")",
        "cortex_bootstrap_furnace=insert_item(",
        f"    {prototype_symbol('coal')},",
        "    cortex_bootstrap_furnace,",
        f"    quantity={coal},",
        ")",
        "cortex_bootstrap_furnace=insert_item(",
        f"    {prototype_symbol('iron-ore')},",
        "    cortex_bootstrap_furnace,",
        f"    quantity={ore},",
        ")",
        f"sleep({settle})",
        (
            "cortex_bootstrap_plate_count=inspect_inventory("
            "cortex_bootstrap_furnace)"
            f"[{prototype_symbol('iron-plate')}]"
        ),
        "if cortex_bootstrap_plate_count < 9:",
        "    raise RuntimeError('bootstrap smelting produced fewer than 9 iron plates')",
        "extract_item(",
        f"    {prototype_symbol('iron-plate')},",
        "    cortex_bootstrap_furnace,",
        "    quantity=cortex_bootstrap_plate_count,",
        ")",
        "pickup_entity(cortex_bootstrap_furnace)",
    ]


def _compile_craft_extraction_cell(
    operation: StructuralOperation,
) -> list[str]:
    extractor = str(operation.parameters.get("extractor") or "")
    buffer = str(operation.parameters.get("buffer") or "")
    if not extractor or not buffer:
        raise ValueError("craft_extraction_cell requires extractor and buffer")
    return [
        f"craft_item({prototype_symbol(extractor)}, quantity=1)",
        f"craft_item({prototype_symbol(buffer)}, quantity=1)",
    ]


def _compile_place_extractor(operation: StructuralOperation) -> list[str]:
    entity = str(operation.parameters.get("entity") or "")
    position = operation.parameters.get("position")
    direction = str(operation.parameters.get("direction") or "DOWN")
    if not isinstance(position, Mapping):
        raise TypeError("place_extractor requires position")
    return [
        f"move_to({_position(position)})",
        "cortex_extractor=place_entity(",
        f"    {prototype_symbol(entity)},",
        f"    position={_position(position)},",
        f"    direction={_direction(direction)},",
        ")",
    ]


def _compile_fuel_extractor(operation: StructuralOperation) -> list[str]:
    fuel_item = str(operation.parameters.get("fuel_item") or "")
    quantity = operation.parameters.get("quantity")
    if (
        not isinstance(quantity, Real)
        or isinstance(quantity, bool)
        or int(quantity) <= 0
        or float(quantity) != float(int(quantity))
    ):
        raise ValueError("fuel_extractor quantity must be a positive integer")
    return [
        "cortex_extractor=insert_item(",
        f"    {prototype_symbol(fuel_item)},",
        "    cortex_extractor,",
        f"    quantity={int(quantity)},",
        ")",
    ]


def _compile_place_output_buffer(operation: StructuralOperation) -> list[str]:
    entity = str(operation.parameters.get("entity") or "")
    direction = str(operation.parameters.get("direction") or "DOWN")
    if not entity:
        raise ValueError("place_output_buffer requires entity")
    return [
        "cortex_buffer=place_entity_next_to(",
        f"    {prototype_symbol(entity)},",
        "    cortex_extractor.position,",
        f"    direction={_direction(direction)},",
        ")",
    ]


def _compile_operation(operation: StructuralOperation) -> list[str]:
    dispatch = {
        "ensure_item": _compile_ensure_item,
        "place_processor": _compile_place_processor,
        "adopt_processor": _compile_adopt_processor,
        "configure_processing": _compile_configure_processing,
        "fuel_processor": _compile_fuel_processor,
        "connect_delivery": _compile_delivery,
        "fuel_delivery_actuator": _compile_fuel_delivery_actuator,
        "harvest_bootstrap_resources": _compile_harvest_bootstrap_resources,
        "bootstrap_smelt_iron": _compile_bootstrap_smelt_iron,
        "craft_extraction_cell": _compile_craft_extraction_cell,
        "place_extractor": _compile_place_extractor,
        "fuel_extractor": _compile_fuel_extractor,
        "place_output_buffer": _compile_place_output_buffer,
        "establish_coal_self_sufficiency": compile_coal_self_sufficiency,
        "establish_iron_smelting": compile_iron_smelting,
        "establish_steam_power": compile_steam_power,
        "establish_copper_chain": compile_copper_chain,
        "establish_automation_science": compile_automation_science,
        "recover_promoted_copper_furnace": compile_rollback_recovery,
        "recover_promoted_baseline": compile_rollback_recovery,
        "establish_powered_manufacturing": compile_powered_manufacturing,
    }
    if operation.op == "verify_postconditions":
        return []
    compiler = dispatch.get(operation.op)
    if compiler is None:
        raise NotImplementedError(
            f"unsupported structural operation {operation.op!r}"
        )
    return compiler(operation)


def compile_structural_action(
    prepared: PreparedStructuralAction,
    *,
    settle_seconds: int = DEFAULT_SETTLE_SECONDS,
) -> StructuralCompilationResult:
    if prepared.contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        return StructuralCompilationResult(
            prepared=prepared,
            refusal=Refusal(
                code=REFUSAL_CONTRACT_UNSUPPORTED,
                detail=(
                    "expected one of "
                    f"{sorted(SUPPORTED_CONTRACT_VERSIONS)!r}, got "
                    f"{prepared.contract_version}"
                ),
            ),
        )

    settle = int(settle_seconds)
    max_settle = (
        MAX_COAL_SELF_SUFFICIENCY_SECONDS
        if prepared.contract_version == COAL_SELF_SUFFICIENCY_CONTRACT_VERSION
        else (
            MAX_IRON_SMELTING_SECONDS
            if prepared.contract_version == IRON_SMELTING_CONTRACT_VERSION
            else (
                MAX_STEAM_POWER_SECONDS
                if prepared.contract_version == STEAM_POWER_CONTRACT_VERSION
                else (
                    MAX_COPPER_CHAIN_SECONDS
                    if prepared.contract_version == COPPER_CHAIN_CONTRACT_VERSION
                    else (
                        MAX_POWERED_MANUFACTURING_SECONDS
                        if prepared.contract_version
                        == POWERED_MANUFACTURING_CONTRACT_VERSION
                        else (
                            MAX_AUTOMATION_SCIENCE_SECONDS
                            if prepared.contract_version
                            == AUTOMATION_SCIENCE_CONTRACT_VERSION
                            else (
                                MAX_ROLLBACK_RECOVERY_SECONDS
                                if prepared.contract_version
                                == ROLLBACK_RECOVERY_CONTRACT_VERSION
                                else MAX_SETTLE_SECONDS
                            )
                        )
                    )
                )
            )
        )
    )
    if settle <= 0 or settle > max_settle:
        return StructuralCompilationResult(
            prepared=prepared,
            refusal=Refusal(
                code=REFUSAL_OPERATION_UNSUPPORTED,
                detail=(
                    f"settle_seconds must be within 1..{max_settle}, "
                    f"got {settle_seconds!r}"
                ),
            ),
        )

    final_validation_settle = settle
    if prepared.contract_version == RESOURCE_EXTRACTION_CONTRACT_VERSION:
        bootstrap_seconds = sum(
            int(operation.parameters.get("settle_seconds") or 0)
            for operation in prepared.operations
            if operation.op == "bootstrap_smelt_iron"
        )
        final_validation_settle = settle - bootstrap_seconds
        if final_validation_settle <= 0:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "resource extraction Option budget must exceed bootstrap "
                        f"settle time ({bootstrap_seconds}s)"
                    ),
                ),
            )

    if prepared.contract_version == IRON_SMELTING_CONTRACT_VERSION:
        smelting_ops = [
            operation
            for operation in prepared.operations
            if operation.op == "establish_iron_smelting"
        ]
        if len(smelting_ops) != 1:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "iron smelting contract requires exactly one "
                        "establish_iron_smelting operation"
                    ),
                ),
            )
        params=smelting_ops[0].parameters
        required_seconds=sum(
            int(params.get(key) or 0)
            for key in (
                "smelt_window_seconds",
                "survival_window_seconds",
            )
        )
        if settle < required_seconds:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "iron smelting Option budget must cover internal "
                        f"causal windows ({required_seconds}s)"
                    ),
                ),
            )

    if prepared.contract_version == STEAM_POWER_CONTRACT_VERSION:
        steam_ops = [
            operation
            for operation in prepared.operations
            if operation.op == "establish_steam_power"
        ]
        if len(steam_ops) != 1:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "steam power contract requires exactly one "
                        "establish_steam_power operation"
                    ),
                ),
            )
        params=steam_ops[0].parameters
        required_seconds=sum(
            int(params.get(key) or 0)
            for key in (
                "iron_trigger_window_seconds",
                "copper_trigger_window_seconds",
                "power_window_seconds",
                "survival_window_seconds",
            )
        )
        if settle < required_seconds:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "steam power Option budget must cover internal "
                        f"causal windows ({required_seconds}s)"
                    ),
                ),
            )

    if prepared.contract_version == AUTOMATION_SCIENCE_CONTRACT_VERSION:
        science_ops = [
            operation
            for operation in prepared.operations
            if operation.op == "establish_automation_science"
        ]
        if len(science_ops) != 1:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "automation science contract requires exactly one "
                        "establish_automation_science operation"
                    ),
                ),
            )
        params=science_ops[0].parameters
        required_seconds=sum(
            int(params.get(key) or 0)
            for key in (
                "iron_recovery_window_seconds",
                "iron_smelt_window_seconds",
                "copper_smelt_window_seconds",
                "batch_gap_seconds",
                "survival_recovery_window_seconds",
                "survival_window_seconds",
            )
        )
        if settle < required_seconds:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "automation science Option budget must cover internal "
                        f"causal windows ({required_seconds}s)"
                    ),
                ),
            )

    if prepared.contract_version == POWERED_MANUFACTURING_CONTRACT_VERSION:
        powered_ops = [
            operation
            for operation in prepared.operations
            if operation.op == "establish_powered_manufacturing"
        ]
        if len(powered_ops) != 1:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "powered manufacturing contract requires exactly one "
                        "establish_powered_manufacturing operation"
                    ),
                ),
            )
        params=powered_ops[0].parameters
        required_seconds=(
            int(params.get("coal_cycle_seconds") or 0)
            * int(params.get("coal_amplification_cycles") or 0)
            + sum(
                int(params.get(key) or 0)
                for key in (
                    "ore_recovery_window_seconds",
                    "smelt_window_seconds",
                    "research_window_seconds",
                    "manufacturing_window_seconds",
                    "survival_recovery_window_seconds",
                    "survival_window_seconds",
                )
            )
        )
        if settle < required_seconds:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "powered manufacturing Option budget must cover internal "
                        f"causal windows ({required_seconds}s)"
                    ),
                ),
            )

    if prepared.contract_version == ROLLBACK_RECOVERY_CONTRACT_VERSION:
        recovery_ops = [
            operation
            for operation in prepared.operations
            if operation.op == "recover_promoted_baseline"
        ]
        if len(recovery_ops) != 1:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "rollback recovery contract requires exactly one "
                        "recover_promoted_baseline operation"
                    ),
                ),
            )
        params=recovery_ops[0].parameters
        required_seconds=sum(
            int(params.get(key) or 0)
            for key in (
                "iron_smelt_seconds",
                "recovery_window_seconds",
                "copper_smelt_seconds",
                "settle_seconds",
            )
        )
        if settle < required_seconds:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "rollback recovery Option budget must cover internal "
                        f"causal windows ({required_seconds}s)"
                    ),
                ),
            )

    if prepared.contract_version == COPPER_CHAIN_CONTRACT_VERSION:
        copper_ops = [
            operation
            for operation in prepared.operations
            if operation.op == "establish_copper_chain"
        ]
        if len(copper_ops) != 1:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "copper chain contract requires exactly one "
                        "establish_copper_chain operation"
                    ),
                ),
            )
        params=copper_ops[0].parameters
        required_seconds=sum(
            int(params.get(key) or 0)
            for key in (
                "iron_recovery_window_seconds",
                "iron_smelt_window_seconds",
                "copper_extract_window_seconds",
                "copper_smelt_window_seconds",
                "survival_recovery_window_seconds",
                "survival_window_seconds",
            )
        )
        if settle < required_seconds:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "copper chain Option budget must cover internal "
                        f"causal windows ({required_seconds}s)"
                    ),
                ),
            )

    if prepared.contract_version == COAL_SELF_SUFFICIENCY_CONTRACT_VERSION:
        coal_ops = [
            operation
            for operation in prepared.operations
            if operation.op == "establish_coal_self_sufficiency"
        ]
        if len(coal_ops) != 1:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "coal self-sufficiency contract requires exactly one "
                        "establish_coal_self_sufficiency operation"
                    ),
                ),
            )
        params = coal_ops[0].parameters
        required_seconds = sum(
            int(params.get(key) or 0)
            for key in (
                "bootstrap_smelt_seconds",
                "seed_window_seconds",
                "endogenous_window_seconds",
            )
        )
        if settle < required_seconds:
            return StructuralCompilationResult(
                prepared=prepared,
                refusal=Refusal(
                    code=REFUSAL_OPERATION_UNSUPPORTED,
                    detail=(
                        "coal self-sufficiency Option budget must cover internal "
                        f"causal windows ({required_seconds}s)"
                    ),
                ),
            )

    lines = [
        "# Cortex F2-E controlled structural transaction",
        f"# action_id={prepared.action_id}",
    ]
    try:
        for operation in prepared.operations:
            lines.extend(_compile_operation(operation))
    except _WorldFuelDrawUnsupported as exc:
        return StructuralCompilationResult(
            prepared=prepared,
            refusal=Refusal(
                code=REFUSAL_WORLD_FUEL_DRAW_UNSUPPORTED,
                detail=str(exc),
            ),
        )
    except NotImplementedError as exc:
        return StructuralCompilationResult(
            prepared=prepared,
            refusal=Refusal(
                code=REFUSAL_OPERATION_UNSUPPORTED,
                detail=str(exc),
            ),
        )
    except (TypeError, ValueError) as exc:
        refusal_code = (
            REFUSAL_PROTOTYPE_UNRESOLVED
            if "Prototype member" in str(exc)
            else REFUSAL_OPERATION_UNSUPPORTED
        )
        return StructuralCompilationResult(
            prepared=prepared,
            refusal=Refusal(code=refusal_code, detail=str(exc)),
        )

    if prepared.contract_version == RESOURCE_EXTRACTION_CONTRACT_VERSION:
        lines.extend(
            (
                f"sleep({final_validation_settle})",
                (
                    "cortex_buffer_iron_ore=inspect_inventory(cortex_buffer)"
                    f"[{prototype_symbol('iron-ore')}]"
                ),
                (
                    "cortex_extractor_fuel=inspect_inventory(cortex_extractor)"
                    f"[{prototype_symbol('coal')}]"
                ),
                "cortex_extractor_exists=cortex_extractor is not None",
                "cortex_destination_reachable=cortex_buffer is not None",
                "cortex_drill_operational=(cortex_buffer_iron_ore > 0)",
                "cortex_production_positive=(cortex_buffer_iron_ore > 0)",
                (
                    "print({'cortex_buffer_iron_ore':cortex_buffer_iron_ore,"
                    "'cortex_extractor_fuel':cortex_extractor_fuel,"
                    "'cortex_drill_operational':cortex_drill_operational})"
                ),
            )
        )
    elif prepared.contract_version in {
        COAL_SELF_SUFFICIENCY_CONTRACT_VERSION,
        IRON_SMELTING_CONTRACT_VERSION,
        STEAM_POWER_CONTRACT_VERSION,
        COPPER_CHAIN_CONTRACT_VERSION,
        AUTOMATION_SCIENCE_CONTRACT_VERSION,
        ROLLBACK_RECOVERY_CONTRACT_VERSION,
    }:
        # Specialized contracts contain their own causal validation windows.
        pass
    else:
        lines.extend(
            (
                f"sleep({settle})",
                (
                    "cortex_processor_output=inspect_inventory("
                    "cortex_processor)[cortex_expected_product]"
                ),
                "print({'cortex_processor_output':cortex_processor_output})",
            )
        )
    return StructuralCompilationResult(
        prepared=prepared,
        compiled=CompiledStructuralAction(
            action_id=prepared.action_id,
            contract_version=prepared.contract_version,
            purpose=prepared.purpose,
            code="\n".join(lines) + "\n",
            operation_names=tuple(op.op for op in prepared.operations),
            settle_seconds=settle,
        ),
    )


def _evidence(raw: object) -> tuple[EvidenceRef, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    refs: list[EvidenceRef] = []
    for value in raw:
        if not isinstance(value, Mapping):
            continue
        try:
            status = EvidenceStatus(str(value.get("status")))
        except ValueError:
            continue
        refs.append(
            EvidenceRef(
                source=str(value.get("source") or "prepared_action"),
                path=str(value.get("path") or "unknown"),
                status=status,
                reason=(
                    None
                    if value.get("reason") is None
                    else str(value.get("reason"))
                ),
                digest=(
                    None
                    if value.get("digest") is None
                    else str(value.get("digest"))
                ),
            )
        )
    return tuple(refs)


def _condition_from_dict(raw: Mapping[str, Any]) -> ActionCondition:
    return ActionCondition(
        name=str(raw.get("name") or ""),
        operator=ConditionOperator(str(raw.get("operator"))),
        state=ConditionState(str(raw.get("state") or "unknown")),
        expected=raw.get("expected"),
        hard=bool(raw.get("hard", True)),
        evidence=_evidence(raw.get("evidence")),
    )


def prepared_postconditions(
    prepared: PreparedStructuralAction,
) -> tuple[ActionCondition, ...]:
    verify = [
        operation
        for operation in prepared.operations
        if operation.op == "verify_postconditions"
    ]
    if len(verify) != 1:
        raise ValueError(
            "prepared action must contain exactly one verify_postconditions operation"
        )
    raw_conditions = verify[0].parameters.get("conditions")
    if not isinstance(raw_conditions, Sequence) or isinstance(
        raw_conditions, (str, bytes)
    ):
        raise TypeError("verify_postconditions.conditions must be a sequence")
    conditions = tuple(
        _condition_from_dict(value)
        for value in raw_conditions
        if isinstance(value, Mapping)
    )
    if not conditions:
        raise ValueError("prepared action has no postconditions")
    return conditions


def execution_guard_conditions(
    prepared: PreparedStructuralAction | None = None,
) -> tuple[ActionCondition, ...]:
    """Hard guards that prevent topological false-positive commits."""

    if (
        prepared is not None
        and prepared.contract_version == RESOURCE_EXTRACTION_CONTRACT_VERSION
    ):
        return (
            ActionCondition(
                name="extractor_exists",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=True,
            ),
            ActionCondition(
                name="buffer_iron_ore",
                operator=ConditionOperator.INCREASE,
                state=ConditionState.UNKNOWN,
                expected=None,
                hard=True,
            ),
        )
    if (
        prepared is not None
        and prepared.contract_version == COAL_SELF_SUFFICIENCY_CONTRACT_VERSION
    ):
        return (
            ActionCondition(
                name="coal_extractor_exists",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=True,
            ),
            ActionCondition(
                name="coal_endogenous_growth",
                operator=ConditionOperator.INCREASE,
                state=ConditionState.UNKNOWN,
                expected=None,
                hard=True,
            ),
            ActionCondition(
                name="incumbent_iron_survives",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=True,
            ),
            ActionCondition(
                name="incumbent_iron_buffer_growth",
                operator=ConditionOperator.INCREASE,
                state=ConditionState.UNKNOWN,
                expected=None,
                hard=True,
            ),
        )
    if (
        prepared is not None
        and prepared.contract_version == IRON_SMELTING_CONTRACT_VERSION
    ):
        return (
            ActionCondition(
                name="iron_furnace_exists",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=True,
            ),
            ActionCondition(
                name="iron_plate_count",
                operator=ConditionOperator.INCREASE,
                state=ConditionState.UNKNOWN,
                expected=None,
                hard=True,
            ),
        )
    if (
        prepared is not None
        and prepared.contract_version == STEAM_POWER_CONTRACT_VERSION
    ):
        return (
            ActionCondition(
                name="steam_engine_exists",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=True,
            ),
        )
    if (
        prepared is not None
        and prepared.contract_version in {
            AUTOMATION_SCIENCE_CONTRACT_VERSION,
            POWERED_MANUFACTURING_CONTRACT_VERSION,
            ROLLBACK_RECOVERY_CONTRACT_VERSION,
        }
    ):
        return ()
    if (
        prepared is not None
        and prepared.contract_version == COPPER_CHAIN_CONTRACT_VERSION
    ):
        return (
            ActionCondition(
                name="copper_extractor_exists",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=True,
            ),
            ActionCondition(
                name="copper_furnace_exists",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=True,
            ),
        )
    return (
        ActionCondition(
            name="processor_exists",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
        ActionCondition(
            name="processor_output",
            operator=ConditionOperator.INCREASE,
            state=ConditionState.UNKNOWN,
            expected=None,
            hard=True,
        ),
    )


def _measurement(
    values: Mapping[str, Any],
    condition_name: str,
) -> tuple[bool, Any]:
    if condition_name in values:
        return True, values[condition_name]
    tail = condition_name.rsplit(".", 1)[-1]
    return (tail in values, values.get(tail))


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return float(value)


def _evaluate_condition(
    condition: ActionCondition,
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> ActionCondition:
    before_known, before_value = _measurement(before, condition.name)
    after_known, after_value = _measurement(after, condition.name)
    state = ConditionState.UNKNOWN
    op = condition.operator

    if op is ConditionOperator.EXISTS:
        if after_known:
            state = (
                ConditionState.SATISFIED
                if after_value is not None
                else ConditionState.UNSATISFIED
            )
    elif op is ConditionOperator.EQUALS:
        if after_known:
            state = (
                ConditionState.SATISFIED
                if after_value == condition.expected
                else ConditionState.UNSATISFIED
            )
    elif op in {ConditionOperator.AT_LEAST, ConditionOperator.AT_MOST}:
        actual = _numeric(after_value) if after_known else None
        expected = _numeric(condition.expected)
        if actual is not None and expected is not None:
            ok = (
                actual >= expected
                if op is ConditionOperator.AT_LEAST
                else actual <= expected
            )
            state = ConditionState.SATISFIED if ok else ConditionState.UNSATISFIED
    elif op in {ConditionOperator.INCREASE, ConditionOperator.DECREASE}:
        old = _numeric(before_value) if before_known else None
        new = _numeric(after_value) if after_known else None
        if old is not None and new is not None:
            ok = new > old if op is ConditionOperator.INCREASE else new < old
            state = ConditionState.SATISFIED if ok else ConditionState.UNSATISFIED
    elif op is ConditionOperator.UNCHANGED and before_known and after_known:
        state = (
            ConditionState.SATISFIED
            if after_value == before_value
            else ConditionState.UNSATISFIED
        )

    return ActionCondition(
        name=condition.name,
        operator=condition.operator,
        state=state,
        expected=condition.expected,
        hard=condition.hard,
        evidence=condition.evidence,
    )


def _refused(
    prepared: PreparedStructuralAction,
    *,
    authority: ActionAuthority,
    code: str,
    detail: str,
    retriable: bool = False,
) -> ActionResult:
    return ActionResult(
        action_id=prepared.action_id,
        family=prepared.family,
        intent=prepared.intent,
        status=ActionStatus.REFUSED,
        authority=authority,
        changed_world=False,
        binding=prepared.binding,
        refusal=Refusal(
            code=code,
            detail=detail,
            retriable=retriable,
        ),
    )


class StructuralTransactionalAdapter:
    """Execute one prepared action under one explicit authority grant."""

    def execute(
        self,
        prepared: PreparedStructuralAction,
        *,
        authority: ActionAuthority,
        executor: Any,
        measure: MeasurementProbe,
        use_checkpoint_for_action: bool = True,
        settle_seconds: int = DEFAULT_SETTLE_SECONDS,
    ) -> ActionResult:
        if authority is not ActionAuthority.EXECUTE:
            return _refused(
                prepared,
                authority=authority,
                code=REFUSAL_EXECUTE_AUTHORITY_REQUIRED,
                detail="structural transaction requires explicit EXECUTE authority",
            )

        compilation = compile_structural_action(
            prepared,
            settle_seconds=settle_seconds,
        )
        if not compilation.ready or compilation.compiled is None:
            refusal = compilation.refusal
            return _refused(
                prepared,
                authority=authority,
                code=(
                    REFUSAL_OPERATION_UNSUPPORTED
                    if refusal is None
                    else refusal.code
                ),
                detail=(
                    "structural compilation failed"
                    if refusal is None
                    else refusal.detail
                ),
                retriable=False if refusal is None else refusal.retriable,
            )

        try:
            contract = (
                prepared_postconditions(prepared)
                + execution_guard_conditions(prepared)
            )
        except (TypeError, ValueError) as exc:
            return _refused(
                prepared,
                authority=authority,
                code=REFUSAL_POSTCONDITION_CONTRACT,
                detail=str(exc),
            )

        try:
            before = dict(measure(prepared))
        except (
            AttributeError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            return _refused(
                prepared,
                authority=authority,
                code=REFUSAL_MEASUREMENT_FAILED,
                detail=(
                    f"pre-action measurement failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                retriable=True,
            )

        candidate_after: dict[str, Any] = {}
        evaluated: tuple[ActionCondition, ...] = contract
        measurement_error: str | None = None
        transaction_error: str | None = None
        transaction_result_excerpt: str | None = None

        def accept(step: Any) -> bool:
            nonlocal candidate_after
            nonlocal evaluated
            nonlocal measurement_error
            nonlocal transaction_error
            nonlocal transaction_result_excerpt

            info = getattr(step, "info", {})
            if bool(info.get("error_occurred")):
                transaction_error = "FLE step reported error_occurred"
                raw_result=info.get("result")
                if isinstance(raw_result,str) and raw_result.strip():
                    transaction_result_excerpt=raw_result.strip()[-4000:]
                return False
            if getattr(step, "candidate_game_state", None) is None:
                transaction_error = "FLE step returned no candidate_game_state"
                return False
            try:
                candidate_after = dict(measure(prepared))
            except (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                measurement_error = f"{type(exc).__name__}: {exc}"
                return False

            evaluated = tuple(
                _evaluate_condition(
                    condition,
                    before=before,
                    after=candidate_after,
                )
                for condition in contract
            )
            return all(
                not condition.hard or condition.satisfied
                for condition in evaluated
            )

        step = executor.execute(
            compilation.compiled.code,
            accept=accept,
            use_checkpoint_for_action=use_checkpoint_for_action,
            purpose=compilation.compiled.purpose,
        )

        step_info = getattr(step, "info", {})
        raw_step_ticks = (
            step_info.get("ticks")
            if isinstance(step_info, dict)
            else None
        )
        executor_step_ticks = (
            int(raw_step_ticks)
            if isinstance(raw_step_ticks, (int, float))
            and not isinstance(raw_step_ticks, bool)
            and raw_step_ticks >= 0
            else None
        )
        import hashlib

        measurements: dict[str, Any] = {
            "before": before,
            "candidate_after": candidate_after,
            "transaction_accepted": bool(step.accepted),
            "contract_version": compilation.compiled.contract_version,
            "operation_names": list(compilation.compiled.operation_names),
            "settle_seconds": compilation.compiled.settle_seconds,
            "checkpoint_used": bool(use_checkpoint_for_action),
            "executor_step_ticks": executor_step_ticks,
            "compiled_code_sha256":hashlib.sha256(
                compilation.compiled.code.encode("utf-8")
            ).hexdigest(),
        }
        if measurement_error is not None:
            measurements["measurement_error"] = measurement_error
        if transaction_error is not None:
            measurements["transaction_error"] = transaction_error
        if transaction_result_excerpt is not None:
            measurements["transaction_result_excerpt"]=transaction_result_excerpt

        if step.accepted:
            return ActionResult(
                action_id=prepared.action_id,
                family=prepared.family,
                intent=prepared.intent,
                status=ActionStatus.ACCEPTED,
                authority=authority,
                changed_world=True,
                binding=prepared.binding,
                postconditions=evaluated,
                measurements=measurements,
            )

        failed = [
            condition.name
            for condition in evaluated
            if condition.hard and not condition.satisfied
        ]
        detail = "candidate transaction rejected"
        if failed:
            detail += "; hard postconditions not satisfied: " + ", ".join(failed)
        if measurement_error is not None:
            detail += f"; measurement failed: {measurement_error}"
        if transaction_error is not None:
            detail += f"; transaction failed: {transaction_error}"

        if transaction_error is not None:
            refusal_code = REFUSAL_TRANSACTION_FAILED
        elif measurement_error is not None:
            refusal_code = REFUSAL_MEASUREMENT_FAILED
        else:
            refusal_code = REFUSAL_POSTCONDITION_FAILED

        return ActionResult(
            action_id=prepared.action_id,
            family=prepared.family,
            intent=prepared.intent,
            status=ActionStatus.REJECTED,
            authority=authority,
            changed_world=False,
            binding=prepared.binding,
            refusal=Refusal(
                code=refusal_code,
                detail=detail,
                retriable=True,
            ),
            postconditions=evaluated,
            measurements=measurements,
        )
