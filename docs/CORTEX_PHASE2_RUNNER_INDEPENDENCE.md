# Cortex Research — F2-G1 Runner Independence

**Phase:** F2 — Action/Option Ontology and Universal Executor
**Checkpoint:** F2-G1 — generic runtime instrumentation / runner decoupling
**Status:** PASS parcial de F2-G1 — implementation published
**Authority:** SHADOW; no new live authority
**Previous live evidence:** F2-F4C functional accept, bounded operation

## 1. Objective

F2-F4C proved that the typed Cortex path can establish a real processing chain and commit only after measured output. It still failed the literal F2 exit gate because the one-shot Cortex canary imported a private runtime helper from `experiments.curriculum_runner.py`.

F2-G1 removes that dependency without changing footprint semantics or baseline behavior.

Research/engineering question:

> Can runtime footprint observation become a generic read-only instrumentation capability shared by Cortex and the legacy baseline, while keeping legacy behavior equivalent?

## 2. Canonical instrumentation

A new read-only instrumentation package owns live footprint observation:

`factorio_ai_lab.instrumentation.runtime`

Public capability:

`runtime_entity_footprints(instance) -> dict[str, tuple[int, int]]`

The function:

- reuses the canonical dashboard entity-prototype RCON command;
- parses the payload through `planning.footprints.prototype_footprints`;
- returns only plain measured footprint facts;
- never mutates the Factorio world;
- degrades to `{}` for absent/malformed/unavailable runtime responses;
- leaves snapshot/static fallback semantics to the existing planning layer.

## 3. Legacy compatibility

`curriculum_runner._runtime_entity_footprints()` remains as a compatibility wrapper only:

`return runtime_entity_footprints(instance)`

The legacy baseline keeps its behavior while no longer owning the instrumentation implementation.

No runner-specific semantics are allowed in the generic instrumentation package.

## 4. Cortex independence invariant

`scripts/run_cortex_structural_canary.py` imports the generic read-only instrument directly.

It no longer imports:

`factorio_ai_lab.experiments.curriculum_runner`

A source-level regression test enforces this boundary so future changes cannot silently reintroduce the dependency.

## 5. Measurement/fallback semantics

Footprint resolution remains layered:

1. live prototype footprint measurement;
2. serialized entity `tile_dimensions` when present;
3. static Factorio 2.0 fallback table;
4. 1x1 only when all stronger evidence is absent.

F2-G1 does not change pathfinding cost, placement policy or routing authority. It relocates only the runtime measurement boundary.

## 6. Failure semantics

Runtime instrumentation is best-effort because placement already has deterministic fallback data.

Failures including unavailable RCON, empty response, malformed JSON or missing `rcon_client` return an empty runtime map. They do not invent dimensions.

The caller then continues through the already-versioned fallback hierarchy.

## 7. Tests

Focused F2-G1 coverage proves:

- valid live runtime footprint payload;
- canonical dashboard prototype command reuse;
- empty response handling;
- malformed JSON handling;
- RCON failure handling;
- missing `rcon_client` handling;
- exact footprint fallback behavior;
- A* still avoids occupied machine tiles;
- legacy wrapper parity;
- source-level Cortex canary independence from `curriculum_runner`.

Focused independence subset: 37 PASS; combined F2-G1 continuity/dashboard gate: 64 PASS.

Full gate F2-G1:

- core/FLE: 1403 PASS;
- ML/PyTorch: 2 PASS;
- Ruff: PASS;
- compileall: PASS;
- dashboard JavaScript syntax: PASS;
- frontend TypeScript/Vite build: PASS;
- whitespace/diff check: PASS.

No new Factorio mutation or authority was introduced by F2-G1.

Implementation commit: `f1680811c2bee828c435cbf66c6c03aaf60156e6` — `feat: desacopla instrumentação Cortex do runner legado`.

Full-gate environment note: the first invocation failed only at compileall because a generated instrumentation/__pycache__ directory had root ownership from an earlier administrative compile. The cache was removed and regenerated as user ti; the complete gate then passed. No source behavior changed in that remediation.

**F2-G1 closure status: PASS parcial de F2. F2-G2 authorized in SHADOW/replay only.**


## 8. Scientific continuity

F2-G1 does not alter F2-F4C evidence:

- functional_accept=true;
- processor_output=13;
- transaction_committed=true;
- sustained_operation=false/not proven;
- final processor_status=no_fuel;
- confirmatory holdout untouched.

The F2-F4C temporal diagnosis also remains open: nominal `settle_seconds` and actual unpaused simulation horizon are not the same quantity.

## 9. What F2-G1 does not prove

F2-G1 does not yet prove:

- an initial temporally extended Option;
- generic option execution;
- a full functional-chain integration test independent of all curriculum-runner code;
- legacy runner baseline-only enforcement;
- sustained production;
- continuous autonomous authority.

Therefore none of the four original F2 closure checkboxes is closed by F2-G1 alone.

## 10. Mechanical continuity

`cortex_phase_state.py` recognizes F2-G1 from this document.

The dashboard shows:

- current phase: F2-G1 runner independence · SHADOW;
- last live evidence: F2-F4C functional accept;
- sustainability warning remains visible;
- no new live mutation is implied by the F2-G1 checkpoint.

## 11. Decision

**PASS parcial de F2-G.** One concrete Cortex -> legacy-runner dependency has been removed and guarded by tests.

F2 remains open.

## 12. Next — F2-G2

Formalize the first temporally extended typed Option for establishing a functional processing chain.

The Option must:

1. represent preconditions, child actions/options, predicted effects and termination conditions;
2. compose existing structural, processor-fuel and delivery-actuator dependencies instead of duplicating them;
3. carry explicit authority/provenance through the generic executor boundary;
4. define an option-level time/energy budget based on effective ticks, not only `sleep()`;
5. expose success/failure evidence suitable for replay and later policy learning;
6. remain in SHADOW/replay before any new live canary.

F3 remains blocked.
