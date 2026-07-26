from __future__ import annotations

import pandas as pd

from price_impact_residual import (
    build_price_impact_availability_decisions,
    validate_price_impact_availability_config,
)


def _config() -> dict:
    return {
        "data": {"interval_minutes": 1},
        "audit": {
            "sample_interval_seconds": 60,
            "sweep_quote_amounts": [10_000, 50_000, 100_000],
            "minimum_l2_minute_share": 1.0,
            "minimum_flow_to_l2_match_share": 1.0,
        },
    }


def test_price_impact_availability_config_is_fixed() -> None:
    validate_price_impact_availability_config(_config())


def test_availability_gate_requires_point_in_time_and_sweeps() -> None:
    panel = pd.DataFrame(
        {
            **{
                f"{side}_{field}_quote_{amount}": [1.0]
                for amount in [10_000, 50_000, 100_000]
                for side in ["buy", "sell"]
                for field in ["vwap", "fill_ratio"]
            }
        }
    )
    summary = pd.DataFrame(
        [
            {
                "l2_minute_share": 1.0,
                "flow_to_l2_match_share": 1.0,
                "duplicate_panel_minutes": 0,
                "core_missing_values": 0,
                "timestamp_violations": 0,
            }
        ]
    )
    decisions = build_price_impact_availability_decisions(panel, summary, _config())
    assert bool(decisions.iloc[-1]["passed"])

    summary.loc[0, "timestamp_violations"] = 1
    failed = build_price_impact_availability_decisions(panel, summary, _config())
    assert not bool(failed.iloc[-1]["passed"])
