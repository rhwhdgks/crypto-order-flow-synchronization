from __future__ import annotations

import zipfile
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from okx_order_flow_external_validation import (
    aggregate_okx_month_archive,
    build_okx_external_decisions,
    validate_okx_external_config,
)


def frozen_config() -> dict:
    return {
        "source": {"source_archive_timezone": "Asia/Shanghai"},
        "data": {
            "expected_interval_minutes": 15,
            "symbols": [
                "BTC-USDT",
                "ETH-USDT",
                "XRP-USDT",
                "SOL-USDT",
                "DOGE-USDT",
                "ADA-USDT",
                "AVAX-USDT",
            ],
            "major_symbols": ["BTC-USDT", "ETH-USDT"],
        },
        "analysis": {
            "null_repetitions": 499,
            "bootstrap_repetitions": 499,
            "alignment_minimum_assets": 6,
            "cascade_primary_horizon_minutes": 15,
            "cascade_minimum_standardized_beta": 0.02,
            "fdr_alpha": 0.05,
        },
    }


def _write_zip(path, frame: pd.DataFrame) -> None:
    csv_path = path.with_suffix(".csv")
    frame.to_csv(csv_path, index=False)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(csv_path, arcname=csv_path.name)


def test_okx_archive_aggregation_uses_quote_weighted_taker_side(tmp_path) -> None:
    raw = pd.DataFrame(
        {
            "instrument_name": ["BTC-USDT"] * 4,
            "trade_id": [1, 2, 3, 4],
            "side": ["buy", "sell", "buy", "buy"],
            "price": [100.0, 100.0, 101.0, 102.0],
            "size": [2.0, 1.0, 1.0, 1.0],
            "created_time": [
                1_744_041_600_000,
                1_744_041_660_000,
                1_744_042_500_000,
                1_744_042_560_000,
            ],
        }
    )
    archive = tmp_path / "BTC-USDT-trades-2025-04.zip"
    _write_zip(archive, raw)
    buckets, metadata = aggregate_okx_month_archive(archive, "BTC-USDT", 15, chunksize=2)
    assert metadata["raw_rows"] == 4
    assert len(buckets) == 2
    first = buckets.iloc[0]
    assert first["transaction_count"] == 2
    assert first["total_quote_quantity"] == pytest.approx(300.0)
    assert first["aggressor_imbalance"] == pytest.approx(1 / 3)
    assert first["bucket_return"] == pytest.approx(0.0)


def test_okx_archive_rejects_duplicate_trade_ids(tmp_path) -> None:
    raw = pd.DataFrame(
        {
            "instrument_name": ["BTC-USDT", "BTC-USDT"],
            "trade_id": [1, 1],
            "side": ["buy", "sell"],
            "price": [100.0, 100.0],
            "size": [1.0, 1.0],
            "created_time": [1_744_041_600_000, 1_744_041_600_001],
        }
    )
    archive = tmp_path / "duplicate.zip"
    _write_zip(archive, raw)
    with pytest.raises(ValueError, match="trade IDs"):
        aggregate_okx_month_archive(archive, "BTC-USDT", 15, chunksize=10)


def test_okx_external_decisions_preserve_identification_limits() -> None:
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
            "standardized_beta": [0.03],
            "q_value_bh_fdr": [0.01],
            "first_half_beta": [0.03],
            "second_half_beta": [0.02],
        }
    )
    bootstrap = pd.DataFrame({"horizon_minutes": [15], "ci_lower_95": [0.005]})
    decisions = build_okx_external_decisions(
        synchronization,
        lead_lag,
        bootstrap,
        frozen_config(),
    )
    assert bool(
        decisions.loc[
            decisions["decision"].eq("okx_spot_synchronization_external_replication"),
            "passed",
        ].iloc[0]
    )
    assert not bool(
        decisions.loc[decisions["decision"].eq("intentional_herding_identification"), "passed"].iloc[0]
    )
    assert decisions.loc[decisions["decision"].eq("directional_alpha"), "classification"].iloc[0] == "not_tested"


def test_okx_config_rejects_drift_and_text_layers() -> None:
    config = frozen_config()
    validate_okx_external_config(config)
    drifted = deepcopy(config)
    drifted["analysis"]["null_repetitions"] = 500
    with pytest.raises(ValueError, match="null repetitions"):
        validate_okx_external_config(drifted)
    forbidden = deepcopy(config)
    forbidden["data"]["sentiment_path"] = "unused.csv"
    with pytest.raises(ValueError, match="Forbidden data layer"):
        validate_okx_external_config(forbidden)
