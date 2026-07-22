from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from okx_order_flow_external_validation import (
    build_okx_external_decisions,
    build_okx_external_report,
    build_provider_comparison,
    collect_okx_monthly_buckets,
    validate_okx_external_config,
    verify_okx_preregistration_seal,
)
from order_flow_synchronization import (
    analyze_lead_lag,
    analyze_synchronization,
    build_flow_composites,
    plot_lead_lag,
    plot_pairwise_correlations,
    plot_synchronization_null,
    residualize_aggressor_flow,
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
    parser = argparse.ArgumentParser(description="OKX order-flow synchronization provider external validation")
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
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    seal = verify_okx_preregistration_seal(protocol_path, config_path, seal_path)

    output_dir = PROJECT_ROOT / config["output"]["base_dir"]
    if (output_dir / "decisions.csv").exists():
        raise FileExistsError("Frozen OKX external-validation results already exist; refusing to overwrite")
    intermediate_dir = output_dir / "intermediate"
    plots_dir = output_dir / "plots"
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    def progress(state: dict) -> None:
        LOGGER.info(
            "OKX collection %d/%d (%.1f%%): %s %s",
            state["completed_jobs"],
            state["total_jobs"],
            100 * state["progress_share"],
            state["last_completed_symbol"],
            state["last_completed_month"],
        )

    LOGGER.info("봉인된 protocol을 확인하고 OKX 공식 월별 tick archive를 수집·집계합니다")
    spot, coverage, source_manifest = collect_okx_monthly_buckets(config, PROJECT_ROOT, progress)
    spot.to_parquet(intermediate_dir / "okx_trade_buckets_15m.parquet", index=False)
    save_dataframe(coverage, output_dir / "source_coverage.csv", index=False)
    save_dataframe(source_manifest, output_dir / "source_file_manifest.csv", index=False)

    residual, coefficients, residual_diagnostics = residualize_aggressor_flow(spot, config)
    synchronization, null_draws, pairwise, residual_matrix = analyze_synchronization(residual, config)
    composites = build_flow_composites(residual, config)
    lead_lag, bootstrap = analyze_lead_lag(composites, config)
    decisions = build_okx_external_decisions(synchronization, lead_lag, bootstrap, config)

    binance_dir = PROJECT_ROOT / "outputs" / "v2" / "order_flow_synchronization_v1"
    binance_sync = pd.read_csv(binance_dir / "synchronization_summary.csv")
    binance_lead_lag = pd.read_csv(binance_dir / "lead_lag_summary.csv")
    comparison = build_provider_comparison(
        synchronization,
        lead_lag,
        binance_sync,
        binance_lead_lag,
    )

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
    composites.to_parquet(intermediate_dir / "flow_composites.parquet", index=False)
    save_dataframe(synchronization, output_dir / "synchronization_summary.csv", index=False)
    null_draws.to_parquet(output_dir / "synchronization_null_draws.parquet", index=False)
    save_dataframe(pairwise, output_dir / "pairwise_residual_correlations.csv", index=False)
    save_dataframe(lead_lag, output_dir / "lead_lag_summary.csv", index=False)
    save_dataframe(bootstrap, output_dir / "lead_lag_direction_bootstrap.csv", index=False)
    save_dataframe(comparison, output_dir / "provider_comparison.csv", index=False)
    save_dataframe(decisions, output_dir / "decisions.csv", index=False)

    synchronization_plot = plots_dir / "okx_synchronization_observed_vs_null.png"
    pairwise_plot = plots_dir / "okx_pairwise_residual_correlations.png"
    lead_lag_plot = plots_dir / "okx_major_alt_lead_lag.png"
    plot_synchronization_null(synchronization, synchronization_plot)
    plot_pairwise_correlations(pairwise, config["data"]["symbols"], pairwise_plot)
    plot_lead_lag(lead_lag, lead_lag_plot)
    plot_paths = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in [synchronization_plot, pairwise_plot, lead_lag_plot]
    ]
    report = build_okx_external_report(
        coverage,
        synchronization,
        lead_lag,
        bootstrap,
        comparison,
        decisions,
        plot_paths,
    )
    save_text(report, output_dir / "okx_order_flow_external_validation_report.md")
    save_config_snapshot(config, output_dir / "config_snapshot.yaml")
    save_text(protocol_path.read_text(encoding="utf-8"), output_dir / "protocol_snapshot.md")
    save_json(seal, output_dir / "preregistration_seal_verification.json")
    input_manifest = save_input_manifest(
        [
            config_path,
            protocol_path,
            seal_path,
            protocol_path.with_name(f"{protocol_path.stem}_amendment_1.md"),
            protocol_path.with_name(f"{protocol_path.stem}_amendment_2.md"),
            protocol_path.with_name(f"{protocol_path.stem}.pre_timezone.seal.json"),
            protocol_path.with_name(f"{protocol_path.stem}.pre_composite.seal.json"),
            output_dir / "source_file_manifest.csv",
        ],
        output_dir / "input_manifest.json",
    )
    save_provenance_manifest(
        config,
        output_dir / "provenance.json",
        schema_version=1,
        pipeline_version="okx-order-flow-external-validation-v1",
        statistical_method=(
            "provider external validation; development-fitted residualization; "
            "499 half-year/day circular shifts; HAC lead-lag; 499 UTC-day block bootstraps"
        ),
        input_manifest_path=input_manifest,
        random_seed=int(config["analysis"]["null_seed"]),
        train_start=config["sample_split"]["development_start"],
        train_end=config["sample_split"]["development_end_exclusive"],
        oos_start=config["sample_split"]["oos_start"],
        oos_end=config["sample_split"]["oos_end_exclusive"],
    )
    replication = decisions.loc[
        decisions["decision"].eq("okx_spot_synchronization_external_replication")
    ].iloc[0]
    cascade = decisions.loc[decisions["decision"].eq("okx_major_to_alt_cascade")].iloc[0]
    summary = {
        "status": "complete",
        "external_replication_classification": str(replication["classification"]),
        "okx_spot_synchronization_passed": bool(replication["passed"]),
        "okx_major_to_alt_cascade_passed": bool(cascade["passed"]),
        "intentional_herding_identified": False,
        "directional_alpha_tested": False,
        "news_reddit_twitter_sentiment_used": False,
    }
    save_json(summary, output_dir / "run_summary.json")
    LOGGER.info("OKX 외부검증 완료: %s", replication["classification"])
    print(json.dumps({"output_dir": str(output_dir), **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
