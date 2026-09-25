# Cortex Research — F2-G5 Baseline-Only Enforcement

Phase: F2 — Action/Option Ontology and Universal Executor
Checkpoint: F2-G5 — legacy runner baseline-only enforcement
Decision: PASS — F2 Exit Gate complete
Implementation commit: 4fc7217d3e5e0fecb076bfd63305e5498eb52f4e
Control-plane freeze fix: 581ed8c38de0a9ffdc908c250063ee9c9a0e8158
Test isolation follow-up: 4ef61eea2d1d6890cba257c5df1a5deaae2c31a0
Continuous autonomous authority: OFF
Evolution: inactive + disabled
Confirmatory seeds: untouched / frozen

## Scientific purpose

F2 could not close while curriculum_runner remained callable as generic execution authority. The
Cortex had already demonstrated a functional live chain through the generic Option API in F2-G4B,
but the old stage-coded runner still needed an explicit fail-closed boundary so that it could remain
a baseline instrument without becoming an accidental Cortex execution path.

F2-G5 establishes that boundary mechanically.

## Runtime enforcement

The legacy runner now requires the exact execution identity baseline. run_curriculum receives a
mandatory execution_role and calls require_legacy_baseline_role before importing gym, listing FLE
environments, creating an environment, acquiring a WorldLease, or mutating the world.

Any other role is refused with LegacyRunnerAuthorityError and the named refusal
legacy_runner_baseline_only.

Authorized legacy launchers label themselves explicitly as baseline:

- scripts/run_corrected_baseline_seed.py;
- scripts/run_curriculum.sh;
- the historical evolution_loop callsite.

The Cortex package is separately audited by AST and contains no import of curriculum_runner.

## Canonical audit

Artifact:

runs/audits/cortex_f2g5_baseline_only_enforcement.json

SHA-256:

f7d796d1c406a7425fd3b14a783227364bb62ab42cef477a59960c41f4d721ae

Artifact provenance:

- code commit: 4fc7217d3e5e0fecb076bfd63305e5498eb52f4e;
- source tree clean: true;
- status: pass;
- legacy_runner_role: baseline;
- fail_closed_before_environment_creation: true;
- cortex_imports_legacy_runner: false;
- world_mutation: false;
- factorio_rcon_used: false;
- all static checks: true.

The audit was generated only after the implementation commit existed on a clean tree. Before this
document existed, the machine-readable control plane deliberately stayed at F2-G4B even though the
audit artifact already passed. This proves that neither documentation alone nor artifact alone can
self-promote G5.

## F2 Exit Gate

The machine-readable F2 gate requires all four conditions:

1. generic Option API exists;
2. universal transactional execution exists;
3. a functional chain was built live through the Option API without curriculum_runner;
4. the legacy runner is mechanically baseline-only.

F2-G4B supplies the bounded live functional evidence. F2-G5 supplies the final authority
separation. With both present and validated, F2 is complete.

This does not claim sustained autonomy. The last live Option canary produced 13 iron plates and
terminated no_fuel. It also does not authorize F3 execution, continuous autonomous authority,
evolution, or confirmatory-seed tuning.

## Dashboard recovery included in the implementation commit

The same implementation commit repairs an operational failure that made the dashboard appear to
regress to F2-B with zero entities.

Root cause:

- the deployed dashboard was still on the older G4A release;
- /api/world raised RCONNotConnected from a stale RCON client after a checkpoint/reset;
- loadInitialState used all-or-nothing Promise.all across 17 endpoints;
- one HTTP 500 prevented every initial payload from hydrating;
- the WebSocket then died on the same sampling exception and entered a reconnect loop;
- the service was still scoped to the old exploratory baseline.

Hardening:

- RCONNetworkError is caught and stale clients are discarded for reconnection;
- WebSocket sampling degrades locally instead of closing the whole stream;
- initial HTTP hydration uses Promise.allSettled and applies successful endpoints independently;
- the main dashboard scope is global Cortex state;
- Cortex phase rendering now works in global scope;
- the frontend build stamp was regenerated.

This is resilience hardening, not scientific evidence. It must be validated again after deployment
against BUILD_INFO, /api/context, /api/world and the live WebSocket.

## Verification

Focused gate:

- 106 tests PASS;
- Ruff PASS;
- JavaScript syntax PASS;
- whitespace PASS.

Post-implementation control-plane gate:

- 21 phase-continuity tests PASS;
- Ruff PASS;
- F2 complete forces do_not_start_another_seed=true while confirmatory seeds remain frozen.

Full implementation gate:

- 1467 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

Final closure gate after the confirmatory-freeze and G4B test-isolation follow-ups:

- 1467 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff, compileall, JavaScript, TypeScript/Vite and whitespace PASS.

## Decision

F2-G5: PASS.

F2: COMPLETE.

F3 is no longer blocked by an unmet F2 Exit Gate, but remains not started. Opening F3 requires an
explicit phase transition and does not inherit continuous authority from F2.
