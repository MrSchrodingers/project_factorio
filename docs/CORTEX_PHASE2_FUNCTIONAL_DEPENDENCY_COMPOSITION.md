# Cortex Research — F2-F2 Functional Dependency Composition

**Phase:** F2
**Checkpoint:** F2-F2 — typed fuel/energy dependency composition
**Authority:** planning + prepared execution contract; no new ambient authority
**Scientific F1 runtime:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Confirmatory seeds:** 20261101–20261110 frozen and unspent

## 1. Causal origin

F2-E2 attempt 2 produced a clean functional counterexample in real Factorio:

- physical topology became valid;
- producers_reaching_processor increased from 0 to 1;
- physical_processing_coverage increased from 0.0 to 1.0;
- the stone-furnace existed;
- processor_status was no_fuel;
- processor_output stayed at 0;
- structural_postcondition_failed rejected the candidate;
- rollback_observed was true.

Therefore the structural option was topologically correct but functionally incomplete.

F2-F1 measured the missing runtime facts: machine energy-source type, energy usage, fuel categories,
fuel items, fuel values and fuel compatibility. F2-F2 composes those facts into the option itself.

## 2. Research hypothesis

A processing option is functionally complete only when every mandatory operating dependency is
represented and covered before execution.

For a burner processor, fuel is therefore not an executor side-effect. It is a typed child
dependency of the structural option.

The F2-F2 hypothesis is:

> Given a measured machine energy contract, measured compatible fuels and observed supply
> availability, the Cortex can derive a bounded fuel requirement, select only a supply plan that
> actually covers it, and encode that dependency in the same prepared action without hardcoding
> coal or weakening the functional output gate.

## 3. Energy-to-fuel derivation

The runtime reports energy usage in joules per game tick.

Factorio game time uses 60 ticks/s, therefore:

    P = E_tick * 60

where P is machine power in joules/s.

For observation horizon T and safety margin m:

    E_required = P * T * m

For a fuel with measured energy value F:

    units = ceil(E_required / F)

The implementation reuses planning/fuel.py:

    BurnerProfile.fuel_units_for_seconds(T, F, margin=m)

Coal-specific wrappers remain for backward compatibility, but F2-F2 does not select by fuel name.

## 4. Generic BurnerProfile

BurnerProfile now supports arbitrary measured fuel values:

- seconds_per_fuel_unit(fuel_value_j);
- fuel_units_for_seconds(seconds, fuel_value_j, margin).

Existing methods remain wrappers:

- seconds_per_coal();
- coal_for_seconds().

Invalid, zero, negative, NaN or infinite fuel values are rejected rather than coerced.

## 5. Machine energy input

complete_structural_dependencies reads:

    RuntimeFactorioCatalog.machine_energy(processor)

The processor energy source must be measured.

Cases:

- probe unknown/failed -> structural_energy_source_unmeasured;
- measured absent -> no energy child dependency is added;
- burner -> F2-F2 composition path;
- other measured source types -> structural_energy_source_not_composed until their own adapter exists.

A machine class name never substitutes for measured energy-source type.

## 6. Fuel compatibility

Candidate fuels come from:

    RuntimeFactorioCatalog.compatible_fuels(machine.fuel_categories)

Compatibility requires measured category intersection.

F2-F2 does not assume:

- coal is universally valid;
- the highest-energy fuel is available;
- any item with a fuel value can power every burner.

The catalog's deterministic ordering may place high-energy fuels first, but this is not yet a
utility policy. Each candidate is independently tested against actual supply.

## 7. Availability and provenance

For each compatible fuel, F2-F2 combines:

- carried inventory;
- optional observed FuelSource objects;
- the required unit count;
- the processor placement anchor.

The existing planner:

    planning.resupply.plan_supply

produces SupplyPlan with:

- carried fuel coverage;
- world fuel draws;
- source positions;
- provenance;
- named refusals.

F2-F2 does not hand-write a fuel draw.

A high-density fuel that is unavailable is skipped. Selection falls through deterministically to
the first measured compatible candidate whose SupplyPlan covers the requirement.

Example proven in tests:

- nuclear-fuel compatible but unavailable;
- coal carried and available;
- selected fuel = coal.

A second test proves fallback to wood when nuclear-fuel and coal are unavailable.

## 8. FuelDependency

The selected dependency is represented by FuelDependency:

- machine;
- MachineEnergy;
- horizon_s;
- FuelSpec;
- units_needed;
- SupplyPlan.

It also exposes carried_only.

FuelCandidateEvaluation records every candidate attempted, including candidates refused by
plan_supply. This preserves why a lower-density fuel may have been selected.

## 9. PreparedStructuralAction v2

F2-F2 introduces:

    cortex_structural_ops_v2

The original v1 contract remains supported.

When a burner dependency is covered, the completed action receives:

    preflight.energy_dependency

and a semantic operation:

    fuel_processor

The operation records:

- fuel_item;
- quantity;
- fuel_value_j;
- measured fuel categories;
- measured machine energy usage;
- horizon_s;
- full SupplyPlan.

This operation is inserted before connect_delivery.

The dependency is therefore part of the option, not hidden inside the executor.

## 10. Execution support boundary

structural_execute.py accepts v1 and v2 contracts.

For carried-only fuel, fuel_processor compiles to an insertion into cortex_processor using the
prototype resolved from the real FLE enum.

For a plan that requires world fuel draws, compilation refuses with:

    structural_world_fuel_draw_unsupported

This is deliberate.

World draw execution needs its own provenance-preserving adapter. F2-F2 may plan such a path, but
must not silently turn it into teleportation/manual inventory injection.

## 11. Named refusals

F2-F2 preserves explicit causal refusals:

- structural_energy_source_unmeasured;
- structural_energy_usage_unmeasured;
- structural_fuel_categories_unmeasured;
- structural_no_compatible_fuel;
- structural_fuel_supply_unavailable;
- structural_energy_source_not_composed;
- structural_dependency_anchor_missing;
- structural_world_fuel_draw_unsupported.

A refusal is a valid planning outcome.

## 12. Scientific invariants

F2-F2 preserves:

- missing != zero;
- measured source type rather than machine-name inference;
- measured fuel compatibility rather than item-name assumptions;
- demand from energy usage rather than literal coal counts;
- supply coverage before selection;
- provenance for world sources;
- no world-draw execution without an explicit adapter;
- processor_output INCREASE remains a hard execution gate;
- TransactionalFLEExecutor remains the only commit/rollback boundary;
- no scheduler or persistent EXECUTE authority;
- confirmatory seeds remain untouched.

## 13. Focused verification

Focused F2-F2 gate:

- tests/test_cortex_functional_dependency.py;
- tests/test_fuel.py;
- tests/test_cortex_structural_execute.py;
- tests/test_cortex_structural.py;
- tests/test_world_resupply.py.

Final verification:

- 83 focused PASS;
- 1386 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff PASS;
- compileall PASS;
- frontend TypeScript/Vite + node PASS;
- diff check PASS;
- scientific F1 runtime unchanged;
- evolution inactive + disabled.

The focused gate proves:

- arbitrary measured fuel-value sizing;
- energy-density effect without fuel-name hardcode;
- invalid fuel values are refused;
- available coal can beat unavailable nuclear fuel;
- available wood is selected when denser fuels are unavailable;
- world supply can be planned with provenance while execution remains refused;
- no available compatible fuel becomes a named refusal;
- non-burner energy source remains a named refusal;
- carried-fuel v2 compiles to fuel_processor insertion.

## 14. Decision

**PASS.**

Implementation commit: `95ec3dfc23c3d39a88fc6b5abe64e9042902a413`.

F2-F2 adds planning/composition capability only.

It does not grant:

- continuous autonomous authority;
- scheduler authority;
- new production-policy authority;
- world fuel-draw execution;
- use of confirmatory seeds.

A new real Factorio canary remains forbidden until:

1. F2-F2 implementation is committed and pushed;
2. repository is clean;
3. dashboard deployment is validated;
4. factorio-ai-evolution remains inactive and disabled;
5. the canary consumes the v2 dependency-completed PreparedStructuralAction.

## 15. Next checkpoint — F2-F3

F2-F3 is a single isolated canary with the same seed 424242.

The canary must compare directly against the F2-E2 counterexample.

Expected causal difference:

F2-E2:
    processor_status = no_fuel
    processor_output = 0
    rollback = true

F2-F3 hypothesis:
    typed fuel dependency covered
    processor_status != no_fuel
    processor_output > 0
    hard functional gate satisfied

The output gate is unchanged.

If F2-F3 still rejects, the next action is determined by the measured failure, not by weakening
acceptance criteria.

F3 remains blocked until F2 Exit Gate is satisfied.
## 16. F2-F3 observed result

The real Factorio canary on clean commit `e1aad03dafa8604a002ca11641f0000b69cbefa0` reached the dependency-completed option.

Observed causal transition:

- fuel dependency: ready;
- selected fuel: coal;
- processor fuel units: 1;
- processor status changed from the previous F2-E2 `no_fuel` failure to `no_ingredients`;
- physical processing coverage increased from 0.0 to 1.0;
- processor existed;
- processor output remained 0.0;
- transaction was rejected;
- rollback was observed.

This validates the fuel-composition mechanism while falsifying the stronger hypothesis that processor fuel was the only missing functional dependency.

The delivery contract still specifies the electric `inserter`, while `planning/delivery.py` has no power/energy contract and the canary creates no electrical network. F2-F4 must therefore model delivery-actuator energy/capability generically. The functional output gate remains unchanged.

Artifact: `runs/audits/cortex_f2f_structural_canary.json`.
