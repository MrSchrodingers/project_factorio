"""Failing a stage the incumbent never reached is exploration, not regression.

`compare_challenger` compared `failures` as an absolute count:

    if challenger.failures > champion.failures:
        regressions.append(f"failed stages increased {champion.failures}...")

The incumbent is generation 6, promoted when the curriculum stopped earlier;
it records `failures: 0` because it never faced the hard stage. Every later
challenger runs a 16-stage curriculum, completes 13 or 14 of them -- further
than the incumbent ever went -- and fails the next one. The absolute count
then reads 0 vs 1 and rejects it.

Measured on runs/evolution_history.jsonl, twelve of the thirty-seven recorded
generations were rejected with `failed stages increased 0->1` as their ONLY
regression: 7, 10, 11, 15, 29, 30, 31, 32, 33, 34, 35 and 36. Four of them had
completed `Electronic circuits`, the stage the run had been stuck on since
generation 13: generations 29, 34 and 36 among the rejected, and 37, which was
the first to be kept. The stage is not reliably reachable -- it was completed
in 4 of the 26 generations that recorded stage names -- so the count is a
statement about what selection discarded, not about a solved bottleneck.

This is the same shape as the incommensurability `_commensurate` already
guards against, applied to the wrong axis: a rule enforced against a
challenger that the incumbent was never measured by. The rule makes the
incumbent unbeatable by making the frontier unreachable -- any genome that
tries the next capability and misses is rejected, so the only safe strategy is
to attempt nothing new.

The fix compares stages by NAME. Failing a stage the incumbent completed is a
real regression: capability was lost. Failing a stage the incumbent never
completed is the cost of exploring, and carries no verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factorio_ai_lab.learning.survival import FitnessVector, compare_challenger

HISTORY = Path(__file__).resolve().parents[1] / "runs" / "evolution_history.jsonl"

BASE_RATES = {"iron-ore": 1.0}
CAPS = frozenset({"iron_backbone"})


def _vector(
    *,
    completed: frozenset[str] | None,
    failed: frozenset[str] | None,
    capabilities: frozenset[str] = CAPS,
    rates: dict[str, float] | None = None,
) -> FitnessVector:
    return FitnessVector(
        capabilities=capabilities,
        rates_per_s=dict(rates if rates is not None else BASE_RATES),
        completed_stages=completed,
        failed_stages=failed,
        failures=0 if failed is None else len(failed),
    )


def test_failing_a_stage_the_incumbent_never_reached_is_not_a_regression() -> None:
    champion = _vector(completed=frozenset({"Baseline iron mining"}), failed=frozenset())
    challenger = _vector(
        completed=frozenset({"Baseline iron mining", "Belt-fed smelting"}),
        failed=frozenset({"Electronic circuits"}),
    )
    decision = compare_challenger(champion, challenger)
    # Asserting on the old message text would be unfalsifiable: the string no
    # longer exists anywhere in the source, so the assertion passes even with
    # the defect reintroduced under another wording. The observable property
    # is the verdict itself.
    assert decision.regressions == (), f"reprovou por explorar: {decision.regressions}"
    assert decision.promoted, "um desafiante estritamente melhor nao foi promovido"


def test_reaching_further_is_reported_as_an_improvement() -> None:
    champion = _vector(completed=frozenset({"Baseline iron mining"}), failed=frozenset())
    challenger = _vector(
        completed=frozenset({"Baseline iron mining", "Belt-fed smelting"}),
        failed=frozenset({"Electronic circuits"}),
    )
    decision = compare_challenger(champion, challenger)
    advanced = [i for i in decision.improvements if "frontier" in i]
    assert advanced, f"avanco de fronteira nao registrado: {decision.improvements}"
    assert "Belt-fed smelting" in advanced[0]


def test_losing_a_stage_the_incumbent_completed_is_still_a_regression() -> None:
    # The guard that matters is not removed: breaking something that worked is
    # a real loss, and must keep rejecting.
    champion = _vector(
        completed=frozenset({"Baseline iron mining", "Belt-fed smelting"}),
        failed=frozenset(),
    )
    challenger = _vector(
        completed=frozenset({"Baseline iron mining"}),
        failed=frozenset({"Belt-fed smelting"}),
    )
    decision = compare_challenger(champion, challenger)
    assert not decision.promoted
    lost = [i for i in decision.regressions if "Belt-fed smelting" in i]
    assert lost, f"perda real de estagio nao reprovou: {decision.regressions}"


def test_a_stage_both_sides_failed_carries_no_verdict() -> None:
    champion = _vector(
        completed=frozenset({"Baseline iron mining"}),
        failed=frozenset({"Electronic circuits"}),
    )
    challenger = _vector(
        completed=frozenset({"Baseline iron mining"}),
        failed=frozenset({"Electronic circuits"}),
    )
    decision = compare_challenger(champion, challenger)
    assert not any("Electronic circuits" in item for item in decision.regressions)
    assert not any("Electronic circuits" in item for item in decision.improvements)


def test_an_incumbent_without_stage_names_is_declared_incommensurable() -> None:
    # Generation 6 was recorded before the field existed. Comparing its count
    # against a challenger's is exactly the defect; the honest answer is that
    # the axis cannot decide, stated out loud rather than assumed.
    champion = _vector(completed=None, failed=None)
    challenger = _vector(
        completed=frozenset({"Baseline iron mining", "Belt-fed smelting"}),
        failed=frozenset({"Electronic circuits"}),
    )
    decision = compare_challenger(champion, challenger)
    # No verdict is drawn from the axis: no regression, and no credit either.
    # Promotion still depends on dominance elsewhere, which this pair does not
    # establish, so asserting it here would test the wrong rule.
    assert decision.regressions == (), f"decidiu sem evidencia: {decision.regressions}"
    reported = " ".join(decision.incommensurable_metrics)
    assert "stages" in reported, (
        f"a ausencia nao foi declarada: {decision.incommensurable_metrics}"
    )
    # The axis must not claim to have carried a comparison it could not make.
    assert "failures" not in decision.compared_metrics, (
        f"registro de auditoria falso: {decision.compared_metrics}"
    )


def test_stage_names_survive_a_round_trip() -> None:
    vector = _vector(
        completed=frozenset({"Baseline iron mining"}),
        failed=frozenset({"Electronic circuits"}),
    )
    restored = FitnessVector.from_dict(vector.to_dict())
    assert restored.completed_stages == frozenset({"Baseline iron mining"})
    assert restored.failed_stages == frozenset({"Electronic circuits"})


def test_absent_is_not_the_same_as_empty() -> None:
    # None means the generation was never measured for stages; frozenset()
    # means it was measured and completed none. Collapsing the two is the
    # substitution that cost this project eleven generations.
    absent = FitnessVector.from_dict({"capabilities": []})
    assert absent.completed_stages is None
    assert absent.failed_stages is None
    empty = _vector(completed=frozenset(), failed=frozenset())
    assert FitnessVector.from_dict(empty.to_dict()).completed_stages == frozenset()


@pytest.mark.skipif(not HISTORY.exists(), reason="sem historico gravado")
def test_the_recorded_rejections_are_reproduced_and_then_lifted() -> None:
    """Replay the real generations that were rejected only for this reason."""
    rows = [json.loads(line) for line in HISTORY.read_text(encoding="utf-8").splitlines() if line.strip()]
    affected = [
        row
        for row in rows
        if (row.get("decision") or {}).get("regressions")
        == ["failed stages increased 0→1"]
        and isinstance(row.get("completed_stages"), list)
        and isinstance(row.get("failed_stages"), list)
    ]
    assert affected, "o instrumento nao achou o caso positivo conhecido no historico"

    # Judged with the fitness each generation actually recorded, against an
    # incumbent holding the stages it completed. A synthetic vector would drop
    # the capability and route improvements that carry the promotion, and the
    # replay would then measure the stub rather than the rule.
    replayed = 0
    for row in affected:
        recorded = (row.get("challenger") or {}).get("fitness")
        if not isinstance(recorded, dict):
            continue
        completed = [str(name) for name in row["completed_stages"]]
        failed = [str(name) for name in row["failed_stages"]]
        challenger = FitnessVector.from_dict(
            dict(recorded, completed_stages=completed, failed_stages=failed)
        )
        champion = FitnessVector.from_dict(
            dict(
                recorded,
                completed_stages=sorted(set(completed) - set(failed)),
                failed_stages=[],
                failures=0,
            )
        )
        # The control is the same generation that attempted nothing extra.
        # Whatever else is wrong with it -- starvation, coverage -- is wrong
        # in both, so the only difference these two can show is the price the
        # rule puts on exploring. Equality is falsifiable: a rule that
        # penalises the attempt makes the explorer strictly worse.
        control = FitnessVector.from_dict(
            dict(
                recorded,
                completed_stages=completed,
                failed_stages=[],
                failures=0,
            )
        )
        explored = compare_challenger(champion, challenger)
        stayed = compare_challenger(champion, control)
        replayed += 1
        assert set(explored.regressions) == set(stayed.regressions), (
            f"geracao {row.get('generation')} paga por explorar: "
            f"{set(explored.regressions) - set(stayed.regressions)}"
        )
    assert replayed, "nenhuma geracao afetada trouxe fitness gravado"


def test_the_axis_is_falsifiable_by_the_count_alone() -> None:
    """A challenger that only differs by failure count must not be rejected.

    This is the assertion the old text-matching tests could not make. The two
    sides are identical except that the challenger attempted -- and missed --
    one stage the incumbent never completed. Any rule that reads the raw
    counts rejects it; the rule under test must not.
    """
    champion = _vector(completed=frozenset({"Baseline iron mining"}), failed=frozenset())
    challenger = _vector(
        completed=frozenset({"Baseline iron mining"}),
        failed=frozenset({"Logistic science"}),
    )
    assert challenger.failures > champion.failures, "o caso nao exerce a regra"
    decision = compare_challenger(champion, challenger)
    assert decision.regressions == (), f"reprovou pela contagem: {decision.regressions}"


def test_the_audit_trail_does_not_claim_an_unmade_comparison() -> None:
    # compared_metrics is read back from evolution_history.jsonl as the record
    # of what carried the verdict. Naming a metric there that decided nothing
    # makes the record lie about its own reasoning.
    champion = _vector(completed=frozenset({"Baseline iron mining"}), failed=frozenset())
    challenger = _vector(
        completed=frozenset({"Baseline iron mining"}),
        failed=frozenset({"Logistic science"}),
    )
    decision = compare_challenger(champion, challenger)
    assert "failures" not in decision.compared_metrics, (
        f"afirmou comparar failures: {decision.compared_metrics}"
    )
    assert "stages" in decision.compared_metrics, (
        f"nao registrou o eixo que de fato decidiu: {decision.compared_metrics}"
    )
