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

from stats_utils import benjamini_hochberg


LIQUIDITY_COMPONENTS = [
    "common_spread",
    "common_depth_depletion",
    "common_abs_imbalance",
]
DIRECTIONS = ["liquidity_to_flow", "flow_to_liquidity"]


def validate_dynamic_config(config: Mapping) -> None:
    if int(config["data"]["interval_minutes"]) != 15:
        raise ValueError("Dynamic protocol requires 15-minute inputs")
    if list(config["data"]["horizons_minutes"]) != [15, 30, 60]:
        raise ValueError("Dynamic horizons have drifted")
    if config["analysis"]["primary_direction"] != "liquidity_to_flow":
        raise ValueError("Primary direction has drifted")
    if int(config["analysis"]["primary_horizon_minutes"]) != 15:
        raise ValueError("Primary horizon has drifted")
    if float(config["analysis"]["minimum_oos_mse_improvement"]) != 0.01:
        raise ValueError("Minimum effect has drifted")
    if int(config["analysis"]["bootstrap_repetitions"]) != 499:
        raise ValueError("Bootstrap repetitions have drifted")


def verify_dynamic_seal(
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
            raise ValueError(f"Dynamic preregistration seal mismatch: {key}")
    if seal.get("results_observed_at_seal") is not False:
        raise ValueError("Dynamic seal does not certify unobserved results")
    if set(seal.get("excluded_data_layers", [])) != {
        "news",
        "reddit",
        "twitter_x",
        "sentiment",
    }:
        raise ValueError("Dynamic excluded data layers drifted")
    return {**seal, **observed, "verified": True}


def load_common_panel(path: str | Path, config: Mapping) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = [
        "bucket_start",
        "sample_split",
        "common_flow",
        "liquidity_stress",
        *LIQUIDITY_COMPONENTS,
    ]
    raw = pd.read_parquet(path, columns=required)
    raw["bucket_start"] = pd.to_datetime(raw["bucket_start"], utc=True)
    for column in required[1:]:
        inconsistent = raw.groupby("bucket_start")[column].nunique(dropna=False).gt(1)
        if inconsistent.any():
            raise ValueError(f"Common panel is inconsistent within timestamp: {column}")
    frame = (
        raw.drop_duplicates("bucket_start")[required]
        .sort_values("bucket_start")
        .reset_index(drop=True)
    )
    start = _utc(config["sample_split"]["development_start"])
    development_end = _utc(config["sample_split"]["development_end_exclusive"])
    end = _utc(config["sample_split"]["oos_end_exclusive"])
    frame = frame.loc[
        frame["bucket_start"].ge(start) & frame["bucket_start"].lt(end)
    ].copy()
    expected_split = np.where(
        frame["bucket_start"].lt(development_end), "development", "oos"
    )
    if not np.array_equal(frame["sample_split"].to_numpy(), expected_split):
        raise ValueError("Input split differs from the sealed boundaries")
    if frame[required[2:]].isna().any().any():
        raise ValueError("Dynamic input contains missing common features")
    coverage = pd.DataFrame(
        [
            {
                "sample_split": split,
                "timestamps": len(group),
                "utc_days": group["bucket_start"].dt.floor("D").nunique(),
                "start": group["bucket_start"].min(),
                "end": group["bucket_start"].max(),
            }
            for split, group in frame.groupby("sample_split", sort=False)
        ]
    )
    return frame, coverage


def build_prediction_design(
    frame: pd.DataFrame,
    direction: str,
    horizon_minutes: int,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown dynamic direction: {direction}")
    source = frame.set_index("bucket_start").copy()
    horizon = pd.Timedelta(minutes=int(horizon_minutes))
    target_time = source.index + horizon
    if direction == "liquidity_to_flow":
        target_series = source["common_flow"].abs()
        target_name = "future_absolute_common_flow"
        baseline_features = ["current_absolute_common_flow", *_time_control_names()]
        augmented_features = [
            "current_absolute_common_flow",
            *LIQUIDITY_COMPONENTS,
            *_time_control_names(),
        ]
    else:
        target_series = source["liquidity_stress"]
        target_name = "future_liquidity_stress"
        baseline_features = ["current_liquidity_stress", *_time_control_names()]
        augmented_features = [
            "current_liquidity_stress",
            "current_absolute_common_flow",
            *_time_control_names(),
        ]
    target_lookup = target_series.to_dict()
    split_lookup = source["sample_split"].to_dict()
    design = pd.DataFrame(
        {
            "source_time": source.index,
            "target_time": target_time,
            "sample_split": source["sample_split"].to_numpy(),
            target_name: [target_lookup.get(value, np.nan) for value in target_time],
            "target_split": [split_lookup.get(value) for value in target_time],
            "current_absolute_common_flow": source["common_flow"].abs().to_numpy(),
            "current_liquidity_stress": source["liquidity_stress"].to_numpy(),
        }
    )
    for feature in LIQUIDITY_COMPONENTS:
        design[feature] = source[feature].to_numpy()
    controls = _time_controls(pd.DatetimeIndex(design["source_time"]))
    design = pd.concat([design.reset_index(drop=True), controls], axis=1)
    design = design.loc[
        design[target_name].notna()
        & design["target_split"].eq(design["sample_split"])
    ].copy()
    design.rename(columns={target_name: "target"}, inplace=True)
    return design, baseline_features, augmented_features


def run_dynamic_tests(
    frame: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    coefficient_rows = []
    draw_frames = []
    for direction_index, direction in enumerate(DIRECTIONS):
        for horizon_index, horizon in enumerate(config["data"]["horizons_minutes"]):
            design, baseline_features, augmented_features = build_prediction_design(
                frame, direction, int(horizon)
            )
            development = design.loc[design["sample_split"].eq("development")]
            oos = design.loc[design["sample_split"].eq("oos")].copy()
            baseline_coefficients = _fit_ols(
                development[baseline_features], development["target"]
            )
            augmented_coefficients = _fit_ols(
                development[augmented_features], development["target"]
            )
            oos["baseline_error_squared"] = (
                oos["target"] - _predict(oos[baseline_features], baseline_coefficients)
            ) ** 2
            oos["augmented_error_squared"] = (
                oos["target"] - _predict(oos[augmented_features], augmented_coefficients)
            ) ** 2
            effect = _mse_improvement(oos)
            midpoint = oos["source_time"].min() + (
                oos["source_time"].max() - oos["source_time"].min()
            ) / 2
            first_effect = _mse_improvement(oos.loc[oos["source_time"].le(midpoint)])
            second_effect = _mse_improvement(oos.loc[oos["source_time"].gt(midpoint)])
            seed = (
                int(config["analysis"]["random_seed"])
                + direction_index * 100
                + horizon_index
            )
            draws = _day_block_bootstrap(
                oos,
                repetitions=int(config["analysis"]["bootstrap_repetitions"]),
                seed=seed,
            )
            draws.insert(0, "horizon_minutes", int(horizon))
            draws.insert(0, "direction", direction)
            draw_frames.append(draws)
            p_value = float(
                (1 + draws["mse_improvement"].le(0).sum()) / (len(draws) + 1)
            )
            summary_rows.append(
                {
                    "hypothesis": f"{direction}_{int(horizon)}m",
                    "direction": direction,
                    "horizon_minutes": int(horizon),
                    "development_rows": len(development),
                    "oos_rows": len(oos),
                    "oos_days": oos["source_time"].dt.floor("D").nunique(),
                    "baseline_mse": float(oos["baseline_error_squared"].mean()),
                    "augmented_mse": float(oos["augmented_error_squared"].mean()),
                    "effect": effect,
                    "first_half_effect": first_effect,
                    "second_half_effect": second_effect,
                    "bootstrap_ci_lower": float(
                        draws["mse_improvement"].quantile(0.025)
                    ),
                    "bootstrap_ci_upper": float(
                        draws["mse_improvement"].quantile(0.975)
                    ),
                    "p_value_one_sided": p_value,
                }
            )
            coefficient_rows.extend(
                _coefficient_records(
                    direction,
                    int(horizon),
                    "baseline",
                    baseline_features,
                    baseline_coefficients,
                )
            )
            coefficient_rows.extend(
                _coefficient_records(
                    direction,
                    int(horizon),
                    "augmented",
                    augmented_features,
                    augmented_coefficients,
                )
            )
    summary = pd.DataFrame(summary_rows)
    summary["q_value_bh_fdr"] = benjamini_hochberg(
        summary["p_value_one_sided"]
    )
    return summary, pd.concat(draw_frames, ignore_index=True), pd.DataFrame(
        coefficient_rows
    )


def build_dynamic_decisions(
    summary: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    analysis = config["analysis"]
    primary = summary.loc[
        summary["direction"].eq(analysis["primary_direction"])
        & summary["horizon_minutes"].eq(int(analysis["primary_horizon_minutes"]))
    ].iloc[0]
    primary_passed = bool(
        primary["q_value_bh_fdr"] <= float(analysis["fdr_alpha"])
        and primary["effect"] >= float(analysis["minimum_oos_mse_improvement"])
        and primary["first_half_effect"] > 0
        and primary["second_half_effect"] > 0
    )
    positive_forward = int(
        (
            summary["direction"].eq("liquidity_to_flow")
            & summary["q_value_bh_fdr"].le(float(analysis["fdr_alpha"]))
            & summary["effect"].gt(0)
        ).sum()
    )
    positive_reverse = int(
        (
            summary["direction"].eq("flow_to_liquidity")
            & summary["q_value_bh_fdr"].le(float(analysis["fdr_alpha"]))
            & summary["effect"].gt(0)
        ).sum()
    )
    return pd.DataFrame(
        [
            {
                "decision": "primary_15m_liquidity_to_flow",
                "passed": primary_passed,
                "classification": "supported" if primary_passed else "not_supported",
                "evidence": (
                    f"effect={primary['effect']:.6f}, "
                    f"q={primary['q_value_bh_fdr']:.6f}, "
                    f"halves={primary['first_half_effect']:.6f}/"
                    f"{primary['second_half_effect']:.6f}"
                ),
            },
            {
                "decision": "positive_forward_horizons",
                "passed": positive_forward > 0,
                "classification": "descriptive_only",
                "evidence": f"{positive_forward}/3 q-significant positive effects",
            },
            {
                "decision": "positive_reverse_horizons",
                "passed": positive_reverse > 0,
                "classification": "descriptive_only",
                "evidence": f"{positive_reverse}/3 q-significant positive effects",
            },
            {
                "decision": "directional_return_alpha",
                "passed": False,
                "classification": "not_tested",
                "evidence": "future returns and PnL are outside the sealed protocol",
            },
        ]
    )


def build_dynamic_report(
    coverage: pd.DataFrame,
    summary: pd.DataFrame,
    decisions: pd.DataFrame,
) -> str:
    primary = summary.loc[
        summary["direction"].eq("liquidity_to_flow")
        & summary["horizon_minutes"].eq(15)
    ].iloc[0]
    lines = [
        "# L2 유동성과 공통 주문흐름의 동적 OOS 결과",
        "",
        "## 연구 질문",
        "",
        "현재 OKX 공통 L2 유동성 상태가 15분, 30분, 60분 뒤 시장 공통 공격적 주문흐름의 크기를 예측하는지 검정했다. 반대 방향도 같은 방식으로 비교했다.",
        "",
        "## 설계",
        "",
        f"- Development timestamp: {int(coverage.loc[coverage['sample_split'].eq('development'), 'timestamps'].iloc[0]):,}",
        f"- OOS timestamp: {int(coverage.loc[coverage['sample_split'].eq('oos'), 'timestamps'].iloc[0]):,}",
        "- 모든 회귀계수는 development에서 적합하고 OOS에 고정했다.",
        "- OOS UTC-day paired bootstrap 499회와 6개 검정 BH-FDR을 적용했다.",
        "",
        "## Primary",
        "",
        f"- 15분 L2→주문흐름 MSE 개선율: {primary['effect']:.3%}",
        f"- OOS 전반/후반: {primary['first_half_effect']:.3%} / {primary['second_half_effect']:.3%}",
        f"- Bootstrap 95% 구간: [{primary['bootstrap_ci_lower']:.3%}, {primary['bootstrap_ci_upper']:.3%}]",
        f"- 단측 p={primary['p_value_one_sided']:.4g}, BH-FDR q={primary['q_value_bh_fdr']:.4g}",
        f"- 사전 판정: **{decisions.iloc[0]['classification']}**",
        "",
        "## 전체 Horizon",
        "",
        "| 방향 | Horizon | OOS MSE 개선 | 95% 구간 | q-value |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in summary.sort_values(["direction", "horizon_minutes"]).itertuples():
        label = "L2→Flow" if row.direction == "liquidity_to_flow" else "Flow→L2"
        lines.append(
            f"| {label} | {row.horizon_minutes}분 | {row.effect:.3%} | "
            f"[{row.bootstrap_ci_lower:.3%}, {row.bootstrap_ci_upper:.3%}] | "
            f"{row.q_value_bh_fdr:.4g} |"
        )
    lines.extend(["", "## 결론", ""])
    if bool(decisions.iloc[0]["passed"]):
        lines.append(
            "현재 L2 상태는 15분 뒤 공통 주문흐름 크기에 사전 최소효과 이상의 안정적인 incremental predictive information을 제공했다."
        )
    else:
        lines.append(
            "현재 L2 상태는 15분 뒤 공통 주문흐름 크기에 사전 최소효과 이상의 안정적인 OOS 예측 개선을 제공하지 못했다."
        )
    lines.append(
        "이 결과는 주문흐름 크기 예측 연구이며 가격 방향, 미래수익률, intentional herding 또는 거래 가능한 alpha를 의미하지 않는다."
    )
    return "\n".join(lines) + "\n"


def save_effect_plot(summary: pd.DataFrame, path: str | Path) -> None:
    figure, axis = plt.subplots(figsize=(10, 5.5))
    colors = {
        "liquidity_to_flow": "#b94718",
        "flow_to_liquidity": "#08717a",
    }
    offsets = {"liquidity_to_flow": -1.5, "flow_to_liquidity": 1.5}
    for direction in DIRECTIONS:
        group = summary.loc[summary["direction"].eq(direction)].sort_values(
            "horizon_minutes"
        )
        x = group["horizon_minutes"].to_numpy() + offsets[direction]
        y = group["effect"].to_numpy()
        lower = y - group["bootstrap_ci_lower"].to_numpy()
        upper = group["bootstrap_ci_upper"].to_numpy() - y
        label = "L2 to flow" if direction == "liquidity_to_flow" else "Flow to L2"
        axis.errorbar(
            x,
            y,
            yerr=np.vstack([lower, upper]),
            marker="o",
            capsize=4,
            linewidth=1.5,
            color=colors[direction],
            label=label,
        )
    axis.axhline(0, color="#333333", linewidth=0.8)
    axis.axhline(0.01, color="#777777", linestyle="--", linewidth=0.9)
    axis.set_xticks([15, 30, 60])
    axis.set_xlabel("Horizon (minutes)")
    axis.set_ylabel("OOS MSE improvement")
    axis.set_title("Dynamic L2 liquidity and common order-flow prediction")
    axis.legend()
    figure.tight_layout()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=170)
    plt.close(figure)


def _fit_ols(features: pd.DataFrame, target: pd.Series) -> np.ndarray:
    design = np.column_stack([np.ones(len(features)), features.to_numpy(float)])
    return np.linalg.lstsq(design, target.to_numpy(float), rcond=None)[0]


def _predict(features: pd.DataFrame, coefficients: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(features)), features.to_numpy(float)])
    return design @ coefficients


def _mse_improvement(frame: pd.DataFrame) -> float:
    baseline = float(frame["baseline_error_squared"].mean())
    augmented = float(frame["augmented_error_squared"].mean())
    return 1.0 - augmented / baseline


def _day_block_bootstrap(
    oos: pd.DataFrame,
    repetitions: int,
    seed: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    day = oos["source_time"].dt.floor("D")
    unique_days = pd.DatetimeIndex(day.unique())
    positions = {value: np.flatnonzero(day.eq(value)) for value in unique_days}
    rows = []
    for draw in range(repetitions):
        sampled = rng.choice(unique_days, size=len(unique_days), replace=True)
        selected = np.concatenate([positions[pd.Timestamp(value)] for value in sampled])
        rows.append(
            {
                "draw": draw + 1,
                "mse_improvement": _mse_improvement(oos.iloc[selected]),
            }
        )
    return pd.DataFrame(rows)


def _time_controls(index: pd.DatetimeIndex) -> pd.DataFrame:
    minute = index.hour * 60 + index.minute
    angle = 2 * np.pi * minute / 1_440
    result = pd.DataFrame(
        {
            "utc_sin": np.sin(angle),
            "utc_cos": np.cos(angle),
        }
    )
    for weekday in range(1, 7):
        result[f"weekday_{weekday}"] = (index.dayofweek == weekday).astype(float)
    return result


def _time_control_names() -> list[str]:
    return ["utc_sin", "utc_cos", *[f"weekday_{value}" for value in range(1, 7)]]


def _coefficient_records(
    direction: str,
    horizon: int,
    model: str,
    features: list[str],
    coefficients: np.ndarray,
) -> list[dict]:
    names = ["intercept", *features]
    return [
        {
            "direction": direction,
            "horizon_minutes": horizon,
            "model": model,
            "term": name,
            "coefficient": value,
        }
        for name, value in zip(names, coefficients)
    ]


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )
