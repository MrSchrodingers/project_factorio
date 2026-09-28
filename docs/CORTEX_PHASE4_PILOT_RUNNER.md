# F4-C pilot runner

Status: implementation candidate; validation required before pilot execution.

## Scope

The F4-C frozen tasks are abstract transfer benchmarks. The protocol runner
executes them in a deterministic task-world runtime backed by existing project
planners. It does not mutate the LIVE WORLD and does not create an FLE
environment, RCON session, WorldLease, execution grant, or continuous runner.

The candidate surface is frozen by the protocol. MEMORY ON and MEMORY ABLATED
see the same task, candidate classes, deterministic tools, feasibility rules,
budgets, and outcome extractor. Memory may affect only ranking/selection.

## Runner readiness

Before execution_ready may become true, a clean-tree audit must prove:

- all 13 frozen candidate labels have protocol executors;
- executor semantics are exercised on independent NON-PROTOCOL fixtures;
- paired preflight restores the identical checkpoint before both arms;
- source memory remains unchanged and writes remain quarantined;
- candidate, tool, budget and outcome surfaces are matched;
- all eight frozen pilot seeds map exactly and are counterbalanced 4/4;
- evaluation and confirmatory seeds are rejected;
- no canonical pilot artifact already exists;
- evolution remains separately required to be inactive+disabled at execute time.

## Pilot design exposure boundary

During implementation, the already-frozen pilot task specifications were used
for executor smoke checks before the canonical runner existed. Because the
task-world runtime is deterministic, those pilots are therefore treated as
instrumentation-only. Their outcomes must not be used to estimate variance,
adapt sample size, tune endpoint weights, or support primary F4-C inference.

The held-out evaluation partition 20261221 through 20261240 remains untouched
and is the only partition eligible for the preregistered primary causal
analysis. Confirmatory seeds 20261101 through 20261110 remain frozen.

## Execution discipline

The CLI scripts/run_cortex_f4c_pilot.py executes exactly one requested frozen
pilot pair. It refuses non-pilot seeds, requires a clean tree, requires
execution_ready=true and a validated runner audit, requires evolution
inactive+disabled, refuses overwrite of existing canonical pair evidence, and
has no automatic retry.
