# Cortex Phase 0 — Baseline Manifest

- captured_at_utc: 2026-09-24T03:07:34.155283+00:00
- repository: /srv/factorio-ai-lab
- branch_before_cortex: fix/live-payload-e-mapa
- baseline_commit: 74a1bf9c0f8792a68d7252b11d477835ec93d508
- baseline_tag: cortex-pre-research-baseline-20260923
- cortex_branch: research/cortex-v1

## Scientific status at capture

~~~json
{
  "challenger_run_id": "curriculum-20260924T025516Z",
  "challenger_status": "rejected",
  "champion_generation": 37,
  "champion_run_id": "curriculum-20260923T035414Z",
  "generation": 96,
  "stage": "Logistic science",
  "stage_status": "failed"
}
~~~

## Pre-existing dirty tree

These files were modified/untracked before the Cortex Phase 0 documentation commit. They are
not part of the scientific-foundation change and must be reviewed/validated in Phase 1.

~~~text
M README.md
 M src/factorio_ai_lab/dashboard/static/index.html
 M src/factorio_ai_lab/dashboard/static/styles.css
 M src/factorio_ai_lab/experiments/curriculum_runner.py
 M src/factorio_ai_lab/planning/delivery.py
 M tests/test_coal_distribution.py
?? docs/CORTEX_HANDOFF.md
?? docs/CORTEX_RESEARCH_PROGRAM.md
?? tests/test_ore_handoff.py
?? tests/test_trunk_delivery.py
~~~

### SHA-256 of pre-existing dirty files

| Path | SHA-256 |
|---|---|
| src/factorio_ai_lab/experiments/curriculum_runner.py | 3403fb65af2f6718e511e482ddb421b0653ede09008275644829eebdf53f29d1 |
| src/factorio_ai_lab/planning/delivery.py | 29075141e8011f4770bced913dd57a3b89970ee15edb5974193a8a24c84d6d9b |
| tests/test_coal_distribution.py | aa4b4593b2eb445135a540df2100ff65b8f8b48daf75506567218217eb8ce646 |
| tests/test_ore_handoff.py | ebfb330a224cebb490a79218c1a1290f551826222693b72707eebbbb9f4aee9b |
| tests/test_trunk_delivery.py | bdd197377fad420aa2eb46fa83b1406c74a0e2c8d3a8cfd9b401c9fc585944e5 |

## Diff statistics at capture

~~~text
.../experiments/curriculum_runner.py               | 963 ++++++++++++++++++---
 src/factorio_ai_lab/planning/delivery.py           | 442 +++++++++-
 tests/test_coal_distribution.py                    | 205 ++++-
 3 files changed, 1497 insertions(+), 113 deletions(-)
~~~

## Interpretation

This manifest does not endorse or reject the dirty changes. It freezes their identity so the
Cortex refactor cannot accidentally claim them as its own work. Phase 1 must test and either
commit, split, or retire them deliberately.
