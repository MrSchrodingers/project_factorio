"""Fuel sizing and window measurement.

These pin the two halves of the copper regression: a charge large enough to
last the window, and a denominator that is the window actually observed
rather than the literal passed to sleep().
"""

from __future__ import annotations

import pytest

from factorio_ai_lab.planning.fuel import (
    BURNER_MINING_DRILL,
    STONE_FURNACE,
    observed_window_seconds,
    profile_from_energy_per_tick,
)


def test_drill_burn_time_matches_the_runtime_figures() -> None:
    # 4 MJ / 150 kW = 26.667 s per coal.
    assert BURNER_MINING_DRILL.seconds_per_coal() == pytest.approx(26.667, abs=0.01)


def test_furnace_burn_time_matches_the_runtime_figures() -> None:
    # 4 MJ / 90 kW = 44.44 s per coal.
    assert STONE_FURNACE.seconds_per_coal() == pytest.approx(44.44, abs=0.01)


def test_one_coal_does_not_cover_the_observed_window() -> None:
    """The measured window was ~76-80 game seconds; one coal buys ~26.7."""
    assert BURNER_MINING_DRILL.coal_for_seconds(78) > 1


def test_charge_covers_the_window_with_headroom() -> None:
    for window in (16, 30, 60, 78, 120):
        coal = BURNER_MINING_DRILL.coal_for_seconds(window)
        covered = coal * BURNER_MINING_DRILL.seconds_per_coal()
        assert covered >= window, f"janela {window}s descoberta por {coal} carvao"


def test_charge_is_at_least_one_for_any_positive_window() -> None:
    assert BURNER_MINING_DRILL.coal_for_seconds(0.5) == 1
    assert BURNER_MINING_DRILL.coal_for_seconds(0) == 0


def test_window_uses_observed_ticks() -> None:
    # 4800 ticks at 60 ticks/s = 80 game seconds, whatever sleep() was told.
    assert observed_window_seconds(1000, 5800, 16) == pytest.approx(80.0)


def test_window_falls_back_when_ticks_are_unavailable() -> None:
    assert observed_window_seconds(None, 5800, 16) == 16.0
    assert observed_window_seconds(1000, None, 16) == 16.0


def test_window_falls_back_when_the_counter_did_not_advance() -> None:
    # A frozen or rewound counter must not produce a zero or negative window,
    # which would make the rate infinite or negative.
    assert observed_window_seconds(5800, 5800, 16) == 16.0
    assert observed_window_seconds(5800, 1000, 16) == 16.0


def test_observed_window_is_not_the_literal_it_replaces() -> None:
    # The defect in one line: dividing by 16 what took 80 seconds inflates
    # every derived rate five-fold.
    literal = 16.0
    observed = observed_window_seconds(0, 4800, literal)
    assert observed / literal == pytest.approx(5.0)


def test_a_profile_is_built_from_the_runtime_energy_figure() -> None:
    """The figures the constants above were read from, as a conversion.

    ``LuaEntityPrototype.get_max_energy_usage()`` answers in joules per tick:
    2500 for burner-mining-drill and 1500 for stone-furnace, which are the
    two profiles stated in this module, and 30000 for boiler, which is not.
    """
    drill = profile_from_energy_per_tick("burner-mining-drill", 2500)
    assert drill is not None
    assert drill.power_w == pytest.approx(BURNER_MINING_DRILL.power_w)
    furnace = profile_from_energy_per_tick("stone-furnace", 1500)
    assert furnace is not None
    assert furnace.power_w == pytest.approx(STONE_FURNACE.power_w)


def test_the_boiler_burns_a_coal_in_seconds_not_minutes() -> None:
    # 30000 J/tick is 1.8 MW: twelve burner drills' worth of draw, and 2.22 s
    # of one coal. Sizing a boiler's charge from the drill figure overstates
    # what it covers by that factor.
    boiler = profile_from_energy_per_tick("boiler", 30000)
    assert boiler is not None
    assert boiler.seconds_per_coal() == pytest.approx(2.222, abs=0.001)
    assert boiler.coal_for_seconds(4200) > BURNER_MINING_DRILL.coal_for_seconds(4200)


def test_an_unanswered_probe_has_no_profile() -> None:
    # None is not a machine that draws nothing. A caller with no profile has
    # to report the charge it could not size as unmeasured.
    assert profile_from_energy_per_tick("boiler", None) is None
    assert profile_from_energy_per_tick("boiler", 0) is None
    assert profile_from_energy_per_tick("boiler", -1) is None
    assert profile_from_energy_per_tick("boiler", "nope") is None

def test_generic_fuel_sizing_uses_measured_energy_value() -> None:
    furnace = profile_from_energy_per_tick("stone-furnace", 1500)
    assert furnace is not None

    coal = furnace.fuel_units_for_seconds(10, 4_000_000)
    wood = furnace.fuel_units_for_seconds(10, 2_000_000)
    solid = furnace.fuel_units_for_seconds(10, 12_000_000)

    assert coal == 1
    assert wood == 1
    assert solid == 1
    assert furnace.seconds_per_fuel_unit(4_000_000) == pytest.approx(
        furnace.seconds_per_coal()
    )


def test_long_horizon_rewards_energy_density_without_hardcoded_fuel_name() -> None:
    furnace = profile_from_energy_per_tick("stone-furnace", 1500)
    assert furnace is not None

    coal = furnace.fuel_units_for_seconds(120, 4_000_000)
    wood = furnace.fuel_units_for_seconds(120, 2_000_000)
    solid = furnace.fuel_units_for_seconds(120, 12_000_000)

    assert wood > coal > solid


@pytest.mark.parametrize("fuel_value", [0, -1, float("inf"), float("nan")])
def test_invalid_fuel_value_is_not_sized(fuel_value: float) -> None:
    with pytest.raises(ValueError):
        STONE_FURNACE.fuel_units_for_seconds(10, fuel_value)
