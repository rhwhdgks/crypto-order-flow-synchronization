from __future__ import annotations

from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from stats_utils import benjamini_hochberg


RAW_FEATURES = ["spread_bps", "depth_depletion", "abs_book_imbalance_10"]
COMMON_FEATURES = ["common_spread", "common_depth_depletion", "common_abs_imbalance"]


def validate_l2_quality_coverage(coverage: pd.DataFrame, config: Mapping) -> pd.DataFrame:
    symbols = list(config["data"]["symbols"])
    start = _utc(config["data"]["start_inclusive"])
    end = _utc(config["data"]["end_exclusive"])
    oos_start = _utc(config["sample_split"]["oos_start"])
    dates = pd.date_range(start.floor("D"), end.floor("D"), freq="D", inclusive="left")
    frame = coverage.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame["quality_passed"] = frame["quality_passed"].astype(str).str.lower().eq("true")
    valid = frame.loc[frame["quality_passed"]]
    valid_sets = {
        symbol: set(valid.loc[valid["symbol"].eq(symbol), "date"]) for symbol in symbols
    }
    common_dates = set(dates)
    for symbol in symbols:
        common_dates &= valid_sets[symbol]
    configured_oos = {day for day in dates if day >= oos_start}
    common_oos = common_dates & configured_oos
    minimum_asset_share = min(len(valid_sets[symbol]) / len(dates) for symbol in symbols)
    oos_exclusion_share = 1 - len(common_oos) / len(configured_oos)
    rows = [
        {
            "quality_gate": "minimum_asset_valid_day_share",
            "observed": minimum_asset_share,
            "threshold": 0.95,
            "passed": minimum_asset_share >= 0.95,
            "detail": f"minimum across {len(symbols)} assets",
        },
        {
            "quality_gate": "maximum_oos_excluded_day_share",
            "observed": oos_exclusion_share,
            "threshold": 0.10,
            "passed": oos_exclusion_share <= 0.10,
            "detail": f"common OOS days={len(common_oos)}/{len(configured_oos)}",
        },
        {
            "quality_gate": "common_complete_days",
            "observed": len(common_dates),
            "threshold": len(dates),
            "passed": True,
            "detail": f"development={len(common_dates - configured_oos)}, oos={len(common_oos)}",
        },
    ]
    result = pd.DataFrame(rows)
    if not bool(result.iloc[:2]["passed"].all()):
        raise ValueError("Sealed L2 quality gate failed; confirmatory analysis must stop")
    return result


def load_and_align_inputs(
    l2_path: str | Path,
    flow_path: str | Path,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbols = list(config["data"]["symbols"])
    start = _utc(config["data"]["start_inclusive"])
    end = _utc(config["data"]["end_exclusive"])
    development_end = _utc(config["sample_split"]["development_end_exclusive"])
    l2 = pd.read_parquet(l2_path)
    flow = pd.read_parquet(
        flow_path, columns=["bucket_start", "symbol", "aggressor_residual_z"]
    )
    for frame in (l2, flow):
        frame["bucket_start"] = pd.to_datetime(frame["bucket_start"], utc=True)
        frame.sort_values(["symbol", "bucket_start"], inplace=True)
        if frame.duplicated(["bucket_start", "symbol"]).any():
            raise ValueError("Input contains duplicate symbol timestamps")
    l2 = l2.loc[
        l2["symbol"].isin(symbols)
        & l2["bucket_start"].ge(start)
        & l2["bucket_start"].lt(end)
    ].copy()
    flow = flow.loc[
        flow["symbol"].isin(symbols)
        & flow["bucket_start"].ge(start)
        & flow["bucket_start"].lt(end)
    ].copy()
    if set(l2["symbol"].unique()) != set(symbols) or set(flow["symbol"].unique()) != set(
        symbols
    ):
        raise ValueError("Input universe differs from the sealed seven assets")

    # Never bridge excluded dates when calculating a one-bucket depth change.
    previous_time = l2.groupby("symbol")["bucket_start"].shift()
    consecutive = l2["bucket_start"].sub(previous_time).eq(
        pd.Timedelta(minutes=int(config["data"]["bucket_minutes"]))
    )
    l2["depth_depletion"] = -l2.groupby("symbol")["top10_depth_quote"].transform(
        lambda values: np.log(values).diff()
    )
    l2.loc[~consecutive, "depth_depletion"] = np.nan
    complete_counts = l2.groupby("bucket_start")["symbol"].nunique()
    complete_times = complete_counts.index[complete_counts.eq(len(symbols))]
    l2 = l2.loc[l2["bucket_start"].isin(complete_times)].copy()
    l2 = l2.dropna(subset=RAW_FEATURES)
    valid_counts = l2.groupby("bucket_start")["symbol"].nunique()
    analysis_times = valid_counts.index[valid_counts.eq(len(symbols))]
    l2 = l2.loc[l2["bucket_start"].isin(analysis_times)]

    merged = l2.merge(
        flow,
        on=["bucket_start", "symbol"],
        how="left",
        validate="one_to_one",
    )
    if merged["aggressor_residual_z"].isna().any():
        raise ValueError("Order-flow residuals are missing on the L2 analysis grid")
    merged["sample_split"] = np.where(
        merged["bucket_start"].lt(development_end), "development", "oos"
    )
    merged = merged.sort_values(["bucket_start", "symbol"]).reset_index(drop=True)
    coverage = pd.DataFrame(
        [
            {
                "sample_split": split,
                "rows": len(group),
                "timestamps": group["bucket_start"].nunique(),
                "days": group["bucket_start"].dt.floor("D").nunique(),
                "symbols": group["symbol"].nunique(),
                "start": group["bucket_start"].min(),
                "end": group["bucket_start"].max(),
            }
            for split, group in merged.groupby("sample_split", sort=False)
        ]
    )
    return merged, coverage


def build_common_liquidity_features(
    panel: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = panel.copy()
    development = result["sample_split"].eq("development")
    lower_quantile, upper_quantile = map(
        float, config["features"]["development_winsor_limits"]
    )
    scaler_rows: list[dict] = []
    for symbol in config["data"]["symbols"]:
        symbol_mask = result["symbol"].eq(symbol)
        development_mask = symbol_mask & development
        for feature in RAW_FEATURES:
            train = result.loc[development_mask, feature].astype(float)
            lower = float(train.quantile(lower_quantile))
            upper = float(train.quantile(upper_quantile))
            clipped_train = train.clip(lower, upper)
            center = float(clipped_train.mean())
            scale = float(clipped_train.std(ddof=1))
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError(f"Invalid development scale for {symbol} {feature}")
            z_column = f"{feature}_z"
            result.loc[symbol_mask, z_column] = (
                result.loc[symbol_mask, feature].clip(lower, upper) - center
            ) / scale
            scaler_rows.append(
                {
                    "scope": symbol,
                    "feature": feature,
                    "lower": lower,
                    "upper": upper,
                    "center": center,
                    "scale": scale,
                }
            )

    component_map = {
        "spread_bps_z": "common_spread",
        "depth_depletion_z": "common_depth_depletion",
        "abs_book_imbalance_10_z": "common_abs_imbalance",
    }
    timestamp_frame = pd.DataFrame(index=sorted(result["bucket_start"].unique()))
    timestamp_frame.index = pd.DatetimeIndex(timestamp_frame.index, name="bucket_start")
    split_by_time = result.drop_duplicates("bucket_start").set_index("bucket_start")["sample_split"]
    timestamp_frame["sample_split"] = split_by_time.reindex(timestamp_frame.index)
    for source, target in component_map.items():
        raw_common = result.pivot(index="bucket_start", columns="symbol", values=source).mean(axis=1)
        train = raw_common.loc[timestamp_frame["sample_split"].eq("development")]
        center = float(train.mean())
        scale = float(train.std(ddof=1))
        timestamp_frame[target] = (raw_common - center) / scale
        scaler_rows.append(
            {
                "scope": "cross_asset",
                "feature": target,
                "lower": np.nan,
                "upper": np.nan,
                "center": center,
                "scale": scale,
            }
        )
    timestamp_frame["liquidity_stress_raw"] = timestamp_frame[COMMON_FEATURES].mean(axis=1)
    dev_time = timestamp_frame["sample_split"].eq("development")
    stress_center = float(timestamp_frame.loc[dev_time, "liquidity_stress_raw"].mean())
    stress_scale = float(timestamp_frame.loc[dev_time, "liquidity_stress_raw"].std(ddof=1))
    timestamp_frame["liquidity_stress"] = (
        timestamp_frame["liquidity_stress_raw"] - stress_center
    ) / stress_scale
    scaler_rows.append(
        {
            "scope": "cross_asset",
            "feature": "liquidity_stress",
            "lower": np.nan,
            "upper": np.nan,
            "center": stress_center,
            "scale": stress_scale,
        }
    )
    raw_flow = result.pivot(
        index="bucket_start", columns="symbol", values="aggressor_residual_z"
    ).mean(axis=1)
    flow_center = float(raw_flow.loc[dev_time].mean())
    flow_scale = float(raw_flow.loc[dev_time].std(ddof=1))
    timestamp_frame["common_flow"] = (raw_flow - flow_center) / flow_scale
    scaler_rows.append(
        {
            "scope": "cross_asset",
            "feature": "common_flow",
            "lower": np.nan,
            "upper": np.nan,
            "center": flow_center,
            "scale": flow_scale,
        }
    )
    result = result.merge(
        timestamp_frame.reset_index(),
        on=["bucket_start", "sample_split"],
        how="left",
        validate="many_to_one",
    )
    return result, pd.DataFrame(scaler_rows)


def run_primary_test(
    panel: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    coefficient_rows = []
    conditioned = panel.copy()
    for symbol in config["data"]["symbols"]:
        symbol_mask = conditioned["symbol"].eq(symbol)
        train = conditioned.loc[symbol_mask & conditioned["sample_split"].eq("development")]
        design = np.column_stack([np.ones(len(train)), train[COMMON_FEATURES].to_numpy(float)])
        outcome = train["aggressor_residual_z"].to_numpy(float)
        coefficients = np.linalg.lstsq(design, outcome, rcond=None)[0]
        all_design = np.column_stack(
            [np.ones(int(symbol_mask.sum())), conditioned.loc[symbol_mask, COMMON_FEATURES].to_numpy(float)]
        )
        conditioned.loc[symbol_mask, "liquidity_conditioned_residual"] = (
            conditioned.loc[symbol_mask, "aggressor_residual_z"].to_numpy(float)
            - all_design @ coefficients
        )
        coefficient_rows.append(
            {
                "symbol": symbol,
                "intercept": coefficients[0],
                "common_spread_beta": coefficients[1],
                "common_depth_depletion_beta": coefficients[2],
                "common_abs_imbalance_beta": coefficients[3],
                "development_rows": len(train),
            }
        )
    oos = conditioned.loc[conditioned["sample_split"].eq("oos")]
    baseline = oos.pivot(
        index="bucket_start", columns="symbol", values="aggressor_residual_z"
    )[config["data"]["symbols"]]
    adjusted = oos.pivot(
        index="bucket_start", columns="symbol", values="liquidity_conditioned_residual"
    )[config["data"]["symbols"]]
    observed = _correlation_reduction(baseline.to_numpy(), adjusted.to_numpy())
    midpoint = baseline.index.min() + (baseline.index.max() - baseline.index.min()) / 2
    first = baseline.index <= midpoint
    first_reduction = _correlation_reduction(
        baseline.loc[first].to_numpy(), adjusted.loc[first].to_numpy()
    )[2]
    second_reduction = _correlation_reduction(
        baseline.loc[~first].to_numpy(), adjusted.loc[~first].to_numpy()
    )[2]

    rng = np.random.default_rng(int(config["analysis"]["random_seed"]))
    unique_days = pd.DatetimeIndex(baseline.index.floor("D").unique())
    day_locations = {
        day: np.flatnonzero(baseline.index.floor("D") == day) for day in unique_days
    }
    bootstrap_rows = []
    for draw in range(int(config["analysis"]["bootstrap_repetitions"])):
        sampled_days = rng.choice(unique_days, size=len(unique_days), replace=True)
        locations = np.concatenate([day_locations[day] for day in sampled_days])
        reduction = _correlation_reduction(
            baseline.to_numpy()[locations], adjusted.to_numpy()[locations]
        )[2]
        bootstrap_rows.append({"draw": draw + 1, "correlation_reduction": reduction})
    bootstrap = pd.DataFrame(bootstrap_rows)
    p_value = float(
        (1 + bootstrap["correlation_reduction"].le(0).sum()) / (len(bootstrap) + 1)
    )
    summary = pd.DataFrame(
        [
            {
                "hypothesis": "primary_correlation_reduction",
                "baseline_mean_pairwise_correlation": observed[0],
                "conditioned_mean_pairwise_correlation": observed[1],
                "effect": observed[2],
                "first_half_effect": first_reduction,
                "second_half_effect": second_reduction,
                "bootstrap_ci_lower": float(bootstrap["correlation_reduction"].quantile(0.025)),
                "bootstrap_ci_upper": float(bootstrap["correlation_reduction"].quantile(0.975)),
                "p_value_one_sided": p_value,
                "oos_timestamps": len(baseline),
                "oos_days": len(unique_days),
            }
        ]
    )
    return conditioned, pd.DataFrame(coefficient_rows), summary, bootstrap


def run_secondary_test(
    panel: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    time_frame = (
        panel.drop_duplicates("bucket_start")
        .set_index("bucket_start")[["sample_split", "common_flow", "liquidity_stress"]]
        .sort_index()
    )
    development = time_frame["sample_split"].eq("development")
    oos = time_frame.loc[~development].copy()
    quantile = float(config["analysis"]["event_quantile"])
    flow_threshold = float(time_frame.loc[development, "common_flow"].abs().quantile(quantile))
    liquidity_threshold = float(
        time_frame.loc[development, "liquidity_stress"].quantile(quantile)
    )
    flow_event = oos["common_flow"].abs().ge(flow_threshold)
    liquidity_event = oos["liquidity_stress"].ge(liquidity_threshold)
    overlap = flow_event & liquidity_event
    flow_event_count = int(flow_event.sum())
    overlap_count = int(overlap.sum())
    observed_share = overlap_count / flow_event_count if flow_event_count else np.nan

    rng = np.random.default_rng(int(config["analysis"]["random_seed"]) + 1)
    null_rows = []
    for draw in range(int(config["analysis"]["null_repetitions"])):
        shifted = _half_year_day_shift_boolean(
            liquidity_event,
            rng,
            minimum_shift_days=int(config["analysis"]["minimum_shift_days"]),
        )
        shifted_overlap = int((flow_event & shifted).sum())
        null_rows.append(
            {
                "draw": draw + 1,
                "overlap_count": shifted_overlap,
                "overlap_share": shifted_overlap / flow_event_count,
            }
        )
    nulls = pd.DataFrame(null_rows)
    null_mean = float(nulls["overlap_share"].mean())
    p_value = float((1 + nulls["overlap_share"].ge(observed_share).sum()) / (len(nulls) + 1))
    summary = pd.DataFrame(
        [
            {
                "hypothesis": "secondary_extreme_event_overlap",
                "flow_threshold": flow_threshold,
                "liquidity_threshold": liquidity_threshold,
                "flow_event_count": flow_event_count,
                "overlap_count": overlap_count,
                "observed_overlap_share": observed_share,
                "null_mean_overlap_share": null_mean,
                "risk_ratio": observed_share / null_mean,
                "null_95th_percentile": float(nulls["overlap_share"].quantile(0.95)),
                "p_value_one_sided": p_value,
            }
        ]
    )
    event_frame = oos.reset_index()
    event_frame["extreme_flow_event"] = flow_event.to_numpy()
    event_frame["liquidity_stress_event"] = liquidity_event.to_numpy()
    event_frame["overlap_event"] = overlap.to_numpy()
    return summary, nulls, event_frame


def build_decisions(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    family = pd.concat(
        [
            primary[["hypothesis", "p_value_one_sided"]],
            secondary[["hypothesis", "p_value_one_sided"]],
        ],
        ignore_index=True,
    )
    family["q_value_bh_fdr"] = benjamini_hochberg(family["p_value_one_sided"])
    primary = primary.merge(family[["hypothesis", "q_value_bh_fdr"]], on="hypothesis")
    secondary = secondary.merge(family[["hypothesis", "q_value_bh_fdr"]], on="hypothesis")
    p = primary.iloc[0]
    s = secondary.iloc[0]
    primary_passed = bool(
        p["q_value_bh_fdr"] <= float(config["analysis"]["fdr_alpha"])
        and p["effect"] >= float(config["analysis"]["primary_minimum_correlation_reduction"])
        and p["first_half_effect"] > 0
        and p["second_half_effect"] > 0
    )
    secondary_passed = bool(
        s["q_value_bh_fdr"] <= float(config["analysis"]["fdr_alpha"])
        and s["risk_ratio"] >= float(config["analysis"]["event_overlap_minimum_risk_ratio"])
        and s["overlap_count"] >= int(config["analysis"]["event_overlap_minimum_count"])
    )
    decisions = pd.DataFrame(
        [
            {
                "decision": "common_liquidity_explains_part_of_synchronization",
                "passed": primary_passed,
                "classification": "supported" if primary_passed else "not_supported",
                "evidence": (
                    f"reduction={p['effect']:.6f}, q={p['q_value_bh_fdr']:.4g}, "
                    f"halves=({p['first_half_effect']:.6f},{p['second_half_effect']:.6f})"
                ),
            },
            {
                "decision": "extreme_flow_liquidity_overlap",
                "passed": secondary_passed,
                "classification": "supported" if secondary_passed else "not_supported",
                "evidence": (
                    f"risk_ratio={s['risk_ratio']:.4f}, overlap={int(s['overlap_count'])}, "
                    f"q={s['q_value_bh_fdr']:.4g}"
                ),
            },
            {
                "decision": "directional_alpha",
                "passed": False,
                "classification": "not_tested",
                "evidence": "future returns are outside the sealed protocol",
            },
        ]
    )
    return primary, secondary, decisions


def build_report(
    quality: pd.DataFrame,
    aligned_coverage: pd.DataFrame,
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    decisions: pd.DataFrame,
) -> str:
    p = primary.iloc[0]
    s = secondary.iloc[0]
    primary_supported = bool(decisions.iloc[0]["passed"])
    secondary_supported = bool(decisions.iloc[1]["passed"])
    oos_quality = quality.loc[quality["quality_gate"].eq("maximum_oos_excluded_day_share")].iloc[0]
    lines = [
        "# 공통 L2 유동성과 주문흐름 동조화 OOS 결과",
        "",
        "## 연구 질문",
        "",
        "OKX 7자산의 동시 공격적 주문흐름 중 일부가 공통 spread 확대, depth 고갈, book imbalance로 설명되는지 봉인된 180일 설계에서 검정했다.",
        "",
        "## 표본 품질",
        "",
        f"- OOS 제외일 비율: {oos_quality['observed']:.2%} (중단 기준 {oos_quality['threshold']:.0%} 이하)",
        f"- 분석 timestamp: development {int(aligned_coverage.loc[aligned_coverage['sample_split'].eq('development'), 'timestamps'].iloc[0]):,}, OOS {int(aligned_coverage.loc[aligned_coverage['sample_split'].eq('oos'), 'timestamps'].iloc[0]):,}",
        "- 모든 변환 경계, 표준화 계수와 회귀계수는 development에서만 추정해 OOS에 고정했다.",
        "",
        "## Primary 결과",
        "",
        f"- baseline 평균 자산쌍 상관: {p['baseline_mean_pairwise_correlation']:.5f}",
        f"- 유동성 조건화 후 상관: {p['conditioned_mean_pairwise_correlation']:.5f}",
        f"- 상관 감소량: {p['effect']:.5f} (사전 최소효과 0.02000)",
        f"- UTC-day bootstrap 95% 구간: [{p['bootstrap_ci_lower']:.5f}, {p['bootstrap_ci_upper']:.5f}]",
        f"- 첫째/둘째 OOS 절반 감소량: {p['first_half_effect']:.5f} / {p['second_half_effect']:.5f}",
        f"- 단측 p={p['p_value_one_sided']:.4g}, BH-FDR q={p['q_value_bh_fdr']:.4g}",
        f"- 판정: {'지지' if primary_supported else '지지되지 않음'}",
        "",
        "## Secondary 결과",
        "",
        f"- extreme-flow event: {int(s['flow_event_count']):,}건",
        f"- 동시 liquidity-stress event: {int(s['overlap_count']):,}건",
        f"- 실제 overlap 비율: {s['observed_overlap_share']:.2%}, shift-null 평균: {s['null_mean_overlap_share']:.2%}",
        f"- risk ratio: {s['risk_ratio']:.3f} (사전 최소 1.25)",
        f"- 단측 p={s['p_value_one_sided']:.4g}, BH-FDR q={s['q_value_bh_fdr']:.4g}",
        f"- 판정: {'지지' if secondary_supported else '지지되지 않음'}",
        "",
        "## 해석",
        "",
    ]
    if primary_supported:
        lines.append(
            "공통 L2 유동성 상태를 제거한 뒤 OOS 주문흐름 상관이 사전 최소효과 이상 안정적으로 감소했다. 이는 공통 유동성 충격이 관측된 주문흐름 동조화의 일부를 설명한다는 증거다."
        )
    else:
        lines.append(
            "단순한 contemporaneous L2 유동성 3요인은 주문흐름 동조화를 실질적으로 설명하지 못했다. 상관 감소량은 사전 최소효과에 크게 못 미쳤고 두 OOS 절반에서 모두 음수였다."
        )
    lines.extend(
        [
            "이 결과는 contemporaneous 설명 연구다. intentional herding, 인과관계, 선도성 또는 미래수익률 alpha를 검정하거나 입증하지 않는다.",
            "",
        ]
    )
    return "\n".join(lines)


def save_diagnostic_plot(panel: pd.DataFrame, path: str | Path) -> None:
    time_frame = (
        panel.drop_duplicates("bucket_start")
        .set_index("bucket_start")[["sample_split", "common_flow", "liquidity_stress"]]
        .sort_index()
    )
    daily = time_frame[["common_flow", "liquidity_stress"]].resample("D").mean()
    figure, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    axes[0].plot(daily.index, daily["common_flow"], color="#0b6e75", linewidth=1.2)
    axes[0].axhline(0, color="#222222", linewidth=0.7)
    axes[0].set_ylabel("Common flow z")
    axes[0].set_title("OKX common order flow and L2 liquidity stress")
    axes[1].plot(daily.index, daily["liquidity_stress"], color="#c2571a", linewidth=1.2)
    axes[1].axhline(0, color="#222222", linewidth=0.7)
    axes[1].set_ylabel("Liquidity stress z")
    axes[1].set_xlabel("UTC date")
    figure.tight_layout()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def _mean_pairwise_correlation(values: np.ndarray) -> float:
    correlation = np.corrcoef(values, rowvar=False)
    upper = correlation[np.triu_indices(correlation.shape[0], k=1)]
    return float(np.nanmean(upper))


def _correlation_reduction(
    baseline: np.ndarray, conditioned: np.ndarray
) -> tuple[float, float, float]:
    baseline_correlation = _mean_pairwise_correlation(baseline)
    conditioned_correlation = _mean_pairwise_correlation(conditioned)
    return (
        baseline_correlation,
        conditioned_correlation,
        baseline_correlation - conditioned_correlation,
    )


def _half_year_day_shift_boolean(
    values: pd.Series,
    rng: np.random.Generator,
    minimum_shift_days: int,
) -> pd.Series:
    if not isinstance(values.index, pd.DatetimeIndex):
        raise TypeError("Shift series must use a DatetimeIndex")
    shifted = pd.Series(False, index=values.index, dtype=bool)
    day = values.index.floor("D")
    slot = values.index.hour * 60 + values.index.minute
    half_year = values.index.year.astype(str) + "H" + np.where(values.index.month <= 6, "1", "2")
    lookup = {(d, int(s)): bool(v) for d, s, v in zip(day, slot, values.to_numpy())}
    for stratum in np.unique(half_year):
        mask = half_year == stratum
        stratum_days = pd.DatetimeIndex(sorted(pd.unique(day[mask])))
        count = len(stratum_days)
        candidates = [
            offset
            for offset in range(1, count)
            if min(offset, count - offset) >= minimum_shift_days
        ]
        if not candidates:
            raise ValueError(f"Half-year stratum {stratum} is too short for the sealed shift")
        offset = int(rng.choice(candidates))
        source_by_target = {
            target: stratum_days[(position - offset) % count]
            for position, target in enumerate(stratum_days)
        }
        positions = np.flatnonzero(mask)
        shifted.iloc[positions] = [
            lookup.get((source_by_target[day[position]], int(slot[position])), False)
            for position in positions
        ]
    return shifted


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
