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

from fle.env.game_types import Prototype

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
from factorio_ai_lab.cortex.structural_prepare import (
    CONTRACT_VERSION,
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

DEFAULT_SETTLE_SECONDS = 8
MAX_SETTLE_SECONDS = 60

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


def _compile_operation(operation: StructuralOperation) -> list[str]:
    dispatch = {
        "ensure_item": _compile_ensure_item,
        "place_processor": _compile_place_processor,
        "adopt_processor": _compile_adopt_processor,
        "configure_processing": _compile_configure_processing,
        "connect_delivery": _compile_delivery,
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
    if prepared.contract_version != CONTRACT_VERSION:
        return StructuralCompilationResult(
            prepared=prepared,
            refusal=Refusal(
                code=REFUSAL_CONTRACT_UNSUPPORTED,
                detail=(
                    f"expected {CONTRACT_VERSION}, got "
                    f"{prepared.contract_version}"
                ),
            ),
        )

    settle = int(settle_seconds)
    if settle <= 0 or settle > MAX_SETTLE_SECONDS:
        return StructuralCompilationResult(
            prepared=prepared,
            refusal=Refusal(
                code=REFUSAL_OPERATION_UNSUPPORTED,
                detail=(
                    f"settle_seconds must be within 1..{MAX_SETTLE_SECONDS}, "
                    f"got {settle_seconds!r}"
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


def execution_guard_conditions() -> tuple[ActionCondition, ...]:
    """Hard guards that prevent topological false-positive commits."""

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
                + execution_guard_conditions()
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

        def accept(step: Any) -> bool:
            nonlocal candidate_after
            nonlocal evaluated
            nonlocal measurement_error
            nonlocal transaction_error

            info = getattr(step, "info", {})
            if bool(info.get("error_occurred")):
                transaction_error = "FLE step reported error_occurred"
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

        measurements: dict[str, Any] = {
            "before": before,
            "candidate_after": candidate_after,
            "transaction_accepted": bool(step.accepted),
            "contract_version": compilation.compiled.contract_version,
            "operation_names": list(compilation.compiled.operation_names),
            "settle_seconds": compilation.compiled.settle_seconds,
        }
        if measurement_error is not None:
            measurements["measurement_error"] = measurement_error
        if transaction_error is not None:
            measurements["transaction_error"] = transaction_error

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
