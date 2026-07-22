from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from order_flow_synchronization import (
    analyze_futures_confirmation,
    load_futures_metrics,
    validate_frozen_config,
    verify_preregistration_seal,
)
from utils import load_config, save_dataframe, save_json, save_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Futures 15분 집계 방식 사후 민감도 검증")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs" / "research" / "order_flow_synchronization_v1.yaml"),
    )
    return parser.parse_args()


def _gate(summary: pd.DataFrame, config: dict) -> bool:
    analysis = config["analysis"]
    regression = summary.loc[summary["test"].eq("spot_to_futures_flow_regression")].iloc[0]
    concordance = summary.loc[summary["test"].eq("extreme_event_direction_concordance")].iloc[0]
    return bool(
        regression["estimate"] >= float(analysis["futures_minimum_standardized_beta"])
        and regression["q_value_bh_fdr"] <= float(analysis["fdr_alpha"])
        and concordance["event_count"] >= int(analysis["futures_minimum_event_count"])
        and concordance["estimate"] >= float(analysis["futures_minimum_directional_concordance"])
        and concordance["q_value_bh_fdr"] <= float(analysis["fdr_alpha"])
    )


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_frozen_config(config)
    protocol_path = PROJECT_ROOT / config["protocol"]["path"]
    seal_path = PROJECT_ROOT / config["protocol"]["seal_path"]
    verify_preregistration_seal(protocol_path, config_path, seal_path)

    primary_dir = PROJECT_ROOT / config["output"]["base_dir"]
    output_dir = primary_dir / "futures_aggregation_sensitivity"
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = output_dir / "futures_aggregation_comparison.csv"
    if comparison_path.exists():
        raise FileExistsError("Futures sensitivity results already exist; refusing to overwrite")

    primary_summary = pd.read_csv(primary_dir / "futures_confirmation_summary.csv")
    composites = pd.read_parquet(primary_dir / "intermediate" / "flow_composites.parquet")
    futures_dir = PROJECT_ROOT / config["data"]["futures_archive_dir"]
    last_panel, _ = load_futures_metrics(futures_dir, config, taker_aggregation="last")
    last_summary, last_diagnostics, last_scaling = analyze_futures_confirmation(
        last_panel, composites, config
    )

    comparison = pd.concat(
        [
            primary_summary.assign(aggregation="mean_primary"),
            last_summary.assign(aggregation="last_sensitivity"),
        ],
        ignore_index=True,
    )
    quality = (
        last_panel["source_rows"]
        .value_counts()
        .sort_index()
        .rename_axis("source_rows_per_15m_bucket")
        .reset_index(name="bucket_asset_rows")
    )
    quality["share"] = quality["bucket_asset_rows"] / quality["bucket_asset_rows"].sum()
    mean_pass = _gate(primary_summary, config)
    last_pass = _gate(last_summary, config)

    regression = comparison.loc[comparison["test"].eq("spot_to_futures_flow_regression")]
    concordance = comparison.loc[comparison["test"].eq("extreme_event_direction_concordance")]
    report = "\n".join(
        [
            "# Futures 15분 집계 민감도 검증",
            "",
            "## 목적",
            "",
            "이 검증은 primary 결과를 확인한 뒤 실행한 사후 민감도 분석입니다. 기존 15분 평균을 바꾸지 않고, 각 15분 버킷의 마지막 futures taker 스냅샷을 사용했을 때도 결론이 유지되는지만 확인합니다.",
            "",
            "## 결과",
            "",
            f"- 평균 집계 regression beta: {regression.loc[regression['aggregation'].eq('mean_primary'), 'estimate'].iloc[0]:.5f}",
            f"- 마지막 스냅샷 regression beta: {regression.loc[regression['aggregation'].eq('last_sensitivity'), 'estimate'].iloc[0]:.5f}",
            f"- 평균 집계 극단 event 방향 일치율: {concordance.loc[concordance['aggregation'].eq('mean_primary'), 'estimate'].iloc[0]:.2%}",
            f"- 마지막 스냅샷 극단 event 방향 일치율: {concordance.loc[concordance['aggregation'].eq('last_sensitivity'), 'estimate'].iloc[0]:.2%}",
            f"- Primary gate: **{'통과' if mean_pass else '미통과'}**",
            f"- Last-snapshot sensitivity gate: **{'통과' if last_pass else '미통과'}**",
            "",
            "## 해석",
            "",
            "두 집계법의 gate 판정이 같으면, 일부 불규칙한 source snapshot 개수가 futures confirmation 결론을 만든 것은 아닙니다. 이 민감도 검증은 사전등록 primary를 대체하지 않으며 추가 검증으로만 보고합니다.",
            "",
            "- 뉴스·Reddit·sentiment는 사용하지 않았습니다.",
            "- 미래수익률 alpha는 검정하지 않았습니다.",
            "",
        ]
    )

    save_dataframe(last_summary, output_dir / "futures_confirmation_last_summary.csv", index=False)
    save_dataframe(last_diagnostics, output_dir / "futures_last_diagnostics.csv", index=False)
    save_dataframe(last_scaling, output_dir / "futures_last_scaling.csv", index=False)
    save_dataframe(comparison, comparison_path, index=False)
    save_dataframe(quality, output_dir / "futures_source_row_quality.csv", index=False)
    save_text(report, output_dir / "futures_aggregation_sensitivity_report.md")
    save_json(
        {
            "status": "complete",
            "analysis_type": "post_result_sensitivity",
            "primary_mean_gate_passed": mean_pass,
            "last_snapshot_gate_passed": last_pass,
            "primary_decision_replaced": False,
            "news_reddit_sentiment_used": False,
        },
        output_dir / "run_summary.json",
    )
    print(json.dumps({"output_dir": str(output_dir), "last_snapshot_gate_passed": last_pass}, ensure_ascii=False))


if __name__ == "__main__":
    main()
