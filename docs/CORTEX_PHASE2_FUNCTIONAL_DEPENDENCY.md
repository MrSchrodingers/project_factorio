# Cortex Research — F2-F Functional Dependency Completion

**Phase:** F2
**Checkpoint:** F2-F1 — runtime energy/fuel observability
**Authority:** read-only / no new world mutation
**Scientific F1 runtime:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Confirmatory seeds:** 20261101–20261110 frozen and unspent

## 1. Causal motivation

F2-E2 produced a real Factorio counterexample:

- the structural transaction reached the expected topology;
- producers_reaching_processor increased;
- physical_processing_coverage increased to 1.0;
- processor_exists became true;
- stone-furnace status was no_fuel;
- processor_output remained zero;
- the hard functional gate rejected the candidate;
- TransactionalFLEExecutor restored the exact pre-action checkpoint.

Therefore placement + material delivery are not sufficient to establish processing.

The missing capability is not another placement heuristic. It is an explicit energy/fuel dependency.

## 2. F2-F decomposition

F2-F is split into three checkpoints.

### F2-F1 — observability

Measure from the runtime:

- machine energy-source type;
- maximum energy usage per game tick;
- accepted fuel categories for burner machines;
- runtime fuel items;
- fuel value;
- fuel categories.

No fuel plan is selected and no world mutation is performed.

### F2-F2 — dependency composition

Use the measured facts with existing planning/fuel.py and planning/resupply.py to produce a typed
fuel dependency / child option for the processor.

The dependency must be part of the structural option. It must not be a special-case coal insertion
inside the executor.

### F2-F3 — isolated canary

Only after F2-F2 is committed and the repository gate is green may a single isolated canary run.

The same functional gate remains mandatory:

processor_output INCREASE.

## 3. Runtime machine energy contract

game_knowledge now probes machine prototypes for:

- get_max_energy_usage();
- burner_prototype;
- electric_energy_source_prototype;
- heat_energy_source_prototype;
- fluid_energy_source_prototype;
- void_energy_source_prototype.

Each observation carries probe status.

The normalized runtime fields are:

- energy_source_type;
- energy_source_status;
- energy_usage_per_tick_j;
- energy_usage_status;
- fuel_categories;
- fuel_categories_status.

Unknown, absent and failed probes remain distinguishable.

## 4. Runtime fuel contract

game_knowledge also enumerates runtime items with positive fuel_value.

Each fuel row carries:

- name;
- fuel_value_j;
- fuel_value_status;
- fuel_categories;
- fuel_categories_status.

The catalog does not assume that a fuel named coal is valid for a given machine. Compatibility is
defined by intersection between the machine's measured fuel categories and the fuel item's measured
fuel categories.

## 5. Factorio 2.0.73 compatibility

The live runtime is Factorio 2.0.73.

In this runtime:

- LuaItemPrototype.fuel_categories is not available;
- reading it raises a property error;
- legacy LuaItemPrototype.fuel_category is available;
- coal reports fuel_category = chemical;
- coal reports fuel_value = 4,000,000 J.

The observer therefore follows a version-compatible probe sequence:

1. try fuel_categories;
2. if that accessor fails, try fuel_category;
3. normalize the legacy single category to a one-element category list;
4. preserve probe status.

This is measured compatibility logic, not a silent hardcoded default.

## 6. RuntimeFactorioCatalog additions

F2-F1 adds MachineEnergy and FuelSpec.

MachineEnergy records:

- machine;
- source_type and status;
- energy_usage_per_tick_j and status;
- fuel_categories and status.

FuelSpec records:

- fuel item name;
- fuel_value_j and status;
- fuel_categories and status.

New catalog APIs:

- machine_energy(machine);
- machine_energies();
- fuel(name);
- fuels();
- compatible_fuels(categories).

Unknown machine energy stays UNKNOWN rather than becoming zero or electric.

compatible_fuels only returns measured fuels whose categories intersect the requested categories.

## 7. Live read-only evidence

The new observer was executed read-only against the current Factorio 2.0.73 runtime.

Observed counts:

- recipes: 217;
- technologies: 196;
- machines: 14;
- belts: 15;
- fuels: 6.

Observed stone-furnace energy contract:

- source_type = burner;
- source_type_status = measured;
- energy_usage_per_tick_j = 1500.0;
- energy_usage_status = measured;
- fuel_categories = [chemical];
- fuel_categories_status = measured.

Measured chemical fuels visible to the runtime, ordered by the catalog's current deterministic
energy-density ordering:

1. nuclear-fuel — 1,210,000,000 J;
2. rocket-fuel — 100,000,000 J;
3. solid-fuel — 12,000,000 J;
4. coal — 4,000,000 J;
5. wood — 2,000,000 J.

This ordering is not yet a selection policy. F2-F2 must combine compatibility with actual
availability and supply provenance.

## 8. Relationship with existing planners

F2-F does not replace the existing fuel/resupply engineering code.

planning/fuel.py already provides:

- BurnerProfile;
- runtime-derived profile_from_energy_per_tick;
- measured-window sizing;
- explicit no-measurement behavior.

planning/resupply.py already provides:

- FuelSource;
- FuelDraw;
- SupplyPlan;
- fuel carried vs world shortfall;
- provenance-bearing world draws;
- deterministic source preference;
- named refusals for no fuel / insufficient fuel.

F2-F2 must compose these components into the Cortex option.

## 9. Scientific invariants

F2-F preserves:

- missing != zero;
- compatibility is measured, not inferred from item name;
- source type is measured, not inferred from machine class;
- fuel quantity derives from energy usage + fuel value + horizon;
- unavailable fuel cannot be selected merely because it has higher energy density;
- world fuel draws preserve provenance;
- a refusal is a valid planning result;
- functional output remains a hard postcondition;
- TransactionalFLEExecutor remains the only commit/rollback boundary.

## 10. F2-F1 tests

Focused F2-F1 gate currently covers:

- runtime machine-data parsing;
- RuntimeFactorioCatalog;
- persisted game-knowledge behavior.

Result:

- 53 focused continuity/runtime tests PASS;
- 1373 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff/static checks PASS;
- py_compile PASS;
- frontend TypeScript/Vite build PASS;
- diff check PASS;
- live read-only runtime probe PASS;
- scientific F1 runtime unchanged;
- factorio-ai-evolution inactive + disabled.

## 11. Decision F2-F1

**PASS.**

F2-F1 provides the missing factual substrate required to plan a dependency-complete burner option.

No new EXECUTE path was introduced.

## 12. Next checkpoint — F2-F2

F2-F2 will:

1. generalize burner sizing to arbitrary measured fuel values while preserving coal wrappers;
2. derive processor fuel demand from MachineEnergy;
3. filter candidate fuels by measured compatibility;
4. combine candidates with actual carried/world availability;
5. call plan_supply rather than hand-writing a fuel draw;
6. represent the selected fuel plan as a typed child dependency;
7. add a semantic fuel_processor operation to PreparedStructuralAction;
8. compile only the supported carried-fuel path initially;
9. preserve named refusal for unimplemented world-draw execution;
10. keep F3 and continuous autonomous authority blocked.

The next canary is forbidden until F2-F2 passes its own gate and is committed cleanly.
