from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dynamic_liquidity_flow import (
    build_prediction_design,
    validate_dynamic_config,
    verify_dynamic_seal,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _synthetic_panel() -> pd.DataFrame:
    index = pd.date_range("2025-10-08", periods=20, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "bucket_start": index,
            "sample_split": ["development"] * 10 + ["oos"] * 10,
            "common_flow": np.arange(20, dtype=float),
            "liquidity_stress": np.arange(20, dtype=float) / 2,
            "common_spread": 0.1,
            "common_depth_depletion": 0.2,
            "common_abs_imbalance": 0.3,
        }
    )


def test_target_alignment_never_crosses_sample_split() -> None:
    design, _, _ = build_prediction_design(
        _synthetic_panel(), "liquidity_to_flow", 15
    )
    assert design["target_split"].eq(design["sample_split"]).all()
    assert len(design) == 18
    assert design.loc[
        design["sample_split"].eq("development"), "target"
    ].max() == 9


def test_target_alignment_does_not_bridge_missing_timestamp() -> None:
    panel = _synthetic_panel().drop(index=5).reset_index(drop=True)
    design, _, _ = build_prediction_design(panel, "flow_to_liquidity", 15)
    missing_source = pd.Timestamp("2025-10-08 01:00", tz="UTC")
    assert missing_source not in set(design["source_time"])


def test_dynamic_protocol_is_still_sealed() -> None:
    config_path = PROJECT_ROOT / "configs/research/dynamic_liquidity_flow_v1.yaml"
    protocol_path = PROJECT_ROOT / "research_protocols/dynamic_liquidity_flow_v1.md"
    seal_path = PROJECT_ROOT / "research_protocols/dynamic_liquidity_flow_v1.seal.json"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    validate_dynamic_config(config)
    verification = verify_dynamic_seal(protocol_path, config_path, seal_path)
    assert verification["verified"] is True
