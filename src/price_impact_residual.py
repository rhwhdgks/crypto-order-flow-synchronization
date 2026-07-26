from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from okx_l2_audit import audit_l2_archive
from okx_order_flow_external_validation import aggregate_okx_month_archive


FLOW_ZERO_COLUMNS = [
    "transaction_count",
    "total_quantity",
    "total_quote_quantity",
    "buy_quote_quantity",
    "sell_quote_quantity",
    "aggressor_imbalance",
]


def validate_price_impact_availability_config(config: Mapping) -> None:
    data = config["data"]
    audit = config["audit"]
    if int(data["interval_minutes"]) != 1:
        raise ValueError("Price-impact availability audit is fixed to one-minute buckets")
    if int(audit["sample_interval_seconds"]) != 60:
        raise ValueError("L2 sampling must remain fixed at 60 seconds")
    if list(audit["sweep_quote_amounts"]) != [10_000, 50_000, 100_000]:
        raise ValueError("Execution-capacity tiers have drifted")
    if float(audit["minimum_l2_minute_share"]) != 1.0:
        raise ValueError("The pilot requires all 1,440 L2 minutes")
    if float(audit["minimum_flow_to_l2_match_share"]) != 1.0:
        raise ValueError("Every observed trade minute must match an L2 minute")


def build_price_impact_pilot_panel(
    trade_archive_path: str | Path,
    l2_archive_path: str | Path,
    symbol: str,
    sample_date: str,
    chunksize: int,
    sweep_quote_amounts: list[float],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    flow, metadata = aggregate_okx_month_archive(
        trade_archive_path,
        symbol=symbol,
        interval_minutes=1,
        chunksize=chunksize,
    )
    day_start = pd.Timestamp(sample_date, tz="UTC")
    day_end = day_start + pd.Timedelta(days=1)
    flow = flow.loc[
        flow["bucket_start"].ge(day_start) & flow["bucket_start"].lt(day_end)
    ].copy()

    l2_summary, l2 = audit_l2_archive(
        l2_archive_path,
        expected_symbol=symbol,
        expected_date=sample_date,
        sample_interval_seconds=60,
        sweep_quote_amounts=sweep_quote_amounts,
    )
    l2["bucket_start"] = pd.to_datetime(l2["timestamp"], utc=True).dt.floor("1min")
    if flow["bucket_start"].duplicated().any() or l2["bucket_start"].duplicated().any():
        raise ValueError("Duplicate one-minute timestamps detected")

    observed_flow_minutes = int(len(flow))
    matched_flow_minutes = int(flow["bucket_start"].isin(l2["bucket_start"]).sum())
    panel = l2.merge(flow, on="bucket_start", how="left", validate="one_to_one")
    panel["symbol"] = symbol
    panel["has_trades"] = panel["transaction_count"].notna()
    panel[FLOW_ZERO_COLUMNS] = panel[FLOW_ZERO_COLUMNS].fillna(0.0)
    panel["decision_timestamp"] = panel["bucket_start"] + pd.Timedelta(minutes=1)
    panel["observed_midpoint_return"] = np.log(
        panel["midpoint"].shift(-1) / panel["midpoint"]
    )
    panel["feature_timestamp_valid"] = (
        pd.to_datetime(panel["timestamp"], utc=True) <= panel["decision_timestamp"]
    )

    core_columns = [
        "midpoint",
        "spread_bps",
        "ask_depth_quote_10",
        "bid_depth_quote_10",
        "book_imbalance_10",
        "aggressor_imbalance",
    ]
    summary = l2_summary.copy()
    summary["raw_trade_rows_in_month"] = int(metadata["raw_rows"])
    summary["observed_flow_minutes"] = observed_flow_minutes
    summary["matched_flow_minutes"] = matched_flow_minutes
    summary["flow_to_l2_match_share"] = (
        matched_flow_minutes / observed_flow_minutes if observed_flow_minutes else np.nan
    )
    summary["l2_minute_share"] = len(l2) / 1_440
    summary["zero_trade_minutes"] = int((~panel["has_trades"]).sum())
    summary["duplicate_panel_minutes"] = int(panel["bucket_start"].duplicated().sum())
    summary["core_missing_values"] = int(panel[core_columns].isna().sum().sum())
    summary["timestamp_violations"] = int((~panel["feature_timestamp_valid"]).sum())
    return panel, summary


def build_price_impact_availability_decisions(
    panel: pd.DataFrame,
    summary: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    row = summary.iloc[0]
    audit = config["audit"]
    sweep_amounts = audit["sweep_quote_amounts"]
    checks = [
        (
            "full_l2_day",
            float(row["l2_minute_share"]) >= float(audit["minimum_l2_minute_share"]),
            f"share={float(row['l2_minute_share']):.6f}",
        ),
        (
            "all_trade_minutes_match_l2",
            float(row["flow_to_l2_match_share"])
            >= float(audit["minimum_flow_to_l2_match_share"]),
            f"share={float(row['flow_to_l2_match_share']):.6f}",
        ),
        (
            "unique_minute_keys",
            int(row["duplicate_panel_minutes"]) == 0,
            f"duplicates={int(row['duplicate_panel_minutes'])}",
        ),
        (
            "core_features_complete",
            int(row["core_missing_values"]) == 0,
            f"missing={int(row['core_missing_values'])}",
        ),
        (
            "point_in_time_ordering",
            int(row["timestamp_violations"]) == 0,
            f"violations={int(row['timestamp_violations'])}",
        ),
    ]
    for amount in sweep_amounts:
        suffix = f"quote_{int(amount)}"
        columns = [
            f"buy_vwap_{suffix}",
            f"sell_vwap_{suffix}",
            f"buy_fill_ratio_{suffix}",
            f"sell_fill_ratio_{suffix}",
        ]
        available = all(column in panel.columns for column in columns)
        complete = available and not panel[columns].isna().any().any()
        checks.append(
            (
                f"sweep_features_{int(amount)}",
                complete,
                "all minutes populated" if complete else "missing sweep fields",
            )
        )
    decisions = pd.DataFrame(checks, columns=["quality_gate", "passed", "evidence"])
    decisions.loc[len(decisions)] = [
        "h2_preregistration_ready",
        bool(decisions["passed"].all()),
        "all fixed one-minute availability gates passed",
    ]
    return decisions


def build_price_impact_availability_report(
    summary: pd.DataFrame,
    decisions: pd.DataFrame,
) -> str:
    row = summary.iloc[0]
    ready = bool(
        decisions.loc[
            decisions["quality_gate"].eq("h2_preregistration_ready"), "passed"
        ].iloc[0]
    )
    lines = [
        "# H2 가격충격 잔차 1분 데이터 가용성 감사",
        "",
        "## 목적",
        "",
        "미래수익률을 열람하기 전에 1분 aggressor flow, L2 상태, 실행가능 top-10 sweep feature가 같은 UTC 분에 결합되는지 검사했다.",
        "",
        "## 파일 감사",
        "",
        f"- 대상: {row['expected_symbol']} {row['expected_date']} UTC",
        f"- L2 분: {int(row['sample_count']):,}/1,440",
        f"- 체결이 존재한 분: {int(row['observed_flow_minutes']):,}",
        f"- L2와 결합된 체결 분: {int(row['matched_flow_minutes']):,} ({float(row['flow_to_l2_match_share']):.2%})",
        f"- 무체결 0-flow 분: {int(row['zero_trade_minutes']):,}",
        f"- 중복 분: {int(row['duplicate_panel_minutes'])}, 핵심 결측: {int(row['core_missing_values'])}",
        f"- point-in-time 위반: {int(row['timestamp_violations'])}",
        "",
        "## 시점 규칙",
        "",
        "- 분 t 시작 직후의 L2 상태와 분 t 동안 확정된 체결 흐름을 사용한다.",
        "- observed impact는 t와 t+1의 midpoint로 계산하며 의사결정은 t+1에만 가능하다.",
        "- 무체결 분은 aggressor flow를 0으로 두고 가격 상태는 L2 midpoint로 유지한다.",
        "- 10,000/50,000/100,000 USDT 명목금액의 top-10 sweep VWAP, impact, fill-rate를 보존한다.",
        "",
        "## 결론",
        "",
        (
            "모든 고정 gate를 통과했다. H2 protocol과 config를 결과 열람 전에 봉인할 수 있다."
            if ready
            else "한 개 이상의 gate가 실패했다. H2 사전등록과 본 분석을 진행하지 않는다."
        ),
    ]
    return "\n".join(lines) + "\n"
