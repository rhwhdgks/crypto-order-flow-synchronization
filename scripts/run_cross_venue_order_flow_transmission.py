from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from cross_venue_order_flow import (
    build_common_flows,
    build_decisions,
    build_report,
    lead_lag_test,
    load_residual_panels,
    save_plot,
    synchronization_test,
    validate_frozen_config,
    verify_preregistration_seal,
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
    parser = argparse.ArgumentParser(description="Binance-OKX 교차거래소 주문흐름 연구")
    parser.add_argument(
        "--config",
        default=str(
            PROJECT_ROOT
            / "configs"
            / "research"
            / "cross_venue_order_flow_transmission_v1.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    validate_frozen_config(config)

    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_preregistration_seal(protocol_path, config_path, seal_path)
    output = prepare_output_dirs(PROJECT_ROOT, config)
    data = config["data"]
    binance_path = PROJECT_ROOT / data["binance_residual_path"]
    okx_path = PROJECT_ROOT / data["okx_residual_path"]
    LOGGER.info("Loading sealed residual panels")
    panels, coverage = load_residual_panels(binance_path, okx_path, config)
    common_flows = build_common_flows(panels, config)

    LOGGER.info("Running cross-venue synchronization null")
    synchronization, nulls = synchronization_test(panels, common_flows, config)
    LOGGER.info("Running symmetric lead-lag and day-block bootstrap")
    lead_lag, bootstrap = lead_lag_test(common_flows, config)
    decisions = build_decisions(synchronization, lead_lag, bootstrap, config)
    report = build_report(coverage, synchronization, lead_lag, bootstrap, decisions)

    save_dataframe(coverage, output["base"] / "source_coverage.csv", index=False)
    save_dataframe(synchronization, output["base"] / "synchronization_summary.csv", index=False)
    nulls.to_parquet(output["base"] / "synchronization_null_draws.parquet", index=False)
    save_dataframe(lead_lag, output["base"] / "lead_lag_summary.csv", index=False)
    save_dataframe(bootstrap, output["base"] / "lead_lag_direction_bootstrap.csv", index=False)
    save_dataframe(decisions, output["base"] / "decisions.csv", index=False)
    common_flows.to_parquet(output["intermediate"] / "cross_venue_common_flows.parquet")
    save_text(report, output["base"] / "cross_venue_order_flow_report.md")
    save_plot(common_flows, output["plots"] / "cross_venue_common_flow.png")
    save_config_snapshot(config, output["base"] / "config_snapshot.yaml")
    save_text(protocol_path.read_text(encoding="utf-8"), output["base"] / "protocol_snapshot.md")
    save_json(seal, output["base"] / "preregistration_seal_verification.json")
    manifest = save_input_manifest(
        [binance_path, okx_path], output["base"] / "input_manifest.json"
    )
    save_provenance_manifest(
        config,
        output["base"] / "provenance.json",
        schema_version=1,
        pipeline_version="cross_venue_order_flow_transmission_v1",
        statistical_method="half-year circular-shift null; HAC OLS; UTC-day bootstrap",
        input_manifest_path=manifest,
        random_seed=int(config["analysis"]["null_seed"]),
        train_start=config["sample_split"]["development_start"],
        train_end=config["sample_split"]["development_end_exclusive"],
        oos_start=config["sample_split"]["oos_start"],
        oos_end=config["sample_split"]["oos_end_exclusive"],
    )
    run_summary = {
        "study": "cross_venue_order_flow_transmission_v1",
        "rows_per_venue": int(coverage["rows"].min()),
        "timestamps": int(coverage["timestamps"].min()),
        "oos_timestamps": int(common_flows["sample_split"].eq("oos").sum()),
        "cross_venue_synchronization": decisions.loc[
            decisions["decision"].eq("cross_venue_synchronization"), "classification"
        ].iloc[0],
        "stable_directional_transmission": decisions.loc[
            decisions["decision"].eq("stable_directional_transmission"), "classification"
        ].iloc[0],
        "intentional_herding_identified": False,
        "directional_alpha_tested": False,
        "news_reddit_sentiment_used": False,
    }
    save_json(run_summary, output["base"] / "run_summary.json")
    LOGGER.info("Study complete: %s", json.dumps(run_summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
