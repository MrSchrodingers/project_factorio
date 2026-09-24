"""The production runtime must be an immutable, attributable release."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "ops" / "systemd"


def _unit_text(name: str) -> str:
    return (SYSTEMD / name).read_text(encoding="utf-8")


def _single_directive(name: str, key: str) -> str:
    prefix = f"{key}="
    matches = [
        line[len(prefix):].strip()
        for line in _unit_text(name).splitlines()
        if line.startswith(prefix)
    ]
    assert len(matches) == 1, (name, key, matches)
    return matches[0]


def test_evolution_executes_the_immutable_current_release() -> None:
    name = "factorio-ai-evolution.service"
    assert _single_directive(name, "WorkingDirectory") == "/srv/factorio-ai-lab"
    assert _single_directive(name, "ExecStart").startswith(
        "/srv/factorio-ai-runtime/current/scripts/run_evolution_loop.sh"
    )
    environment = "\n".join(
        line.strip()
        for line in _unit_text(name).splitlines()
        if line.startswith("Environment=")
    )
    assert "FACTORIO_AI_STATE_ROOT=/srv/factorio-ai-lab" in environment
    assert "FACTORIO_AI_REQUIRE_CLEAN_PROMOTION=1" in environment


def test_dashboard_reads_state_but_executes_its_own_release_code() -> None:
    name = "factorio-ai-dashboard.service"
    assert _single_directive(name, "WorkingDirectory") == "/srv/factorio-ai-lab"
    assert _single_directive(
        name, "ExecStart"
    ) == "/srv/factorio-ai-dashboard-runtime/current/scripts/run_dashboard.sh"
    environment = "\n".join(
        line.strip()
        for line in _unit_text(name).splitlines()
        if line.startswith("Environment=")
    )
    assert "FACTORIO_AI_STATE_ROOT=/srv/factorio-ai-lab" in environment
    assert (
        "FACTORIO_AI_DASHBOARD_SCOPE="
        "baseline:cortex_baseline_protocol_v1:exploratory:auto"
    ) in environment


def test_llm_has_a_host_memory_guard() -> None:
    name = "factorio-ai-llm.service"

    assert _single_directive(name, "MemoryHigh") == "6G"
    assert _single_directive(name, "MemoryMax") == "9G"
    assert _single_directive(name, "MemorySwapMax") == "512M"
    assert _single_directive(name, "Restart") == "on-failure"


def test_deployer_requires_clean_source_and_embeds_build_provenance() -> None:
    script = (ROOT / "scripts" / "deploy_runtime.sh").read_text(encoding="utf-8")

    assert 'git status --porcelain' in script
    assert 'refusing deploy: source checkout is dirty' in script
    assert 'git archive "$COMMIT"' in script
    assert "BUILD_INFO.json" in script
    assert '"dirty":False' in script
    assert 'current.next' in script
    assert 'runs/deployment.json' in script


def test_dashboard_deployer_is_independent_and_attributable() -> None:
    script = (ROOT / "scripts" / "deploy_dashboard.sh").read_text(encoding="utf-8")

    assert 'git status --porcelain' in script
    assert 'refusing dashboard deploy: source checkout is dirty' in script
    assert 'FACTORIO_AI_DASHBOARD_RUNTIME_ROOT' in script
    assert '/srv/factorio-ai-dashboard-runtime' in script
    assert 'BUILD_INFO.json' in script
    assert 'runs/dashboard_deployment.json' in script
    assert 'cortex_phase_state.py' in script
    assert '--state-root "$STATE_ROOT"' in script
    assert '--write' in script
    assert 'current.next' in script


def test_runtime_launchers_separate_code_from_state() -> None:
    for name in (
        "run_evolution_loop.sh",
        "run_curriculum.sh",
        "run_open_play_validation.sh",
        "run_dashboard.sh",
    ):
        script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert 'CODE_ROOT=' in script
        assert 'STATE_ROOT=' in script
        assert 'FACTORIO_AI_STATE_ROOT="$STATE_ROOT"' in script
        assert 'PYTHONPATH="$CODE_ROOT/src"' in script
