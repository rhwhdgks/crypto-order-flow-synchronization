from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


OKX_TRADE_COLUMNS = [
    "instrument_name",
    "trade_id",
    "side",
    "price",
    "size",
    "created_time",
]


def validate_okx_external_config(config: Mapping) -> None:
    data = config["data"]
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
        raise ValueError("OKX external-validation universe has drifted")
    if list(data["major_symbols"]) != ["BTC-USDT", "ETH-USDT"]:
        raise ValueError("OKX major-symbol definition has drifted")
    if int(data["expected_interval_minutes"]) != 15:
        raise ValueError("OKX protocol v1 only permits 15-minute buckets")
    if config["source"].get("source_archive_timezone") != "Asia/Shanghai":
        raise ValueError("OKX source archive timezone has drifted")
    if int(analysis["null_repetitions"]) != 499:
        raise ValueError("OKX null repetitions have drifted")
    if int(analysis["bootstrap_repetitions"]) != 499:
        raise ValueError("OKX bootstrap repetitions have drifted")
    if int(analysis["alignment_minimum_assets"]) != 6:
        raise ValueError("OKX alignment threshold has drifted")
    serialized = json.dumps(config, ensure_ascii=False).lower()
    for forbidden in ["news", "reddit", "twitter", "sentiment"]:
        if forbidden in serialized:
            raise ValueError(f"Forbidden data layer appears in OKX config: {forbidden}")


def verify_okx_preregistration_seal(
    protocol_path: str | Path,
    config_path: str | Path,
    seal_path: str | Path,
) -> dict:
    protocol = Path(protocol_path)
    config = Path(config_path)
    seal = json.loads(Path(seal_path).read_text(encoding="utf-8"))
    observed = {
        "protocol_sha256": _sha256(protocol),
        "config_sha256": _sha256(config),
    }
    for key, value in observed.items():
        if seal.get(key) != value:
            raise ValueError(f"OKX preregistration seal mismatch: {key}")
    amendment_paths = {
        "amendment_1_sha256": protocol.with_name(f"{protocol.stem}_amendment_1.md"),
        "amendment_2_sha256": protocol.with_name(f"{protocol.stem}_amendment_2.md"),
        "prior_seal_sha256": protocol.with_name(f"{protocol.stem}.pre_composite.seal.json"),
    }
    for key, path in amendment_paths.items():
        if seal.get(key) != _sha256(path):
            raise ValueError(f"OKX preregistration amendment mismatch: {key}")
    if seal.get("results_observed_at_seal") is not False:
        raise ValueError("OKX seal does not certify unobserved results")
    if set(seal.get("excluded_data_layers", [])) != {
        "news",
        "reddit",
        "twitter_x",
        "sentiment",
    }:
        raise ValueError("OKX excluded data layers drifted from the sealed protocol")
    return {**seal, **observed, "verified": True}


def collect_okx_monthly_buckets(
    config: Mapping,
    project_root: str | Path,
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    root = Path(project_root)
    source = config["source"]
    data = config["data"]
    symbols = list(data["symbols"])
    start = _utc(data["expected_start"])
    end = _utc(data["expected_end_exclusive"])
    months = pd.date_range(start.floor("D").replace(day=1), (end - pd.Timedelta(days=1)).replace(day=1), freq="MS")
    raw_root = root / source["local_data_dir"]
    output_root = root / config["output"]["base_dir"]
    cache_root = output_root / "monthly_cache"
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    lock = threading.Lock()
    total_jobs = len(symbols) * len(months)

    def record(item: dict) -> None:
        with lock:
            records.append(item)
            progress = pd.DataFrame(records).sort_values(["symbol", "month"])
            _atomic_csv(progress, output_root / "collection_progress.csv")
            state = {
                "status": "running",
                "completed_jobs": len(records),
                "total_jobs": total_jobs,
                "progress_share": len(records) / total_jobs,
                "last_completed_symbol": item["symbol"],
                "last_completed_month": item["month"],
            }
            _atomic_json(state, output_root / "collection_state.json")
            if progress_callback is not None:
                progress_callback(state)

    def process_symbol(symbol: str) -> None:
        for month in months:
            month_key = month.strftime("%Y-%m")
            archive_path = raw_root / symbol / f"{symbol}-trades-{month_key}.zip"
            cache_path = cache_root / symbol / f"{symbol}_{month_key}_15m.parquet"
            metadata_path = cache_path.with_suffix(".metadata.json")
            if cache_path.is_file() and archive_path.is_file() and metadata_path.is_file():
                bucket = pd.read_parquet(cache_path)
                _validate_month_cache(
                    bucket, symbol, month_key, str(source["source_archive_timezone"])
                )
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                source_used = "cache"
            else:
                source_used = ensure_okx_month_archive(symbol, month, archive_path, source)
                bucket, metadata = aggregate_okx_month_archive(
                    archive_path,
                    symbol,
                    int(data["expected_interval_minutes"]),
                    int(source["chunksize"]),
                )
                _validate_month_cache(
                    bucket, symbol, month_key, str(source["source_archive_timezone"])
                )
                _atomic_parquet(bucket, cache_path)
                _atomic_json(metadata, metadata_path)
            record(
                {
                    "symbol": symbol,
                    "month": month_key,
                    "source_used": source_used,
                    "archive_path": archive_path.as_posix(),
                    "archive_size_bytes": archive_path.stat().st_size,
                    "archive_sha256": _sha256(archive_path),
                    "raw_rows": int(metadata["raw_rows"]),
                    "bucket_rows": len(bucket),
                    "bucket_start": str(bucket["bucket_start"].min()),
                    "bucket_end": str(bucket["bucket_start"].max()),
                    "cache_path": cache_path.as_posix(),
                    "cache_sha256": _sha256(cache_path),
                }
            )

    max_workers = min(int(config.get("download", {}).get("max_workers", 1)), len(symbols))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="okx-tick") as executor:
        futures = {executor.submit(process_symbol, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            future.result()

    frames = []
    for symbol in symbols:
        for month in months:
            month_key = month.strftime("%Y-%m")
            frames.append(pd.read_parquet(cache_root / symbol / f"{symbol}_{month_key}_15m.parquet"))
    panel = pd.concat(frames, ignore_index=True)
    panel["bucket_start"] = pd.to_datetime(panel["bucket_start"], utc=True)
    panel = panel.loc[panel["bucket_start"].ge(start) & panel["bucket_start"].lt(end)].copy()
    panel = panel.sort_values(["bucket_start", "symbol"]).reset_index(drop=True)
    coverage, intersection = validate_okx_panel_quality(panel, config)
    panel = panel.loc[panel["bucket_start"].isin(intersection)].reset_index(drop=True)
    manifest = pd.DataFrame(records).sort_values(["symbol", "month"]).reset_index(drop=True)
    final_state = {
        "status": "complete",
        "completed_jobs": total_jobs,
        "total_jobs": total_jobs,
        "progress_share": 1.0,
        "panel_rows": len(panel),
        "intersection_timestamps": len(intersection),
    }
    _atomic_json(final_state, output_root / "collection_state.json")
    return panel, coverage, manifest


def ensure_okx_month_archive(
    symbol: str,
    month: pd.Timestamp,
    destination: Path,
    source: Mapping,
) -> str:
    if destination.is_file() and not source.get("overwrite_existing", False) and _readable_zip(destination):
        return "cache"
    destination.parent.mkdir(parents=True, exist_ok=True)
    month_compact = month.strftime("%Y%m")
    month_key = month.strftime("%Y-%m")
    url = f"{str(source['base_url']).rstrip('/')}/{month_compact}/{symbol}-trades-{month_key}.zip?v=999"
    temporary = destination.with_suffix(".zip.part")
    attempts = int(source.get("retries", 5))
    timeout = int(source.get("timeout_seconds", 300))
    backoff = float(source.get("retry_backoff_seconds", 3))
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers={"User-Agent": "crypto-herding-research/1.0"})
            with urlopen(request, timeout=timeout) as response, temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
            if not _readable_zip(temporary):
                raise zipfile.BadZipFile(f"Unreadable OKX archive: {url}")
            temporary.replace(destination)
            return "download"
        except (HTTPError, URLError, TimeoutError, OSError, zipfile.BadZipFile) as exc:
            error = exc
            temporary.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(backoff * attempt)
    raise FileNotFoundError(f"OKX monthly archive unavailable after {attempts} attempts: {url}") from error


def aggregate_okx_month_archive(
    archive_path: str | Path,
    symbol: str,
    interval_minutes: int,
    chunksize: int,
) -> tuple[pd.DataFrame, dict]:
    path = Path(archive_path)
    partials = []
    raw_rows = 0
    first_trade_id: int | None = None
    last_trade_id: int | None = None
    with zipfile.ZipFile(path) as archive:
        member = _csv_member(archive)
        with archive.open(member) as handle:
            for chunk in pd.read_csv(handle, chunksize=int(chunksize), low_memory=False):
                missing = sorted(set(OKX_TRADE_COLUMNS).difference(chunk.columns))
                if missing:
                    raise ValueError(f"OKX trade CSV is missing columns: {', '.join(missing)}")
                if not chunk["instrument_name"].astype(str).eq(symbol).all():
                    raise ValueError(f"OKX archive symbol mismatch: {symbol}")
                trade_id = pd.to_numeric(chunk["trade_id"], errors="raise").astype("int64")
                if trade_id.duplicated().any() or not trade_id.is_monotonic_increasing:
                    raise ValueError(f"OKX trade IDs are not strictly ordered within chunk: {symbol}")
                if last_trade_id is not None and int(trade_id.iloc[0]) <= last_trade_id:
                    raise ValueError(f"OKX trade IDs overlap across chunks: {symbol}")
                first_trade_id = int(trade_id.iloc[0]) if first_trade_id is None else first_trade_id
                last_trade_id = int(trade_id.iloc[-1])

                normalized = pd.DataFrame(
                    {
                        "timestamp": pd.to_datetime(
                            pd.to_numeric(chunk["created_time"], errors="coerce"),
                            unit="ms",
                            utc=True,
                        ),
                        "trade_id": trade_id,
                        "side": chunk["side"].astype(str).str.lower(),
                        "price": pd.to_numeric(chunk["price"], errors="coerce"),
                        "size": pd.to_numeric(chunk["size"], errors="coerce"),
                    }
                )
                if normalized.isna().any().any() or not normalized["side"].isin(["buy", "sell"]).all():
                    raise ValueError(f"OKX primary trade fields contain invalid values: {symbol}")
                normalized = normalized.sort_values(["timestamp", "trade_id"])
                normalized["quote_quantity"] = normalized["price"] * normalized["size"]
                normalized["signed_quote_quantity"] = np.where(
                    normalized["side"].eq("buy"),
                    normalized["quote_quantity"],
                    -normalized["quote_quantity"],
                )
                normalized["buy_quote_quantity"] = np.where(
                    normalized["side"].eq("buy"), normalized["quote_quantity"], 0.0
                )
                normalized["sell_quote_quantity"] = np.where(
                    normalized["side"].eq("sell"), normalized["quote_quantity"], 0.0
                )
                normalized["bucket_start"] = normalized["timestamp"].dt.floor(f"{interval_minutes}min")
                grouped = normalized.groupby("bucket_start", sort=True).agg(
                    first_timestamp=("timestamp", "first"),
                    last_timestamp=("timestamp", "last"),
                    first_price=("price", "first"),
                    last_price=("price", "last"),
                    transaction_count=("trade_id", "size"),
                    total_quantity=("size", "sum"),
                    total_quote_quantity=("quote_quantity", "sum"),
                    signed_quote_quantity=("signed_quote_quantity", "sum"),
                    buy_quote_quantity=("buy_quote_quantity", "sum"),
                    sell_quote_quantity=("sell_quote_quantity", "sum"),
                )
                partials.append(grouped.reset_index())
                raw_rows += len(normalized)
    if not partials:
        raise ValueError(f"OKX archive contains no trade rows: {path}")
    combined = pd.concat(partials, ignore_index=True).sort_values("bucket_start")
    bucket = combined.groupby("bucket_start", sort=True, as_index=False).agg(
        first_timestamp=("first_timestamp", "first"),
        last_timestamp=("last_timestamp", "last"),
        first_price=("first_price", "first"),
        last_price=("last_price", "last"),
        transaction_count=("transaction_count", "sum"),
        total_quantity=("total_quantity", "sum"),
        total_quote_quantity=("total_quote_quantity", "sum"),
        signed_quote_quantity=("signed_quote_quantity", "sum"),
        buy_quote_quantity=("buy_quote_quantity", "sum"),
        sell_quote_quantity=("sell_quote_quantity", "sum"),
    )
    bucket["symbol"] = symbol
    bucket["interval_minutes"] = int(interval_minutes)
    bucket["schema_version"] = 1
    bucket["bucket_return"] = bucket["last_price"] / bucket["first_price"] - 1.0
    bucket["aggressor_imbalance"] = (
        bucket["signed_quote_quantity"] / bucket["total_quote_quantity"]
    )
    columns = [
        "bucket_start",
        "symbol",
        "interval_minutes",
        "schema_version",
        "first_timestamp",
        "last_timestamp",
        "first_price",
        "last_price",
        "bucket_return",
        "transaction_count",
        "total_quantity",
        "total_quote_quantity",
        "buy_quote_quantity",
        "sell_quote_quantity",
        "aggressor_imbalance",
    ]
    metadata = {
        "symbol": symbol,
        "raw_rows": raw_rows,
        "first_trade_id": first_trade_id,
        "last_trade_id": last_trade_id,
        "bucket_rows": len(bucket),
    }
    return bucket[columns], metadata


def validate_okx_panel_quality(
    panel: pd.DataFrame,
    config: Mapping,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    data = config["data"]
    symbols = list(data["symbols"])
    start = _utc(data["expected_start"])
    end = _utc(data["expected_end_exclusive"])
    interval = int(data["expected_interval_minutes"])
    expected = pd.date_range(start, end, freq=f"{interval}min", inclusive="left")
    if panel.duplicated(["bucket_start", "symbol"]).any():
        raise ValueError("OKX panel contains duplicate symbol timestamps")
    if set(panel["symbol"].unique()) != set(symbols):
        raise ValueError("OKX panel symbols do not match the frozen universe")
    primary = ["bucket_return", "transaction_count", "total_quote_quantity", "aggressor_imbalance"]
    if panel[primary].isna().any().any() or not np.isfinite(panel[primary].to_numpy(dtype=float)).all():
        raise ValueError("OKX panel primary fields contain missing or non-finite values")
    indices = {
        symbol: pd.DatetimeIndex(panel.loc[panel["symbol"].eq(symbol), "bucket_start"].sort_values().unique())
        for symbol in symbols
    }
    intersection = expected
    for symbol in symbols:
        intersection = intersection.intersection(indices[symbol])
    share = len(intersection) / len(expected)
    minimum = float(data["minimum_complete_intersection_share"])
    coverage_rows = []
    for symbol in symbols:
        coverage_rows.append(
            {
                "symbol": symbol,
                "observed_buckets": len(indices[symbol]),
                "expected_buckets": len(expected),
                "coverage_share": len(indices[symbol].intersection(expected)) / len(expected),
                "first_bucket": indices[symbol].min(),
                "last_bucket": indices[symbol].max(),
                "intersection_buckets": len(intersection),
                "intersection_share": share,
                "quality_gate_pass": share >= minimum,
            }
        )
    coverage = pd.DataFrame(coverage_rows)
    if share < minimum:
        raise ValueError(
            f"blocked_by_source_coverage: common intersection {share:.4%} is below {minimum:.4%}"
        )
    return coverage, intersection


def build_okx_external_decisions(
    synchronization: pd.DataFrame,
    lead_lag: pd.DataFrame,
    bootstrap: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    analysis = config["analysis"]
    synchronization_pass = bool(synchronization["gate_pass"].all())
    horizon = int(analysis["cascade_primary_horizon_minutes"])
    cascade = lead_lag.loc[
        lead_lag["direction"].eq("major_to_alt") & lead_lag["horizon_minutes"].eq(horizon)
    ].iloc[0]
    direction = bootstrap.loc[bootstrap["horizon_minutes"].eq(horizon)].iloc[0]
    cascade_pass = bool(
        cascade["standardized_beta"] >= float(analysis["cascade_minimum_standardized_beta"])
        and cascade["q_value_bh_fdr"] <= float(analysis["fdr_alpha"])
        and cascade["first_half_beta"] > 0
        and cascade["second_half_beta"] > 0
        and direction["ci_lower_95"] > 0
    )
    return pd.DataFrame(
        [
            {
                "decision": "okx_spot_synchronization_external_replication",
                "passed": synchronization_pass,
                "classification": (
                    "okx_order_flow_synchronization_replicated"
                    if synchronization_pass
                    else "okx_order_flow_synchronization_not_replicated"
                ),
            },
            {
                "decision": "okx_major_to_alt_cascade",
                "passed": cascade_pass,
                "classification": (
                    "okx_major_to_alt_cascade_supported"
                    if cascade_pass
                    else "okx_major_to_alt_cascade_not_supported"
                ),
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


def build_provider_comparison(
    okx_sync: pd.DataFrame,
    okx_lead_lag: pd.DataFrame,
    binance_sync: pd.DataFrame,
    binance_lead_lag: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for metric in okx_sync["metric"]:
        okx = okx_sync.loc[okx_sync["metric"].eq(metric)].iloc[0]
        binance = binance_sync.loc[binance_sync["metric"].eq(metric)].iloc[0]
        rows.append(
            {
                "family": "synchronization",
                "metric": metric,
                "binance_estimate": binance["observed"],
                "okx_estimate": okx["observed"],
                "okx_to_binance_ratio": okx["observed"] / binance["observed"],
                "binance_gate_pass": bool(binance["gate_pass"]),
                "okx_gate_pass": bool(okx["gate_pass"]),
            }
        )
    for horizon in [15, 30, 60]:
        for direction in ["major_to_alt", "alt_to_major"]:
            okx = okx_lead_lag.loc[
                okx_lead_lag["horizon_minutes"].eq(horizon)
                & okx_lead_lag["direction"].eq(direction)
            ].iloc[0]
            binance = binance_lead_lag.loc[
                binance_lead_lag["horizon_minutes"].eq(horizon)
                & binance_lead_lag["direction"].eq(direction)
            ].iloc[0]
            rows.append(
                {
                    "family": "lead_lag",
                    "metric": f"{direction}_{horizon}m",
                    "binance_estimate": binance["standardized_beta"],
                    "okx_estimate": okx["standardized_beta"],
                    "okx_to_binance_ratio": np.nan,
                    "binance_gate_pass": np.nan,
                    "okx_gate_pass": np.nan,
                }
            )
    return pd.DataFrame(rows)


def build_okx_external_report(
    coverage: pd.DataFrame,
    synchronization: pd.DataFrame,
    lead_lag: pd.DataFrame,
    bootstrap: pd.DataFrame,
    comparison: pd.DataFrame,
    decisions: pd.DataFrame,
    plot_paths: list[str],
) -> str:
    sync_pass = bool(
        decisions.loc[
            decisions["decision"].eq("okx_spot_synchronization_external_replication"),
            "passed",
        ].iloc[0]
    )
    cascade_pass = bool(
        decisions.loc[decisions["decision"].eq("okx_major_to_alt_cascade"), "passed"].iloc[0]
    )
    lines = [
        "# OKX Order-Flow Synchronization 외부검증 보고서",
        "",
        "## 한 문장 결론",
        "",
        (
            "Binance에서 관찰한 시장 전체 주문흐름 동조화는 OKX에서도 동일한 사전 기준을 통과했습니다."
            if sync_pass
            else "Binance에서 관찰한 시장 전체 주문흐름 동조화는 OKX에서 동일한 사전 기준을 통과하지 못했습니다."
        ),
        "이 결과는 거래소 외부타당성에 관한 것이며 의도적 모방이나 미래수익률 alpha를 뜻하지 않습니다.",
        "",
        "## 데이터와 품질",
        "",
        "- 출처: OKX 공식 Historical Market Data spot trade history",
        f"- 공통 15분 bucket: {int(coverage['intersection_buckets'].iloc[0]):,}개",
        f"- 전체 grid 대비 공통 coverage: {coverage['intersection_share'].iloc[0]:.4%}",
        f"- 자산: {', '.join(coverage['symbol'])}",
        "- 뉴스·Reddit·Twitter/X·sentiment 사용 안 함",
        "",
        "## 1. OKX Spot 동조화",
        "",
        "| 지표 | 실제 | null 평균 | null p95 | BH q | 효과크기 | 판정 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in synchronization.itertuples(index=False):
        lines.append(
            f"| {row.metric} | {row.observed:.5f} | {row.null_mean:.5f} | {row.null_p95:.5f} | "
            f"{row.q_value_bh_fdr:.4g} | {'통과' if row.effect_size_pass else '미통과'} | "
            f"{'통과' if row.gate_pass else '미통과'} |"
        )
    lines.extend(
        [
            "",
            f"- OKX provider external replication: **{'통과' if sync_pass else '미통과'}**",
            "- Null은 반기 내 UTC 날짜 block을 자산별로 독립 순환 이동해 자기상관과 일중 패턴을 보존했습니다.",
            "",
            "## 2. Major↔Alt lead-lag",
            "",
            "| 방향 | horizon | beta | HAC t | BH q | OOS 전반 | OOS 후반 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in lead_lag.itertuples(index=False):
        lines.append(
            f"| {row.direction} | {row.horizon_minutes}m | {row.standardized_beta:.5f} | "
            f"{row.t_stat_hac:.3f} | {row.q_value_bh_fdr:.4g} | {row.first_half_beta:.5f} | "
            f"{row.second_half_beta:.5f} |"
        )
    primary_bootstrap = bootstrap.loc[bootstrap["horizon_minutes"].eq(15)].iloc[0]
    lines.extend(
        [
            "",
            f"- 15분 방향차 bootstrap 95% CI: [{primary_bootstrap['ci_lower_95']:.5f}, {primary_bootstrap['ci_upper_95']:.5f}]",
            f"- OKX 15분 major→alt cascade: **{'통과' if cascade_pass else '미통과'}**",
            "",
            "## 3. Binance와 OKX 비교",
            "",
            "| 지표 | Binance | OKX | OKX/Binance |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in comparison.loc[comparison["family"].eq("synchronization")].itertuples(index=False):
        lines.append(
            f"| {row.metric} | {row.binance_estimate:.5f} | {row.okx_estimate:.5f} | "
            f"{row.okx_to_binance_ratio:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 해석 제한",
            "",
            "- OKX trade history와 Binance aggTrades는 체결 집계 단위가 다를 수 있어 transaction count 수준은 직접 비교하지 않았습니다.",
            "- 두 거래소에서 반복되더라도 계정·지갑 ID가 없어 intentional herding을 직접 식별할 수 없습니다.",
            "- 미래수익률, 거래비용, Sharpe ratio와 자동매매 가능성은 검정하지 않았습니다.",
            "- 뉴스, Reddit, Twitter/X, sentiment는 입력·필터·해석에 사용하지 않았습니다.",
            "",
            "## 그림",
            "",
        ]
    )
    lines.extend(f"- `{path}`" for path in plot_paths)
    lines.append("")
    return "\n".join(lines)


def _validate_month_cache(
    frame: pd.DataFrame,
    symbol: str,
    month_key: str,
    source_archive_timezone: str,
) -> None:
    required = {
        "bucket_start",
        "symbol",
        "bucket_return",
        "transaction_count",
        "total_quote_quantity",
        "aggressor_imbalance",
    }
    missing = sorted(required.difference(frame.columns))
    if missing or frame.empty:
        raise ValueError(f"Invalid OKX monthly cache {symbol} {month_key}: missing={missing}")
    timestamps = pd.to_datetime(frame["bucket_start"], utc=True)
    if not timestamps.is_monotonic_increasing or timestamps.duplicated().any():
        raise ValueError(f"Invalid OKX monthly timestamps: {symbol} {month_key}")
    if set(frame["symbol"].astype(str).unique()) != {symbol}:
        raise ValueError(f"Invalid OKX monthly cache symbol: {symbol} {month_key}")
    source_month = timestamps.dt.tz_convert(source_archive_timezone).dt.strftime("%Y-%m")
    if not source_month.eq(month_key).all():
        raise ValueError(f"OKX monthly cache crosses month boundary: {symbol} {month_key}")


def _readable_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            member = _csv_member(archive)
            with archive.open(member) as handle:
                return bool(handle.read(32))
    except (OSError, FileNotFoundError, zipfile.BadZipFile, KeyError):
        return False


def _csv_member(archive: zipfile.ZipFile) -> str:
    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(members) != 1:
        raise ValueError(f"Expected exactly one CSV in OKX archive, found {len(members)}")
    return members[0]


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _atomic_json(payload: Mapping, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
