from __future__ import annotations

import pytest

from scripts.run_cortex_structural_canary import (
    CONFIRMATORY_SEEDS,
    DEFAULT_SEED,
    validate_canary_seed,
)


def test_default_canary_seed_is_outside_confirmatory_holdout() -> None:
    assert DEFAULT_SEED not in CONFIRMATORY_SEEDS
    validate_canary_seed(DEFAULT_SEED)


@pytest.mark.parametrize("seed", sorted(CONFIRMATORY_SEEDS))
def test_confirmatory_seeds_are_hard_refused_by_canary(seed: int) -> None:
    with pytest.raises(ValueError, match="reserved for confirmatory evaluation"):
        validate_canary_seed(seed)
