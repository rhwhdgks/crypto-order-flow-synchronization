from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dynamic_liquidity_flow import (
    build_dynamic_decisions,
    build_dynamic_report,
    load_common_panel,
    run_dynamic_tests,
    save_effect_plot,
    validate_dynamic_config,
    verify_dynamic_seal,
)
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
    parser = argparse.ArgumentParser(description="동적 L2→주문흐름 OOS 연구")
    parser.add_argument(
        "--config",
        default=str(
            PROJECT_ROOT / "configs/research/dynamic_liquidity_flow_v1.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    validate_dynamic_config(config)
    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_dynamic_seal(protocol_path, config_path, seal_path)
    output = prepare_output_dirs(PROJECT_ROOT, config)
    input_path = PROJECT_ROOT / config["data"]["input_panel_path"]

    LOGGER.info("Loading frozen common-flow and L2 panel")
    panel, coverage = load_common_panel(input_path, config)
    LOGGER.info("Running six development-frozen OOS models with 499 day bootstraps")
    summary, draws, coefficients = run_dynamic_tests(panel, config)
    decisions = build_dynamic_decisions(summary, config)
    report = build_dynamic_report(coverage, summary, decisions)

    save_dataframe(coverage, output["base"] / "analysis_coverage.csv", index=False)
    save_dataframe(summary, output["base"] / "dynamic_test_summary.csv", index=False)
    save_dataframe(draws, output["base"] / "bootstrap_draws.csv", index=False)
    save_dataframe(
        coefficients,
        output["base"] / "development_coefficients.csv",
        index=False,
    )
    save_dataframe(decisions, output["base"] / "decisions.csv", index=False)
    save_text(report, output["base"] / "dynamic_liquidity_flow_report.md")
    save_effect_plot(summary, output["plots"] / "dynamic_mse_improvement.png")
    save_config_snapshot(config, output["base"] / "config_snapshot.yaml")
    save_text(
        protocol_path.read_text(encoding="utf-8"),
        output["base"] / "protocol_snapshot.md",
    )
    save_json(seal, output["base"] / "preregistration_seal_verification.json")
    manifest = save_input_manifest(
        [input_path], output["base"] / "analysis_input_manifest.json"
    )
    save_provenance_manifest(
        config,
        output["base"] / "analysis_provenance.json",
        schema_version=1,
        pipeline_version="dynamic_liquidity_flow_v1",
        statistical_method=(
            "development-frozen OLS forecast comparison; paired UTC-day bootstrap; "
            "six-test BH-FDR"
        ),
        input_manifest_path=manifest,
        random_seed=int(config["analysis"]["random_seed"]),
        train_start=config["sample_split"]["development_start"],
        train_end=config["sample_split"]["development_end_exclusive"],
        oos_start=config["sample_split"]["oos_start"],
        oos_end=config["sample_split"]["oos_end_exclusive"],
    )
    run_summary = {
        "study": "dynamic_liquidity_flow_v1",
        "primary_supported": bool(decisions.iloc[0]["passed"]),
        "tests": len(summary),
        "bootstrap_repetitions_per_test": int(
            config["analysis"]["bootstrap_repetitions"]
        ),
        "directional_return_alpha_tested": False,
        "news_reddit_twitter_sentiment_used": False,
    }
    save_json(run_summary, output["base"] / "analysis_run_summary.json")
    LOGGER.info("Study complete: %s", json.dumps(run_summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
