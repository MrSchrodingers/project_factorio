# Evolution and survival model

The lab deliberately separates fast experimentation from real progression.

## Two arenas

### Lab arena

The curriculum runner uses the FLE iron_ore_throughput environment as a fast experimental
arena. It has populated inventory and unlocked technologies, so it is useful for measuring
placement, routing, buffering, throughput and repair strategies without spending most of an
experiment on bootstrap.

A lab generation is a challenger, not automatically a new baseline.

### Open-play validation

open_play_runner.py uses the FLE open_play environment. It starts with an empty inventory
and the Factorio 2.0 technology tree is not pre-unlocked. A lab champion must therefore
bootstrap from harvested world resources and cross the real early-game technology triggers
before it can become an open-play validated champion.

For Factorio 2.0.73 the early trigger sequence used by the validator is:

- craft 50 iron plates -> Steam Power;
- craft 10 copper plates -> Electronics;
- craft a lab after Steam Power + Electronics -> Automation Science Pack;
- produce red science and research Automation;
- use the unlocked assembling machine to produce powered red science.

These conditions are read from the Factorio 2.0.73 runtime data, not invented by the lab.

## Champion/challenger selection

Every lab generation records a fitness vector containing retained capabilities, normalized
production rates, external dependencies, failed validation stages, A* route cost/turn count,
and the genome used for placement exploration and routing.

A challenger is rejected if it fails hard survival gates. When an incumbent exists, the
challenger must also retain the incumbent's capabilities and throughput above the configured
retention ratio before a novel capability can justify promotion.

The absence of a champion is meaningful: no tested generation has yet satisfied all baseline
survival gates. The UI must not manufacture a baseline merely to fill the card.

## What is actually learning?

The learning surfaces must not be conflated:

- UCB1 placement learner updates action values from real Factorio placement trials.
- A* hyperparameter learner calibrates deterministic routing costs offline.
- Knowledge memory stores typed lessons and counterexamples from measured runs.
- Evolutionary selection mutates measured control parameters and keeps surviving challengers.
- Spatial demonstration data accumulates accepted planner demonstrations for future policies.
- Qwen3-4B weights are static; the model synthesizes plans and lessons but is not fine-tuned.
- The neural world model is not trained yet; the current model is explicit.

A completed research generation therefore means an experiment finished and its evidence was
recorded. It does not imply neural-weight training.

## Survival semantics

Transient success is rejected. A coal mine is not self-sufficient because it produced coal
once. Bootstrap fuel is quarantined; the cell must transfer mined coal back into itself and
continue growing its internal stock in a later window.

Copper expansion consumes coal from that validated internal buffer. If coal self-sufficiency
is rolled back, copper cannot claim an endogenous supply chain.

The dashboard exposes physical machine states such as no_fuel, no_power, no_ingredients and
blocked output in addition to historical capability claims.
