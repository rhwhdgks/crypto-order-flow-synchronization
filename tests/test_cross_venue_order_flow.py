from __future__ import annotations

import numpy as np
import pandas as pd

from cross_venue_order_flow import (
    _half_year_day_shift,
    _mean_diagonal_correlation,
    build_decisions,
)


def test_half_year_shift_preserves_rows_and_cross_asset_structure() -> None:
    index = pd.date_range("2025-01-01", periods=30 * 96, freq="15min", tz="UTC")
    values = np.column_stack([np.arange(len(index)), np.arange(len(index)) + 10])
    shifted = _half_year_day_shift(values, index, np.random.default_rng(7), 7)
    assert shifted.shape == values.shape
    np.testing.assert_array_equal(np.sort(shifted[:, 0]), np.sort(values[:, 0]))
    np.testing.assert_array_equal(shifted[:, 1] - shifted[:, 0], np.full(len(index), 10))


def test_mean_diagonal_correlation_uses_matching_assets() -> None:
    rng = np.random.default_rng(11)
    left = rng.normal(size=(500, 3))
    right = left + rng.normal(scale=0.05, size=(500, 3))
    assert _mean_diagonal_correlation(left, right) > 0.99


def test_decision_requires_both_synchronization_metrics() -> None:
    config = {
        "analysis": {
            "fdr_alpha": 0.05,
            "synchronization_minimum_correlation_excess": 0.05,
            "synchronization_minimum_concordance_ratio": 1.10,
            "synchronization_minimum_event_count": 100,
            "transmission_primary_horizon_minutes": 15,
            "transmission_minimum_standardized_beta": 0.02,
        }
    }
    synchronization = pd.DataFrame(
        [
            {
                "metric": "mean_same_asset_correlation",
                "observed": 0.2,
                "null_95th_percentile": 0.05,
                "effect_excess": 0.15,
                "effect_ratio": 4.0,
                "event_count": np.nan,
                "q_value_bh_fdr": 0.01,
            },
            {
                "metric": "extreme_direction_concordance",
                "observed": 0.7,
                "null_95th_percentile": 0.55,
                "effect_excess": 0.2,
                "effect_ratio": 1.4,
                "event_count": 200,
                "q_value_bh_fdr": 0.01,
            },
        ]
    )
    lead_lag = pd.DataFrame(
        [
            {
                "direction": direction,
                "horizon_minutes": 15,
                "standardized_beta": 0.0,
                "q_value_bh_fdr": 1.0,
                "first_half_beta": 0.0,
                "second_half_beta": 0.0,
            }
            for direction in ["binance_to_okx", "okx_to_binance"]
        ]
    )
    bootstrap = pd.DataFrame({"beta_difference_bo_minus_ob": np.zeros(100)})
    decisions = build_decisions(synchronization, lead_lag, bootstrap, config)
    assert bool(
        decisions.loc[
            decisions["decision"].eq("cross_venue_synchronization"), "passed"
        ].iloc[0]
    )
