"""A floor the incumbent does not meet cannot reject the challenger.

`compare_challenger` carried three absolute floors: physical processing
coverage of at least 50%, no fuel-starved entity once coal mining exists, no
power-starved entity once steam power exists. They read as statements about
what a factory has to be, and they were enforced against the challenger alone.

The incumbent is generation 37. Read off runs/evolution_champion.json it
records coverage 16.7%, one fuel-starved entity and four power-starved ones,
so it fails two of the three floors it was rejecting challengers with, and
would fail the third if it were ever re-judged. Thirty-six generations
followed it without a promotion; generations 64 to 73 each reached 14 of 16
stages and each was rejected, generations 70 to 73 with those two floors and
nothing else but one comparative reading.

This is the shape of the defect commit 8223cc4 removed from `failures`: a rule
only one side was ever measured by makes the incumbent unbeatable rather than
merely hard to beat. The answer here is narrower than deleting the floor. The
floor is withheld only while the incumbent fails it, and returns the moment a
promoted champion satisfies it -- the same way `_commensurate` lets the first
challenger promoted on a new metric establish its floor. What rejects a
challenger in the meantime is the comparative reading of the same metric,
which is already in the module: coverage below the incumbent's, starvation
above it. A challenger that wrecks the factory is therefore still rejected.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factorio_ai_lab.learning.survival import FitnessVector, compare_challenger

HISTORY = Path(__file__).resolve().parents[1] / "runs" / "evolution_history.jsonl"

#: The floors, by the metric each one reads and the prose the rejection used.
#: The prose is only used to select generations out of the recorded history,
#: which is a frozen artifact; every assertion below is made on the verdict or
#: on the metric name, never on the wording of the current code.
FLOORS: tuple[tuple[str, str], ...] = (
    ("physical_processing_coverage", "physical processing coverage below"),
    ("fuel_starved_entities", "fuel starvation remains after coal capability"),
    (
        "power_starved_entities",
        "power starvation remains after steam-power capability",
    ),
)

CAPABILITIES = frozenset({"iron_backbone", "coal_mining", "steam_power"})


def _incumbent_standing(champion: FitnessVector, metric: str) -> str:
    """"satisfies", "misses" or "unmeasured" -- three states, not two.

    An incumbent that was never measured for the metric is not an incumbent
    that fails it: `_commensurate` refuses the whole axis before any floor is
    reached, so nothing is withheld and nothing is rejected.
    """
    value = getattr(champion, metric)
    if value is None:
        return "unmeasured"
    if metric == "physical_processing_coverage":
        return "misses" if value < 0.50 else "satisfies"
    return "misses" if value > 0 else "satisfies"


def _vector(
    *,
    coverage: float | None,
    fuel: int | None,
    power: int | None,
    manual: int | None = 50,
    autonomy: float | None = 0.125,
) -> FitnessVector:
    return FitnessVector(
        capabilities=CAPABILITIES,
        rates_per_s={"coal": 0.05},
        physical_processing_coverage=coverage,
        fuel_starved_entities=fuel,
        power_starved_entities=power,
        manual_logistics_calls=manual,
        autonomy_score=autonomy,
    )


#: Generation 37 as recorded, reduced to the fields the floors read.
CHAMPION_37 = _vector(coverage=0.1667, fuel=1, power=4, manual=51)


def test_a_floor_the_incumbent_also_misses_does_not_reject() -> None:
    # Generation 73 as recorded: better coverage, equal starvation, no power
    # starvation left, more autonomy. Under the floors it was rejected twice.
    challenger = _vector(
        coverage=0.1818,
        fuel=1,
        power=0,
        manual=51,
        autonomy=0.250,
    )
    decision = compare_challenger(CHAMPION_37, challenger)
    assert decision.regressions == (), (
        f"reprovado por regua que o incumbente nao passa: {decision.regressions}"
    )
    assert decision.promoted


def test_the_withheld_floor_is_named_in_the_record() -> None:
    # Silence would leave no way to tell a floor that held from one that was
    # never applied, and the record is read back as the reasoning.
    challenger = _vector(coverage=0.1818, fuel=1, power=2, autonomy=0.250)
    decision = compare_challenger(CHAMPION_37, challenger)
    named = " ".join(decision.withheld_gates)
    for metric, _ in FLOORS:
        assert metric in named, (
            f"trava retida sem registro: {metric} / {decision.withheld_gates}"
        )


def test_the_floor_still_rejects_when_the_incumbent_satisfies_it() -> None:
    """The floor is withheld, not removed.

    An incumbent that meets the standard is entitled to it: this is the case
    that distinguishes withholding from deleting, and the only one in this
    file that must still produce the absolute rejection.
    """
    champion = _vector(coverage=0.60, fuel=0, power=0, manual=51)
    challenger = _vector(coverage=0.45, fuel=3, power=2, manual=51)
    decision = compare_challenger(champion, challenger)
    assert not decision.promoted
    assert decision.withheld_gates == (), (
        f"reteve trava que o incumbente satisfaz: {decision.withheld_gates}"
    )
    assert len(decision.regressions) >= 3, (
        f"trava absoluta deixou de morder: {decision.regressions}"
    )


def test_a_challenger_that_wrecks_the_factory_is_still_rejected() -> None:
    """The adversarial case: every floor withheld, and the verdict still no.

    Both sides are below every floor, so none of them is enforceable; what
    rejects this challenger is the comparative reading of the same metrics,
    which is the substitute the withholding relies on. If that substitute were
    missing, a challenger that starves the factory would now be promoted.
    """
    challenger = _vector(
        coverage=0.02,
        fuel=12,
        power=9,
        manual=51,
        autonomy=0.250,
    )
    decision = compare_challenger(CHAMPION_37, challenger)
    assert not decision.promoted, "desafiante que destroi a fabrica foi promovido"
    assert any("coverage regressed" in row for row in decision.regressions)
    assert any("fuel-starved entities increased" in row for row in decision.regressions)
    assert any(
        "power-starved entities increased" in row for row in decision.regressions
    )


def test_the_first_champion_still_faces_the_floors() -> None:
    # With no incumbent there is nobody the floor could be unfair to, and the
    # floors are the baseline survival gates. Withholding them here would let
    # a broken factory become the champion the whole run is measured against.
    challenger = _vector(coverage=0.10, fuel=5, power=5, manual=None, autonomy=None)
    decision = compare_challenger(None, challenger)
    assert not decision.promoted
    assert decision.regressions


def test_an_incumbent_never_measured_for_the_metric_still_withholds() -> None:
    # Generation 6 recorded None for all three. `_commensurate` already
    # refuses the axis; the floor must not sneak back in through the absolute
    # branch, and the absence must not be read as a passing score.
    champion = _vector(coverage=None, fuel=None, power=None, manual=None)
    challenger = _vector(coverage=0.10, fuel=5, power=5, manual=None)
    decision = compare_challenger(champion, challenger)
    assert decision.regressions == (), f"decidiu sem evidencia: {decision.regressions}"


@pytest.mark.skipif(not HISTORY.exists(), reason="sem historico gravado")
def test_the_recorded_floors_the_incumbent_also_missed_are_lifted() -> None:
    """Replay every generation the floors actually rejected.

    Judged with the fitness each side recorded: the challenger as written, the
    incumbent as the generation named by `incumbent_generation`. A synthetic
    incumbent would measure the stub instead of the rule.
    """
    rows = [
        json.loads(line)
        for line in HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_generation = {
        row["generation"]: row
        for row in rows
        if isinstance(row.get("generation"), int)
    }

    affected: list[tuple[int, FitnessVector, FitnessVector, list[str]]] = []
    for row in rows:
        recorded = (row.get("decision") or {}).get("regressions") or []
        hit = [
            reason
            for reason in recorded
            if any(reason.startswith(prose) for _, prose in FLOORS)
        ]
        if not hit:
            continue
        challenger_payload = (row.get("challenger") or {}).get("fitness")
        incumbent_row = by_generation.get(row.get("incumbent_generation"))
        incumbent_payload = (
            (incumbent_row or {}).get("challenger") or {}
        ).get("fitness")
        if not isinstance(challenger_payload, dict):
            continue
        if not isinstance(incumbent_payload, dict):
            continue
        affected.append(
            (
                row["generation"],
                FitnessVector.from_dict(incumbent_payload),
                FitnessVector.from_dict(challenger_payload),
                hit,
            )
        )

    assert affected, "o instrumento nao achou o caso positivo conhecido no historico"

    lifted = 0
    for generation, champion, challenger, recorded_hits in affected:
        decision = compare_challenger(champion, challenger)
        named = " ".join(decision.withheld_gates)
        for metric, prose in FLOORS:
            rejected_for_it = any(row.startswith(prose) for row in recorded_hits)
            if not rejected_for_it:
                continue
            standing = _incumbent_standing(champion, metric)
            still_rejects = any(
                row.startswith(prose) for row in decision.regressions
            )
            if standing == "misses":
                assert metric in named, (
                    f"geracao {generation}: trava {metric} retida sem registro"
                )
                assert not still_rejects, (
                    f"geracao {generation}: ainda reprovada por {metric}, que o "
                    "incumbente tambem nao satisfaz"
                )
                lifted += 1
            elif standing == "satisfies":
                assert still_rejects, (
                    f"geracao {generation}: trava {metric} sumiu com um "
                    "incumbente que a satisfaz"
                )
            else:
                # Never measured on the incumbent: the axis carries no verdict
                # at all, which `_commensurate` already decided.
                assert not still_rejects, (
                    f"geracao {generation}: decidiu {metric} sem evidencia"
                )
    assert lifted, "nenhuma trava injusta foi levantada: o replay nao exerce a regra"


@pytest.mark.skipif(not HISTORY.exists(), reason="sem historico gravado")
def test_the_replay_does_not_promote_a_generation_that_lost_capabilities() -> None:
    """A rule change that promotes everything is as wrong as one that promotes
    nothing. Over the recorded history, every challenger that came back with
    fewer capabilities than its incumbent must still be rejected.
    """
    rows = [
        json.loads(line)
        for line in HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_generation = {
        row["generation"]: row
        for row in rows
        if isinstance(row.get("generation"), int)
    }
    checked = 0
    for row in rows:
        challenger_payload = (row.get("challenger") or {}).get("fitness")
        incumbent_row = by_generation.get(row.get("incumbent_generation"))
        incumbent_payload = (
            (incumbent_row or {}).get("challenger") or {}
        ).get("fitness")
        if not isinstance(challenger_payload, dict):
            continue
        if not isinstance(incumbent_payload, dict):
            continue
        champion = FitnessVector.from_dict(incumbent_payload)
        challenger = FitnessVector.from_dict(challenger_payload)
        if not champion.capabilities - challenger.capabilities:
            continue
        checked += 1
        decision = compare_challenger(champion, challenger)
        assert not decision.promoted, (
            f"geracao {row.get('generation')} perdeu capacidades e foi promovida"
        )
    assert checked, "o instrumento nao achou nenhuma perda de capacidade"


def test_the_floor_returns_once_a_champion_satisfies_it() -> None:
    """The floor establishes itself, the same way a new metric's does.

    Promote a challenger that meets the standard and the next comparison is
    held to it again. Without this the withholding would be permanent, which
    is the version of the fix that removes the floor.
    """
    clean = replace(
        CHAMPION_37,
        physical_processing_coverage=0.55,
        fuel_starved_entities=0,
        power_starved_entities=0,
    )
    successor = _vector(coverage=0.20, fuel=4, power=3, manual=51, autonomy=0.9)
    decision = compare_challenger(clean, successor)
    assert not decision.promoted
    assert decision.withheld_gates == ()
    assert any("coverage below 50%" in row for row in decision.regressions)
