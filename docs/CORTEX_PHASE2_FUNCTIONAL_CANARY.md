# Cortex Research — F2-F3 Functional Canary

**Phase:** F2
**Checkpoint:** F2-F3 — dependency-complete structural canary
**Authority:** one-shot EXECUTE only
**Seed:** 424242
**Confirmatory holdout:** 20261101–20261110 untouched
**Runner commit:** e1aad03dafa8604a002ca11641f0000b69cbefa0

## 1. Objective

F2-E2 established that the structural transaction could create the expected topology but failed
functionally because the stone-furnace had no fuel.

F2-F2 then promoted fuel from an executor side-effect into a measured typed dependency.

F2-F3 tests whether the dependency-completed v2 option is now sufficient to satisfy the unchanged
functional gate:

    processor_output INCREASE

No criterion is relaxed relative to F2-E2.

## 2. Valid capability run

The scientifically valid F2-F3 run is:

    run_id = cortex-f2f-20260924T200043Z
    code_revision = e1aad03dafa8604a002ca11641f0000b69cbefa0
    dirty = false
    seed = 424242

Artifact:

    runs/audits/cortex_f2f_structural_canary.json

Preserved copy:

    runs/audits/cortex_f2f_structural_canary_attempt1_rejected_no_ingredients.json

SHA-256:

    4ff12bcaa22b7544fa3dbf30281d60e0c893df05e1eeca24253d8f897ae77e86

The bootstrap succeeded:

- accepted = true;
- iron_buffered = 1;
- graph_before node_count = 3;
- categories = agent + extraction + buffer;
- power_edge_count = 0;
- producers_reaching_buffer = 1;
- producers_reaching_processor = 0.

Therefore this run reached the capability under test.

## 3. Fuel dependency result

FunctionalDependency completed successfully.

Measured processor energy:

- machine = stone-furnace;
- source_type = burner;
- energy_usage_per_tick_j = 1500;
- fuel_categories = chemical.

Candidate evaluation:

- nuclear-fuel: compatible but unavailable;
- rocket-fuel: compatible but unavailable;
- solid-fuel: compatible but unavailable;
- coal: compatible and carried.

Selected dependency:

- fuel = coal;
- units_needed = 1;
- fuel_carried = 480;
- carried_only = true;
- world fuel draws = none;
- refusals = none.

This is direct evidence that F2-F2 corrected the previous no_fuel dependency.

## 4. Candidate transaction

Before:

- producers_reaching_processor = 0;
- physical_processing_coverage = 0.0;
- processor_exists = false;
- processor_output = 0.0.

Candidate after EXECUTE:

- producers_reaching_processor = 1;
- physical_processing_coverage = 1.0;
- processor_exists = true;
- processor_status = no_ingredients;
- processor_output = 0.0.

Hard postconditions:

- producers_reaching_processor INCREASE: satisfied;
- processor_exists == true: satisfied;
- processor_output INCREASE: unsatisfied.

ActionResult:

- status = rejected;
- refusal = structural_postcondition_failed;
- transaction_committed = false;
- changed_world = false;
- rollback_observed = true.

Final measurement returned exactly to the pre-transaction state.

## 5. Scientific interpretation

F2-F3 demonstrates two things simultaneously.

First, F2-F2 worked for the dependency it was designed to solve: the furnace no longer failed with
no_fuel.

Second, the structural option is still not functionally complete: material never reached the
processor during the observation window, yielding no_ingredients.

Therefore F2-F3 is a valid negative capability result, not an infrastructure failure.

The output gate remains unchanged.

## 6. Delivery actuator evidence

The prepared delivery operation selected:

    entity = inserter
    mode = inserter

The valid pre-action graph contained no power edges and the bootstrap fixture only created a
burner mining drill and wooden chest.

A read-only runtime prototype probe against Factorio 2.0.73 measured:

| prototype | source type | energy usage |
| --- | --- | ---: |
| inserter | electric | 245 J/tick |
| burner-inserter | burner | 2400 J/tick |

Probe artifact:

    runs/audits/cortex_f2f_delivery_actuator_probe.json

SHA-256:

    f1fc7a0ca3efa3d745e5a9f3817542b7df1a7ea622af4932d653344a00e9c0ad

No prepared operation established electric power for the selected inserter.

This makes missing delivery-actuator energy the leading causal explanation for no_ingredients.
It is supported by measured prototype energy, absence of power topology, and the observed processor
state. F2-F4 must represent and test that dependency explicitly rather than hardcode a different
inserter.

## 7. Duplicate invalid run during stream recovery

A second invocation occurred later during connection/stream recovery:

    run_id = cortex-f2f-20260924T200238Z
    started_at = 20:02:38Z
    status = failed
    failure = producer+buffer bootstrap was rejected

Artifacts:

    runs/audits/cortex_f2f3_structural_canary_attempt1_invalid_bootstrap.json
    runs/audits/cortex_f2f3_structural_canary_attempt1_invalid_bootstrap.stdout.json

SHA-256:

    88cb0ea8e7eab1bbf547883e1668df803c7c0c3e0105d94524485b2da199bd79
    b843c8489b6c9f7b8f3bf7503c9a30a835fa32dd7b06f75b8cd28f1d3723bef0

This duplicate is classified as:

    invalid duplicate / fixture timing failure

It is not counted as an independent F2-F3 capability sample and does not supersede the valid run
at 20:00:43Z.

No additional F2-F3 retry is authorized.

## 8. Fixture hardening

The duplicate invalid run exposed a real test-fixture weakness: the previous bootstrap depended on
one fixed sleep at the edge where F2-E2 had produced exactly one ore.

The fixture is hardened for future experiments by:

- bounded 1-second polling;
- explicit 12-second deadline;
- stopping on the first observed buffered ore;
- recording poll_count;
- recording elapsed_s;
- recording deadline_s;
- recording iron_buffered on success/failure.

This hardening changes only fixture timing/telemetry. It does not change:

- structural planning;
- FunctionalDependency;
- fuel selection;
- StructuralTransactionalAdapter;
- hard postconditions;
- commit/rollback semantics.

It does not justify another F2-F3 attempt.

Hardening commit:

    0a747b99328bf084a82fdde79ee8259018ba7931 — fix: estabiliza fixture do canário Cortex

## 9. Decision F2-F3

**VALID NEGATIVE RESULT.**

F2-F3 is complete as an experiment.

Evidence supports:

- typed furnace fuel dependency: solved;
- physical processor topology: solved for this branch;
- processor functional output: not solved;
- current candidate state: no_ingredients;
- rollback: verified;
- continuous authority: false.

The next scientific checkpoint is F2-F4:

    delivery actuator energy dependency

F2-F4 must decide delivery actuator/power as a typed engineering dependency using measured runtime
facts. It must not simply replace inserter with burner-inserter by stage-specific special case.

## 10. F2-F4 requirements

At minimum:

1. represent actuator energy source and operating requirement;
2. evaluate candidate delivery entities by measured availability and energy feasibility;
3. allow burner-inserter only through generic capability/energy reasoning;
4. allow electric inserter only when an explicit power dependency is satisfiable;
5. preserve provenance of any fuel/power child dependency;
6. keep functional processor_output as a hard gate;
7. keep TransactionalFLEExecutor as the sole commit boundary;
8. no continuous authority;
9. no confirmatory seeds;
10. no new real canary before implementation + full gate + clean commit.

F3 remains blocked.
