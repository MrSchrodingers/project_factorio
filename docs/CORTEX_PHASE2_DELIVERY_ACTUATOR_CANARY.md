# Cortex Research — F2-F4C Functional Delivery Canary

**Phase:** F2
**Checkpoint:** F2-F4C — accepted functional chain with typed processor + actuator energy dependencies
**Seed:** 424242
**Source commit:** `20aac7f8eb0c3b71c8017b632892f37624b79fd0`
**Artifact:** `runs/audits/cortex_f2f4c_structural_canary.json`
**SHA-256:** `2c074e8ec312a5119c59487ee25acae5f1cab7f2ca7e2a2c95cedffada955d37`
**Authority:** one-shot EXECUTE only
**Continuous authority:** false

## 1. Result

The F2-F4C one-shot canary completed successfully and committed the candidate world.

Observed transition:

- producers_reaching_processor: 0 -> 1;
- physical_processing_coverage: 0.0 -> 1.0;
- processor_exists: false -> true;
- processor_output: 0.0 -> 13.0;
- action_status: accepted;
- transaction_committed: true;
- rollback_observed: false.

All hard postconditions were satisfied:

- producers_reaching_processor INCREASE;
- processor_exists EQUALS true;
- processor_output INCREASE.

## 2. Delivery actuator dependency

Power capability was derived unavailable from the preserved canary fixture contract plus observed `power_edge_count=0`.

Actuator evaluation:

- electric `inserter`: carried=50, rejected because power unavailable;
- `bulk-inserter`: not carried;
- `burner-inserter`: carried=50, burner energy measured, selected;
- actuator fuel: coal;
- actuator fuel units: 1;
- actuator dependency ready=true.

The compiled v3 operation sequence was:

`ensure_item -> place_processor -> configure_processing -> fuel_processor -> connect_delivery -> fuel_delivery_actuator -> verify_postconditions`

## 3. Processor dependency

The stone-furnace dependency was also composed generically:

- source type: burner;
- fuel category: chemical;
- selected carried fuel: coal;
- fuel units: 1 for the bounded 10 s horizon.

## 4. Functional conclusion

F2-F4C proves that the typed Cortex path can:

1. observe processor and actuator energy requirements;
2. select compatible carried fuel;
3. choose an actuator based on measured energy capability rather than a stage-specific name;
4. compile a v3 action;
5. execute through TransactionalFLEExecutor;
6. measure causal postconditions;
7. commit only after functional output is observed.

This is a real functional accept, not a topological proxy.

## 5. Sustainability limitation

The final observed processor status was `no_fuel` after producing 13 iron plates.

Therefore F2-F4C proves **bounded functional operation**, not sustained autonomous operation.

The correct state is:

- functional_accept = true;
- sustained_operation = false / not proven;
- sustainability classification = functional_accept_terminal_no_fuel.

The output gate must not be weakened, and the result must not be reported as continuous production.

## 6. Safety and provenance

- seed 424242 is outside confirmatory holdout;
- confirmatory seeds 20261101–20261110 remain untouched;
- source tree was clean;
- source commit matched origin;
- F1 runtime remained frozen at `95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac`;
- evolution remained inactive+disabled;
- FactorioWorldLease serialized the experimental writer;
- continuous authority remained disabled.

A concurrent duplicate attempt was blocked by FactorioWorldLease before mutation because the authorized F2-F4C writer was already active. No second canary was executed.

## 7. F2 is not yet complete

The original F2 checklist still contains open structural requirements:

- transactional execution universal;
- initial options;
- a functional-chain test independent of `curriculum_runner`;
- legacy runner constrained to baseline-only status.

The current one-shot canary still imports `_runtime_entity_footprints` from `curriculum_runner`, so the literal F2 exit gate is not yet satisfied.

## 8. Next checkpoint — F2-G

F2-G must close the ontology/executor boundary rather than add another special-case dependency.

Required work:

1. move runtime footprint access behind a generic planning/instrumentation interface;
2. remove Cortex canary imports from `curriculum_runner`;
3. formalize one initial temporally extended option for functional processing-chain establishment;
4. execute that option through the generic typed executor boundary;
5. add an integration test that builds a functional chain without importing or dispatching through `curriculum_runner`;
6. keep the legacy runner available only as baseline/reference infrastructure;
7. retain explicit authority, provenance, postconditions and rollback;
8. do not grant continuous autonomous authority.

F3 remains blocked until the F2 Exit Gate is satisfied.

## 9. Closure gate

Final F2-F4C closure validation:

- focused phase/dashboard continuity: 41 PASS;
- core/FLE full profile: 1401 PASS;
- ML/PyTorch profile: 2 PASS;
- Ruff: PASS;
- compileall: PASS;
- dashboard JavaScript syntax: PASS;
- frontend TypeScript/Vite build: PASS;
- whitespace/diff check: PASS;
- scientific F1 runtime unchanged;
- evolution inactive+disabled.

Mechanical phase-state/UI publication:

`01c4390d1668631722dcef8d33c980e7e704247e` — `feat: registra aceite funcional F2-F4C`.

The mechanical state exposes both:

- classification = functional_accept;
- sustained_operation = false;
- sustainability_classification = functional_accept_terminal_no_fuel.

This prevents a green functional result from being misreported as sustained autonomous production.
