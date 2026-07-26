from __future__ import annotations

import numpy as np
import pandas as pd

from common_liquidity_order_flow import (
    _correlation_reduction,
    _half_year_day_shift_boolean,
    build_decisions,
)


def test_correlation_reduction_detects_removed_common_factor() -> None:
    rng = np.random.default_rng(7)
    common = rng.normal(size=(2_000, 1))
    baseline = common + rng.normal(scale=0.5, size=(2_000, 4))
    conditioned = baseline - common
    before, after, reduction = _correlation_reduction(baseline, conditioned)
    assert before > 0.7
    assert abs(after) < 0.05
    assert reduction > 0.7


def test_half_year_shift_preserves_event_count_on_complete_grid() -> None:
    index = pd.date_range("2026-01-01", periods=40 * 96, freq="15min", tz="UTC")
    values = pd.Series(index.minute == 0, index=index)
    shifted = _half_year_day_shift_boolean(values, np.random.default_rng(11), 7)
    assert int(shifted.sum()) == int(values.sum())
    assert shifted.index.equals(values.index)


def test_primary_decision_requires_effect_and_both_halves() -> None:
    config = {
        "analysis": {
            "fdr_alpha": 0.05,
            "primary_minimum_correlation_reduction": 0.02,
            "event_overlap_minimum_risk_ratio": 1.25,
            "event_overlap_minimum_count": 100,
        }
    }
    primary = pd.DataFrame(
        [
            {
                "hypothesis": "primary_correlation_reduction",
                "p_value_one_sided": 0.01,
                "effect": 0.03,
                "first_half_effect": 0.02,
                "second_half_effect": -0.001,
            }
        ]
    )
    secondary = pd.DataFrame(
        [
            {
                "hypothesis": "secondary_extreme_event_overlap",
                "p_value_one_sided": 0.01,
                "risk_ratio": 1.5,
                "overlap_count": 200,
            }
        ]
    )
    _, _, decisions = build_decisions(primary, secondary, config)
    assert not bool(decisions.iloc[0]["passed"])
    assert bool(decisions.iloc[1]["passed"])
