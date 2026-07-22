from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from okx_order_flow_external_validation import (
    build_okx_external_decisions,
    validate_okx_external_config,
    verify_okx_preregistration_seal,
)
from utils import load_config, save_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OKX order-flow external-validation verifier")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs" / "research" / "okx_order_flow_external_validation_v1.yaml"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_okx_external_config(config)
    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_okx_preregistration_seal(protocol_path, config_path, seal_path)
    output_dir = PROJECT_ROOT / config["output"]["base_dir"]
    required = [
        "source_coverage.csv",
        "source_file_manifest.csv",
        "synchronization_summary.csv",
        "synchronization_null_draws.parquet",
        "lead_lag_summary.csv",
        "lead_lag_direction_bootstrap.csv",
        "provider_comparison.csv",
        "decisions.csv",
        "okx_order_flow_external_validation_report.md",
        "run_summary.json",
    ]
    missing = [name for name in required if not (output_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing OKX result artifacts: {', '.join(missing)}")

    coverage = pd.read_csv(output_dir / "source_coverage.csv")
    manifest = pd.read_csv(output_dir / "source_file_manifest.csv")
    synchronization = pd.read_csv(output_dir / "synchronization_summary.csv")
    lead_lag = pd.read_csv(output_dir / "lead_lag_summary.csv")
    bootstrap = pd.read_csv(output_dir / "lead_lag_direction_bootstrap.csv")
    stored = pd.read_csv(output_dir / "decisions.csv")
    recomputed = build_okx_external_decisions(synchronization, lead_lag, bootstrap, config)
    pd.testing.assert_frame_equal(stored, recomputed, check_dtype=False)

    expected_jobs = len(config["data"]["symbols"]) * 25
    if len(manifest) != expected_jobs or manifest.duplicated(["symbol", "month"]).any():
        raise ValueError("OKX source manifest is incomplete or duplicated")
    if not coverage["quality_gate_pass"].all():
        raise ValueError("OKX source coverage gate is not preserved")
    if len(synchronization) != 2 or synchronization["q_value_bh_fdr"].isna().any():
        raise ValueError("OKX synchronization family is incomplete")
    if len(lead_lag) != 6 or lead_lag["q_value_bh_fdr"].isna().any():
        raise ValueError("OKX lead-lag family is incomplete")
    if len(pd.read_parquet(output_dir / "synchronization_null_draws.parquet")) != 499:
        raise ValueError("OKX null repetitions do not match the protocol")
    if not stored.loc[stored["decision"].eq("intentional_herding_identification"), "passed"].eq(False).all():
        raise ValueError("Intentional-herding limitation was not preserved")
    run_summary = json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8"))
    if run_summary.get("news_reddit_twitter_sentiment_used") is not False:
        raise ValueError("Forbidden text data were marked as used")
    verification = {
        "verified": True,
        "preregistration_seal_verified": bool(seal["verified"]),
        "artifact_count_checked": len(required),
        "decision_recomputation_match": True,
        "source_manifest_jobs": len(manifest),
        "minimum_intersection_share": float(coverage["intersection_share"].min()),
        "synchronization_family_size": len(synchronization),
        "lead_lag_family_size": len(lead_lag),
        "intentional_herding_identified": False,
        "directional_alpha_tested": False,
        "news_reddit_twitter_sentiment_used": False,
    }
    save_json(verification, output_dir / "verification.json")
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
