# Cortex Phase 5-C — Deterministic Autonomous Baseline

Status: **F5-C ACTIVE — iron_extraction promoted; coal_self_sufficiency implementation gate pending live execution**

## Objective

F5-C establishes the non-learned engineering baseline that every later learned
policy must beat under matched authority and physical constraints. The baseline
does not receive ambient EXECUTE authority. The control plane remains A0 and
each physical mutation requires one expiring A2 grant for one exact Option.

The first capability, iron_extraction, is promoted. The active deterministic target is coal_self_sufficiency.

## First development episode

Frozen seed: 1619515465

Partition: development

Environment: open_play

The environment is reset inside an exclusive WorldLease. The reset is episode
initialization and receives no Cortex capability credit. open_play is used
because the project defines it as empty inventory with the real technology
tree.

## Zero-inventory bootstrap

The first Option is establish_resource_extraction. Its plan is inert until A2
is issued. Bootstrap material must be harvested from the same world inside the
same transaction:

- stone: 30
- coal: 24
- iron ore: 16
- wood: 10

No external inventory injection is allowed.

The transaction then:

1. harvests the frozen bootstrap resources;
2. smelts bootstrap iron;
3. crafts the extraction cell;
4. places one burner mining drill on a surveyed iron patch;
5. fuels it from world-harvested coal;
6. places an output wooden chest;
7. waits through the fixed validation window;
8. measures physical postconditions;
9. commits only on PASS, otherwise rolls back.

## Physical gate — iron_extraction

All hard conditions must pass:

- resource_patch_valid;
- drill_operational;
- iron_ore_produced > before;
- destination_reachable;
- production_positive_during_validation_window;
- execution guard extractor_exists;
- execution guard buffer_iron_ore > before.

For the first capability, the survival invariant has an empty incumbent set and
therefore passes only if no regression is observed. Later capabilities must
revalidate every previously promoted capability.

## Authority invariants

- ambient authority = A0;
- maximum live grant = A2;
- max_executions = 1;
- grant persisted before mutation;
- grant bound to exact Option/plan/code/run/WorldLease;
- grant consumed atomically before external mutation;
- no automatic retry;
- no scheduler;
- no A3+;
- learned policy cannot issue a grant;
- evolution remains OFF.

## Evidence and trajectory

Live runner:

scripts/run_cortex_f5c_deterministic_baseline.py

Read-only preflight:

PYTHONPATH=src .venv-fle/bin/python scripts/run_cortex_f5c_deterministic_baseline.py

Explicit live execution, allowed only from clean committed/deployed source:

PYTHONPATH=src .venv-fle/bin/python scripts/run_cortex_f5c_deterministic_baseline.py --execute

Canonical development artifact:

runs/audits/cortex_f5c_development_1619515465_iron_extraction.json

The artifact includes the frozen plan, exact grant, WorldLease attestation,
before/after measurements, capability gate, survival gate, transaction
commit/rollback, intervention evidence and one typed trajectory record.

## Promotion semantics

iron_extraction is promoted only when the canonical artifact proves:

- status completed;
- exactly one execution attempt;
- transaction committed;
- all physical gate rows true;
- survival gate PASS;
- external_resource_injection=false;
- human_intervention_count=0;
- automatic_retry=false;
- ambient A0 and bounded A2.

If the transaction is rejected or fails, the artifact is a counterexample.
There is no automatic second attempt.

## Current transition

Before the development artifact exists:

- phase5_checkpoint = F5-B;
- phase5_next_checkpoint = F5-C;
- capabilities = 0/9.

After a valid physical artifact:

- phase5_checkpoint = F5-C;
- iron_extraction = promoted;
- capabilities = 1/9;
- next deterministic target = coal_self_sufficiency.

F5-C is not complete after the first capability. It remains active until the
deterministic physical baseline reaches the frozen F5-C completion criterion
defined by the broader F5 protocol.

## Development counterexample 1 — resource perception boundary

Seed 1619515465 was initialized once under an exclusive WorldLease and produced
a canonical counterexample before any A2 grant or Option execution.

Observed result:

- status = failed;
- world_reset = true, initialization only;
- option_execution_attempts = 0;
- grant_issued was never reached;
- capability promotion = none;
- failure = F5-C resource survey returned no resources;
- WorldLease was released normally;
- intervention ledger remained empty.

The failure was not absence of Factorio resources. The canonical RCON resource
overview on the initialized world observed 2562 resource points inside radius
192, including 624 iron-ore, 600 coal, 650 copper-ore and 624 stone points.

Root cause: the runner used save_entity_state with resource_entities enabled as
the resource-patch perception source. That API is not the canonical wide-area
resource survey used by the dashboard and returned no resource rows in this
open_play episode.

Correction:

- use FactorioObserver.resource_overview with max_age_s=0;
- convert it with resource_survey_from_overview;
- keep save_entity_state for physical player entities/inventory only;
- preserve the failed seed artifact unchanged;
- do not retry seed 1619515465;
- continue with the next frozen development seed only after the correction is
  committed, tested and deployed.

Next eligible development seed after the correction: 853367368.

## Development counterexample 2 — authority ledger writability

Seed 853367368 was initialized after the canonical RCON resource-perception fix.
The corrected perception succeeded and observed 2562 resource points, including
624 iron-ore points.

The episode then failed before grant issuance and before Option execution with:

attempt to write a readonly database

Observed result:

- status = failed;
- resource perception = PASS;
- option_execution_attempts = 0;
- no A2 grant issued;
- no Option executed;
- no capability promoted;
- WorldLease released normally;
- intervention ledger remained empty.

Root cause: runs/authority and cortex_option_grants.sqlite3 were owned by root
and were readable but not writable by the runtime user ti.

Operational correction:

- runs/authority ownership changed to ti:devs;
- directory mode changed to 2775;
- SQLite mode changed to 0664.

Protocol correction:

- F5-C preflight now validates authority-ledger directory/file writability
  before acquiring WorldLease or resetting the episode;
- a regression test proves readonly ledger state fails closed in preflight.

Seed 853367368 is preserved as a counterexample and will not be retried.
Next eligible development seed after committed/deployed correction: 245044303.

## Development success 3 — iron_extraction promoted

Seed 245044303 closed the first physical F5 capability on commit 7045e6df6fd4afa9770e20b9772665c6e7e08710.

Run: cortex-f5c-dev-245044303-20260929T063645Z

Observed physical evidence:

- transaction_committed = true;
- option_execution_attempts = 1;
- ambient authority = A0;
- bounded authority = A2;
- max_executions = 1;
- grant consumed before runtime mutation;
- resource_patch_valid = true;
- drill_operational = true;
- iron_ore_produced = true;
- destination_reachable = true;
- production_positive_during_validation_window = true;
- extractor_exists = true;
- buffer iron ore increased from 0 to 3;
- external_resource_injection = false;
- human_intervention_count = 0;
- survival gate = PASS;
- evolution remained inactive+disabled;
- WorldLease released normally;
- intervention ledger remained empty.

The live WORLD after commit contains the player, one working burner mining drill
at (15, 70), and one wooden chest at (15.5, 71.5).

Machine-readable state after regeneration:

- phase5_checkpoint = F5-C;
- iron_extraction_validated = true;
- achieved capabilities = 1/9;
- next deterministic target = coal_self_sufficiency.

The observed execution exceeded the requested 60-second Option budget:
9690 game ticks = 161.5 seconds. This is retained as real temporal evidence and
must inform later F5-C budget sizing; it does not invalidate the capability
because the hard physical gate passed and no deadline was preregistered as an
iron_extraction success criterion.

## Next capability protocol — coal_self_sufficiency

The coal capability continues on the surviving seed-245044303 WORLD. It does
not reset the environment. Before A2 is issued, the runner verifies:

- iron_extraction is the only promoted capability;
- the deployed dashboard/runtime commit equals the clean source HEAD;
- evolution is inactive+disabled;
- no WorldLease is active;
- the F5 intervention ledger is empty;
- the persistent A2 ledger is writable;
- the promoted iron drill and output chest exist at their canonical positions;
- the iron drill is physically working.

The transaction captures the exact current FLE GameState as rollback checkpoint
and then composes one inert establish_coal_self_sufficiency Option.

Causal bootstrap isolation:

1. bootstrap materials are harvested from the same WORLD; no inventory injection;
2. exactly one bootstrap coal item is retained as the coal-drill seed;
3. all excess harvested bootstrap coal is quarantined;
4. all pre-existing bootstrap fuel in the promoted iron drill is removed and
   quarantined before the causal seed window;
5. the seed coal must be exhausted;
6. coal produced by the new drill is then transferred to both the coal drill and
   the incumbent iron drill;
7. the iron buffer baseline is measured only after the bootstrap-fuel retirement
   window, preventing residual burning energy from receiving survival credit;
8. the endogenous window must show positive coal growth and positive iron-buffer
   growth.

Promotion requires:

- coal_mined = true;
- endogenous_coal_reaches_fuel_consumer = true;
- external_bootstrap_fuel_retired = true;
- incumbent_iron_bootstrap_removed > 0;
- incumbent_iron_bootstrap_remaining = 0;
- coal_endogenous_growth > 0;
- incumbent_iron_buffer_growth > 0;
- survival gate preserves iron_extraction;
- one A2 execution attempt;
- transaction commit;
- zero human intervention;
- zero external resource injection.

Each live attempt writes a commit-bound artifact:

runs/audits/cortex_f5c_continuation_<base_seed>_coal_self_sufficiency_<commit12>.json

A failed attempt is retained as a counterexample and is never overwritten. The
next attempt requires a new committed/deployed correction. No automatic retry
or scheduler is permitted.

## Coal counterexample 1 — provisioning reset before A2 execution

The first coal continuation attempt on commit
191f3399a98aca15dc2ef4406f3e6b9dd96f38c6 is preserved at:

runs/audits/cortex_f5c_continuation_245044303_coal_self_sufficiency_191f3399a98a.json

The A2 grant was issued and validated, but the transaction body never ran.
The immediate failure was Gym ResetNeeded because the wrapper had not received
env.reset before step.

A deeper audit identified the causal infrastructure defect: FLE's registered
open_play factory calls TaskABC.setup(instance), and TaskABC.setup performs
instance.reset before returning the Gym environment. Consequently,
gym.make("open_play") reset the live WORLD during environment construction,
before the A2 Option transaction. The promoted iron cell disappeared from the
live WORLD even though the Option body did not execute.

This is a continuation-infrastructure counterexample, not a coal capability
failure. The artifact is immutable and receives no promotion credit.

Correction:

- continuation runners must not instantiate the registered open_play factory;
- attach_live_factorio_environment creates FactorioInstance + FactorioGymEnv
  directly with task=None and therefore does not call TaskABC.setup;
- the runner verifies the incumbent iron entities again immediately after live
  attachment and before checkpoint/grant;
- every coal attempt remains bound to the currently selected promoted iron seed;
- coal counterexamples across older base seeds remain visible in phase-state;
- the destroyed seed-245 WORLD is not manually repaired.

The next development action is a fresh preregistered episode on seed 1441387776.
It may intentionally reset the environment as episode initialization and must
rebuild iron_extraction autonomously before coal continuation is attempted.
