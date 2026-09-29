# CORTEX Phase 5 — Autonomous Factory Bootstrap & Learned Control

Status: **F5-B PASS / bounded authority bridge**  
Authority: **A0 ambient / A2 explicit one-shot grant available**  
WORLD mutation: **not performed by F5-B; first physical F5 use belongs to F5-C**  
Legacy evolution loop: **OFF**  
Continuous authority: **forbidden throughout F5**

## 1. Scientific objective

F5 tests whether the Cortex can transform an empty live WORLD into a physically
productive and sustainable factory while preserving every previously promoted
capability. Offline metrics, memory growth, model loss, UCB scores, datasets or
inference artifacts cannot complete F5 without physical construction evidence.

F5 starts only after the F4-C causal-memory exit gate is positive. The frozen
source inference is bound by SHA-256:

`3c4f782dec7c2bb5cebb47a60178ed11b251e2f23618e6341ab514d240499cb7`

## 2. Physical capability ladder

The canonical progression contains exactly nine physical capabilities:

1. `iron_extraction`
2. `coal_self_sufficiency`
3. `iron_smelting`
4. `steam_power`
5. `electric_mining`
6. `copper_chain`
7. `powered_manufacturing`
8. `automation_science`
9. `logistic_science`

A capability is promoted only from measured physical evidence, never merely
from entity existence.

## 3. Survival invariant

For the current promoted capability set C and a candidate new capability x,
promotion requires:

`forall c in C: validate(c) == PASS` and `validate(x) == PASS`.

Any regression of a previously promoted capability rejects the transaction and
requires rollback.

## 4. Authority ladder

- **A0** — observe only.
- **A1** — proposal only.
- **A2** — one transactional option.
- **A3** — one whole capability.
- **A4** — one bootstrap stage.
- **A5** — one bounded autonomous episode.
- **A6** — continuous authority; forbidden in F5.

Authority is external to learned policy. A learned ranker may rank candidates;
it never grants its own execution authority.

## 5. Transaction boundary

Every live action must preserve the boundary:

`Observation -> Option -> Validate -> Authority Grant -> Execute -> Measure -> Commit/Rollback`

F5-A creates no execution grant, acquires no WORLD lease and performs no RCON
or FLE mutation.

The intervention ledger is runtime state and therefore is not committed under
runs/. Its schema is frozen in
configs/cortex_f5_intervention_ledger_schema_v1.json; initialize it
idempotently with:

    PYTHONPATH=src .venv-fle/bin/python scripts/init_cortex_f5_state.py

The initializer refuses an existing ledger whose schema or protocol differs.

## 6. Frozen partitions

Seeds are generated before outcomes using SHA-256 over
`<protocol_id>:<partition>:<index>`, transformed deterministically to a positive
integer. This makes the partition independent of observed map difficulty.

- development: [1619515465, 853367368, 245044303, 1441387776, 1773655334, 1402093191, 1947355959, 1050682343]
- pilot: [911399256, 428306321, 252737005, 642270530, 1525084253, 821775588, 257016410, 432865231]
- held-out: [622056058, 103821794, 877445653, 1193350577, 1500483931, 134333095, 1929921547, 273758933, 1397125841, 1980668166, 919781304, 1717735329]
- F4 confirmatory reservation: [20261101, 20261102, 20261103, 20261104, 20261105, 20261106, 20261107, 20261108, 20261109, 20261110]

The F4 confirmatory partition remains untouched and is not reusable by F5.

## 7. Stage plan

### F5-A — Protocol freeze and semantic cleanup

No WORLD mutation. Freeze partitions, capability schema, authority schema and
UI semantics. Remove G97/UCB/curriculum progress from the current operational
story; retain them only as historical evidence.

### F5-B — Bounded Authority Bridge

Validate A2 with one transactional option and expiring authority grant.

### F5-C — Deterministic Autonomous Baseline

Use the engineering stack (goal DAG, production DAG, A*, structural planner,
resupply, memory, universal executor and rollback) to prove physical bootstrap
without neural policy control.

### F5-D — Trajectory Dataset

Persist typed transitions containing state, candidates, retrieval, selection,
execution, postconditions, capability delta, cost, rollback, reward components
and next state.

### F5-E — Policy Challenger

Train ranking/value challengers offline. Begin with contextual ranking/bandits;
conservative offline RL remains a challenger and never an authority source.

### F5-F — Paired Policy Evaluation

Compare deterministic baseline and learned challenger from the same checkpoint,
candidate set, tools, budgets, memory surface and authority.

### F5-G — Bounded Autonomous Episodes

Grant A5 only for finite episodes with tick, option and resource budgets,
exclusive lease, kill switch, per-option rollback and capability survival
monitoring.

## 8. Physical success gate

F5 physical success requires all of:

- functional logistic science;
- every promoted capability alive;
- zero human intervention;
- no external fuel/material injection;
- frozen sustainability soak passed;
- zero authority violations.

Hard physical failure cannot be compensated by a high weighted score.

## 9. F5-A exit condition

F5-A exits only when:

1. this protocol and `configs/cortex_f5_autonomy_v1.json` are frozen;
2. phase state reports F5-A with A0;
3. dashboard no longer reports G97/6%/second-miner work as current;
4. WORLD remains live but unmodified;
5. legacy evolution remains OFF;
6. the next authorized engineering task is F5-B bounded authority bridge.

## 10. F5-B exit condition

F5-B is PASS.

Canonical evidence:

- implementation commit: 080cd1544da1573cdf21a7c912e7388df45ea383
- audit: runs/audits/cortex_f5b_authority_bridge.json
- audit SHA-256: 9bcc4328f13e8dabf250bfe3a2d09ffd16994444571217825491ce703737cc2a
- targeted authority tests: PASS
- ambient authority: A0
- maximum bounded grant: A2
- max_executions: 1
- continuous authority: false
- policy self-grant: forbidden
- WorldLease acquired by audit: false
- grant issued by audit: false
- live Option executed by audit: false
- WORLD mutation by audit: false
- intervention ledger count: 0

F5-B proves the control-plane boundary. It does not count as physical factory
progress. The next checkpoint is F5-C deterministic autonomous baseline, whose
first real Option must use the same A2 grant/lease/transaction path.

