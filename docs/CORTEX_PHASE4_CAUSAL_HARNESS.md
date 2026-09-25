# Cortex F4-C — Paired causal evaluation harness

Status: **synthetic preflight only**.

This checkpoint implements the mechanics required by the frozen F4-C
preregistration without running a pilot, held-out evaluation, confirmatory seed,
FLE environment, RCON mutation, world lease, or execution grant.

## What the harness owns

The harness freezes and audits the experimental boundary around a task adapter:

- capture one checkpoint and restore that exact state before both paired arms;
- give both arms the same task, candidate surface, deterministic tool surface,
  outcome extractor and protocol budget;
- expose F4-B memory normally in MEMORY ON;
- expose an explicit empty retrieval result in MEMORY ABLATED, removing no
  unrelated capability;
- quarantine all evaluation-time memory writes;
- verify the canonical memory snapshot before and after each arm;
- extract the preregistered primary endpoint J centrally;
- invalidate missing primary data rather than coercing it to zero;
- invalidate budget overruns and cross-arm surface mismatches;
- persist enough structure for J and delta_J to be recomputed.

## Synthetic preflight boundary

validate_cortex_f4c_harness.py uses a deterministic in-memory world fixture.
It deliberately mutates that fixture inside an arm so restore/isolation can be
tested, but it never imports or creates FLE, never contacts Factorio/RCON, and
never executes a protocol seed. The fixture runs once per task family solely to
exercise the harness contract.

A PASS means the **paired harness mechanics are validated in simulation**. It
does **not** mean the real task adapters are validated and therefore does not
set execution_ready=true.

The next gate is validation of real, family-specific adapters for:

1. structural flow repair;
2. fuel / energy recovery;
3. spatial logistics routing;
4. production transition planning.

Those adapters must satisfy the same harness interface and must be proven
against a disposable non-protocol preflight world before any pilot seed may be
launched.

## Scientific boundary

Synthetic preflight results are infrastructure evidence only. Their J or
delta_J values are never pooled with pilot/evaluation data and cannot support
a memory-benefit claim. The F4 Exit Gate remains open until the frozen held-out
paired experiment is completed and its preregistered inference rule passes.
