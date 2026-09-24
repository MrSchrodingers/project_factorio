# Cortex Research — F2-F4 Delivery Actuator Dependency

**Phase:** F2
**Checkpoint:** F2-F4A — energy-aware delivery actuator planning
**Authority:** SHADOW / NO LIVE-WORLD MUTATION
**Source counterexample:** F2-F3 `no_ingredients` with rollback
**Confirmatory holdout:** 20261101–20261110 untouched

## 1. Motivation

F2-F3 validated processor fuel completion but the furnace still produced zero output. The candidate state changed from the previous `no_fuel` failure to `no_ingredients` while physical processing coverage reached 1.0.

The prepared delivery contract selected `entities=["inserter"]`. The preserved pre-action graph had no power edges and the canary fixture created no electrical infrastructure.

A read-only Factorio 2.0.73 probe measured:

- `inserter`: type=inserter, source=electric, 245 J/tick;
- `burner-inserter`: type=inserter, source=burner, 2400 J/tick.

This made delivery-actuator energy the next falsifiable dependency.

## 2. Instrumentation correction

The canonical `FactorioObserver.game_knowledge()` previously measured energy for every entity prototype but only retained rows with crafting or mining categories. Inserters were therefore discarded after measurement.

F2-F4 broadens the canonical machine-energy instrument to retain any prototype where `energy_source_status != absent`, including measured and probe-failed energy actors.

This is generic instrumentation. It is not an inserter special case.

The runtime catalog now exposes `machine_names_by_type(entity_type)` from observed prototype type metadata.

Live read-only validation against Factorio 2.0.73 returned five inserter prototypes, including the electric `inserter` and burner `burner-inserter` with measured energy.

## 3. Reusable burner fuel planning

F2-F2 fuel selection was extracted into `plan_burner_fuel_dependency()`.

The function consumes:

- machine identity;
- measured `MachineEnergy`;
- placement anchor;
- runtime fuel catalog;
- observed inventory;
- optional world fuel sources;
- bounded operating horizon.

It returns a typed `FuelDependencyPlan` without mutating a prepared action. The existing processor dependency completion now uses this same function, preserving F2-F2 behavior while making the logic reusable by delivery actuators.

## 4. Delivery actuator candidates

`complete_delivery_actuator_dependency()` discovers candidates from `catalog.machine_names_by_type("inserter")`.

The actuator originally requested by the prepared delivery operation is evaluated first, followed by other observed inserter prototypes deterministically.

A candidate must be carried in observed inventory. F2-F4A does not silently craft an alternative actuator.

## 5. Explicit electric power capability

Electric power availability is an explicit tri-state planner input:

- `True`: power capability was observed/satisfied;
- `False`: power capability was observed unavailable;
- `None`: power capability was not measured.

`None` never becomes `False` and never licenses an electric actuator.

F2-F4A does not infer power availability silently from a numeric default.

## 6. Energy evaluation

For each carried actuator:

- source absent -> ready without child energy dependency;
- electric + power=True -> ready;
- electric + power=False -> `delivery_actuator_power_unavailable`;
- electric + power=None -> `delivery_actuator_power_unmeasured`;
- burner -> reuse `plan_burner_fuel_dependency()`;
- unknown/uncomposed source -> named refusal.

## 7. Contract v3

F2-F4 introduces:

`cortex_structural_ops_v3`

When a burner actuator is selected:

1. `connect_delivery` is rewritten to the selected observed prototype;
2. `fuel_delivery_actuator` is inserted immediately after it;
3. the fuel child dependency is recorded under `preflight.delivery_actuator_dependency`.

The compiler targets `cortex_delivery` for actuator fuel. World fuel draws remain refused unless a provenance-preserving adapter exists.

## 8. Focused gate

F2-F4A focused tests verify:

- runtime lookup by observed entity type;
- F2-F2 fuel regression remains intact;
- power unavailable rejects electric inserter;
- unmeasured power does not license electric inserter;
- burner-inserter is selected only when carried and fuel-covered;
- electric inserter remains preferred when power is explicitly available;
- v3 compilation places the burner actuator before fueling it;
- existing processor fuel and transactional compiler behavior remains valid.

Focused gate: 54 PASS before canonical instrument expansion; canonical instrument + F2-F4 subset: 22 PASS. Ruff and py_compile PASS.

## 9. Shadow replay on real F2-F3 evidence

Artifact:

`runs/audits/cortex_f2f4_delivery_actuator_shadow.json`

Source:

`runs/audits/cortex_f2f_structural_canary.json`

The replay is read-only (`world_mutation=false`). Power unavailable is recorded as a derived capability based on the preserved graph (`power_edge_count=0`) and fixture contract (no power operation).

Observed result:

- requested `inserter`: carried=50, electric, refused because power unavailable;
- `bulk-inserter`: unavailable in inventory;
- `burner-inserter`: carried=50, burner, fuel dependency covered by coal;
- selected actuator: `burner-inserter`;
- selected actuator fuel: coal;
- compiled v3 contract: ready;
- operation sequence includes `connect_delivery -> fuel_delivery_actuator`.

## 10. Scientific interpretation

F2-F4A does not prove that a burner inserter will make the furnace produce. It proves a narrower claim: the previously hidden actuator-energy dependency can now be represented, measured, selected and compiled without stage-specific logic.

The functional hard gate remains `processor_output INCREASE`.

## 11. Non-goals

F2-F4A does not:

- execute a new Factorio canary;
- grant continuous authority;
- assume burner-inserter by stage name;
- use confirmatory seeds;
- relax output acceptance;
- implement world fuel draws;
- declare F2 complete.

## 12. Next checkpoint — F2-F4B

F2-F4B must integrate `complete_delivery_actuator_dependency()` into the one-shot canary path after processor functional dependency completion and before `EXECUTE`.

Before any real canary:

1. runner integration tests must pass;
2. explicit power capability evidence must be persisted;
3. F2-F4 prepared v3 action must be recorded in the artifact;
4. full repository gate must pass;
5. implementation must be committed and pushed cleanly;
6. evolution must remain inactive+disabled;
7. only one non-confirmatory canary may be authorized.

F3 remains blocked.
