from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from common_liquidity_order_flow import build_decisions
from okx_l2_audit import validate_common_liquidity_config, verify_common_liquidity_seal
from utils import load_config, save_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="공통 L2 유동성 OOS 결과 검증")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs/research/common_liquidity_order_flow_v1.yaml"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_common_liquidity_config(config)
    seal = verify_common_liquidity_seal(
        PROJECT_ROOT / config["protocol"]["path"],
        config_path,
        PROJECT_ROOT / config["protocol"]["seal_path"],
    )
    output = PROJECT_ROOT / config["output"]["base_dir"]
    required = [
        "analysis_quality_gates.csv",
        "analysis_coverage.csv",
        "development_feature_scalers.csv",
        "development_regression_coefficients.csv",
        "primary_summary.csv",
        "primary_bootstrap_draws.parquet",
        "secondary_summary.csv",
        "secondary_null_draws.parquet",
        "oos_event_flags.csv",
        "decisions.csv",
        "common_liquidity_order_flow_report.md",
        "analysis_run_summary.json",
        "config_snapshot.yaml",
        "protocol_snapshot.md",
        "preregistration_seal_verification.json",
        "analysis_input_manifest.json",
        "analysis_provenance.json",
        "plots/common_flow_liquidity_stress.png",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing result artifacts: {', '.join(missing)}")

    quality = pd.read_csv(output / "analysis_quality_gates.csv")
    if not bool(quality.iloc[:2]["passed"].all()):
        raise ValueError("Sealed data-quality gates do not pass")
    primary = pd.read_csv(output / "primary_summary.csv")
    secondary = pd.read_csv(output / "secondary_summary.csv")
    bootstrap = pd.read_parquet(output / "primary_bootstrap_draws.parquet")
    nulls = pd.read_parquet(output / "secondary_null_draws.parquet")
    if len(bootstrap) != int(config["analysis"]["bootstrap_repetitions"]):
        raise ValueError("Primary bootstrap repetitions differ from sealed config")
    if len(nulls) != int(config["analysis"]["null_repetitions"]):
        raise ValueError("Secondary null repetitions differ from sealed config")

    primary_p = (1 + bootstrap["correlation_reduction"].le(0).sum()) / (len(bootstrap) + 1)
    secondary_p = (
        1 + nulls["overlap_share"].ge(secondary.iloc[0]["observed_overlap_share"]).sum()
    ) / (len(nulls) + 1)
    checks = [
        np.isclose(primary.iloc[0]["p_value_one_sided"], primary_p),
        np.isclose(
            primary.iloc[0]["bootstrap_ci_lower"],
            bootstrap["correlation_reduction"].quantile(0.025),
        ),
        np.isclose(
            primary.iloc[0]["bootstrap_ci_upper"],
            bootstrap["correlation_reduction"].quantile(0.975),
        ),
        np.isclose(secondary.iloc[0]["p_value_one_sided"], secondary_p),
        np.isclose(
            secondary.iloc[0]["null_mean_overlap_share"], nulls["overlap_share"].mean()
        ),
    ]
    if not all(checks):
        raise ValueError("Stored summary statistics do not match public draws")

    recomputed_primary, recomputed_secondary, recomputed_decisions = build_decisions(
        primary.drop(columns=["q_value_bh_fdr"]),
        secondary.drop(columns=["q_value_bh_fdr"]),
        config,
    )
    pd.testing.assert_frame_equal(
        primary, recomputed_primary, check_dtype=False, check_exact=False, rtol=1e-12
    )
    pd.testing.assert_frame_equal(
        secondary, recomputed_secondary, check_dtype=False, check_exact=False, rtol=1e-12
    )
    stored_decisions = pd.read_csv(output / "decisions.csv")
    pd.testing.assert_frame_equal(stored_decisions, recomputed_decisions, check_dtype=False)
    if not stored_decisions.loc[
        stored_decisions["decision"].eq("directional_alpha"), "classification"
    ].eq("not_tested").all():
        raise ValueError("Directional alpha limitation was not preserved")

    run_summary = json.loads(
        (output / "analysis_run_summary.json").read_text(encoding="utf-8")
    )
    if run_summary.get("news_reddit_twitter_sentiment_used") is not False:
        raise ValueError("Excluded text data were marked as used")
    verification = {
        "verified": True,
        "preregistration_seal_verified": bool(seal["verified"]),
        "artifact_count_checked": len(required),
        "quality_gate_passed": True,
        "decision_recomputation_match": True,
        "bootstrap_repetitions": len(bootstrap),
        "null_repetitions": len(nulls),
        "primary_supported": bool(stored_decisions.iloc[0]["passed"]),
        "secondary_supported": bool(stored_decisions.iloc[1]["passed"]),
        "directional_alpha_tested": False,
        "news_reddit_twitter_sentiment_used": False,
    }
    save_json(verification, output / "analysis_verification.json")
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
