from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from stats_utils import benjamini_hochberg


REQUIRED_COLUMNS = [
    "bucket_start",
    "symbol",
    "sample_split",
    "aggressor_residual_z",
]


def verify_preregistration_seal(
    protocol_path: str | Path,
    config_path: str | Path,
    seal_path: str | Path,
) -> dict:
    seal = json.loads(Path(seal_path).read_text(encoding="utf-8"))
    observed = {
        "protocol_sha256": hashlib.sha256(Path(protocol_path).read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
    }
    for key, value in observed.items():
        if seal.get(key) != value:
            raise ValueError(f"Preregistration seal mismatch: {key}")
    if seal.get("results_observed_at_seal") is not False:
        raise ValueError("Seal does not certify unobserved results")
    if set(seal.get("excluded_data_layers", [])) != {"news", "reddit", "sentiment"}:
        raise ValueError("Excluded data layers drifted")
    return {**seal, **observed, "verified": True}


def validate_frozen_config(config: Mapping) -> None:
    expected_symbols = [
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
    ]
    data = config["data"]
    analysis = config["analysis"]
    if list(data["symbols"]) != expected_symbols:
        raise ValueError("Cross-venue universe has drifted")
    if int(data["expected_rows_per_venue"]) != 490_560:
        raise ValueError("Each frozen residual panel must contain 490,560 rows")
    if int(data["expected_interval_minutes"]) != 15:
        raise ValueError("Protocol v1 only permits 15-minute buckets")
    if int(analysis["null_repetitions"]) != 499:
        raise ValueError("Null repetitions have drifted")
    if int(analysis["bootstrap_repetitions"]) != 499:
        raise ValueError("Bootstrap repetitions have drifted")
    serialized = json.dumps(config, ensure_ascii=False).lower()
    for forbidden in ["news", "reddit", "sentiment"]:
        if forbidden in serialized:
            raise ValueError(f"Forbidden data layer appears in config: {forbidden}")


def load_residual_panels(
    binance_path: str | Path,
    okx_path: str | Path,
    config: Mapping,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    symbols = list(config["data"]["symbols"])
    expected_rows = int(config["data"]["expected_rows_per_venue"])
    start = _utc(config["data"]["expected_start"])
    end = _utc(config["data"]["expected_end_exclusive"])
    expected_index = pd.date_range(start, end, freq="15min", inclusive="left")
    split = config["sample_split"]
    development_end = _utc(split["development_end_exclusive"])

    panels: dict[str, pd.DataFrame] = {}
    coverage_rows = []
    for venue, path in [("binance", binance_path), ("okx", okx_path)]:
        frame = pd.read_parquet(path, columns=REQUIRED_COLUMNS)
        frame["bucket_start"] = pd.to_datetime(frame["bucket_start"], utc=True)
        if venue == "okx":
            frame["symbol"] = frame["symbol"].str.replace("-", "", regex=False)
        if len(frame) != expected_rows:
            raise ValueError(f"{venue} rows={len(frame):,}; expected={expected_rows:,}")
        if set(frame["symbol"].unique()) != set(symbols):
            raise ValueError(f"{venue} symbols do not match frozen universe")
        if frame.duplicated(["bucket_start", "symbol"]).any():
            raise ValueError(f"{venue} contains duplicate symbol timestamps")
        if frame["aggressor_residual_z"].isna().any():
            raise ValueError(f"{venue} residuals contain missing values")
        panel = frame.pivot(
            index="bucket_start", columns="symbol", values="aggressor_residual_z"
        ).reindex(index=expected_index, columns=symbols)
        if panel.isna().any().any():
            raise ValueError(f"{venue} does not have a complete timestamp intersection")
        expected_split = np.where(panel.index < development_end, "development", "oos")
        observed_split = (
            frame.drop_duplicates("bucket_start")
            .set_index("bucket_start")["sample_split"]
            .reindex(expected_index)
            .to_numpy()
        )
        if not np.array_equal(observed_split, expected_split):
            raise ValueError(f"{venue} sample split differs from frozen boundaries")
        panels[venue] = panel.astype(float)
        coverage_rows.append(
            {
                "venue": venue,
                "rows": len(frame),
                "timestamps": len(panel),
                "symbols": len(symbols),
                "start": panel.index.min(),
                "end": panel.index.max(),
                "complete_intersection": True,
            }
        )
    if not panels["binance"].index.equals(panels["okx"].index):
        raise ValueError("Venue timestamp grids do not match")
    return panels, pd.DataFrame(coverage_rows)


def build_common_flows(
    panels: Mapping[str, pd.DataFrame], config: Mapping
) -> pd.DataFrame:
    split = config["sample_split"]
    development_end = _utc(split["development_end_exclusive"])
    result = pd.DataFrame(index=panels["binance"].index)
    result["sample_split"] = np.where(
        result.index < development_end, "development", "oos"
    )
    for venue in ["binance", "okx"]:
        raw = panels[venue].mean(axis=1)
        development = raw.loc[result["sample_split"].eq("development")]
        center = float(development.mean())
        scale = float(development.std(ddof=1))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError(f"Invalid development common-flow scale: {venue}")
        result[f"{venue}_common_flow"] = (raw - center) / scale
    return result


def synchronization_test(
    panels: Mapping[str, pd.DataFrame],
    common_flows: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    analysis = config["analysis"]
    oos = common_flows["sample_split"].eq("oos")
    development = ~oos
    binance = panels["binance"].loc[oos].to_numpy()
    okx = panels["okx"].loc[oos].to_numpy()
    oos_index = panels["binance"].index[oos]
    b_flow = common_flows.loc[oos, "binance_common_flow"].to_numpy()
    o_flow = common_flows.loc[oos, "okx_common_flow"].to_numpy()
    b_threshold = float(
        common_flows.loc[development, "binance_common_flow"].abs().quantile(
            float(analysis["extreme_event_quantile"])
        )
    )
    o_threshold = float(
        common_flows.loc[development, "okx_common_flow"].abs().quantile(
            float(analysis["extreme_event_quantile"])
        )
    )
    observed_corr = _mean_diagonal_correlation(binance, okx)
    observed_concordance, observed_events = _extreme_concordance(
        b_flow, o_flow, b_threshold, o_threshold
    )

    rng = np.random.default_rng(int(analysis["null_seed"]))
    null_rows = []
    for draw in range(int(analysis["null_repetitions"])):
        shifted = _half_year_day_shift(
            okx,
            oos_index,
            rng,
            minimum_shift_days=int(analysis["minimum_shift_days"]),
        )
        shifted_flow = shifted.mean(axis=1)
        # Reproduce the development-standardized common flow's OOS scale exactly.
        original_mean = okx.mean(axis=1)
        slope = np.polyfit(original_mean, o_flow, 1)
        shifted_flow = slope[0] * shifted_flow + slope[1]
        concordance, event_count = _extreme_concordance(
            b_flow, shifted_flow, b_threshold, o_threshold
        )
        null_rows.append(
            {
                "draw": draw + 1,
                "mean_same_asset_correlation": _mean_diagonal_correlation(binance, shifted),
                "extreme_direction_concordance": concordance,
                "event_count": event_count,
            }
        )
    nulls = pd.DataFrame(null_rows)
    rows = []
    for metric, observed, event_count in [
        ("mean_same_asset_correlation", observed_corr, np.nan),
        ("extreme_direction_concordance", observed_concordance, observed_events),
    ]:
        values = nulls[metric].to_numpy(dtype=float)
        rows.append(
            {
                "metric": metric,
                "observed": observed,
                "null_mean": float(np.nanmean(values)),
                "null_95th_percentile": float(np.nanquantile(values, 0.95)),
                "p_value_one_sided": float((1 + np.sum(values >= observed)) / (len(values) + 1)),
                "event_count": event_count,
                "effect_excess": observed - float(np.nanmean(values)),
                "effect_ratio": observed / float(np.nanmean(values)),
            }
        )
    summary = pd.DataFrame(rows)
    summary["q_value_bh_fdr"] = benjamini_hochberg(summary["p_value_one_sided"])
    return summary, nulls


def lead_lag_test(
    common_flows: pd.DataFrame, config: Mapping
) -> tuple[pd.DataFrame, pd.DataFrame]:
    analysis = config["analysis"]
    oos = common_flows.loc[common_flows["sample_split"].eq("oos")].copy()
    rows = []
    for horizon in analysis["lead_horizons_minutes"]:
        steps = int(horizon) // 15
        for direction, source, target in [
            ("binance_to_okx", "binance_common_flow", "okx_common_flow"),
            ("okx_to_binance", "okx_common_flow", "binance_common_flow"),
        ]:
            design = _lead_lag_design(oos, source, target, steps)
            fit = _fit_lead_lag(design, int(analysis["hac_maxlags"]))
            midpoint = design["source_time"].min() + (
                design["source_time"].max() - design["source_time"].min()
            ) / 2
            first = _fit_lead_lag(
                design.loc[design["source_time"].le(midpoint)],
                int(analysis["hac_maxlags"]),
            )
            second = _fit_lead_lag(
                design.loc[design["source_time"].gt(midpoint)],
                int(analysis["hac_maxlags"]),
            )
            rows.append(
                {
                    "direction": direction,
                    "horizon_minutes": int(horizon),
                    "standardized_beta": fit["beta"],
                    "t_stat": fit["t_stat"],
                    "p_value": fit["p_value"],
                    "observations": fit["observations"],
                    "first_half_beta": first["beta"],
                    "second_half_beta": second["beta"],
                }
            )
    summary = pd.DataFrame(rows)
    summary["q_value_bh_fdr"] = benjamini_hochberg(summary["p_value"])

    primary_steps = int(analysis["transmission_primary_horizon_minutes"]) // 15
    bo = _lead_lag_design(oos, "binance_common_flow", "okx_common_flow", primary_steps)
    ob = _lead_lag_design(oos, "okx_common_flow", "binance_common_flow", primary_steps)
    common_days = sorted(set(bo["day"]).intersection(ob["day"]))
    rng = np.random.default_rng(int(analysis["bootstrap_seed"]))
    bootstrap_rows = []
    for draw in range(int(analysis["bootstrap_repetitions"])):
        sampled = rng.choice(common_days, size=len(common_days), replace=True)
        bo_sample = pd.concat([bo.loc[bo["day"].eq(day)] for day in sampled], ignore_index=True)
        ob_sample = pd.concat([ob.loc[ob["day"].eq(day)] for day in sampled], ignore_index=True)
        beta_bo = _fit_lead_lag(bo_sample, 0)["beta"]
        beta_ob = _fit_lead_lag(ob_sample, 0)["beta"]
        bootstrap_rows.append(
            {
                "draw": draw + 1,
                "beta_binance_to_okx": beta_bo,
                "beta_okx_to_binance": beta_ob,
                "beta_difference_bo_minus_ob": beta_bo - beta_ob,
            }
        )
    return summary, pd.DataFrame(bootstrap_rows)


def build_decisions(
    synchronization: pd.DataFrame,
    lead_lag: pd.DataFrame,
    bootstrap: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    analysis = config["analysis"]
    alpha = float(analysis["fdr_alpha"])
    corr = synchronization.loc[
        synchronization["metric"].eq("mean_same_asset_correlation")
    ].iloc[0]
    concordance = synchronization.loc[
        synchronization["metric"].eq("extreme_direction_concordance")
    ].iloc[0]
    sync_passed = bool(
        corr["q_value_bh_fdr"] <= alpha
        and corr["observed"] > corr["null_95th_percentile"]
        and corr["effect_excess"] >= float(analysis["synchronization_minimum_correlation_excess"])
        and concordance["q_value_bh_fdr"] <= alpha
        and concordance["observed"] > concordance["null_95th_percentile"]
        and concordance["effect_ratio"] >= float(analysis["synchronization_minimum_concordance_ratio"])
        and concordance["event_count"] >= int(analysis["synchronization_minimum_event_count"])
    )

    primary = lead_lag.loc[
        lead_lag["horizon_minutes"].eq(int(analysis["transmission_primary_horizon_minutes"]))
    ].set_index("direction")
    difference = bootstrap["beta_difference_bo_minus_ob"]
    lower = float(difference.quantile(0.025))
    upper = float(difference.quantile(0.975))
    minimum_beta = float(analysis["transmission_minimum_standardized_beta"])

    def stable(direction: str) -> bool:
        row = primary.loc[direction]
        return bool(
            row["standardized_beta"] >= minimum_beta
            and row["q_value_bh_fdr"] <= alpha
            and row["first_half_beta"] > 0
            and row["second_half_beta"] > 0
        )

    bo_passed = stable("binance_to_okx") and lower > 0
    ob_passed = stable("okx_to_binance") and upper < 0
    transmission_passed = bool(bo_passed ^ ob_passed)
    direction = (
        "binance_to_okx"
        if bo_passed and not ob_passed
        else "okx_to_binance"
        if ob_passed and not bo_passed
        else "not_supported"
    )
    return pd.DataFrame(
        [
            {
                "decision": "cross_venue_synchronization",
                "passed": sync_passed,
                "classification": (
                    "cross_venue_synchronization_supported"
                    if sync_passed
                    else "cross_venue_synchronization_not_supported"
                ),
            },
            {
                "decision": "stable_directional_transmission",
                "passed": transmission_passed,
                "classification": direction,
            },
            {
                "decision": "intentional_herding_identification",
                "passed": False,
                "classification": "not_identified_without_participant_ids",
            },
            {
                "decision": "directional_alpha",
                "passed": False,
                "classification": "not_tested",
            },
        ]
    )


def build_report(
    coverage: pd.DataFrame,
    synchronization: pd.DataFrame,
    lead_lag: pd.DataFrame,
    bootstrap: pd.DataFrame,
    decisions: pd.DataFrame,
) -> str:
    corr = synchronization.loc[
        synchronization["metric"].eq("mean_same_asset_correlation")
    ].iloc[0]
    concordance = synchronization.loc[
        synchronization["metric"].eq("extreme_direction_concordance")
    ].iloc[0]
    primary = lead_lag.loc[lead_lag["horizon_minutes"].eq(15)].set_index("direction")
    interval = bootstrap["beta_difference_bo_minus_ob"].quantile([0.025, 0.975])
    decision_map = decisions.set_index("decision")["classification"].to_dict()
    return f"""# Binance-OKX 교차거래소 주문흐름 연구 v1

## 데이터

- 기간: 2024-04-08 포함 ~ 2026-04-08 미포함
- 빈도·자산: 15분, 7자산
- 거래소별 입력 행: {int(coverage['rows'].min()):,}
- Development/OOS: 1년/1년 고정 분할

## 동시 동조화

- 동일 자산 교차거래소 평균 correlation: `{corr['observed']:.5f}`
- 시간 null 평균: `{corr['null_mean']:.5f}`, BH q: `{corr['q_value_bh_fdr']:.4g}`
- 극단 공통흐름 방향 일치율: `{concordance['observed']:.2%}`
- 시간 null 평균: `{concordance['null_mean']:.2%}`, event: `{int(concordance['event_count']):,}`
- 판정: `{decision_map['cross_venue_synchronization']}`

## 방향성 전파

- 15분 Binance→OKX beta: `{primary.loc['binance_to_okx', 'standardized_beta']:.5f}`
- 15분 OKX→Binance beta: `{primary.loc['okx_to_binance', 'standardized_beta']:.5f}`
- beta 차이 95% bootstrap interval: `[{interval.loc[0.025]:.5f}, {interval.loc[0.975]:.5f}]`
- 판정: `{decision_map['stable_directional_transmission']}`

## 해석 제한

이 연구는 두 거래소의 주문흐름이 같은 시간에 함께 움직이는지와 안정적인 선행 방향이
있는지를 검정한다. 참여자 ID, 미래수익률, 뉴스·커뮤니티·sentiment를 사용하지 않았다.
따라서 intentional herding 또는 거래 가능한 alpha를 식별하지 않는다.
"""


def save_plot(common_flows: pd.DataFrame, output_path: str | Path) -> None:
    oos = common_flows.loc[common_flows["sample_split"].eq("oos")]
    fig, ax = plt.subplots(figsize=(8, 6))
    sample = oos.iloc[::8]
    ax.hexbin(
        sample["binance_common_flow"],
        sample["okx_common_flow"],
        gridsize=55,
        mincnt=1,
        cmap="YlGnBu",
    )
    ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax.axvline(0, color="black", linewidth=0.8, alpha=0.5)
    ax.set_xlabel("Binance common aggressor flow")
    ax.set_ylabel("OKX common aggressor flow")
    ax.set_title("Cross-venue common order flow, OOS")
    fig.tight_layout()
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _mean_diagonal_correlation(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.mean([np.corrcoef(left[:, i], right[:, i])[0, 1] for i in range(left.shape[1])]))


def _extreme_concordance(
    left: np.ndarray,
    right: np.ndarray,
    left_threshold: float,
    right_threshold: float,
) -> tuple[float, int]:
    event = (np.abs(left) > left_threshold) | (np.abs(right) > right_threshold)
    count = int(event.sum())
    if count == 0:
        return float("nan"), 0
    return float(np.mean(np.sign(left[event]) == np.sign(right[event]))), count


def _half_year_day_shift(
    values: np.ndarray,
    index: pd.DatetimeIndex,
    rng: np.random.Generator,
    minimum_shift_days: int,
) -> np.ndarray:
    result = np.empty_like(values)
    groups = index.year * 2 + (index.month > 6).astype(int)
    rows_per_day = 96
    for group in np.unique(groups):
        positions = np.flatnonzero(groups == group)
        if len(positions) % rows_per_day:
            raise ValueError("Half-year block does not contain complete UTC days")
        days = len(positions) // rows_per_day
        candidates = np.arange(minimum_shift_days, days - minimum_shift_days + 1)
        if len(candidates) == 0:
            raise ValueError("Half-year block is too short for frozen minimum shift")
        shift = int(rng.choice(candidates)) * rows_per_day
        result[positions] = np.roll(values[positions], shift=shift, axis=0)
    return result


def _lead_lag_design(
    frame: pd.DataFrame, source: str, target: str, steps: int
) -> pd.DataFrame:
    index = frame.index
    design = pd.DataFrame(
        {
            "source_time": index[:-steps],
            "target_time": index[steps:],
            "source": frame[source].to_numpy()[:-steps],
            "target_current": frame[target].to_numpy()[:-steps],
            "target_future": frame[target].to_numpy()[steps:],
        }
    )
    # Do not let overnight bootstrap boundaries create artificial lead observations.
    design = design.loc[
        design["source_time"].dt.normalize().eq(design["target_time"].dt.normalize())
    ].copy()
    design["day"] = design["source_time"].dt.strftime("%Y-%m-%d")
    return design.reset_index(drop=True)


def _fit_lead_lag(design: pd.DataFrame, hac_maxlags: int) -> dict:
    x = sm.add_constant(design[["source", "target_current"]], has_constant="add")
    model = sm.OLS(design["target_future"], x)
    fit = model.fit(cov_type="HAC", cov_kwds={"maxlags": hac_maxlags}) if hac_maxlags else model.fit()
    return {
        "beta": float(fit.params["source"]),
        "t_stat": float(fit.tvalues["source"]),
        "p_value": float(fit.pvalues["source"]),
        "observations": int(fit.nobs),
    }


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
