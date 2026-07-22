from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from order_flow_synchronization import (
    _halfyear_day_circular_shift,
    _synchronization_statistics,
    build_decisions,
    build_flow_composites,
    load_futures_metrics,
    validate_frozen_config,
)


def frozen_config() -> dict:
    return {
        "data": {
            "expected_rows": 490_560,
            "expected_interval_minutes": 15,
            "symbols": [
                "BTCUSDT",
                "ETHUSDT",
                "XRPUSDT",
                "SOLUSDT",
                "DOGEUSDT",
                "ADAUSDT",
                "AVAXUSDT",
            ],
            "major_symbols": ["BTCUSDT", "ETHUSDT"],
        },
        "analysis": {
            "null_repetitions": 499,
            "bootstrap_repetitions": 499,
            "alignment_minimum_assets": 6,
            "cascade_primary_horizon_minutes": 15,
            "cascade_minimum_standardized_beta": 0.02,
            "fdr_alpha": 0.05,
            "futures_minimum_standardized_beta": 0.05,
            "futures_minimum_event_count": 100,
            "futures_minimum_directional_concordance": 0.55,
        },
    }


def test_synchronization_statistics_detect_common_flow() -> None:
    rng = np.random.default_rng(7)
    common = rng.normal(size=(2_000, 1))
    aligned = common + rng.normal(scale=0.2, size=(2_000, 7))
    independent = rng.normal(size=(2_000, 7))
    aligned_stats = _synchronization_statistics(aligned, 6)
    independent_stats = _synchronization_statistics(independent, 6)
    assert aligned_stats["mean_pairwise_correlation"] > 0.9
    assert aligned_stats["extreme_alignment_rate"] > independent_stats["extreme_alignment_rate"]


def test_halfyear_day_shift_preserves_each_asset_distribution() -> None:
    index = pd.date_range("2025-04-01", periods=30 * 96, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        np.arange(len(index) * 7, dtype=float).reshape(len(index), 7),
        index=index,
        columns=[f"A{value}" for value in range(7)],
    )
    shifted = _halfyear_day_circular_shift(frame, np.random.default_rng(11), 7)
    assert shifted.shape == frame.shape
    for column in range(7):
        assert np.array_equal(np.sort(shifted[:, column]), np.sort(frame.iloc[:, column]))
    original_hour_profile = frame.groupby(frame.index.hour).mean().to_numpy()
    shifted_hour_profile = pd.DataFrame(shifted, index=index).groupby(index.hour).mean().to_numpy()
    assert shifted_hour_profile == pytest.approx(original_hour_profile)


def test_decisions_keep_intentional_herding_and_alpha_disabled() -> None:
    synchronization = pd.DataFrame(
        {
            "metric": ["mean_pairwise_correlation", "extreme_alignment_rate"],
            "gate_pass": [True, True],
        }
    )
    lead_lag = pd.DataFrame(
        {
            "direction": ["major_to_alt"],
            "horizon_minutes": [15],
            "standardized_beta": [0.08],
            "q_value_bh_fdr": [0.01],
            "first_half_beta": [0.06],
            "second_half_beta": [0.07],
        }
    )
    bootstrap = pd.DataFrame({"horizon_minutes": [15], "ci_lower_95": [0.01]})
    futures = pd.DataFrame(
        {
            "test": ["spot_to_futures_flow_regression", "extreme_event_direction_concordance"],
            "estimate": [0.2, 0.6],
            "q_value_bh_fdr": [0.01, 0.02],
            "event_count": [np.nan, 200],
        }
    )
    decisions = build_decisions(synchronization, lead_lag, bootstrap, futures, frozen_config())
    assert bool(decisions.loc[decisions["decision"].eq("final"), "passed"].iloc[0])
    assert not bool(
        decisions.loc[decisions["decision"].eq("intentional_herding_identification"), "passed"].iloc[0]
    )
    assert decisions.loc[decisions["decision"].eq("directional_alpha"), "classification"].iloc[0] == "not_tested"


def test_frozen_config_rejects_drift_and_text_layers() -> None:
    config = frozen_config()
    validate_frozen_config(config)
    drifted = deepcopy(config)
    drifted["analysis"]["null_repetitions"] = 500
    with pytest.raises(ValueError, match="Null repetitions"):
        validate_frozen_config(drifted)
    forbidden = deepcopy(config)
    forbidden["data"]["news_path"] = "data/news/news_headlines.csv"
    with pytest.raises(ValueError, match="Forbidden data layer"):
        validate_frozen_config(forbidden)


def test_futures_loader_rejects_unknown_aggregation() -> None:
    with pytest.raises(ValueError, match="taker_aggregation"):
        load_futures_metrics("unused", frozen_config(), taker_aggregation="median")


def test_flow_composites_allow_spot_only_external_validation() -> None:
    index = pd.date_range("2024-04-08", periods=4, freq="15min", tz="UTC")
    symbols = frozen_config()["data"]["symbols"]
    rows = []
    for timestamp in index:
        for position, symbol in enumerate(symbols):
            rows.append(
                {
                    "bucket_start": timestamp,
                    "symbol": symbol,
                    "sample_split": "development",
                    "aggressor_residual_z": float(position),
                }
            )
    config = frozen_config()
    config["data"]["alt_symbols"] = symbols[2:]
    composites = build_flow_composites(pd.DataFrame(rows), config)
    assert {"major_flow", "alt_flow"}.issubset(composites.columns)
    assert "spot_futures5_flow" not in composites.columns
