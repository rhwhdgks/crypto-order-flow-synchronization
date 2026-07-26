from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from price_impact_study import (
    apply_frozen_impact_model,
    fit_development_impact_model,
    select_oos_events,
    validate_price_impact_config,
    verify_price_impact_seal,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return yaml.safe_load(
        (PROJECT_ROOT / "configs/research/price_impact_residual_v1.yaml").read_text(
            encoding="utf-8"
        )
    )


def test_price_impact_protocol_is_sealed() -> None:
    config = _config()
    validate_price_impact_config(config)
    result = verify_price_impact_seal(
        PROJECT_ROOT / config["protocol"]["path"],
        PROJECT_ROOT / "configs/research/price_impact_residual_v1.yaml",
        PROJECT_ROOT / config["protocol"]["seal_path"],
    )
    assert result["verified"]
    assert result["results_observed_at_seal"] is False


def test_development_model_is_applied_without_refit() -> None:
    config = _config()
    config["sample_split"]["development_start"] = "2025-10-08T00:00:00Z"
    config["sample_split"]["development_end_exclusive"] = "2025-10-08T01:00:00Z"
    timestamps = pd.date_range("2025-10-08", periods=20, freq="min", tz="UTC")
    rows = []
    for index, timestamp in enumerate(timestamps):
        for symbol_index, symbol in enumerate(config["data"]["symbols"]):
            common = 0.2 + index / 100
            rows.append(
                {
                    "bucket_start": timestamp,
                    "symbol": symbol,
                    "absolute_common_flow": common,
                    "aligned_local_flow": common + symbol_index / 100,
                    "spread_bps": 2.0 + symbol_index,
                    "log_top10_depth": 10.0,
                    "aligned_book_imbalance": 0.1,
                    "utc_hour_sin": 0.0,
                    "utc_hour_cos": 1.0,
                    "observed_signed_impact": 0.001 * common,
                    "common_direction": 1,
                }
            )
    panel = pd.DataFrame(rows)
    coefficients, scaler, diagnostics, threshold = fit_development_impact_model(
        panel, config
    )
    scored = apply_frozen_impact_model(panel, coefficients, scaler, config)
    assert np.isfinite(scored["expected_signed_impact"]).all()
    assert diagnostics.iloc[0]["development_rows"] == len(panel)
    assert threshold > 0


def test_event_selection_uses_fixed_cross_sectional_extremes() -> None:
    config = _config()
    timestamp = pd.Timestamp("2025-12-07T00:00:00Z")
    panel = pd.DataFrame(
        {
            "bucket_start": timestamp,
            "symbol": config["data"]["symbols"],
            "absolute_common_flow": 1.0,
            "common_flow": 0.8,
            "common_direction": 1,
            "impact_gap": np.arange(7, dtype=float),
        }
    )
    events, positions = select_oos_events(panel, 0.5, config)
    assert len(events) == 1
    assert len(positions) == 4
    assert set(
        positions.loc[positions["role"].eq("underreactor"), "symbol"]
    ) == {"BTC-USDT", "ETH-USDT"}
    assert positions.loc[
        positions["role"].eq("underreactor"), "position_sign"
    ].eq(1).all()
