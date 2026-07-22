from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import binomtest

from stats_utils import benjamini_hochberg


SPOT_REQUIRED_COLUMNS = [
    "bucket_start",
    "symbol",
    "interval_minutes",
    "schema_version",
    "bucket_return",
    "total_quote_quantity",
    "transaction_count",
    "aggressor_imbalance",
]
FUTURES_COLUMNS = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]


def verify_preregistration_seal(
    protocol_path: str | Path,
    config_path: str | Path,
    seal_path: str | Path,
) -> dict:
    protocol = Path(protocol_path)
    config = Path(config_path)
    seal = json.loads(Path(seal_path).read_text(encoding="utf-8"))
    observed = {
        "protocol_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
    }
    for key, value in observed.items():
        if seal.get(key) != value:
            raise ValueError(f"Preregistration seal mismatch: {key}")
    if seal.get("results_observed_at_seal") is not False:
        raise ValueError("Preregistration seal does not certify unobserved results")
    if set(seal.get("excluded_data_layers", [])) != {"news", "reddit", "sentiment"}:
        raise ValueError("Excluded data layers drifted from the sealed protocol")
    return {**seal, **observed, "verified": True}


def validate_frozen_config(config: Mapping) -> None:
    data = config["data"]
    analysis = config["analysis"]
    expected_symbols = [
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
    ]
    if list(data["symbols"]) != expected_symbols:
        raise ValueError("Order-flow synchronization universe has drifted")
    if list(data["major_symbols"]) != ["BTCUSDT", "ETHUSDT"]:
        raise ValueError("Major-symbol definition has drifted")
    if int(data["expected_rows"]) != 490_560:
        raise ValueError("Frozen spot input must contain 490,560 rows")
    if int(data["expected_interval_minutes"]) != 15:
        raise ValueError("Protocol v1 only permits 15-minute buckets")
    if int(analysis["null_repetitions"]) != 499:
        raise ValueError("Null repetitions have drifted")
    if int(analysis["bootstrap_repetitions"]) != 499:
        raise ValueError("Bootstrap repetitions have drifted")
    if int(analysis["alignment_minimum_assets"]) != 6:
        raise ValueError("Alignment threshold has drifted")
    serialized = json.dumps(config, ensure_ascii=False).lower()
    for forbidden in ["news", "reddit", "sentiment"]:
        if forbidden in serialized:
            raise ValueError(f"Forbidden data layer appears in config: {forbidden}")


def load_spot_frame(path: str | Path, config: Mapping) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.read_parquet(path, columns=SPOT_REQUIRED_COLUMNS)
    frame["bucket_start"] = pd.to_datetime(frame["bucket_start"], utc=True)
    data = config["data"]
    expected_symbols = list(data["symbols"])
    expected_start = _utc(data["expected_start"])
    expected_end = _utc(data["expected_end_exclusive"])
    interval = int(data["expected_interval_minutes"])

    if len(frame) != int(data["expected_rows"]):
        raise ValueError(f"Spot rows={len(frame):,} do not match frozen expected rows")
    if set(frame["symbol"].unique()) != set(expected_symbols):
        raise ValueError("Spot symbols do not match frozen universe")
    if frame.duplicated(["bucket_start", "symbol"]).any():
        raise ValueError("Spot frame contains duplicate symbol timestamps")
    if set(pd.to_numeric(frame["interval_minutes"], errors="coerce").dropna().astype(int)) != {interval}:
        raise ValueError("Spot frame interval does not equal 15 minutes")
    if set(pd.to_numeric(frame["schema_version"], errors="coerce").dropna().astype(int)) != {2}:
        raise ValueError("Spot frame must use schema v2")
    if frame["bucket_start"].min() != expected_start:
        raise ValueError("Spot frame start does not match frozen start")
    if frame["bucket_start"].max() != expected_end - pd.Timedelta(minutes=interval):
        raise ValueError("Spot frame end does not match frozen end")
    if frame[SPOT_REQUIRED_COLUMNS[4:]].isna().any().any():
        raise ValueError("Spot primary columns contain missing values")

    expected_index = pd.date_range(
        expected_start,
        expected_end,
        freq=f"{interval}min",
        inclusive="left",
    )
    counts = frame.groupby("symbol")["bucket_start"].nunique().reindex(expected_symbols)
    for symbol in expected_symbols:
        actual = pd.DatetimeIndex(
            frame.loc[frame["symbol"].eq(symbol), "bucket_start"].sort_values()
        )
        if not actual.equals(expected_index):
            raise ValueError(f"Incomplete spot timestamp grid: {symbol}")

    coverage = pd.DataFrame(
        [
            {
                "rows": len(frame),
                "timestamps": len(expected_index),
                "symbols": len(expected_symbols),
                "start": frame["bucket_start"].min(),
                "end": frame["bucket_start"].max(),
                "aggressor_available_share": frame["aggressor_imbalance"].notna().mean(),
                "minimum_rows_per_symbol": int(counts.min()),
                "maximum_rows_per_symbol": int(counts.max()),
                "complete_intersection": True,
            }
        ]
    )
    return frame.sort_values(["bucket_start", "symbol"]).reset_index(drop=True), coverage


def residualize_aggressor_flow(
    frame: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    symbols = list(config["data"]["symbols"])
    split = config["sample_split"]
    development_start = _utc(split["development_start"])
    development_end = _utc(split["development_end_exclusive"])
    oos_start = _utc(split["oos_start"])
    oos_end = _utc(split["oos_end_exclusive"])

    work = frame.copy()
    return_panel = work.pivot(index="bucket_start", columns="symbol", values="bucket_return").reindex(columns=symbols)
    loo_panel = (return_panel.sum(axis=1).to_numpy()[:, None] - return_panel.to_numpy()) / (len(symbols) - 1)
    loo = pd.DataFrame(loo_panel, index=return_panel.index, columns=symbols).stack().rename("loo_market_return")
    work = work.set_index(["bucket_start", "symbol"]).sort_index()
    work["loo_market_return"] = loo.reindex(work.index)
    work = work.reset_index()
    work["own_return"] = work["bucket_return"].astype(float)
    work["abs_own_return"] = work["own_return"].abs()
    work["abs_loo_market_return"] = work["loo_market_return"].abs()
    work["log_quote_volume"] = np.log1p(work["total_quote_quantity"].clip(lower=0.0))
    work["log_transaction_count"] = np.log1p(work["transaction_count"].clip(lower=0.0))
    hour = work["bucket_start"].dt.hour + work["bucket_start"].dt.minute / 60.0
    work["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    work["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    work["weekday"] = work["bucket_start"].dt.weekday.astype(int)
    work["sample_split"] = np.select(
        [
            work["bucket_start"].ge(development_start) & work["bucket_start"].lt(development_end),
            work["bucket_start"].ge(oos_start) & work["bucket_start"].lt(oos_end),
        ],
        ["development", "oos"],
        default="excluded",
    )
    if (work["sample_split"] == "excluded").any():
        raise ValueError("Frozen sample split does not cover the full spot input")

    coefficient_rows: list[dict] = []
    diagnostic_rows: list[dict] = []
    result_parts = []
    for symbol in symbols:
        subset = work.loc[work["symbol"].eq(symbol)].copy()
        design, names = _spot_design_matrix(subset)
        development = subset["sample_split"].eq("development").to_numpy()
        y = subset["aggressor_imbalance"].to_numpy(dtype=float)
        beta = np.linalg.lstsq(design[development], y[development], rcond=None)[0]
        fitted = design @ beta
        residual = y - fitted
        center = float(residual[development].mean())
        scale = float(residual[development].std(ddof=1))
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError(f"Invalid development residual scale: {symbol}")
        subset["aggressor_fitted"] = fitted
        subset["aggressor_residual"] = residual
        subset["aggressor_residual_z"] = (residual - center) / scale
        y_dev = y[development]
        residual_dev = residual[development]
        r_squared = 1.0 - np.sum(residual_dev**2) / np.sum((y_dev - y_dev.mean()) ** 2)
        diagnostic_rows.append(
            {
                "symbol": symbol,
                "development_rows": int(development.sum()),
                "oos_rows": int((subset["sample_split"] == "oos").sum()),
                "development_r_squared": float(r_squared),
                "development_residual_center": center,
                "development_residual_scale": scale,
                "oos_residual_z_mean": float(subset.loc[subset["sample_split"].eq("oos"), "aggressor_residual_z"].mean()),
                "oos_residual_z_std": float(subset.loc[subset["sample_split"].eq("oos"), "aggressor_residual_z"].std(ddof=1)),
            }
        )
        coefficient_rows.extend(
            {"symbol": symbol, "term": name, "coefficient": float(value)}
            for name, value in zip(names, beta, strict=True)
        )
        result_parts.append(subset)

    result = pd.concat(result_parts, ignore_index=True).sort_values(["bucket_start", "symbol"])
    return result.reset_index(drop=True), pd.DataFrame(coefficient_rows), pd.DataFrame(diagnostic_rows)


def analyze_synchronization(
    residual_frame: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    symbols = list(config["data"]["symbols"])
    analysis = config["analysis"]
    oos = residual_frame.loc[residual_frame["sample_split"].eq("oos")]
    matrix = oos.pivot(index="bucket_start", columns="symbol", values="aggressor_residual_z").reindex(columns=symbols)
    if matrix.isna().any().any():
        raise ValueError("OOS residual synchronization matrix is incomplete")
    observed = _synchronization_statistics(
        matrix.to_numpy(dtype=float), int(analysis["alignment_minimum_assets"])
    )

    rng = np.random.default_rng(int(analysis["null_seed"]))
    repetitions = int(analysis["null_repetitions"])
    null_rows = []
    for draw in range(repetitions):
        shifted = _halfyear_day_circular_shift(
            matrix,
            rng,
            int(analysis["minimum_shift_days"]),
        )
        stats = _synchronization_statistics(
            shifted,
            int(analysis["alignment_minimum_assets"]),
        )
        null_rows.append({"draw": draw + 1, **stats})
    null = pd.DataFrame(null_rows)

    metric_rows = []
    for metric in ["mean_pairwise_correlation", "extreme_alignment_rate"]:
        null_values = null[metric].to_numpy(dtype=float)
        observed_value = float(observed[metric])
        metric_rows.append(
            {
                "metric": metric,
                "observed": observed_value,
                "null_mean": float(null_values.mean()),
                "null_std": float(null_values.std(ddof=1)),
                "null_p95": float(np.quantile(null_values, 0.95)),
                "null_p99": float(np.quantile(null_values, 0.99)),
                "empirical_p_value": float((1 + np.sum(null_values >= observed_value)) / (repetitions + 1)),
                "observed_minus_null_mean": observed_value - float(null_values.mean()),
                "observed_to_null_mean_ratio": observed_value / float(null_values.mean()),
            }
        )
    summary = pd.DataFrame(metric_rows)
    summary["q_value_bh_fdr"] = benjamini_hochberg(summary["empirical_p_value"])
    summary["above_null_p95"] = summary["observed"] > summary["null_p95"]
    summary["effect_size_pass"] = np.where(
        summary["metric"].eq("mean_pairwise_correlation"),
        summary["observed_minus_null_mean"] >= float(analysis["synchronization_minimum_correlation_excess"]),
        summary["observed_to_null_mean_ratio"] >= float(analysis["synchronization_minimum_alignment_ratio"]),
    )
    summary["gate_pass"] = (
        summary["q_value_bh_fdr"].le(float(analysis["fdr_alpha"]))
        & summary["above_null_p95"]
        & summary["effect_size_pass"]
    )

    correlation = matrix.corr()
    pair_rows = []
    for left_index, left in enumerate(symbols):
        for right in symbols[left_index + 1 :]:
            pair_rows.append({"left_symbol": left, "right_symbol": right, "correlation": float(correlation.loc[left, right])})
    return summary, null, pd.DataFrame(pair_rows), matrix


def build_flow_composites(residual_frame: pd.DataFrame, config: Mapping) -> pd.DataFrame:
    symbols = list(config["data"]["symbols"])
    matrix = residual_frame.pivot(index="bucket_start", columns="symbol", values="aggressor_residual_z").reindex(columns=symbols)
    split = residual_frame.drop_duplicates("bucket_start").set_index("bucket_start")["sample_split"].reindex(matrix.index)
    composites = pd.DataFrame(index=matrix.index)
    composites["sample_split"] = split
    composites["major_flow_raw"] = matrix[list(config["data"]["major_symbols"])].mean(axis=1)
    composites["alt_flow_raw"] = matrix[list(config["data"]["alt_symbols"])].mean(axis=1)
    futures_symbols = config["data"].get("futures_symbols")
    if futures_symbols:
        composites["spot_futures5_flow_raw"] = matrix[list(futures_symbols)].mean(axis=1)
    development = composites["sample_split"].eq("development")
    scale_pairs = [
        ("major_flow_raw", "major_flow"),
        ("alt_flow_raw", "alt_flow"),
    ]
    if futures_symbols:
        scale_pairs.append(("spot_futures5_flow_raw", "spot_futures5_flow"))
    for source, target in scale_pairs:
        center = composites.loc[development, source].mean()
        scale = composites.loc[development, source].std(ddof=1)
        composites[target] = (composites[source] - center) / scale
    return composites.reset_index()


def analyze_lead_lag(
    composites: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    analysis = config["analysis"]
    interval = int(config["data"]["expected_interval_minutes"])
    oos = composites.loc[composites["sample_split"].eq("oos")].copy().set_index("bucket_start")
    rows = []
    bootstrap_rows = []
    rng = np.random.default_rng(int(analysis["bootstrap_seed"]))
    for horizon in analysis["lead_horizons_minutes"]:
        steps = int(horizon) // interval
        if steps * interval != int(horizon):
            raise ValueError("Lead horizon must be divisible by the spot interval")
        for direction in ["major_to_alt", "alt_to_major"]:
            target = "major_flow" if direction == "major_to_alt" else "alt_flow"
            current_dependent = "alt_flow" if direction == "major_to_alt" else "major_flow"
            future_dependent = current_dependent
            model_frame = pd.DataFrame(
                {
                    "y": oos[future_dependent].shift(-steps),
                    "target": oos[target],
                    "current_dependent": oos[current_dependent],
                },
                index=oos.index,
            ).dropna()
            model = sm.OLS(
                model_frame["y"],
                sm.add_constant(model_frame[["target", "current_dependent"]], has_constant="add"),
            ).fit(cov_type="HAC", cov_kwds={"maxlags": int(analysis["hac_maxlags"])})
            split_point = model_frame.index.min() + (model_frame.index.max() - model_frame.index.min()) / 2
            first_beta = _simple_target_beta(model_frame.loc[model_frame.index <= split_point])
            second_beta = _simple_target_beta(model_frame.loc[model_frame.index > split_point])
            rows.append(
                {
                    "direction": direction,
                    "horizon_minutes": int(horizon),
                    "observations": int(model.nobs),
                    "standardized_beta": float(model.params["target"]),
                    "standard_error_hac": float(model.bse["target"]),
                    "t_stat_hac": float(model.tvalues["target"]),
                    "p_value_hac": float(model.pvalues["target"]),
                    "ci_lower_95": float(model.conf_int().loc["target", 0]),
                    "ci_upper_95": float(model.conf_int().loc["target", 1]),
                    "first_half_beta": first_beta,
                    "second_half_beta": second_beta,
                }
            )

        bootstrap = _bootstrap_direction_difference(
            oos,
            steps,
            int(analysis["bootstrap_repetitions"]),
            rng,
        )
        bootstrap_rows.append(
            {
                "horizon_minutes": int(horizon),
                "repetitions": len(bootstrap),
                "mean_beta_difference": float(np.mean(bootstrap)),
                "ci_lower_95": float(np.quantile(bootstrap, 0.025)),
                "ci_upper_95": float(np.quantile(bootstrap, 0.975)),
                "one_sided_p_value": float((1 + np.sum(bootstrap <= 0.0)) / (len(bootstrap) + 1)),
            }
        )

    summary = pd.DataFrame(rows)
    summary["q_value_bh_fdr"] = benjamini_hochberg(summary["p_value_hac"])
    return summary, pd.DataFrame(bootstrap_rows)


def load_futures_metrics(
    archive_dir: str | Path,
    config: Mapping,
    taker_aggregation: str = "mean",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if taker_aggregation not in {"mean", "last"}:
        raise ValueError("taker_aggregation must be either 'mean' or 'last'")

    root = Path(archive_dir)
    symbols = list(config["data"]["futures_symbols"])
    start = _utc(config["sample_split"]["development_start"])
    end = _utc(config["sample_split"]["oos_end_exclusive"])
    parts = []
    inventory_rows = []
    date_pattern = re.compile(r"metrics-(\d{4}-\d{2}-\d{2})\.zip$")
    for symbol in symbols:
        files = sorted((root / symbol / "metrics").glob(f"{symbol}-metrics-*.zip"))
        selected = []
        for path in files:
            match = date_pattern.search(path.name)
            if not match:
                continue
            date = pd.Timestamp(match.group(1), tz="UTC")
            if start.floor("D") <= date < end.ceil("D"):
                selected.append(path)
        if not selected:
            raise FileNotFoundError(f"No futures metrics selected for {symbol}")
        symbol_parts = []
        for path in selected:
            part = pd.read_csv(path, compression="zip", usecols=FUTURES_COLUMNS)
            symbol_parts.append(part)
            inventory_rows.append(
                {
                    "symbol": symbol,
                    "path": path.as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "rows": len(part),
                    "missing_open_interest": int(part["sum_open_interest"].isna().sum()),
                    "missing_taker_ratio": int(part["sum_taker_long_short_vol_ratio"].isna().sum()),
                    "missing_count_toptrader_ratio": int(part["count_toptrader_long_short_ratio"].isna().sum()),
                    "missing_sum_toptrader_ratio": int(part["sum_toptrader_long_short_ratio"].isna().sum()),
                    "missing_count_long_short_ratio": int(part["count_long_short_ratio"].isna().sum()),
                }
            )
        symbol_frame = pd.concat(symbol_parts, ignore_index=True)
        parts.append(symbol_frame)

    raw = pd.concat(parts, ignore_index=True)
    raw["create_time"] = pd.to_datetime(raw["create_time"], utc=True)
    raw = raw.loc[raw["create_time"].ge(start) & raw["create_time"].lt(end)].copy()
    numeric_columns = [column for column in FUTURES_COLUMNS if column not in {"create_time", "symbol"}]
    raw[numeric_columns] = raw[numeric_columns].apply(pd.to_numeric, errors="coerce")
    primary_columns = ["sum_open_interest", "sum_taker_long_short_vol_ratio"]
    if raw[primary_columns].isna().any().any():
        raise ValueError("Futures primary metric columns contain missing values")
    ratio = raw["sum_taker_long_short_vol_ratio"].clip(lower=0.0)
    raw["taker_direction"] = (ratio - 1.0) / (ratio + 1.0)
    raw["bucket_start"] = raw["create_time"].dt.floor("15min")
    aggregated = (
        raw.sort_values(["symbol", "create_time"])
        .groupby(["bucket_start", "symbol"], as_index=False)
        .agg(
            taker_direction=("taker_direction", taker_aggregation),
            open_interest=("sum_open_interest", "last"),
            count_toptrader_long_short_ratio=("count_toptrader_long_short_ratio", "last"),
            sum_toptrader_long_short_ratio=("sum_toptrader_long_short_ratio", "last"),
            count_long_short_ratio=("count_long_short_ratio", "last"),
            source_rows=("create_time", "size"),
        )
    )
    aggregated["log_open_interest_change"] = aggregated.groupby("symbol")["open_interest"].transform(
        lambda values: np.log(values.where(values > 0)).diff()
    )
    return aggregated, pd.DataFrame(inventory_rows)


def analyze_futures_confirmation(
    futures: pd.DataFrame,
    composites: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    symbols = list(config["data"]["futures_symbols"])
    analysis = config["analysis"]
    split = config["sample_split"]
    development_start = _utc(split["development_start"])
    development_end = _utc(split["development_end_exclusive"])
    oos_start = _utc(split["oos_start"])
    oos_end = _utc(split["oos_end_exclusive"])

    panel = futures.pivot(index="bucket_start", columns="symbol", values="taker_direction").reindex(columns=symbols)
    panel = panel.loc[(panel.index >= development_start) & (panel.index < oos_end)].dropna()
    development = (panel.index >= development_start) & (panel.index < development_end)
    standardized = panel.copy()
    scaling_rows = []
    for symbol in symbols:
        center = panel.loc[development, symbol].mean()
        scale = panel.loc[development, symbol].std(ddof=1)
        standardized[symbol] = (panel[symbol] - center) / scale
        scaling_rows.append({"symbol": symbol, "development_center": center, "development_scale": scale})
    futures_common_raw = standardized.mean(axis=1)
    center = futures_common_raw.loc[development].mean()
    scale = futures_common_raw.loc[development].std(ddof=1)
    futures_common = (futures_common_raw - center) / scale

    spot = composites.set_index("bucket_start")["spot_futures5_flow"]
    joined = pd.concat(
        [spot.rename("spot_flow"), futures_common.rename("futures_flow")],
        axis=1,
        join="inner",
    ).dropna()
    joined["sample_split"] = np.select(
        [
            (joined.index >= development_start) & (joined.index < development_end),
            (joined.index >= oos_start) & (joined.index < oos_end),
        ],
        ["development", "oos"],
        default="excluded",
    )
    joined = joined.loc[joined["sample_split"].ne("excluded")]
    oos = joined.loc[joined["sample_split"].eq("oos")].copy()
    model_frame = oos.assign(futures_lag=oos["futures_flow"].shift(1)).dropna()
    model = sm.OLS(
        model_frame["futures_flow"],
        sm.add_constant(model_frame[["spot_flow", "futures_lag"]], has_constant="add"),
    ).fit(cov_type="HAC", cov_kwds={"maxlags": int(analysis["hac_maxlags"])})

    threshold = joined.loc[joined["sample_split"].eq("development"), "spot_flow"].abs().quantile(
        float(analysis["futures_event_quantile"])
    )
    events = oos.loc[oos["spot_flow"].abs().ge(threshold)].copy()
    events["direction_match"] = np.sign(events["spot_flow"]) == np.sign(events["futures_flow"])
    event_count = len(events)
    match_count = int(events["direction_match"].sum())
    binomial_p = float(binomtest(match_count, event_count, 0.5, alternative="greater").pvalue)

    summary = pd.DataFrame(
        [
            {
                "test": "spot_to_futures_flow_regression",
                "observations": int(model.nobs),
                "estimate": float(model.params["spot_flow"]),
                "standard_error": float(model.bse["spot_flow"]),
                "test_statistic": float(model.tvalues["spot_flow"]),
                "p_value": float(model.pvalues["spot_flow"]),
                "event_count": np.nan,
            },
            {
                "test": "extreme_event_direction_concordance",
                "observations": event_count,
                "estimate": match_count / event_count,
                "standard_error": np.nan,
                "test_statistic": np.nan,
                "p_value": binomial_p,
                "event_count": event_count,
            },
        ]
    )
    summary["q_value_bh_fdr"] = benjamini_hochberg(summary["p_value"])
    summary["development_extreme_threshold"] = threshold

    oi_panel = futures.pivot(index="bucket_start", columns="symbol", values="log_open_interest_change").reindex(columns=symbols)
    oi_common_abs = oi_panel.abs().mean(axis=1).rename("mean_abs_log_oi_change")
    diagnostics = oos.join(oi_common_abs, how="left")
    oi_correlation = diagnostics[["spot_flow", "mean_abs_log_oi_change"]].corr().iloc[0, 1]
    diagnostics_summary = pd.DataFrame(
        [
            {
                "aligned_rows": len(joined),
                "development_rows": int((joined["sample_split"] == "development").sum()),
                "oos_rows": len(oos),
                "event_rows": event_count,
                "event_direction_matches": match_count,
                "spot_flow_abs_oi_change_correlation": float(oi_correlation),
                "minimum_source_rows_per_bucket": int(futures["source_rows"].min()),
                "maximum_source_rows_per_bucket": int(futures["source_rows"].max()),
            }
        ]
    )
    return summary, diagnostics_summary, pd.DataFrame(scaling_rows)


def build_decisions(
    synchronization: pd.DataFrame,
    lead_lag: pd.DataFrame,
    bootstrap: pd.DataFrame,
    futures: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    analysis = config["analysis"]
    synchronization_pass = bool(synchronization["gate_pass"].all())

    primary_horizon = int(analysis["cascade_primary_horizon_minutes"])
    cascade = lead_lag.loc[
        lead_lag["direction"].eq("major_to_alt")
        & lead_lag["horizon_minutes"].eq(primary_horizon)
    ].iloc[0]
    direction = bootstrap.loc[bootstrap["horizon_minutes"].eq(primary_horizon)].iloc[0]
    cascade_pass = bool(
        cascade["standardized_beta"] >= float(analysis["cascade_minimum_standardized_beta"])
        and cascade["q_value_bh_fdr"] <= float(analysis["fdr_alpha"])
        and cascade["first_half_beta"] > 0
        and cascade["second_half_beta"] > 0
        and direction["ci_lower_95"] > 0
    )

    regression = futures.loc[futures["test"].eq("spot_to_futures_flow_regression")].iloc[0]
    concordance = futures.loc[futures["test"].eq("extreme_event_direction_concordance")].iloc[0]
    futures_pass = bool(
        regression["estimate"] >= float(analysis["futures_minimum_standardized_beta"])
        and regression["q_value_bh_fdr"] <= float(analysis["fdr_alpha"])
        and concordance["event_count"] >= int(analysis["futures_minimum_event_count"])
        and concordance["estimate"] >= float(analysis["futures_minimum_directional_concordance"])
        and concordance["q_value_bh_fdr"] <= float(analysis["fdr_alpha"])
    )

    if synchronization_pass and cascade_pass and futures_pass:
        final = "synchronization_cascade_and_futures_confirmation_supported"
    elif synchronization_pass and cascade_pass:
        final = "synchronization_and_cascade_supported_without_futures_confirmation"
    elif synchronization_pass:
        final = "synchronization_supported_without_directional_cascade"
    else:
        final = "cross_asset_order_flow_synchronization_not_supported"
    return pd.DataFrame(
        [
            {
                "decision": "spot_synchronization",
                "passed": synchronization_pass,
                "classification": "order_flow_synchronization_supported" if synchronization_pass else "order_flow_synchronization_not_supported",
            },
            {
                "decision": "major_to_alt_cascade",
                "passed": cascade_pass,
                "classification": "major_to_alt_cascade_supported" if cascade_pass else "major_to_alt_cascade_not_supported",
            },
            {
                "decision": "futures_confirmation",
                "passed": futures_pass,
                "classification": "futures_confirmation_supported" if futures_pass else "futures_confirmation_not_supported",
            },
            {
                "decision": "final",
                "passed": bool(synchronization_pass),
                "classification": final,
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
    futures: pd.DataFrame,
    futures_diagnostics: pd.DataFrame,
    decisions: pd.DataFrame,
    plot_paths: Sequence[str],
) -> str:
    sync_decision = decisions.loc[decisions["decision"].eq("spot_synchronization")].iloc[0]
    cascade_decision = decisions.loc[decisions["decision"].eq("major_to_alt_cascade")].iloc[0]
    futures_decision = decisions.loc[decisions["decision"].eq("futures_confirmation")].iloc[0]
    final = decisions.loc[decisions["decision"].eq("final")].iloc[0]
    lines = [
        "# 교차자산 Order-Flow Synchronization 연구 보고서",
        "",
        "## 한 문장 결론",
        "",
        f"최종 분류는 `{final['classification']}`입니다. 이 결과는 시장 전체 주문흐름 동조화에 관한 것이며 투자자의 의도적 모방이나 미래수익률 alpha를 검정하지 않습니다.",
        "",
        "## 데이터",
        "",
        f"- Spot rows: {int(coverage.iloc[0]['rows']):,}, timestamps: {int(coverage.iloc[0]['timestamps']):,}, assets: {int(coverage.iloc[0]['symbols'])}",
        f"- 기간: {coverage.iloc[0]['start']} ~ {coverage.iloc[0]['end']}",
        f"- Aggressor 가용률: {coverage.iloc[0]['aggressor_available_share']:.2%}",
        "- Development 1년에서 잔차화 계수와 scaler를 적합하고 OOS 1년에 고정 적용",
        "- 뉴스·Reddit·sentiment 사용 안 함",
        "",
        "## 1. Spot 교차자산 동조화",
        "",
        "| 지표 | 실제 | null 평균 | null p95 | BH q | 효과크기 gate | 판정 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in synchronization.itertuples(index=False):
        lines.append(
            f"| {row.metric} | {row.observed:.5f} | {row.null_mean:.5f} | {row.null_p95:.5f} | "
            f"{row.q_value_bh_fdr:.4g} | {'통과' if row.effect_size_pass else '미통과'} | {'통과' if row.gate_pass else '미통과'} |"
        )
    lines.extend(
        [
            "",
            f"- Spot primary: **{'지지' if sync_decision['passed'] else '지지하지 않음'}**",
            "- Null은 반기 내 UTC 날짜 block을 자산별로 독립 circular shift해 자기상관과 일중 패턴을 보존했습니다.",
            "",
            "## 2. BTC·ETH에서 알트코인으로의 전파",
            "",
            "| 방향 | horizon | beta | HAC t | BH q | 전반부 beta | 후반부 beta |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in lead_lag.itertuples(index=False):
        lines.append(
            f"| {row.direction} | {row.horizon_minutes}m | {row.standardized_beta:.5f} | "
            f"{row.t_stat_hac:.3f} | {row.q_value_bh_fdr:.4g} | {row.first_half_beta:.5f} | {row.second_half_beta:.5f} |"
        )
    lines.extend(["", "방향차 bootstrap:", "", "| horizon | MA-AM 평균 | 95% CI | one-sided p |", "|---:|---:|---:|---:|"])
    for row in bootstrap.itertuples(index=False):
        lines.append(
            f"| {row.horizon_minutes}m | {row.mean_beta_difference:.5f} | "
            f"[{row.ci_lower_95:.5f}, {row.ci_upper_95:.5f}] | {row.one_sided_p_value:.4g} |"
        )
    lines.extend(
        [
            "",
            f"- 15분 major→alt cascade: **{'지지' if cascade_decision['passed'] else '지지하지 않음'}**",
            "",
            "## 3. 선물 확인",
            "",
            "| 검정 | n | 추정값 | p | BH q |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in futures.itertuples(index=False):
        lines.append(f"| {row.test} | {int(row.observations):,} | {row.estimate:.5f} | {row.p_value:.4g} | {row.q_value_bh_fdr:.4g} |")
    lines.extend(
        [
            "",
            f"- Spot-flow 절대값과 절대 OI 변화 상관: {futures_diagnostics.iloc[0]['spot_flow_abs_oi_change_correlation']:.4f}",
            f"- Futures confirmation: **{'지지' if futures_decision['passed'] else '지지하지 않음'}**",
            "",
            "## 해석 제한",
            "",
            "- 통과 결과가 있더라도 `market-wide order-flow synchronization` 또는 `order-flow cascade`로만 부릅니다.",
            "- aggTrades에는 계정·지갑 ID가 없어 intentional imitation을 직접 식별할 수 없습니다.",
            "- 미래수익률, 거래비용, 전략 성과를 검정하지 않았습니다.",
            "- 뉴스, Reddit, sentiment는 입력·필터·해석에 사용하지 않았습니다.",
            "",
            "## 그림",
            "",
        ]
    )
    lines.extend(f"- `{path}`" for path in plot_paths)
    lines.append("")
    return "\n".join(lines)


def plot_synchronization_null(summary: pd.DataFrame, path: str | Path) -> None:
    _configure_plot_style()
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8), facecolor="#F4F0E8")
    labels = {
        "mean_pairwise_correlation": "평균 pairwise correlation",
        "extreme_alignment_rate": "6/7 이상 방향 일치율",
    }
    for axis, row in zip(axes, summary.itertuples(index=False), strict=True):
        axis.set_facecolor("#F4F0E8")
        axis.bar([0, 1], [row.null_mean, row.observed], color=["#D2A33B", "#236B6B"], width=0.62)
        axis.axhline(row.null_p95, color="#C6533D", linestyle="--", linewidth=1.7, label="null 95%")
        axis.set_xticks([0, 1], labels=["시간 null", "실제 정렬"])
        axis.set_title(labels[row.metric], fontweight="bold")
        axis.grid(axis="y", alpha=0.25)
        axis.legend(frameon=False)
        for x, value in enumerate([row.null_mean, row.observed]):
            axis.text(x, value, f"{value:.4f}", ha="center", va="bottom", fontweight="bold")
        axis.spines[["top", "right"]].set_visible(False)
    fig.suptitle("실제 동시점 주문흐름은 시간 정렬을 깨뜨린 null과 다른가", fontsize=19, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    _save_plot(fig, path)


def plot_pairwise_correlations(pairwise: pd.DataFrame, symbols: Sequence[str], path: str | Path) -> None:
    _configure_plot_style()
    matrix = pd.DataFrame(np.eye(len(symbols)), index=symbols, columns=symbols)
    for row in pairwise.itertuples(index=False):
        matrix.loc[row.left_symbol, row.right_symbol] = row.correlation
        matrix.loc[row.right_symbol, row.left_symbol] = row.correlation
    fig, axis = plt.subplots(figsize=(8.4, 7.0), facecolor="#F4F0E8")
    image = axis.imshow(matrix, cmap="RdBu_r", vmin=-1, vmax=1)
    axis.set_xticks(range(len(symbols)), labels=[value.replace("USDT", "") for value in symbols], rotation=45, ha="right")
    axis.set_yticks(range(len(symbols)), labels=[value.replace("USDT", "") for value in symbols])
    for i in range(len(symbols)):
        for j in range(len(symbols)):
            axis.text(j, i, f"{matrix.iloc[i, j]:.2f}", ha="center", va="center", color="white" if abs(matrix.iloc[i, j]) > 0.55 else "#172121")
    axis.set_title("OOS 잔차 Aggressor Flow 상관", fontsize=18, fontweight="bold", pad=18)
    fig.colorbar(image, ax=axis, fraction=0.045, pad=0.04)
    fig.tight_layout()
    _save_plot(fig, path)


def plot_lead_lag(summary: pd.DataFrame, path: str | Path) -> None:
    _configure_plot_style()
    fig, axis = plt.subplots(figsize=(10, 5.8), facecolor="#F4F0E8")
    colors = {"major_to_alt": "#236B6B", "alt_to_major": "#C6533D"}
    offsets = {"major_to_alt": -2.0, "alt_to_major": 2.0}
    for direction, group in summary.groupby("direction", sort=False):
        x = group["horizon_minutes"].to_numpy(dtype=float) + offsets[direction]
        y = group["standardized_beta"].to_numpy(dtype=float)
        lower = y - group["ci_lower_95"].to_numpy(dtype=float)
        upper = group["ci_upper_95"].to_numpy(dtype=float) - y
        axis.errorbar(x, y, yerr=np.vstack([lower, upper]), marker="o", capsize=4, linewidth=2, color=colors[direction], label=direction)
    axis.axhline(0, color="#172121", linewidth=1.2)
    axis.set_xticks([15, 30, 60], labels=["15m", "30m", "60m"])
    axis.set_ylabel("표준화 선행 계수")
    axis.set_title("Major와 Alt 주문흐름의 양방향 Lead-Lag", fontsize=18, fontweight="bold")
    axis.legend(frameon=False)
    axis.grid(alpha=0.25)
    axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    _save_plot(fig, path)


def _spot_design_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    names = [
        "const",
        "own_return",
        "abs_own_return",
        "loo_market_return",
        "abs_loo_market_return",
        "log_quote_volume",
        "log_transaction_count",
        "hour_sin",
        "hour_cos",
        *[f"weekday_{value}" for value in range(1, 7)],
    ]
    columns = [
        np.ones(len(frame)),
        *[
            frame[column].to_numpy(dtype=float)
            for column in [
                "own_return",
                "abs_own_return",
                "loo_market_return",
                "abs_loo_market_return",
                "log_quote_volume",
                "log_transaction_count",
                "hour_sin",
                "hour_cos",
            ]
        ],
        *[(frame["weekday"].to_numpy(dtype=int) == value).astype(float) for value in range(1, 7)],
    ]
    return np.column_stack(columns), names


def _synchronization_statistics(values: np.ndarray, minimum_assets: int) -> dict[str, float]:
    correlation = np.corrcoef(values, rowvar=False)
    upper = correlation[np.triu_indices(correlation.shape[0], 1)]
    signs = np.sign(values)
    positive = (signs > 0).sum(axis=1)
    negative = (signs < 0).sum(axis=1)
    aligned = np.maximum(positive, negative) >= minimum_assets
    return {
        "mean_pairwise_correlation": float(np.mean(upper)),
        "extreme_alignment_rate": float(np.mean(aligned)),
        "mean_common_flow_intensity": float(np.mean(np.abs(values.mean(axis=1)))),
    }


def _halfyear_day_circular_shift(
    matrix: pd.DataFrame,
    rng: np.random.Generator,
    minimum_shift_days: int,
) -> np.ndarray:
    values = matrix.to_numpy(dtype=float)
    shifted = np.empty_like(values)
    half_years = matrix.index.year * 2 + ((matrix.index.month - 1) // 6)
    for half_year in np.unique(half_years):
        positions = np.flatnonzero(half_years == half_year)
        if len(positions) % 96 != 0:
            raise ValueError("Half-year block does not contain complete UTC days")
        days = len(positions) // 96
        if days <= 2 * minimum_shift_days:
            raise ValueError("Half-year block is too short for frozen minimum shift")
        block = values[positions].reshape(days, 96, values.shape[1])
        for asset in range(values.shape[1]):
            offset = int(rng.integers(minimum_shift_days, days - minimum_shift_days + 1))
            shifted[positions, asset] = np.roll(block[:, :, asset], offset, axis=0).reshape(-1)
    return shifted


def _simple_target_beta(frame: pd.DataFrame) -> float:
    design = np.column_stack(
        [np.ones(len(frame)), frame["target"].to_numpy(dtype=float), frame["current_dependent"].to_numpy(dtype=float)]
    )
    return float(np.linalg.lstsq(design, frame["y"].to_numpy(dtype=float), rcond=None)[0][1])


def _bootstrap_direction_difference(
    oos: pd.DataFrame,
    steps: int,
    repetitions: int,
    rng: np.random.Generator,
) -> np.ndarray:
    frame = pd.DataFrame(
        {
            "major": oos["major_flow"],
            "alt": oos["alt_flow"],
            "future_major": oos["major_flow"].shift(-steps),
            "future_alt": oos["alt_flow"].shift(-steps),
        },
        index=oos.index,
    ).dropna()
    days = pd.Index(frame.index.floor("D").unique())
    day_positions = [np.flatnonzero(frame.index.floor("D") == day) for day in days]
    results = np.empty(repetitions, dtype=float)
    for draw in range(repetitions):
        sampled_days = rng.integers(0, len(days), size=len(days))
        positions = np.concatenate([day_positions[index] for index in sampled_days])
        sample = frame.iloc[positions]
        ma_design = np.column_stack([np.ones(len(sample)), sample["major"], sample["alt"]])
        am_design = np.column_stack([np.ones(len(sample)), sample["alt"], sample["major"]])
        ma = np.linalg.lstsq(ma_design, sample["future_alt"], rcond=None)[0][1]
        am = np.linalg.lstsq(am_design, sample["future_major"], rcond=None)[0][1]
        results[draw] = ma - am
    return results


def _configure_plot_style() -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    font = next((value for value in ["Noto Sans CJK KR", "Noto Sans CJK JP", "NanumGothic"] if value in available), "DejaVu Sans")
    plt.rcParams.update({"font.family": font, "axes.unicode_minus": False})


def _save_plot(figure: plt.Figure, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=180, bbox_inches="tight", facecolor="#F4F0E8")
    plt.close(figure)


def _utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
