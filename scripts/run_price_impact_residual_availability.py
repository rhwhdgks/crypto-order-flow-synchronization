from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from price_impact_residual import (
    build_price_impact_availability_decisions,
    build_price_impact_availability_report,
    build_price_impact_pilot_panel,
    validate_price_impact_availability_config,
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
    parser = argparse.ArgumentParser(description="H2 가격충격 잔차 1분 데이터 감사")
    parser.add_argument(
        "--config",
        default=str(
            PROJECT_ROOT
            / "configs/research/price_impact_residual_availability_v1.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_price_impact_availability_config(config)
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    output = prepare_output_dirs(PROJECT_ROOT, config)
    data = config["data"]
    trade_path = (PROJECT_ROOT / data["trade_archive_path"]).resolve()
    l2_path = (PROJECT_ROOT / data["l2_archive_path"]).resolve()

    LOGGER.info("Reconstructing one-minute flow and L2 execution features")
    panel, summary = build_price_impact_pilot_panel(
        trade_path,
        l2_path,
        symbol=data["symbol"],
        sample_date=data["sample_date"],
        chunksize=int(data["chunksize"]),
        sweep_quote_amounts=config["audit"]["sweep_quote_amounts"],
    )
    decisions = build_price_impact_availability_decisions(panel, summary, config)
    report = build_price_impact_availability_report(summary, decisions)

    panel.to_parquet(output["base"] / "pilot_1m_panel.parquet", index=False)
    save_dataframe(summary, output["base"] / "availability_summary.csv", index=False)
    save_dataframe(decisions, output["base"] / "quality_decisions.csv", index=False)
    save_text(report, output["base"] / "availability_report.md")
    save_config_snapshot(config, output["base"] / "config_snapshot.yaml")
    manifest = save_input_manifest(
        [trade_path, l2_path], output["base"] / "input_manifest.json"
    )
    save_provenance_manifest(
        config,
        output["base"] / "provenance.json",
        schema_version=1,
        pipeline_version="price_impact_residual_availability_v1",
        statistical_method="fixed-file one-minute point-in-time availability audit",
        input_manifest_path=manifest,
    )
    ready = bool(decisions.iloc[-1]["passed"])
    run_summary = {
        "study": config["study"]["name"],
        "h2_preregistration_ready": ready,
        "directional_alpha_tested": False,
    }
    save_json(run_summary, output["base"] / "run_summary.json")
    LOGGER.info("Audit complete: %s", json.dumps(run_summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
