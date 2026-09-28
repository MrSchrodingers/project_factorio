# Cortex F4-C — causal memory-ablation held-out result

Status: **PASS — F4 Exit Gate validated under the frozen primary protocol**.

This document closes the preregistered F4-C experiment. It does not authorize
confirmatory seeds, continuous evolution, or any further experimental run.

## Frozen design

- Protocol: cortex-f4c-memory-ablation-transfer-v1
- Evaluation code commit: 6772d81c859a776f199f417616fbae8ce99ad40f
- Intervention: MEMORY ON versus retrieval-only MEMORY ABLATED.
- Candidate generation, non-memory tools, budgets, checkpoint restore and
  outcome extraction were matched across arms.
- Held-out evaluation: 20 fixed pairs, exactly 5 per family.
- Counterbalancing: 10 MEMORY ON first / 10 MEMORY ABLATED first.
- No outcome-dependent exclusions or replacement seeds.
- Pilot evidence was instrumentation-only and excluded from primary inference.
- Confirmatory seeds 20261101–20261110 remained untouched.
- Continuous evolution remained inactive and disabled.

## Primary result

All 20 held-out pairs passed canonical review and entered primary inference.

| Quantity | Result |
| --- | ---: |
| Valid pairs | 20 / 20 |
| Valid pairs per family | 5 / 5 / 5 / 5 |
| Mean paired ΔJ | 0.202219444 |
| Median paired ΔJ | 0.001729167 |
| Paired Cohen dz | 0.640886 |
| Exact one-sided sign-flip p | 9.53674316406e-07 |
| Exact 95% confidence-set lower | 0.067739861 |
| Exact 95% confidence-set upper | 0.336780787 |
| SESOI | 0.05 |
| Positive causal criterion | **TRUE** |

The preregistered positive criterion required all of:

1. exact one-sided p <= 0.05;
2. exact confidence-set lower bound > 0;
3. mean ΔJ >= 0.05;
4. at least 16 analyzable pairs overall;
5. at least 3 analyzable pairs per family.

All five requirements passed. Therefore
phase4_exit_gate.causal_memory_ablation_transfer = true and
phase4_exit_gate.validated = true.

## Interpretation boundary

The result supports a **positive aggregate causal effect of frozen cognitive
memory access on the preregistered F4-C benchmark**. It does **not** establish
that the effect is uniform across tasks or families.

The strong separation between the mean (0.202219) and
median (0.001729) indicates substantial heterogeneity.
The following family view is explicitly **post-hoc descriptive** and was not
used for the F4-C decision:

| Family | n | Mean ΔJ | Median ΔJ | Pairs ΔJ >= SESOI |
| --- | ---: | ---: | ---: | ---: |
| fuel_energy_recovery | 5 | 0.671271 | 0.671271 | 5/5 |
| production_transition_planning | 5 | 0.135367 | 0.000333 | 1/5 |
| spatial_logistics_routing | 5 | 0.000403 | 0.000414 | 0/5 |
| structural_flow_repair | 5 | 0.001838 | 0.001854 | 0/5 |

Accordingly, the scientifically defensible conclusion is aggregate: memory
access materially improves the frozen benchmark under the preregistered
endpoint, while the magnitude and prevalence of the benefit vary sharply
across transfer surfaces.

## Provenance

- Protocol file SHA-256: 82253e71dc94cd5ad803e1340523d4053dfc0a11299848709e6f7dc8414f7c41
- Canonical protocol SHA-256: e6633dccc851470a6943ae426b767cb1c039cedb1bede23dc9e6f1c27c94bdac
- Source-memory manifest SHA-256: e6aa69816fe992f6b2a6afc8aff529fa5f1572106ca0939cee830af5a6cf3399
- Treatment audit SHA-256: c833bd2a979da2370f3955f48df20f05fe7562efeede50e2e41b269375c63767
- Pilot-runner audit SHA-256: 1e9f47159d9abcc896a632b49d86ed4ff18fc208f79d5f19f346a42b0ddc2dd8
- Evaluation-runner audit SHA-256: a447efeca4c4398380baf26c80bc762df7e2726c454659b112e8d95272ac0617
- Primary-inference artifact SHA-256: 3c4f782dec7c2bb5cebb47a60178ed11b251e2f23618e6341ab514d240499cb7
- Evidence manifest: docs/CORTEX_PHASE4_CAUSAL_EVIDENCE_MANIFEST.json

The evidence manifest contains the canonical SHA-256 of every pilot and
held-out pair/review artifact without using pilot outcomes in primary inference.

## Operational state after closure

- WORLD live remains connected and empty (0 entities at closure check).
- The task-world experiment never used live Factorio RCON/FLE authority.
- factorio-ai-evolution remains inactive and disabled.
- F4 blocker is clear.
- resume.do_not_start_another_seed = true.
- Confirmatory seeds remain frozen and are not authorized by this result.

No further experimental seed should run as part of F4-C.
