# Cortex Research — F3-B Verification, Credit and Experiment Ledger

Phase: F3 — Executive / Cognitive Loop
Checkpoint: F3-B — verification-after-action + credit assignment + experiment ledger
Decision: PASS parcial de F3
Implementation commit: 8b9330545407138fa4340c695988643e2256fb17
Authority: SHADOW only
Continuous autonomous authority: OFF
Evolution: inactive + disabled
Confirmatory seeds: frozen / untouched

## Purpose

F3-A made choice explicit before action. F3-B closes the opposite side of one episode: once an
action has actually been executed and measured, the Cortex must independently verify the prediction,
decide whether the evidence is eligible for credit, and persist that episode durably.

No counterfactual receives reward in F3-B.

## Verification semantics

The implementation reuses the canonical repair-loop evaluate_prediction semantics:

- held -> reward 1.0;
- did_not_hold -> reward 0.0;
- unmeasured -> reward None.

An unmeasured outcome is never folded into zero.

Credit is fail-closed and requires all of:

1. executed=true;
2. before and after measurements exist;
3. recomputed verdict/reward match the historical record;
4. recomputed reward is numeric.

If any condition fails, the episode is ineligible for credit.

## Persistent episode ledger

Implementation:

src/factorio_ai_lab/cortex/experiment_ledger.py

Ledger:

runs/ledger/cortex_executive_episodes.sqlite3

The SQLite ledger uses:

- WAL;
- synchronous=FULL;
- BEGIN IMMEDIATE for append;
- immutable episode IDs derived from observed episode identity;
- payload SHA-256 per episode;
- idempotent replay: same episode + same digest returns already_present;
- conflicting content for the same episode ID is refused;
- schema version persisted in ledger_meta.

The phase-state does not validate the SQLite file by whole-file hash. It validates the episode IDs
and payload digests published by the canonical artifact. Therefore the ledger may grow in later
phases without invalidating this checkpoint.

## Canonical historical replay

Artifact:

runs/audits/cortex_f3b_verification_credit_replay.json

SHA-256:

a8d76b2b1385606843c19c0fb7e221ccd17ae935d377888ac58a8ea3b89b74c1

Run:

cortex-f3b-credit-20260925T180634Z

Artifact implementation revision:

8b9330545407138fa4340c695988643e2256fb17, dirty=false.

Source:

runs/repairs.jsonl

Source SHA-256 at replay time:

327be3b7f7b594449f52ff18d4dcfe7e750a9473a1cf8da8c65893d0bf2ce376

Selection rule:

executed=true + prediction + before/after + verdict + numeric reward.

Observed results:

- total non-empty historical rows: 117;
- selected measured executed episodes: 54;
- recomputed verdicts matching recorded outcomes: 54 / 54;
- held: 45;
- did_not_hold: 9;
- unmeasured among selected: 0;
- credit eligible: 54;
- credit ineligible: 0;
- reward sum: 45.0;
- mean reward: 0.8333333333333334.

Ledger result:

- count before: 0;
- inserted: 54;
- count after: 54;
- quick_check: ok;
- published episode digests: 54;
- batch digest:
  1381f9260417d34be8419f53562f6a50beb43d7c851134acd6e5340847f68a91.

## Authority boundary

Canonical replay:

- authority=shadow;
- world_mutation=false;
- factorio_rcon_used=false;
- fle_environment_created=false;
- world_lease_acquired=false;
- execution_grant_created=false;
- continuous_authority=false.

No new Factorio experiment was run.

## Tests and gates

Focused F3-B/control-plane/UI gate:

- 38 tests PASS;
- Ruff PASS;
- JavaScript syntax PASS;
- whitespace PASS.

Full implementation gate:

- 1489 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

Tamper control:

- changing a persisted payload digest causes F3-B validation to fail and phase3_checkpoint to fall
  back to F3-A.

## Claim boundary

F3-B proves:

- executed historical outcomes can be reverified independently;
- positive and negative measured outcomes retain their semantics;
- missing measurement does not become zero reward;
- credit assignment is fail-closed;
- executive episodes persist idempotently across restarts;
- persistent episode identity/digest is independently checked by the control plane.

F3-B does not prove:

- learned-policy superiority;
- correctness of unexecuted counterfactual outcomes;
- paired decision quality against the legacy runner;
- live executive authority;
- sustained autonomy;
- F3 Exit Gate completion.

## Decision

F3-B: PASS parcial.

F3 remains active in SHADOW.

Next checkpoint: F3-C — paired shadow comparison against the legacy runner on the same observed
goals/candidate sets. This is a decision-structure comparison, not a performance claim for
unexecuted actions.
