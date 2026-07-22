from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from order_flow_synchronization import (
    build_decisions,
    validate_frozen_config,
    verify_preregistration_seal,
)
from utils import load_config, save_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Order-flow synchronization 결과 검증")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs" / "research" / "order_flow_synchronization_v1.yaml"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_frozen_config(config)
    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_preregistration_seal(protocol_path, config_path, seal_path)
    output_dir = PROJECT_ROOT / config["output"]["base_dir"]

    required = [
        "spot_coverage.csv",
        "synchronization_summary.csv",
        "synchronization_null_draws.parquet",
        "lead_lag_summary.csv",
        "lead_lag_direction_bootstrap.csv",
        "futures_confirmation_summary.csv",
        "futures_file_manifest.csv",
        "decisions.csv",
        "order_flow_synchronization_report.md",
        "run_summary.json",
        "futures_aggregation_sensitivity/futures_aggregation_comparison.csv",
        "futures_aggregation_sensitivity/futures_source_row_quality.csv",
        "futures_aggregation_sensitivity/futures_aggregation_sensitivity_report.md",
        "futures_aggregation_sensitivity/run_summary.json",
    ]
    missing = [name for name in required if not (output_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing result artifacts: {', '.join(missing)}")

    synchronization = pd.read_csv(output_dir / "synchronization_summary.csv")
    lead_lag = pd.read_csv(output_dir / "lead_lag_summary.csv")
    bootstrap = pd.read_csv(output_dir / "lead_lag_direction_bootstrap.csv")
    futures = pd.read_csv(output_dir / "futures_confirmation_summary.csv")
    stored = pd.read_csv(output_dir / "decisions.csv")
    recomputed = build_decisions(synchronization, lead_lag, bootstrap, futures, config)
    pd.testing.assert_frame_equal(stored, recomputed, check_dtype=False)

    if len(synchronization) != 2 or synchronization["q_value_bh_fdr"].isna().any():
        raise ValueError("Synchronization family is incomplete")
    if len(lead_lag) != 6 or lead_lag["q_value_bh_fdr"].isna().any():
        raise ValueError("Lead-lag family is incomplete")
    if len(futures) != 2 or futures["q_value_bh_fdr"].isna().any():
        raise ValueError("Futures confirmation family is incomplete")
    if len(pd.read_parquet(output_dir / "synchronization_null_draws.parquet")) != int(config["analysis"]["null_repetitions"]):
        raise ValueError("Synchronization null repetitions do not match frozen config")
    if not stored.loc[stored["decision"].eq("intentional_herding_identification"), "passed"].eq(False).all():
        raise ValueError("Intentional-herding limitation was not preserved")
    if not stored.loc[stored["decision"].eq("directional_alpha"), "classification"].eq("not_tested").all():
        raise ValueError("Directional alpha must remain untested")

    run_summary = json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8"))
    if run_summary.get("news_reddit_sentiment_used") is not False:
        raise ValueError("Forbidden external text data were marked as used")

    sensitivity_dir = output_dir / "futures_aggregation_sensitivity"
    comparison = pd.read_csv(sensitivity_dir / "futures_aggregation_comparison.csv")
    if set(comparison["aggregation"]) != {"mean_primary", "last_sensitivity"}:
        raise ValueError("Futures sensitivity aggregation family is incomplete")
    primary_copy = comparison.loc[comparison["aggregation"].eq("mean_primary")].drop(
        columns="aggregation"
    )
    pd.testing.assert_frame_equal(
        futures.reset_index(drop=True),
        primary_copy.reset_index(drop=True),
        check_dtype=False,
    )
    quality = pd.read_csv(sensitivity_dir / "futures_source_row_quality.csv")
    if abs(float(quality["share"].sum()) - 1.0) > 1e-12:
        raise ValueError("Futures source-row quality shares do not sum to one")
    sensitivity_summary = json.loads((sensitivity_dir / "run_summary.json").read_text(encoding="utf-8"))
    if sensitivity_summary.get("last_snapshot_gate_passed") is not True:
        raise ValueError("Last-snapshot futures sensitivity did not preserve the gate")
    if sensitivity_summary.get("primary_decision_replaced") is not False:
        raise ValueError("Post-result sensitivity must not replace the primary decision")
    if sensitivity_summary.get("news_reddit_sentiment_used") is not False:
        raise ValueError("Forbidden external text data were used in sensitivity analysis")
    verification = {
        "verified": True,
        "preregistration_seal_verified": bool(seal["verified"]),
        "artifact_count_checked": len(required),
        "decision_recomputation_match": True,
        "synchronization_family_size": len(synchronization),
        "lead_lag_family_size": len(lead_lag),
        "futures_family_size": len(futures),
        "futures_last_snapshot_sensitivity_passed": True,
        "futures_primary_decision_replaced": False,
        "intentional_herding_identified": False,
        "directional_alpha_tested": False,
        "news_reddit_sentiment_used": False,
    }
    save_json(verification, output_dir / "verification.json")
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
