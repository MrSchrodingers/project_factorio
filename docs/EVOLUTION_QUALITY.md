# Evolution quality and maturity gates

The lab deliberately separates **game facts**, **experimental learning** and
**physical autonomy**. A successful stage or a short production spike is not
enough evidence for an autonomous factory.

## Current evidence classes

### Canonical game knowledge

Recipes, technologies and compatible production machines are read from the
live Factorio prototype tables. This graph is deterministic game knowledge and
is not treated as learned evidence. The static early-game catalog remains only
as a compatibility fallback.

The runtime graph drives production DAGs whenever the requested product exists
in the live catalog.

### Experimental learning

The resettable lab arena is used for controlled challenger evaluation. It can
promote a configuration only after incumbent capability/rate retention and
structural survival gates.

The open-play arena starts from the real technology tree and is an independent
generalization test. A lab champion is not an autonomous champion.

### Physical autonomy

Physical autonomy requires all of the following to agree:

- sustained production evidence;
- a compatible physical material/power topology;
- healthy fuel and power state;
- a zero-manual-logistics soak window;
- no loss of incumbent capabilities.

LuaFlowStatistics rates alone do not establish a live chain. Native short
precision samples are quantized and may retain recent production after the
physical producer has stopped. The evaluator therefore combines rates with the
live physical graph.

## Three arenas

1. **Lab** — resettable, controlled experiments and challenger selection.
2. **Open play** — from-scratch transfer/generalization across independent
   seeds and the real technology tree.
3. **Lifelong root** — created only after a champion passes closed-loop
   open-play validation and the multi-seed robustness gate. The final FLE
   GameState is persisted durably and can seed a future persistent factory
   arena across process restarts.

The durable checkpoint is intentionally gated. Persisting a failed or manually
supported factory would turn transient scaffolding into inherited debt.

## Promotion semantics

A promoted lab challenger means **better experimental configuration**, not
self-learning autonomous factory. The stronger claims require:

- open_play pass on three distinct seeds;
- closed_loop=true;
- no manual logistics during the soak;
- structural processing coverage;
- no fuel/power starvation;
- durable survivor checkpoint;
- neural models only when they beat deterministic/persistence baselines on
  generation holdout.

A recurrent model that loses to persistence remains advisory/rejected even if
it has enough samples.

## Observability

The dashboard exposes:

- canonical recipe/technology dependency graph;
- live physical factory topology;
- material, fluid and electrical edges;
- producer-to-processing coverage and isolated producers;
- causal model-data eligibility and multi-seed robustness;
- production charts aggregated to at least one-second bins for legibility;
- a true world-coordinate map viewport rather than CSS-only panning.

This distinction is important: observability should make a weak factory look
weak instead of smoothing it into a false impression of maturity.
