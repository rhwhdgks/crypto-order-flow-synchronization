from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pandas as pd
import yaml

from okx_l2_audit import (
    audit_l2_archive,
    build_catalog_payload,
    build_quality_decisions,
    date_to_epoch_ms,
    validate_common_liquidity_config,
    verify_common_liquidity_seal,
)
from okx_l2_collection import aggregate_minute_book_features, build_collection_jobs


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_catalog_payload_uses_utc_midnight() -> None:
    config = {"data": {"module": "4", "instrument_type": "SPOT"}}
    payload = build_catalog_payload(["ADA-USDT"], "2024-04-08", config)
    assert payload["dateQuery"]["begin"] == "1712534400000"
    assert payload["instQueryParam"]["instIdList"] == ["ADA-USDT"]


def test_snapshot_and_deltas_reconstruct_book(tmp_path: Path) -> None:
    start = date_to_epoch_ms("2024-04-08")
    rows = [
        {
            "instId": "ADA-USDT",
            "action": "snapshot",
            "ts": str(start + 5),
            "asks": [["0.501", "10", "2"], ["0.502", "20", "1"]],
            "bids": [["0.499", "10", "2"], ["0.498", "20", "1"]],
        },
        {
            "instId": "ADA-USDT",
            "action": "update",
            "ts": str(start + 60_005),
            "asks": [["0.501", "0", "0"], ["0.503", "30", "1"]],
            "bids": [["0.499", "12", "3"]],
        },
        {
            "instId": "ADA-USDT",
            "action": "update",
            "ts": str(start + 86_399_500),
            "asks": [],
            "bids": [],
        },
    ]
    source = tmp_path / "book.data"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    archive = tmp_path / "book.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname="book.data")

    summary, samples = audit_l2_archive(archive, "ADA-USDT", "2024-04-08", 60)
    result = summary.iloc[0]
    assert result["rows"] == 3
    assert result["snapshots"] == 1
    assert result["updates"] == 2
    assert result["zero_size_deletions"] == 1
    assert result["out_of_order_rows"] == 0
    assert len(samples) == 3
    assert samples.iloc[1]["best_ask"] == 0.502
    assert samples.iloc[1]["best_bid"] == 0.499


def test_quality_gate_requires_all_catalog_files() -> None:
    catalog = pd.DataFrame(
        {"available": [True, False], "symbol": ["A", "B"], "date": ["2024-01-01"] * 2}
    )
    summary = pd.DataFrame(
        [
            {
                "snapshots": 1,
                "initial_ask_levels": 400,
                "initial_bid_levels": 400,
                "start_delay_ms": 5,
                "end_early_ms": 5,
                "parse_errors": 0,
                "out_of_order_rows": 0,
                "sampled_crossed_books": 0,
            }
        ]
    )
    config = {
        "audit": {
            "minimum_initial_levels_per_side": 390,
            "maximum_start_delay_ms": 1000,
            "maximum_end_early_ms": 1000,
            "maximum_parse_errors": 0,
            "maximum_out_of_order_rows": 0,
            "maximum_sampled_crossed_books": 0,
        }
    }
    decisions = build_quality_decisions(catalog, summary, config)
    assert not bool(decisions.iloc[-1]["passed"])


def test_common_liquidity_protocol_is_still_sealed() -> None:
    config_path = PROJECT_ROOT / "configs/research/common_liquidity_order_flow_v1.yaml"
    protocol_path = PROJECT_ROOT / "research_protocols/common_liquidity_order_flow_v1.md"
    seal_path = PROJECT_ROOT / "research_protocols/common_liquidity_order_flow_v1.seal.json"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    validate_common_liquidity_config(config)
    verification = verify_common_liquidity_seal(protocol_path, config_path, seal_path)
    assert verification["verified"] is True


def test_minute_features_aggregate_to_fifteen_minutes() -> None:
    timestamps = pd.date_range("2025-10-08", periods=30, freq="min", tz="UTC")
    samples = pd.DataFrame(
        {
            "timestamp": timestamps,
            "empty": False,
            "crossed": False,
            "spread_bps": range(30),
            "ask_depth_quote_10": 100.0,
            "bid_depth_quote_10": 200.0,
            "book_imbalance_10": 1 / 3,
        }
    )
    result = aggregate_minute_book_features(samples, "ADA-USDT", 15)
    assert len(result) == 2
    assert result["observed_minutes"].tolist() == [15, 15]
    assert result["top10_depth_quote"].tolist() == [300.0, 300.0]
    assert result["spread_bps"].tolist() == [7.0, 22.0]


def test_sealed_collection_has_1260_jobs() -> None:
    config = yaml.safe_load(
        (PROJECT_ROOT / "configs/research/common_liquidity_order_flow_v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    jobs = build_collection_jobs(config)
    assert len(jobs) == 180 * 7
    assert jobs["date"].nunique() == 180
