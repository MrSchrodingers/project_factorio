# Generative / ML research architecture

Factorio AI Lab treats Factorio as a production-engineering research environment rather than
as a benchmark for a monolithic chat model.

## Research hypothesis

A hierarchy combining generative models, learned models and exact/planning methods should
produce more reliable factories than asking one LLM to directly control every action.

Control flow:

    engineering goal / frontier
            |
            v
    production DAG + constraints
            |
            +---- local Qwen planner / hypothesis generator
            +---- A* deterministic spatial teacher
            +---- neural spatial policy challengers
            +---- recurrent world-model challengers
            +---- evolutionary engineering genome
            |
            v
    transactional FLE executor
            |
            v
    Factorio 2.0.73 ground truth
            |
            v
    telemetry -> counterexamples -> training -> model arena -> selection

No trained neural component receives control authority merely because training completed.
Every learned model competes against an explicit baseline on held-out evidence.

## Generative knowledge

Qwen3-4B runs locally through llama.cpp. Its weights are currently static. Its learning surface
is the external typed knowledge memory.

A generated lesson must cite evidence keys. Numeric claims and rate claims are checked by a
deterministic verifier. Unsupported output is rejected and replaced by a deterministic
fallback. The dashboard reports verified/fallback counts so generative grounding is measurable.

The LLM also advises evolutionary mutation only through a bounded parameter schema. It can
suggest increase/decrease/hold for whitelisted engineering parameters; it cannot invent an
unbounded genome.

## Evolutionary engineering genome

The genome includes structural production decisions:

- route turn penalty;
- UCB exploration;
- placement radius;
- coal safety stock;
- producer refuel budget;
- copper-mining and copper-smelting fuel budgets;
- survival fuel budget;
- buffer target;
- minimum gain required before destructive rebuild;
- open-play harvesting targets and radius.

Champion/challenger selection is multi-objective. Heterogeneous rates are never summed to
decide promotion. Existing capabilities must satisfy retention gates, failures cannot increase,
and improvements are evaluated per commensurate metric.

## Recurrent world models

Continuous telemetry is recorded independently of dashboard clients.

Three references are evaluated:

1. persistence: next state equals current state;
2. Echo State Network: recurrent reservoir with trained ridge readout;
3. PyTorch GRU: trainable recurrent state model with next-state and operational-risk heads.

Temporal validation alone is insufficient because adjacent samples are correlated. Control
eligibility therefore depends on generation-level holdout.

## Spatial policies

Weighted A* remains the ground-truth teacher and baseline. Accepted routes become demonstrations.

Current learned challengers:

- autoregressive NumPy MLP;
- PyTorch Transformer/attention local-route policy.

Evaluation uses unseen rollout tasks. Route cost relative to A* and rollout success matter more
than action-classification accuracy. Proposal eligibility and control eligibility are separate.

## Production DAG

Recipe requirements are expanded into a rate-balanced DAG using Factorio 2.0.73 recipe data.
For each target the planner computes intermediate rates, raw plate requirements, crafts/s and
minimum machine count.

The DAG turns frontiers such as electronic circuits and logistic science into material budgets
instead of relying on incidental inventory left by an earlier curriculum stage.

## Destructive optimization

Destruction/rebuild is transactional. A dominated branch may be removed only inside a
checkpointed candidate action. The replacement must preserve required capabilities and exceed
the configured gain threshold; otherwise the world is rolled back.

## Arenas and validation levels

The project distinguishes:

1. Lab champion: survives accelerated lab-play gates.
2. Open-play production/technology validation: starts empty and crosses the real Factorio 2.0
   technology triggers.
3. Strict spatial/navigation validation: routing/navigation is judged without assisted movement.

FLE 0.4.3 can time out in request-path. Open-play records any fast-reposition fallback
explicitly as assisted navigation. Such a run may validate production and technology
progression, but must not be reported as strict navigation validation.

## Continuous training loop

The systemd evolution service runs one generation per process:

    lab challenger
     -> selection
     -> ESN/GRU training + holdout
     -> open-play transfer test when needed
     -> typed counterexample
     -> bounded mutation advice
     -> process exits
     -> systemd backoff/restart

One-generation process boundaries make artifacts durable and prevent a single long-lived Python
process from silently accumulating corrupted state.

## Physical autonomy gate

Open-play distinguishes commissioning from autonomy.

Manual harvest_resource, insert_item, extract_item and craft_item calls are instrumented
inside the transactional executor. Attempted and committed counters are separated so rejected
transactions do not contaminate accepted intervention debt.

The open-play sequence now uses explicit terminology:

- Raw bootstrap: world harvesting is allowed and measured.
- Coal commissioning / Steam commissioning: the agent may manually commission equipment, but
  these stages do not claim autonomy.
- Electric mining transition: the system researches electric mining and replaces the
  agent-mediated material flow with electric drills, belts, inserters and poles.
- Autonomy soak: no harvest/insert/extract calls are permitted. The factory is free-running.

A validated open-play champion requires the final physical autonomy report to have
closed_loop=true. A successful earlier curriculum stage cannot bypass this guard.

The autonomy evaluator requires simultaneous evidence for fuel distribution, electrical
distribution, smelting logistics, live coal/iron/copper production, no critical fuel/power
starvation and a sufficiently long zero-intervention soak.

## Intervention debt

Two intervention baselines are retained:

- post raw-bootstrap intervention debt, used by evolutionary selection to prefer generations
  that require less operator mediation;
- autonomy-window intervention count, which must be zero for closed-loop validation.

This distinction allows historically necessary commissioning actions to remain visible without
misrepresenting them as autonomous logistics.

## Bounded FLE checkpoints

Long Factorio simulation intervals are not placed inside a single FLE action. Research and
science scale-up are split into bounded checkpoints with persistent diagnostics.

For example, Logistic Science Pack validation is decomposed into preparation, short red-science
production windows, research preparation and short research windows. This prevents a
120-second evaluation timeout from erasing causal evidence and lets the dashboard expose
partial progress while the experiment is running.

## Renderer fidelity

The live map queries the envelope of the physical factory rather than a fixed player-centered
radius. Water and special terrain are read from the live Factorio surface. Thousands of tile
records are compacted into horizontal terrain runs before crossing RCON.

The renderer uses official local game textures for water, refined concrete, concrete and stone
path. PNG frames are cached briefly by the dashboard so repeated requests do not recompose an
unchanged scene.


## Single-writer runtime and causal action telemetry

Every lab/open-play experiment acquires an OS-level exclusive lease on the shared Factorio
world before the first reset. A second writer fails fast instead of contaminating an experiment.

Each FLE transaction receives a stable action id, semantic action class, code hash, start time,
duration and acceptance result. While the transaction is active, a heartbeat is refreshed
independently of Factorio ticks. The dashboard therefore distinguishes an executing action,
the interval between actions and a genuinely idle runtime without inferring liveness from four
equal world ticks.

World telemetry is action-conditioned only while an action is active. The append-only
`action_transitions.jsonl` is the authoritative causal trace; dashboard telemetry supplies the
time-series state observed during that action.

## Action-conditioned world model

The trainable GRU is a controlled dynamics model. Its recurrent input is

    [physical/production state, typed action control]

while its target remains the next physical/production state. Historical snapshots without an
action id are retained for descriptive analysis and the state-only ESN baseline, but cannot make
the controlled GRU eligible. GRU training requires at least 500 action-labelled samples across
three independent runs, followed by generation-level holdout.

This prevents a model trained mainly on duplicated idle snapshots from being promoted as a
control model.

## Material requirements planning ledger

Construction preflight uses a finite-horizon material ledger. For each item it distinguishes:

- on-hand stock;
- work in progress;
- incoming material;
- previously reserved material;
- explicit safety stock.

A requirement can consume only net available material. Electric-backbone construction exposes
the ledger in its diagnostics, including shortages and projected surplus. This prevents the same
plate buffer from being counted simultaneously for research, belts and later construction.

## Structural infrastructure optimization

Power distribution is costed and built as a shared minimum-spanning network over the generator
and electric consumers instead of as independent generator-to-consumer star links.

Belt estimates retain an explicit detour margin. A small route-buffer shortfall is treated first
as a CAPEX/layout counterexample: the strategy tightens route and belt safety margins before it
is allowed to inflate bootstrap iron. If the minimum structural margins still fail, material
requirements may increase from measured evidence.

Thus a failure such as `iron=546/602` can change the design rather than merely requesting more
iron.

## Cross-run counterexample replay

Open-play failures are persisted in an append-only counterexample buffer with a deterministic
failure signature, stage, phase, diagnostics, configuration and applied repair. Duplicate
records from the same run/signature are rejected.

The mutation advisor receives recent counterexamples from the same stage. Deterministic repair
also uses repeated failures to change its structural response. This is the persistent symbolic
learning path between independent processes.

## Multi-seed survival qualification

One successful open-play run is not sufficient to become an operationally validated champion.
The same immutable champion/configuration must pass closed-loop open play on at least three
distinct seeds. While this qualification is pending, the evolution loop freezes the candidate
instead of replacing it with a new lab mutation.

The systemd service deliberately executes one outer iteration per process. The seed is derived
from the persistent evolution history, so service restarts cannot silently repeat the same seed.

After a champion is validated, the loop continues. A later lab challenger is sent back through
the complete open-play and multi-seed survival gate whenever its run id differs from the
currently validated champion. `open_play_validated_champion.json` is therefore an incumbent,
not a terminal flag.
