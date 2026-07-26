from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


CONTINUOUS_FEATURES = [
    "absolute_common_flow",
    "aligned_local_flow",
    "spread_bps",
    "log_top10_depth",
    "aligned_book_imbalance",
    "utc_hour_sin",
    "utc_hour_cos",
]


def validate_price_impact_config(config: Mapping) -> None:
    data = config["data"]
    split = config["sample_split"]
    events = config["events"]
    execution = config["execution"]
    analysis = config["analysis"]
    expected_symbols = [
        "BTC-USDT",
        "ETH-USDT",
        "XRP-USDT",
        "SOL-USDT",
        "DOGE-USDT",
        "ADA-USDT",
        "AVAX-USDT",
    ]
    if list(data["symbols"]) != expected_symbols:
        raise ValueError("Price-impact universe has drifted")
    if int(data["interval_minutes"]) != 1:
        raise ValueError("Price-impact frequency has drifted")
    start = pd.Timestamp(data["start_inclusive"])
    end = pd.Timestamp(data["end_exclusive"])
    if end - start != pd.Timedelta(days=180):
        raise ValueError("Price-impact sample must span 180 days")
    if pd.Timestamp(split["development_end_exclusive"]) - pd.Timestamp(
        split["development_start"]
    ) != pd.Timedelta(days=60):
        raise ValueError("Development window must span 60 days")
    if float(events["common_flow_absolute_percentile"]) != 0.95:
        raise ValueError("Event percentile has drifted")
    if events["primary_direction"] != "catch_up":
        raise ValueError("Primary direction has drifted")
    if list(execution["horizons_minutes"]) != [1, 5, 15, 30]:
        raise ValueError("Horizon family has drifted")
    if int(execution["primary_horizon_minutes"]) != 15:
        raise ValueError("Primary horizon has drifted")
    if int(execution["primary_round_trip_cost_bps"]) != 8:
        raise ValueError("Primary cost has drifted")
    if int(execution["primary_sweep_quote_amount"]) != 10_000:
        raise ValueError("Primary capacity has drifted")
    if int(analysis["bootstrap_repetitions"]) != 499:
        raise ValueError("Bootstrap repetitions have drifted")
    serialized = json.dumps(config, ensure_ascii=False).lower()
    for forbidden in ["news", "reddit", "twitter", "sentiment", "derivatives"]:
        if forbidden in serialized:
            raise ValueError(f"Forbidden data layer appears in config: {forbidden}")


def verify_price_impact_seal(
    protocol_path: str | Path,
    config_path: str | Path,
    seal_path: str | Path,
) -> dict:
    seal = json.loads(Path(seal_path).read_text(encoding="utf-8"))
    observed = {
        "protocol_sha256": _sha256(Path(protocol_path)),
        "config_sha256": _sha256(Path(config_path)),
    }
    for key, value in observed.items():
        if seal.get(key) != value:
            raise ValueError(f"Price-impact preregistration seal mismatch: {key}")
    if seal.get("results_observed_at_seal") is not False:
        raise ValueError("Seal does not certify unobserved results")
    if set(seal.get("excluded_data_layers", [])) != {
        "news",
        "reddit",
        "twitter_x",
        "sentiment",
        "derivatives",
    }:
        raise ValueError("Excluded data layers have drifted")
    return {**seal, **observed, "verified": True}


def prepare_price_impact_panel(
    l2_panel: pd.DataFrame,
    flow_panel: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    l2 = l2_panel.copy()
    flow = flow_panel.copy()
    for frame in [l2, flow]:
        frame["bucket_start"] = pd.to_datetime(frame["bucket_start"], utc=True)
    flow_columns = [
        "bucket_start",
        "symbol",
        "transaction_count",
        "total_quote_quantity",
        "buy_quote_quantity",
        "sell_quote_quantity",
        "aggressor_imbalance",
    ]
    panel = l2.merge(
        flow[flow_columns],
        on=["bucket_start", "symbol"],
        how="left",
        validate="one_to_one",
    )
    flow_zero = flow_columns[2:]
    panel[flow_zero] = panel[flow_zero].fillna(0.0)
    panel = panel.loc[~panel["empty"] & ~panel["crossed"]].copy()
    panel["active_assets"] = panel.groupby("bucket_start")["symbol"].transform("size")
    panel = panel.loc[
        panel["active_assets"].ge(int(config["data"]["minimum_active_assets"]))
    ].copy()
    panel["common_flow"] = panel.groupby("bucket_start")[
        "aggressor_imbalance"
    ].transform("mean")
    panel["common_direction"] = np.sign(panel["common_flow"])
    panel["absolute_common_flow"] = panel["common_flow"].abs()
    panel["aligned_local_flow"] = (
        panel["common_direction"] * panel["aggressor_imbalance"]
    )
    panel["aligned_book_imbalance"] = (
        panel["common_direction"] * panel["book_imbalance_10"]
    )
    panel["log_top10_depth"] = np.log(panel["top10_depth_quote"].clip(lower=1.0))
    hour = (
        panel["bucket_start"].dt.hour
        + panel["bucket_start"].dt.minute / 60.0
    )
    panel["utc_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    panel["utc_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    panel = panel.sort_values(["symbol", "bucket_start"]).reset_index(drop=True)
    next_time = panel.groupby("symbol")["bucket_start"].shift(-1)
    next_midpoint = panel.groupby("symbol")["midpoint"].shift(-1)
    consecutive = next_time.eq(panel["bucket_start"] + pd.Timedelta(minutes=1))
    panel["observed_midpoint_return"] = np.where(
        consecutive,
        np.log(next_midpoint / panel["midpoint"]),
        np.nan,
    )
    panel["observed_signed_impact"] = (
        panel["common_direction"] * panel["observed_midpoint_return"]
    )
    panel["decision_timestamp"] = panel["bucket_start"] + pd.Timedelta(minutes=1)
    panel = panel.sort_values(["bucket_start", "symbol"]).reset_index(drop=True)

    coverage = pd.DataFrame(
        [
            {
                "panel_rows": len(panel),
                "timestamps": panel["bucket_start"].nunique(),
                "symbols": panel["symbol"].nunique(),
                "start": panel["bucket_start"].min(),
                "end": panel["bucket_start"].max(),
                "target_missing_rows": int(
                    panel["observed_signed_impact"].isna().sum()
                ),
                "duplicate_symbol_minutes": int(
                    panel.duplicated(["bucket_start", "symbol"]).sum()
                ),
            }
        ]
    )
    return panel, coverage


def fit_development_impact_model(
    panel: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, float]:
    split = config["sample_split"]
    development = panel.loc[
        panel["bucket_start"].ge(pd.Timestamp(split["development_start"]))
        & panel["bucket_start"].lt(pd.Timestamp(split["development_end_exclusive"]))
        & panel["observed_signed_impact"].notna()
        & panel["common_direction"].ne(0)
    ].copy()
    if development.empty:
        raise ValueError("Development panel is empty")
    quantiles = config["model"]["winsor_quantiles"]
    scaler_rows = []
    transformed = development.copy()
    for column in CONTINUOUS_FEATURES:
        lower = float(development[column].quantile(float(quantiles[0])))
        upper = float(development[column].quantile(float(quantiles[1])))
        clipped = development[column].clip(lower, upper)
        mean = float(clipped.mean())
        std = float(clipped.std(ddof=0))
        if not np.isfinite(std) or std == 0:
            std = 1.0
        transformed[column] = (clipped - mean) / std
        scaler_rows.append(
            {
                "feature": column,
                "lower": lower,
                "upper": upper,
                "mean": mean,
                "std": std,
            }
        )
    symbols = list(config["data"]["symbols"])
    design, names = _design_matrix(transformed, symbols)
    target = transformed["observed_signed_impact"].to_numpy(dtype=float)
    finite = np.isfinite(design).all(axis=1) & np.isfinite(target)
    coefficients = np.linalg.lstsq(design[finite], target[finite], rcond=None)[0]
    coefficient_frame = pd.DataFrame(
        {"term": names, "coefficient": coefficients}
    )
    scaler = pd.DataFrame(scaler_rows)
    event_threshold = float(
        development.drop_duplicates("bucket_start")["absolute_common_flow"].quantile(
            float(config["events"]["common_flow_absolute_percentile"])
        )
    )
    diagnostics = pd.DataFrame(
        [
            {
                "development_rows": int(finite.sum()),
                "development_timestamps": development["bucket_start"].nunique(),
                "event_threshold": event_threshold,
                "rank": int(np.linalg.matrix_rank(design[finite])),
                "terms": len(names),
            }
        ]
    )
    return coefficient_frame, scaler, diagnostics, event_threshold


def apply_frozen_impact_model(
    panel: pd.DataFrame,
    coefficients: pd.DataFrame,
    scaler: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    frame = panel.copy()
    for row in scaler.itertuples(index=False):
        clipped = frame[row.feature].clip(float(row.lower), float(row.upper))
        frame[row.feature] = (clipped - float(row.mean)) / float(row.std)
    design, names = _design_matrix(frame, list(config["data"]["symbols"]))
    coefficient_map = coefficients.set_index("term")["coefficient"]
    beta = np.array([float(coefficient_map[name]) for name in names])
    frame["expected_signed_impact"] = design @ beta
    frame["impact_gap"] = (
        frame["observed_signed_impact"] - frame["expected_signed_impact"]
    )
    return frame


def select_oos_events(
    scored_panel: pd.DataFrame,
    event_threshold: float,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    split = config["sample_split"]
    oos = scored_panel.loc[
        scored_panel["bucket_start"].ge(pd.Timestamp(split["oos_start"]))
        & scored_panel["bucket_start"].lt(pd.Timestamp(split["oos_end_exclusive"]))
        & scored_panel["absolute_common_flow"].ge(float(event_threshold))
        & scored_panel["impact_gap"].notna()
        & scored_panel["common_direction"].ne(0)
    ].copy()
    per_side = int(config["events"]["portfolio_assets_per_side"])
    event_rows = []
    position_rows = []
    for timestamp, group in oos.groupby("bucket_start", sort=True):
        group = group.sort_values(["impact_gap", "symbol"]).reset_index(drop=True)
        if len(group) < 2 * per_side:
            continue
        under = group.head(per_side)
        over = group.tail(per_side)
        direction = int(np.sign(group["common_flow"].iloc[0]))
        event_id = f"{pd.Timestamp(timestamp).isoformat()}_{direction:+d}"
        event_rows.append(
            {
                "event_id": event_id,
                "bucket_start": timestamp,
                "decision_timestamp": timestamp + pd.Timedelta(minutes=1),
                "common_flow": float(group["common_flow"].iloc[0]),
                "common_direction": direction,
                "active_assets": len(group),
            }
        )
        for role, subset, multiplier in [
            ("underreactor", under, 1),
            ("overreactor", over, -1),
        ]:
            for row in subset.itertuples(index=False):
                position_rows.append(
                    {
                        "event_id": event_id,
                        "bucket_start": timestamp,
                        "decision_timestamp": timestamp + pd.Timedelta(minutes=1),
                        "symbol": row.symbol,
                        "role": role,
                        "impact_gap": float(row.impact_gap),
                        "position_sign": int(direction * multiplier),
                    }
                )
    events = pd.DataFrame(event_rows)
    positions = pd.DataFrame(position_rows)
    if events.empty:
        return events, positions
    keep_ids = _non_overlap_ids(
        events,
        int(config["events"]["primary_non_overlap_minutes"]),
    )
    events["primary_non_overlap"] = events["event_id"].isin(keep_ids)
    positions["primary_non_overlap"] = positions["event_id"].isin(keep_ids)
    return events, positions


def evaluate_event_portfolios(
    base_panel: pd.DataFrame,
    events: pd.DataFrame,
    positions: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty:
        return pd.DataFrame(), pd.DataFrame()
    lookup = base_panel.set_index(["bucket_start", "symbol"]).sort_index()
    ledger_rows = []
    event_rows = []
    for event in events.itertuples(index=False):
        selected = positions.loc[positions["event_id"].eq(event.event_id)]
        for horizon in config["execution"]["horizons_minutes"]:
            entry_time = pd.Timestamp(event.decision_timestamp)
            exit_time = entry_time + pd.Timedelta(minutes=int(horizon))
            leg_rows = []
            for position in selected.itertuples(index=False):
                entry = _lookup_row(lookup, entry_time, position.symbol)
                exit_row = _lookup_row(lookup, exit_time, position.symbol)
                midpoint_return = np.nan
                if entry is not None and exit_row is not None:
                    raw_return = float(exit_row["midpoint"] / entry["midpoint"] - 1.0)
                    midpoint_return = int(position.position_sign) * raw_return
                leg = {
                    "event_id": event.event_id,
                    "bucket_start": event.bucket_start,
                    "decision_timestamp": entry_time,
                    "exit_timestamp": exit_time,
                    "horizon_minutes": int(horizon),
                    "symbol": position.symbol,
                    "role": position.role,
                    "position_sign": int(position.position_sign),
                    "midpoint_gross_return": midpoint_return,
                    "primary_non_overlap": bool(event.primary_non_overlap),
                }
                for amount in config["execution"]["sweep_quote_amounts"]:
                    suffix = f"quote_{int(amount)}"
                    sweep_return, full_fill = _execution_return(
                        entry, exit_row, int(position.position_sign), suffix
                    )
                    leg[f"sweep_gross_return_{int(amount)}"] = sweep_return
                    leg[f"sweep_full_fill_{int(amount)}"] = full_fill
                leg_rows.append(leg)
                ledger_rows.append(leg)
            leg_frame = pd.DataFrame(leg_rows)
            row = {
                "event_id": event.event_id,
                "bucket_start": event.bucket_start,
                "horizon_minutes": int(horizon),
                "primary_non_overlap": bool(event.primary_non_overlap),
                "midpoint_gross_return": float(
                    leg_frame["midpoint_gross_return"].mean()
                ),
            }
            for amount in config["execution"]["sweep_quote_amounts"]:
                full_column = f"sweep_full_fill_{int(amount)}"
                return_column = f"sweep_gross_return_{int(amount)}"
                all_full = bool(leg_frame[full_column].all())
                row[full_column] = all_full
                row[return_column] = (
                    float(leg_frame[return_column].mean()) if all_full else np.nan
                )
            event_rows.append(row)
    return pd.DataFrame(event_rows), pd.DataFrame(ledger_rows)


def summarize_price_impact_results(
    event_returns: pd.DataFrame,
    position_ledger: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    primary_rows = []
    costs = config["execution"]["round_trip_cost_bps"]
    for horizon in config["execution"]["horizons_minutes"]:
        horizon_frame = event_returns.loc[
            event_returns["horizon_minutes"].eq(int(horizon))
            & event_returns["primary_non_overlap"]
        ].copy()
        for cost in costs:
            net = horizon_frame["midpoint_gross_return"] - float(cost) / 10_000
            rows.append(
                _summary_row(
                    net,
                    horizon,
                    "midpoint",
                    cost,
                    0,
                )
            )
        for amount in config["execution"]["sweep_quote_amounts"]:
            gross = horizon_frame[f"sweep_gross_return_{int(amount)}"].dropna()
            for cost in costs:
                rows.append(
                    _summary_row(
                        gross - float(cost) / 10_000,
                        horizon,
                        "top10_sweep",
                        cost,
                        amount,
                    )
                )
        primary_net = (
            horizon_frame[
                f"sweep_gross_return_{int(config['execution']['primary_sweep_quote_amount'])}"
            ].dropna()
            - float(config["execution"]["primary_round_trip_cost_bps"]) / 10_000
        )
        p_value, draws = _day_block_bootstrap(
            horizon_frame.loc[primary_net.index, "bucket_start"],
            primary_net,
            repetitions=int(config["analysis"]["bootstrap_repetitions"]),
            seed=int(config["analysis"]["random_seed"]) + int(horizon),
        )
        primary_rows.append(
            {
                "horizon_minutes": int(horizon),
                "event_count": int(primary_net.notna().sum()),
                "mean_net_return": float(primary_net.mean()),
                "median_net_return": float(primary_net.median()),
                "bootstrap_p_value": p_value,
            }
        )
        for draw_id, value in enumerate(draws):
            rows.append(
                {
                    "horizon_minutes": int(horizon),
                    "result_type": "bootstrap_draw",
                    "cost_bps": int(
                        config["execution"]["primary_round_trip_cost_bps"]
                    ),
                    "capacity_quote": int(
                        config["execution"]["primary_sweep_quote_amount"]
                    ),
                    "event_count": len(primary_net),
                    "mean_net_return": value,
                    "median_net_return": np.nan,
                    "win_rate": np.nan,
                    "t_stat": np.nan,
                }
            )
    summary = pd.DataFrame(rows)
    inferential = pd.DataFrame(primary_rows)
    inferential["q_value"] = _bh_adjust(inferential["bootstrap_p_value"])

    primary_horizon = int(config["execution"]["primary_horizon_minutes"])
    cost = float(config["execution"]["primary_round_trip_cost_bps"]) / 10_000
    amount = int(config["execution"]["primary_sweep_quote_amount"])
    ledger = position_ledger.loc[
        position_ledger["horizon_minutes"].eq(primary_horizon)
        & position_ledger["primary_non_overlap"]
        & position_ledger[f"sweep_full_fill_{amount}"]
    ].copy()
    ledger["net_contribution"] = (
        ledger[f"sweep_gross_return_{amount}"] - cost
    )
    asset_summary = (
        ledger.groupby("symbol", as_index=False)
        .agg(
            position_count=("net_contribution", "size"),
            mean_net_contribution=("net_contribution", "mean"),
        )
        .sort_values("symbol")
    )
    return summary, inferential, asset_summary


def build_price_impact_decisions(
    event_returns: pd.DataFrame,
    inferential: pd.DataFrame,
    asset_summary: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    execution = config["execution"]
    analysis = config["analysis"]
    horizon = int(execution["primary_horizon_minutes"])
    amount = int(execution["primary_sweep_quote_amount"])
    cost = float(execution["primary_round_trip_cost_bps"]) / 10_000
    frame = event_returns.loc[
        event_returns["horizon_minutes"].eq(horizon)
        & event_returns["primary_non_overlap"]
    ].dropna(subset=[f"sweep_gross_return_{amount}"]).copy()
    frame["net_return"] = frame[f"sweep_gross_return_{amount}"] - cost
    midpoint = frame["bucket_start"].sort_values().iloc[len(frame) // 2] if not frame.empty else pd.NaT
    half_means = (
        [
            float(frame.loc[frame["bucket_start"].lt(midpoint), "net_return"].mean()),
            float(frame.loc[frame["bucket_start"].ge(midpoint), "net_return"].mean()),
        ]
        if not frame.empty
        else [np.nan, np.nan]
    )
    primary_inference = inferential.loc[
        inferential["horizon_minutes"].eq(horizon)
    ].iloc[0]
    checks = [
        (
            "minimum_non_overlap_events",
            len(frame) >= int(analysis["minimum_non_overlap_oos_events"]),
            f"events={len(frame)}",
        ),
        (
            "positive_mean_and_median_after_8bps",
            float(frame["net_return"].mean()) > 0
            and float(frame["net_return"].median()) > 0,
            f"mean={frame['net_return'].mean():.8f};median={frame['net_return'].median():.8f}",
        ),
        (
            "positive_oos_halves",
            all(value > 0 for value in half_means),
            f"half_means={half_means}",
        ),
        (
            "minimum_assets_positive",
            int((asset_summary["mean_net_contribution"] > 0).sum())
            >= int(analysis["minimum_assets_positive"]),
            f"positive_assets={int((asset_summary['mean_net_contribution'] > 0).sum())}",
        ),
        (
            "primary_top10_capacity_positive",
            float(frame["net_return"].mean()) > 0,
            f"capacity_quote={amount};mean={frame['net_return'].mean():.8f}",
        ),
        (
            "bootstrap_fdr",
            float(primary_inference["q_value"]) <= float(analysis["fdr_alpha"]),
            f"q={float(primary_inference['q_value']):.6f}",
        ),
    ]
    decisions = pd.DataFrame(checks, columns=["gate", "passed", "evidence"])
    decisions.loc[len(decisions)] = [
        "primary_supported",
        bool(decisions["passed"].all()),
        "all sealed H2 gates passed",
    ]
    return decisions


def build_price_impact_report(
    coverage: pd.DataFrame,
    diagnostics: pd.DataFrame,
    inferential: pd.DataFrame,
    decisions: pd.DataFrame,
) -> str:
    ready = bool(decisions.iloc[-1]["passed"])
    lines = [
        "# H2 가격충격 잔차 OOS 연구",
        "",
        "## 설계",
        "",
        "- OKX 현물 7개 자산, 1분 L2 및 aggressor flow",
        "- Development 60일에서 모델·95백분위 threshold 고정, OOS 120일 평가",
        "- Primary: catch-up, 15분, 8bps, 자산당 10,000 USDT top-10 sweep",
        "- 뉴스·Reddit·Twitter/X·sentiment·파생상품 미사용",
        "",
        "## 데이터",
        "",
        f"- 분석 행: {int(coverage.iloc[0]['panel_rows']):,}",
        f"- timestamp: {int(coverage.iloc[0]['timestamps']):,}",
        f"- Development 적합 행: {int(diagnostics.iloc[0]['development_rows']):,}",
        f"- 고정 event threshold: {float(diagnostics.iloc[0]['event_threshold']):.6f}",
        "",
        "## OOS 결과",
        "",
    ]
    for row in inferential.itertuples(index=False):
        lines.append(
            f"- {int(row.horizon_minutes)}분: 사건 {int(row.event_count)}건, "
            f"평균 {float(row.mean_net_return):.4%}, 중앙값 {float(row.median_net_return):.4%}, "
            f"p={float(row.bootstrap_p_value):.4f}, q={float(row.q_value):.4f}"
        )
    lines.extend(["", "## 판정", ""])
    for row in decisions.itertuples(index=False):
        lines.append(
            f"- {row.gate}: {'통과' if bool(row.passed) else '실패'} ({row.evidence})"
        )
    lines.extend(
        [
            "",
            (
                "모든 사전 gate를 통과했다. 독립 표본 재현 전까지 연구 후보로만 유지한다."
                if ready
                else "Primary 가설은 지지되지 않았다. 동일 OOS에서 조건을 다시 고르지 않는다."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def save_price_impact_plot(inferential: pd.DataFrame, path: str | Path) -> None:
    frame = inferential.sort_values("horizon_minutes")
    fig, ax = plt.subplots(figsize=(9, 5))
    colors = ["#2F6B5F" if value > 0 else "#B4473D" for value in frame["mean_net_return"]]
    ax.bar(frame["horizon_minutes"].astype(str), frame["mean_net_return"] * 10_000, color=colors)
    ax.axhline(0, color="black", linewidth=1)
    ax.set_xlabel("보유 기간 (분)")
    ax.set_ylabel("8bps·10k sweep 후 평균 수익 (bps)")
    ax.set_title("가격충격 잔차 catch-up OOS")
    ax.grid(axis="y", alpha=0.2)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def _design_matrix(frame: pd.DataFrame, symbols: list[str]) -> tuple[np.ndarray, list[str]]:
    columns = [np.ones(len(frame))]
    names = ["intercept"]
    for feature in CONTINUOUS_FEATURES:
        columns.append(frame[feature].to_numpy(dtype=float))
        names.append(feature)
    for symbol in symbols[1:]:
        columns.append(frame["symbol"].eq(symbol).to_numpy(dtype=float))
        names.append(f"asset_{symbol}")
    return np.column_stack(columns), names


def _non_overlap_ids(events: pd.DataFrame, gap_minutes: int) -> set[str]:
    kept = set()
    last = None
    for row in events.sort_values("bucket_start").itertuples(index=False):
        timestamp = pd.Timestamp(row.bucket_start)
        if last is None or timestamp >= last + pd.Timedelta(minutes=gap_minutes):
            kept.add(row.event_id)
            last = timestamp
    return kept


def _lookup_row(
    lookup: pd.DataFrame,
    timestamp: pd.Timestamp,
    symbol: str,
) -> pd.Series | None:
    try:
        row = lookup.loc[(timestamp, symbol)]
    except KeyError:
        return None
    if isinstance(row, pd.DataFrame):
        raise ValueError(f"Duplicate execution key: {timestamp} {symbol}")
    return row


def _execution_return(
    entry: pd.Series | None,
    exit_row: pd.Series | None,
    position_sign: int,
    suffix: str,
) -> tuple[float, bool]:
    if entry is None or exit_row is None:
        return np.nan, False
    if position_sign > 0:
        full = (
            float(entry[f"buy_fill_ratio_{suffix}"]) >= 1.0
            and float(exit_row[f"sell_fill_ratio_{suffix}"]) >= 1.0
        )
        value = (
            float(exit_row[f"sell_vwap_{suffix}"])
            / float(entry[f"buy_vwap_{suffix}"])
            - 1.0
        )
    else:
        full = (
            float(entry[f"sell_fill_ratio_{suffix}"]) >= 1.0
            and float(exit_row[f"buy_fill_ratio_{suffix}"]) >= 1.0
        )
        value = (
            float(entry[f"sell_vwap_{suffix}"])
            / float(exit_row[f"buy_vwap_{suffix}"])
            - 1.0
        )
    return (value if full else np.nan), full


def _summary_row(
    values: pd.Series,
    horizon: int,
    result_type: str,
    cost_bps: int,
    capacity_quote: int,
) -> dict:
    clean = values.dropna().astype(float)
    std = float(clean.std(ddof=1)) if len(clean) > 1 else np.nan
    t_stat = (
        float(clean.mean() / (std / np.sqrt(len(clean))))
        if len(clean) > 1 and std > 0
        else np.nan
    )
    return {
        "horizon_minutes": int(horizon),
        "result_type": result_type,
        "cost_bps": int(cost_bps),
        "capacity_quote": int(capacity_quote),
        "event_count": len(clean),
        "mean_net_return": float(clean.mean()),
        "median_net_return": float(clean.median()),
        "win_rate": float((clean > 0).mean()),
        "t_stat": t_stat,
    }


def _day_block_bootstrap(
    timestamps: pd.Series,
    values: pd.Series,
    repetitions: int,
    seed: int,
) -> tuple[float, list[float]]:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(timestamps, utc=True).dt.normalize().to_numpy(),
            "value": values.to_numpy(dtype=float),
        }
    ).dropna()
    if frame.empty:
        return np.nan, []
    by_day = {day: group["value"].to_numpy() for day, group in frame.groupby("date")}
    days = np.array(list(by_day))
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(repetitions):
        sampled_days = rng.choice(days, size=len(days), replace=True)
        sampled = np.concatenate([by_day[day] for day in sampled_days])
        draws.append(float(sampled.mean()))
    p_value = (1 + sum(value <= 0 for value in draws)) / (repetitions + 1)
    return float(p_value), draws


def _bh_adjust(p_values: pd.Series) -> pd.Series:
    values = p_values.to_numpy(dtype=float)
    order = np.argsort(values)
    adjusted = np.empty(len(values))
    running = 1.0
    for reverse_rank in range(len(values) - 1, -1, -1):
        index = order[reverse_rank]
        rank = reverse_rank + 1
        running = min(running, values[index] * len(values) / rank)
        adjusted[index] = running
    return pd.Series(adjusted, index=p_values.index)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
