from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from order_flow_synchronization import (
    analyze_futures_confirmation,
    analyze_lead_lag,
    analyze_synchronization,
    build_decisions,
    build_flow_composites,
    build_report,
    load_futures_metrics,
    load_spot_frame,
    plot_lead_lag,
    plot_pairwise_correlations,
    plot_synchronization_null,
    residualize_aggressor_flow,
    validate_frozen_config,
    verify_preregistration_seal,
)
from utils import (
    load_config,
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
    parser = argparse.ArgumentParser(description="교차자산 order-flow synchronization 연구")
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
    setup_logging(config.get("logging", {}).get("level", "INFO"))

    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_preregistration_seal(protocol_path, config_path, seal_path)
    output_dir = PROJECT_ROOT / config["output"]["base_dir"]
    if (output_dir / "decisions.csv").exists():
        raise FileExistsError("Frozen order-flow results already exist; refusing to overwrite")
    intermediate_dir = output_dir / "intermediate"
    plots_dir = output_dir / "plots"
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    spot_path = PROJECT_ROOT / config["data"]["spot_input_path"]
    LOGGER.info("봉인된 protocol/config를 확인하고 spot panel을 읽습니다")
    spot, coverage = load_spot_frame(spot_path, config)
    residual, coefficients, residual_diagnostics = residualize_aggressor_flow(spot, config)
    LOGGER.info("OOS 교차자산 synchronization null %d회를 생성합니다", int(config["analysis"]["null_repetitions"]))
    synchronization, null_draws, pairwise, residual_matrix = analyze_synchronization(residual, config)
    composites = build_flow_composites(residual, config)
    LOGGER.info("Major↔Alt lead-lag와 day-block bootstrap을 계산합니다")
    lead_lag, bootstrap = analyze_lead_lag(composites, config)

    futures_dir = PROJECT_ROOT / config["data"]["futures_archive_dir"]
    LOGGER.info("5개 공통자산의 futures metrics ZIP을 읽습니다")
    futures_panel, futures_inventory = load_futures_metrics(futures_dir, config)
    futures_summary, futures_diagnostics, futures_scaling = analyze_futures_confirmation(
        futures_panel, composites, config
    )
    decisions = build_decisions(
        synchronization,
        lead_lag,
        bootstrap,
        futures_summary,
        config,
    )

    save_dataframe(coverage, output_dir / "spot_coverage.csv", index=False)
    save_dataframe(coefficients, output_dir / "residualization_coefficients.csv", index=False)
    save_dataframe(residual_diagnostics, output_dir / "residualization_diagnostics.csv", index=False)
    residual[
        [
            "bucket_start",
            "symbol",
            "sample_split",
            "aggressor_imbalance",
            "aggressor_fitted",
            "aggressor_residual",
            "aggressor_residual_z",
        ]
    ].to_parquet(intermediate_dir / "aggressor_residual_panel.parquet", index=False)
    residual_matrix.to_parquet(intermediate_dir / "oos_residual_matrix.parquet")
    save_dataframe(synchronization, output_dir / "synchronization_summary.csv", index=False)
    null_draws.to_parquet(output_dir / "synchronization_null_draws.parquet", index=False)
    save_dataframe(pairwise, output_dir / "pairwise_residual_correlations.csv", index=False)
    composites.to_parquet(intermediate_dir / "flow_composites.parquet", index=False)
    save_dataframe(lead_lag, output_dir / "lead_lag_summary.csv", index=False)
    save_dataframe(bootstrap, output_dir / "lead_lag_direction_bootstrap.csv", index=False)
    futures_panel.to_parquet(intermediate_dir / "futures_metrics_15m.parquet", index=False)
    save_dataframe(futures_inventory, output_dir / "futures_file_manifest.csv", index=False)
    save_dataframe(futures_summary, output_dir / "futures_confirmation_summary.csv", index=False)
    save_dataframe(futures_diagnostics, output_dir / "futures_diagnostics.csv", index=False)
    save_dataframe(futures_scaling, output_dir / "futures_scaling.csv", index=False)
    save_dataframe(decisions, output_dir / "decisions.csv", index=False)

    synchronization_plot = plots_dir / "synchronization_observed_vs_null.png"
    pairwise_plot = plots_dir / "pairwise_residual_correlations.png"
    lead_lag_plot = plots_dir / "major_alt_lead_lag.png"
    plot_synchronization_null(synchronization, synchronization_plot)
    plot_pairwise_correlations(pairwise, config["data"]["symbols"], pairwise_plot)
    plot_lead_lag(lead_lag, lead_lag_plot)
    plot_paths = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in [synchronization_plot, pairwise_plot, lead_lag_plot]
    ]
    report = build_report(
        coverage,
        synchronization,
        lead_lag,
        bootstrap,
        futures_summary,
        futures_diagnostics,
        decisions,
        plot_paths,
    )
    save_text(report, output_dir / "order_flow_synchronization_report.md")
    save_config_snapshot(config, output_dir / "config_snapshot.yaml")
    save_text(protocol_path.read_text(encoding="utf-8"), output_dir / "protocol_snapshot.md")
    save_json(seal, output_dir / "preregistration_seal_verification.json")
    input_manifest = save_input_manifest(
        [config_path, protocol_path, seal_path, spot_path],
        output_dir / "input_manifest.json",
    )
    save_provenance_manifest(
        config,
        output_dir / "provenance.json",
        schema_version=1,
        pipeline_version="order-flow-synchronization-v1",
        statistical_method=(
            "development-fitted residualization; 499 half-year/day circular shifts; "
            "HAC lead-lag; 499 UTC-day block bootstraps; two-test futures confirmation"
        ),
        input_manifest_path=input_manifest,
        random_seed=int(config["analysis"]["null_seed"]),
        train_start=config["sample_split"]["development_start"],
        train_end=config["sample_split"]["development_end_exclusive"],
        oos_start=config["sample_split"]["oos_start"],
        oos_end=config["sample_split"]["oos_end_exclusive"],
    )
    final = decisions.loc[decisions["decision"].eq("final")].iloc[0]
    save_json(
        {
            "status": "complete",
            "final_classification": str(final["classification"]),
            "spot_synchronization_passed": bool(decisions.loc[decisions["decision"].eq("spot_synchronization"), "passed"].iloc[0]),
            "major_to_alt_cascade_passed": bool(decisions.loc[decisions["decision"].eq("major_to_alt_cascade"), "passed"].iloc[0]),
            "futures_confirmation_passed": bool(decisions.loc[decisions["decision"].eq("futures_confirmation"), "passed"].iloc[0]),
            "intentional_herding_identified": False,
            "directional_alpha_tested": False,
            "news_reddit_sentiment_used": False,
        },
        output_dir / "run_summary.json",
    )
    LOGGER.info("연구 완료: %s", final["classification"])
    print(json.dumps({"output_dir": str(output_dir), "classification": str(final["classification"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
