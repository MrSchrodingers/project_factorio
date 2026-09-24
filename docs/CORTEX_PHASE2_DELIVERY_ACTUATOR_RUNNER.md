# Cortex Research — F2-F4B Runner Integration

**Phase:** F2
**Checkpoint:** F2-F4B — delivery actuator dependency integrated into one-shot runner
**Authority:** no live execution authorized yet
**Source checkpoint:** F2-F4A PASS in SHADOW

## 1. Scope

F2-F4B integrates the F2-F4A energy-aware actuator planner into `run_cortex_structural_canary.py` after processor fuel completion and before any `EXECUTE` call.

The integration does not itself authorize a canary. It only makes the runner dependency-complete enough to be tested and versioned.

## 2. Runner sequence

The one-shot path is now:

1. structural branch planning;
2. PreparedStructuralAction v1;
3. processor functional dependency completion (v2);
4. delivery power capability measurement/derivation;
5. delivery actuator dependency completion (v3);
6. persist v3 evidence;
7. only then prepare checkpoint + explicit EXECUTE.

## 3. Power capability contract

`delivery_power_capability()` is fail-closed.

- `power_edge_count == 0` plus `fixture_power_operation=false` -> `available=False`, status `derived_unavailable`;
- `power_edge_count == 0` without a fixture power contract -> `available=None`, status `power_capability_unmeasured`;
- observed `power_edge_count > 0` -> `available=None`, because topology somewhere does not prove power at the planned actuator tile;
- missing/non-numeric metric -> `available=None`, status `missing`.

No numeric default is converted into power availability, and zero topology alone is insufficient without the canary fixture contract.

## 4. Persisted evidence

Before EXECUTE the runner writes:

- `delivery_power_capability`;
- all actuator evaluations;
- selected delivery actuator dependency;
- any child fuel dependency;
- refusal when unresolved;
- `prepared_v3`.

Thus a failed execution can be attributed to a specific prepared contract rather than reconstructed after the fact.

## 5. Fail-closed dry-run

Without `--execute`, the runner still exits with code 2 and never opens the mutation path.

The refusal is now persisted to the requested artifact with:

- `status=refused`;
- `world_mutation=false`;
- `authority=null`;
- `continuous_authority=false`;
- seed and timestamp.

This makes the safety gate auditable across stream interruptions.

## 6. Focused gate

Focused F2-F4B gate:

- 60 focused tests PASS;
- 76 continuity/dashboard tests PASS;
- Ruff PASS;
- py_compile PASS;
- dry-run artifact PASS;
- `world_mutation=false` confirmed;
- confirmatory seed block remains intact.

## 7. Mechanical continuity

`cortex_phase_state.py` now distinguishes:

- `F2-F4A`: planner/instrument/shadow checkpoint;
- `F2-F4B`: runner integration checkpoint.

A future real canary will receive a separate persisted checkpoint; runner integration alone is not evidence of live success.

## 8. Full gate result

F2-F4B full gate:

- core/FLE: 1400 PASS;
- ML/PyTorch: 2 PASS;
- Ruff: PASS;
- compileall: PASS;
- dashboard JavaScript syntax: PASS;
- diff whitespace check: PASS.

The first full-gate invocation ended only on one extra EOF blank line after every functional/static gate had already passed. The file was normalized and static checks rerun successfully; no functional code change followed the 1399+2 test pass.

## 8.1 Integrated read-only replay

Artifact:

`runs/audits/cortex_f2f4b_runner_replay.json`

The replay used the same runner power helper and the real F2-F3 PreparedStructuralAction without calling `TransactionalFLEExecutor`.

Observed:

- `world_mutation=false`;
- power capability = `derived_unavailable`;
- electric `inserter` refused by power dependency;
- `burner-inserter` selected;
- actuator fuel = coal;
- contract = `cortex_structural_ops_v3`;
- compiled action ready;
- operation order includes `connect_delivery -> fuel_delivery_actuator`.

This is integration evidence only, not live execution evidence.
## 9. Remaining publication gate

Before any real canary:

1. commit and push the F2-F4B implementation from a clean tree;
2. confirm local HEAD equals origin/research/cortex-v1;
3. confirm evolution inactive+disabled;
4. confirm frozen F1 runtime unchanged;
5. confirm seed 424242 is non-confirmatory and no F2-F4C artifact already exists;
6. authorize exactly one one-shot canary with a new artifact path.

## 10. Decision status

**FULL GATE PASS. F2-F4C may run exactly one non-confirmatory canary only after clean publication and live isolation/runtime/artifact revalidation. Continuous authority remains forbidden.**

F3 remains blocked.
## 11. Published implementation

- runner/phase-state implementation: `0607d1286b3a0c885e03c11c23e51935e2a16aa1`;
- dashboard rendering: `beb96a6a5fd7142cb6d6d1a3bfaa48d380f9ed65`;
- dashboard cache stamp: `c39cefcef0e7ae9bc6f1b9a127f6ab4d90238690`.

After this documentation closure is pushed, the only remaining pre-canary gate is live revalidation of clean HEAD, evolution isolation, frozen F1 runtime and unique artifact path.
