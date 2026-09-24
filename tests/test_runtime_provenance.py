"""Runtime isolation and clean-build promotion are scientific invariants."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from factorio_ai_lab.experiments.curriculum_runner import enforce_clean_revision
from factorio_ai_lab.learning.survival import PromotionDecision
from factorio_ai_lab.paths import code_revision, revision_is_promotable


def _promoted() -> PromotionDecision:
    return PromotionDecision(
        promoted=True,
        reason="better challenger",
        regressions=(),
        improvements=("throughput",),
        retention_ratio=0.8,
    )


def test_a_clean_build_is_promotable() -> None:
    revision = {"commit": "abc1234", "dirty": False}
    assert revision_is_promotable(revision)


def test_dirty_unknown_or_unversioned_code_cannot_promote() -> None:
    assert not revision_is_promotable({"commit": "abc1234", "dirty": True})
    assert not revision_is_promotable({"commit": "abc1234", "dirty": None})
    assert not revision_is_promotable({"commit": None, "dirty": False})


def test_promotion_is_withheld_for_dirty_code() -> None:
    decision = enforce_clean_revision(
        _promoted(),
        {"commit": "abc1234", "dirty": True},
        required=True,
    )

    assert not decision.promoted
    assert "clean_code_revision_required" in decision.withheld_gates
    assert "withheld" in decision.reason


def test_clean_code_preserves_the_selection_decision() -> None:
    decision = _promoted()
    assert enforce_clean_revision(
        decision,
        {"commit": "abc1234", "dirty": False},
        required=True,
    ) is decision


def test_deployed_release_reads_build_info_without_git(tmp_path) -> None:
    (tmp_path / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "commit": "1234567890abcdef",
                "branch": "research/cortex-v1",
                "dirty": False,
                "built_at": "2026-09-24T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    revision = code_revision(root=tmp_path)

    assert revision["commit"] == "1234567890abcdef"
    assert revision["dirty"] is False
    assert revision["source"] == "build_info"
    assert revision_is_promotable(revision)


def test_state_root_can_be_separate_from_code_root(tmp_path) -> None:
    env = os.environ.copy()
    env["FACTORIO_AI_STATE_ROOT"] = str(tmp_path)
    code = (
        "from factorio_ai_lab.paths import CODE_ROOT,STATE_ROOT,RUNS_DIR;"
        "print(CODE_ROOT);print(STATE_ROOT);print(RUNS_DIR)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    code_root, state_root, runs_dir = done.stdout.strip().splitlines()

    assert code_root != state_root
    assert state_root == str(tmp_path.resolve())
    assert runs_dir == str((tmp_path / "runs").resolve())
