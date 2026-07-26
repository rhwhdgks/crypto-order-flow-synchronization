from __future__ import annotations

import pandas as pd
import yaml

from price_impact_collection import (
    _cache_valid,
    build_price_impact_jobs,
    extract_execution_minute_features,
    validate_price_impact_collection_config,
)


def test_price_impact_collection_has_1260_jobs() -> None:
    research = yaml.safe_load(
        open("configs/research/price_impact_residual_v1.yaml", encoding="utf-8")
    )
    jobs = build_price_impact_jobs(research)
    assert len(jobs) == 180 * 7
    assert jobs["date"].nunique() == 180


def test_execution_feature_extraction_preserves_sweep_fields() -> None:
    samples = pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-10-08", periods=2, freq="min", tz="UTC"),
            "empty": False,
            "crossed": False,
            "best_bid": 99.0,
            "best_ask": 101.0,
            "midpoint": 100.0,
            "spread_bps": 200.0,
            "ask_depth_quote_10": 1_000_000.0,
            "bid_depth_quote_10": 1_000_000.0,
            "book_imbalance_10": 0.0,
            "buy_vwap_quote_10000": 101.0,
            "sell_vwap_quote_10000": 99.0,
            "buy_fill_ratio_quote_10000": 1.0,
            "sell_fill_ratio_quote_10000": 1.0,
        }
    )
    result = extract_execution_minute_features(samples, "BTC-USDT", "2025-10-08")
    assert len(result) == 2
    assert result["top10_depth_quote"].eq(2_000_000.0).all()
    assert "buy_vwap_quote_10000" in result


def test_collection_config_matches_sealed_capacity_tiers() -> None:
    research = yaml.safe_load(
        open("configs/research/price_impact_residual_v1.yaml", encoding="utf-8")
    )
    collection = yaml.safe_load(
        open("configs/collection/price_impact_residual_v1.yaml", encoding="utf-8")
    )
    validate_price_impact_collection_config(research, collection)


def test_cache_validation_requires_matching_hash_and_full_day(tmp_path) -> None:
    cache = tmp_path / "ADA-USDT_2025-10-08_1m.parquet"
    metadata = cache.with_suffix(".metadata.json")
    frame = pd.DataFrame(
        {
            "bucket_start": pd.date_range(
                "2025-10-08", periods=1_440, freq="min", tz="UTC"
            ),
            "symbol": "ADA-USDT",
            "midpoint": 1.0,
            "sample_date": "2025-10-08",
        }
    )
    frame.to_parquet(cache, index=False)
    import hashlib
    import json

    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    metadata.write_text(
        json.dumps(
            {
                "date": "2025-10-08",
                "symbol": "ADA-USDT",
                "cache_sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    assert _cache_valid(cache)
    metadata.write_text("{}", encoding="utf-8")
    assert not _cache_valid(cache)
