from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from price_impact_study import (
    apply_frozen_impact_model,
    build_price_impact_decisions,
    build_price_impact_report,
    evaluate_event_portfolios,
    fit_development_impact_model,
    prepare_price_impact_panel,
    save_price_impact_plot,
    select_oos_events,
    summarize_price_impact_results,
    validate_price_impact_config,
    verify_price_impact_seal,
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
    parser = argparse.ArgumentParser(description="봉인된 H2 가격충격 잔차 OOS 연구")
    parser.add_argument(
        "--config",
        default=str(
            PROJECT_ROOT / "configs/research/price_impact_residual_v1.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    validate_price_impact_config(config)
    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_price_impact_seal(protocol_path, config_path, seal_path)
    output = prepare_output_dirs(PROJECT_ROOT, config)
    input_root = output["base"] / "intermediate"
    l2_path = input_root / "l2_feature_panel_1m.parquet"
    flow_path = input_root / "flow_feature_panel_1m.parquet"
    if not l2_path.is_file() or not flow_path.is_file():
        raise FileNotFoundError(
            "1분 입력 panel이 아직 없습니다. collect_price_impact_residual.py --execute를 먼저 완료하세요."
        )

    LOGGER.info("Preparing point-in-time one-minute panel")
    panel, coverage = prepare_price_impact_panel(
        pd.read_parquet(l2_path),
        pd.read_parquet(flow_path),
        config,
    )
    LOGGER.info("Freezing Development transforms and impact model")
    coefficients, scaler, diagnostics, threshold = fit_development_impact_model(
        panel, config
    )
    scored = apply_frozen_impact_model(panel, coefficients, scaler, config)
    events, positions = select_oos_events(scored, threshold, config)
    event_returns, position_ledger = evaluate_event_portfolios(
        panel, events, positions, config
    )
    summary, inferential, asset_summary = summarize_price_impact_results(
        event_returns, position_ledger, config
    )
    decisions = build_price_impact_decisions(
        event_returns, inferential, asset_summary, config
    )
    report = build_price_impact_report(
        coverage, diagnostics, inferential, decisions
    )

    save_dataframe(coverage, output["base"] / "analysis_coverage.csv", index=False)
    save_dataframe(coefficients, output["base"] / "development_coefficients.csv", index=False)
    save_dataframe(scaler, output["base"] / "development_scaler.csv", index=False)
    save_dataframe(diagnostics, output["base"] / "development_diagnostics.csv", index=False)
    save_dataframe(events, output["base"] / "event_ledger.csv", index=False)
    save_dataframe(positions, output["base"] / "position_selection.csv", index=False)
    save_dataframe(event_returns, output["base"] / "event_returns.csv", index=False)
    save_dataframe(position_ledger, output["base"] / "position_ledger.csv", index=False)
    save_dataframe(summary, output["base"] / "result_summary.csv", index=False)
    save_dataframe(inferential, output["base"] / "inferential_summary.csv", index=False)
    save_dataframe(asset_summary, output["base"] / "asset_contributions.csv", index=False)
    save_dataframe(decisions, output["base"] / "decisions.csv", index=False)
    save_text(report, output["base"] / "price_impact_residual_report.md")
    save_price_impact_plot(inferential, output["plots"] / "horizon_net_returns.png")
    save_config_snapshot(config, output["base"] / "config_snapshot.yaml")
    save_text(protocol_path.read_text(encoding="utf-8"), output["base"] / "protocol_snapshot.md")
    save_json(seal, output["base"] / "preregistration_seal_verification.json")
    manifest = save_input_manifest(
        [l2_path, flow_path], output["base"] / "analysis_input_manifest.json"
    )
    save_provenance_manifest(
        config,
        output["base"] / "analysis_provenance.json",
        schema_version=1,
        pipeline_version="price_impact_residual_v1",
        statistical_method="development-frozen pooled OLS; UTC-day bootstrap; four-horizon BH-FDR",
        input_manifest_path=manifest,
        random_seed=int(config["analysis"]["random_seed"]),
        train_start=config["sample_split"]["development_start"],
        train_end=config["sample_split"]["development_end_exclusive"],
        oos_start=config["sample_split"]["oos_start"],
        oos_end=config["sample_split"]["oos_end_exclusive"],
    )
    run_summary = {
        "study": config["study"]["name"],
        "primary_supported": bool(decisions.iloc[-1]["passed"]),
        "directional_alpha_tested": True,
        "news_reddit_twitter_sentiment_used": False,
    }
    save_json(run_summary, output["base"] / "analysis_run_summary.json")
    LOGGER.info("Study complete: %s", json.dumps(run_summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
