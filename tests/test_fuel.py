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
