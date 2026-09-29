# CORTEX Phase 5 — Capability-Order Amendment

Status: **accepted after live Factorio 2.0 technology validation**
Amendment ID: `cortex-f5-factorio-2-tech-tree-order-amendment-20260929`
Base protocol: `cortex-f5-autonomous-factory-bootstrap-v1`
Effective after: `steam_power`

## Scientific reason

The original F5 capability order was preregistered as:

1. `iron_extraction`
2. `coal_self_sufficiency`
3. `iron_smelting`
4. `steam_power`
5. `electric_mining`
6. `copper_chain`
7. `powered_manufacturing`
8. `automation_science`
9. `logistic_science`

Live read-only inspection of the Factorio 2.0 technology graph showed that the
remaining order is causally non-executable without direct research mutation:

- `electric-mining-drill` is disabled until technology
  `electric-mining-drill` is researched;
- that technology has prerequisite `automation-science-pack` and consumes
  25 automation science packs;
- `assembling-machine-1` is disabled until `automation` is researched;
- `automation` has prerequisite `automation-science-pack` and consumes
  10 automation science packs;
- the `automation-science-pack` recipe is already enabled after the promoted
  steam/electronics trigger path.

Using `set_research()` to keep the preregistered order would bypass the real
technology graph and invalidate the F5 physical-autonomy objective.

## Effective remaining order

The four already-promoted capabilities are unchanged. The effective ladder is:

1. `iron_extraction`
2. `coal_self_sufficiency`
3. `iron_smelting`
4. `steam_power`
5. `copper_chain`
6. `automation_science`
7. `powered_manufacturing`
8. `electric_mining`
9. `logistic_science`

This ordering preserves causal dependencies:

`steam_power -> copper_chain -> automation_science -> powered_manufacturing -> electric_mining -> logistic_science`.

## What does not change

The amendment does **not** change:

- the original F5 protocol ID;
- development, pilot, held-out or reserved seed partitions;
- the authority ladder;
- the A0 ambient / one-shot A2 execution boundary;
- hard postconditions of any capability;
- promotion rule;
- rollback-on-regression rule;
- intervention ledger requirements;
- already-collected artifacts or their interpretation.

## Integrity binding

The machine-readable amendment is:

`configs/cortex_f5_capability_order_amendment_v2.json`

It is accepted only when it references the exact SHA-256 of the frozen v1
capability schema:

`863a0ff1e99617caccef6b0b61899adf3664737dcf968fedf5c12499948e62a8`

The phase-state validates that:

- the preregistered order is reproduced exactly;
- the effective order contains exactly the same nine capabilities;
- the promoted prefix through `steam_power` is unchanged;
- seeds, authority and hard-postcondition contracts are declared preserved;
- the reason code is
  `factorio_2_0_technology_dependency_incompatibility`.

If any amendment check fails, phase-state falls back to the preregistered
order. This is fail-closed: an invalid amendment cannot silently rewrite F5.

## Operational consequence

After `steam_power` promotion, the next bounded F5-C frontier is
`copper_chain`. The supervisor must not attempt `electric_mining` until
`automation_science` and `powered_manufacturing` have supplied the causal
technology/material prerequisites.
