# F1-B — Storage and Docker Log Hardening

**Status:** PASS for Factorio AI Lab scope.

## 1. Trigger

During the exploratory baseline series, /var reached 95% utilization with approximately 2.0 GiB free.

Docker json-file logs were unbounded. Relevant measurements included:

- playit: approximately 2.18 GiB, outside this project's scope;
- fle-local-factorio_0-1: approximately 787 MiB;
- other unrelated services also had large logs.

Only the Factorio AI Lab component was modified.

## 2. Risk

A baseline seed writes telemetry, Factorio logs, screenshots and experiment artifacts. Starting a seed with critically low free space can create infrastructure-invalid results, partial manifests or corrupted evidence.

The previous launcher checked code provenance and concurrency but not storage headroom.

## 3. Preventive gate

scripts/launch_corrected_baseline_seed.py now measures disk usage before starting a seed.

Default minimum:

    1 GiB free on /var

If the requirement is not met, launch fails before:

- isolation snapshot mutation;
- seed process creation;
- Factorio world reset;
- scientific evidence generation.

The measured storage state is persisted in the launcher record.

## 4. Factorio log rotation

scripts/fle_cluster_local.py now generates Docker services with:

    logging:
      driver: json-file
      options:
        max-size: 50m
        max-file: 3

The active Factorio compose was updated between seeds, while no baseline was running.

Before recreation:

- Factorio log: approximately 787 MiB;
- restart: unless-stopped;
- log driver: json-file without options.

After recreation:

- same /factorio volume preserved;
- restart: unless-stopped;
- log driver: json-file;
- max-size: 50m;
- max-file: 3;
- RCON TCP: PASS.

Free space on /var increased from about 2.0 GiB to about 2.8 GiB.

## 5. Scope boundary

The approximately 2.18 GiB playit log was not modified because it is outside the Factorio AI Lab project.

This hardening is infrastructure-only. It does not alter:

- gameplay policy;
- planner;
- repair selection;
- learning algorithm;
- scientific runtime 95c34a53.

## 6. Continuity requirement

Before every future baseline seed:

1. verify no seed is already running;
2. verify frozen scientific release;
3. verify /var storage headroom;
4. verify global isolation preconditions;
5. only then create the detached process.

A storage failure is an infrastructure invalidation, not an agent failure.
