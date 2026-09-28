# F4-C treatment and pilot-runner gate

The real-adapter readiness audit proves component bindings, checkpoint restore,
matched budgets and surfaces, quarantine and fail-closed outcome extraction. It
does not by itself prove that the treatment is active.

A causal MEMORY ON versus MEMORY ABLATED experiment additionally requires:

1. identical frozen candidate classes in both arms;
2. retrieval may affect ranking or selection only, never candidate generation;
3. MEMORY ABLATED contributes zero memory support;
4. every frozen candidate label has a planner binding;
5. every frozen candidate label has a protocol executor before pilot launch;
6. the pilot runner rejects evaluation and confirmatory partitions;
7. no pilot seed runs until treatment and runner audits both pass.

The frozen manifest contains 13 distinct candidate labels across the four task
families. The treatment layer lives in
factorio_ai_lab.cortex.causal_treatment.

The treatment preflight is read-only: pilot seed identifiers are used only as
references to already-frozen task specifications. It creates no FLE
environment, RCON mutation, lease, grant, or protocol outcome.

execution_ready must therefore mean all four gates pass:

- harness preflight;
- real-adapter readiness;
- treatment semantics;
- pilot-runner readiness.

This prevents an inert treatment path, where retrieval is logged but ignored,
from being mistaken for a causal experiment.
