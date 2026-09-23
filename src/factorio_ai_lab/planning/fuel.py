"""Burner fuel sizing, from the game's own energy figures.

A burner machine that runs out of coal stops producing, and a stage that
measures output over a fixed window then reports fuel dose rather than
throughput. That is not a hypothetical: the copper stage inserted a mutated
number of coal into its drill exactly once and never refuelled, which made

    copper_ore_output == floor(6.667 * coal_inserted)

with a hard ceiling where the window ended. Nine runs with one coal all
produced exactly 6 ore; seven runs with two produced exactly 13. The
retention gate on copper was therefore, arithmetically, the test
``coal_budget >= 2`` -- a coin flip in the mutation, not an engineering
regression.

Sizing the charge from the window closes that: with enough fuel to burn for
the whole measurement, the rate reflects mining speed, which is what the gate
is meant to compare.

Values below are prototype figures read from the live Factorio 2.0.73 runtime
over RCON, not from documentation:

    burner-mining-drill  energy_usage 2500 J/tick -> 150 kW, mining_speed 0.25
    stone-furnace        energy_usage 1500 J/tick ->  90 kW, crafting_speed 1
    coal                 fuel_value 4 MJ
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

#: Chemical energy of one coal, in joules.
COAL_FUEL_VALUE_J = 4_000_000.0

#: Electrical-equivalent draw of each burner machine, in watts.
BURNER_MINING_DRILL_W = 150_000.0
STONE_FURNACE_W = 90_000.0

#: Ticks per second of Factorio game time. Game speed changes how much real
#: time a tick takes, never how many ticks make a game second.
TICKS_PER_SECOND = 60.0


@dataclass(frozen=True)
class BurnerProfile:
    """How long one unit of fuel keeps a given machine running."""

    name: str
    power_w: float

    def seconds_per_coal(self) -> float:
        return COAL_FUEL_VALUE_J / self.power_w

    def coal_for_seconds(self, seconds: float, *, margin: float = 1.25) -> int:
        """Coal needed to run for `seconds` of game time, with headroom.

        The margin covers the part of the window spent before the machine is
        fuelled and the jitter in how long a step takes; running dry halfway
        through is what turns a throughput measurement into a fuel measurement.
        """
        if seconds <= 0:
            return 0
        return max(1, ceil(seconds * margin / self.seconds_per_coal()))


BURNER_MINING_DRILL = BurnerProfile("burner-mining-drill", BURNER_MINING_DRILL_W)
STONE_FURNACE = BurnerProfile("stone-furnace", STONE_FURNACE_W)


def observed_window_seconds(
    ticks_before: int | None,
    ticks_after: int | None,
    fallback_seconds: float,
) -> float:
    """Game seconds actually elapsed across a step.

    The stages used to divide output by the literal they passed to ``sleep``.
    The game keeps running during ``move_to``, ``extract_item`` and
    ``place_entity`` too, so the real window was several times the literal and
    every rate derived from it was inflated by that factor. When the tick
    counter is unavailable the literal is still used, and the caller records
    which of the two it got.
    """
    if ticks_before is None or ticks_after is None:
        return max(0.0, float(fallback_seconds))
    elapsed = (int(ticks_after) - int(ticks_before)) / TICKS_PER_SECOND
    if elapsed <= 0:
        return max(0.0, float(fallback_seconds))
    return elapsed
