from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from common_liquidity_order_flow import (
    build_common_liquidity_features,
    build_decisions,
    build_report,
    load_and_align_inputs,
    run_primary_test,
    run_secondary_test,
    save_diagnostic_plot,
    validate_l2_quality_coverage,
)
from okx_l2_audit import validate_common_liquidity_config, verify_common_liquidity_seal
from utils import (
    load_config,
    prepare_output_dirs,
    save_config_snapshot,
    save_dataframe,
    save_input_manifest,
    save_json,
    save_provenance_manifest,
    save_text,
    setup_logging,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="봉인된 공통 L2 유동성 OOS 연구")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs/research/common_liquidity_order_flow_v1.yaml"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    validate_common_liquidity_config(config)
    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_common_liquidity_seal(protocol_path, config_path, seal_path)
    output = prepare_output_dirs(PROJECT_ROOT, config)
    l2_path = output["intermediate"] / "l2_feature_panel_15m.parquet"
    flow_path = PROJECT_ROOT / config["data"]["order_flow_residual_path"]
    coverage_path = output["base"] / "l2_feature_coverage.csv"

    LOGGER.info("Validating sealed L2 quality gates")
    quality = validate_l2_quality_coverage(pd.read_csv(coverage_path), config)
    panel, aligned_coverage = load_and_align_inputs(l2_path, flow_path, config)
    LOGGER.info("Fitting development-only feature transformations")
    panel, scalers = build_common_liquidity_features(panel, config)
    LOGGER.info("Running primary OOS UTC-day bootstrap")
    panel, coefficients, primary, bootstrap = run_primary_test(panel, config)
    LOGGER.info("Running secondary half-year day-shift null")
    secondary, nulls, events = run_secondary_test(panel, config)
    primary, secondary, decisions = build_decisions(primary, secondary, config)
    report = build_report(quality, aligned_coverage, primary, secondary, decisions)

    save_dataframe(quality, output["base"] / "analysis_quality_gates.csv", index=False)
    save_dataframe(aligned_coverage, output["base"] / "analysis_coverage.csv", index=False)
    save_dataframe(scalers, output["base"] / "development_feature_scalers.csv", index=False)
    save_dataframe(coefficients, output["base"] / "development_regression_coefficients.csv", index=False)
    save_dataframe(primary, output["base"] / "primary_summary.csv", index=False)
    bootstrap.to_parquet(output["base"] / "primary_bootstrap_draws.parquet", index=False)
    save_dataframe(secondary, output["base"] / "secondary_summary.csv", index=False)
    nulls.to_parquet(output["base"] / "secondary_null_draws.parquet", index=False)
    save_dataframe(events, output["base"] / "oos_event_flags.csv", index=False)
    save_dataframe(decisions, output["base"] / "decisions.csv", index=False)
    panel.to_parquet(output["intermediate"] / "common_liquidity_analysis_panel.parquet", index=False)
    save_text(report, output["base"] / "common_liquidity_order_flow_report.md")
    save_diagnostic_plot(panel, output["plots"] / "common_flow_liquidity_stress.png")
    save_config_snapshot(config, output["base"] / "config_snapshot.yaml")
    save_text(protocol_path.read_text(encoding="utf-8"), output["base"] / "protocol_snapshot.md")
    save_json(seal, output["base"] / "preregistration_seal_verification.json")
    manifest = save_input_manifest([l2_path, flow_path], output["base"] / "analysis_input_manifest.json")
    save_provenance_manifest(
        config,
        output["base"] / "analysis_provenance.json",
        schema_version=1,
        pipeline_version="common_liquidity_order_flow_v1",
        statistical_method="development-frozen contemporaneous OLS; UTC-day bootstrap; half-year circular day-shift null",
        input_manifest_path=manifest,
        random_seed=int(config["analysis"]["random_seed"]),
        train_start=config["sample_split"]["development_start"],
        train_end=config["sample_split"]["development_end_exclusive"],
        oos_start=config["sample_split"]["oos_start"],
        oos_end=config["sample_split"]["oos_end_exclusive"],
    )
    run_summary = {
        "study": "common_liquidity_order_flow_v1",
        "quality_gate_passed": bool(quality.iloc[:2]["passed"].all()),
        "primary_supported": bool(decisions.iloc[0]["passed"]),
        "secondary_supported": bool(decisions.iloc[1]["passed"]),
        "directional_alpha_tested": False,
        "news_reddit_twitter_sentiment_used": False,
    }
    save_json(run_summary, output["base"] / "analysis_run_summary.json")
    LOGGER.info("Study complete: %s", json.dumps(run_summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
