# Dashboard Temporary-Storage Hardening

**Status:** PASS
**Scope:** dashboard deployment and visual audit tooling
**Scientific impact:** none; F1 scientific runtime unchanged

## Incident

During the F2-B dashboard deployment, systemd failed to spawn the dashboard process with:

    Failed to spawn 'start' task: No space left on device

The application process had not started (Mem peak: 0B), so this was not a FastAPI crash.

Filesystem inspection showed:

    /tmp 437 MiB total
    /tmp 433 MiB used
    /tmp 0 MiB available
    100% usage

The majority of temporary usage came from orphaned Chromium headless profiles created by previous screenshot audits. Three old audit Chromium process trees were still alive and referenced those profiles.

## Remediation

Only project audit Chromium processes were terminated. No interactive Chromium process was running.

Temporary Chromium scoped directories were removed after the owning audit processes exited.

Result:

    /tmp before: 100%
    /tmp after: 23%
    available after: ~318-319 MiB

A subsequent dashboard restart completed immediately:

    ActiveState=active
    Result=success
    NRestarts=0
    ExecMainStatus=0

## capture_dashboard.sh

A canonical capture helper now exists:

    scripts/capture_dashboard.sh

Properties:

- per-capture profile under STATE_ROOT/runs;
- per-capture cache under STATE_ROOT/runs;
- TMPDIR and XDG_RUNTIME_DIR scoped under STATE_ROOT/runs;
- bounded wall-clock timeout;
- bounded virtual-time budget;
- cleanup trap for Chromium processes matching the unique profile;
- removal of the temporary capture tree;
- failure if no screenshot is produced.

The script therefore does not depend on the small host /tmp partition for Chromium profiles.

## deploy_dashboard.sh preflight

Dashboard deployment now measures free space on FACTORIO_AI_TMP_PATH (default /tmp).

Default required headroom:

    64 MiB

If free space is below that threshold, deploy exits with code 65 before switching a new runtime.

Environment override FACTORIO_AI_MIN_TMP_FREE_BYTES exists for controlled testing and future host-specific tuning.

## Verification

Deterministic tests:

    tests/test_dashboard_capture_contract.py

cover project-scoped temporary profile, cleanup after a fake Chromium run, and deploy refusal under a forced impossible /tmp threshold.

Result:

    2 passed

Real capture validation:

    runs/audits/dashboard_f2b_shadow.png

Post-capture invariants:

    chromium-capture.* directories = 0
    audit Chromium processes = 0
    /tmp usage = 23%

## Research boundary

This hardening does not alter ActionRequest semantics, UniversalExecutor, legacy parity, Factorio world, baseline evidence, scientific runtime 95c34a53..., or confirmatory seeds.

It is an infrastructure reliability correction only.
