from __future__ import annotations

import hashlib
import json
import math
import tarfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd


USER_AGENT = "curl/8.5.0"


def validate_common_liquidity_config(config: Mapping) -> None:
    expected_symbols = [
        "BTC-USDT",
        "ETH-USDT",
        "XRP-USDT",
        "SOL-USDT",
        "DOGE-USDT",
        "ADA-USDT",
        "AVAX-USDT",
    ]
    data = config["data"]
    split = config["sample_split"]
    analysis = config["analysis"]
    if list(data["symbols"]) != expected_symbols:
        raise ValueError("Common-liquidity universe has drifted")
    if int(data["bucket_minutes"]) != 15 or str(data["l2_module"]) != "4":
        raise ValueError("Common-liquidity frequency or L2 module has drifted")
    start = pd.Timestamp(data["start_inclusive"])
    end = pd.Timestamp(data["end_exclusive"])
    if (end - start).days != 180:
        raise ValueError("Common-liquidity sample must span exactly 180 days")
    if pd.Timestamp(split["development_end_exclusive"]) - start != pd.Timedelta(days=60):
        raise ValueError("Development window must span exactly 60 days")
    if int(analysis["null_repetitions"]) != 499:
        raise ValueError("Null repetitions have drifted")
    if int(analysis["bootstrap_repetitions"]) != 499:
        raise ValueError("Bootstrap repetitions have drifted")
    if float(analysis["primary_minimum_correlation_reduction"]) != 0.02:
        raise ValueError("Primary minimum effect has drifted")


def verify_common_liquidity_seal(
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
            raise ValueError(f"Common-liquidity seal mismatch: {key}")
    if seal.get("results_observed_at_seal") is not False:
        raise ValueError("Seal does not certify unobserved results")
    if set(seal.get("excluded_data_layers", [])) != {
        "news",
        "reddit",
        "twitter_x",
        "sentiment",
    }:
        raise ValueError("Excluded data layers drifted")
    return {**seal, **observed, "verified": True}


def validate_audit_config(config: Mapping) -> None:
    data = config["data"]
    audit = config["audit"]
    if str(data["module"]) != "4":
        raise ValueError("The audit is fixed to OKX 400-level L2 archives (module 4)")
    if str(data["instrument_type"]) != "SPOT":
        raise ValueError("The audit is fixed to spot instruments")
    if len(set(data["symbols"])) != len(data["symbols"]):
        raise ValueError("Duplicate symbols are not permitted")
    if data["pilot_symbol"] not in data["symbols"]:
        raise ValueError("pilot_symbol must belong to the audited universe")
    if data["pilot_date"] not in data["sample_dates"]:
        raise ValueError("pilot_date must be one of sample_dates")
    if int(audit["sample_interval_seconds"]) <= 0:
        raise ValueError("sample_interval_seconds must be positive")


def date_to_epoch_ms(value: str | date) -> int:
    parsed = date.fromisoformat(value) if isinstance(value, str) else value
    return int(datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc).timestamp() * 1000)


def build_catalog_payload(symbols: Iterable[str], value: str | date, config: Mapping) -> dict:
    epoch_ms = str(date_to_epoch_ms(value))
    return {
        "module": str(config["data"]["module"]),
        "instType": str(config["data"]["instrument_type"]),
        "instQueryParam": {"instIdList": list(symbols)},
        "dateQuery": {
            "dateAggrType": "daily",
            "begin": epoch_ms,
            "end": epoch_ms,
        },
    }


def fetch_archive_catalog(config: Mapping, timeout_seconds: int = 60) -> pd.DataFrame:
    endpoint = str(config["data"]["catalog_endpoint"])
    rows: list[dict] = []
    for sample_date in config["data"]["sample_dates"]:
        payload = build_catalog_payload(config["data"]["symbols"], sample_date, config)
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        result = _request_json_with_backoff(request, timeout_seconds)
        if str(result.get("code")) != "0":
            raise RuntimeError(f"OKX catalog request failed: {result}")
        details = result.get("data", {}).get("details", [])
        by_symbol = {entry.get("instId"): entry for entry in details}
        for symbol in config["data"]["symbols"]:
            detail = by_symbol.get(symbol, {})
            groups = detail.get("groupDetails") or []
            group = groups[0] if groups else {}
            rows.append(
                {
                    "date": sample_date,
                    "symbol": symbol,
                    "available": bool(group.get("url")),
                    "filename": group.get("filename"),
                    "size_mb": _optional_float(group.get("sizeMB")),
                    "url": group.get("url"),
                }
            )
        time.sleep(1.0)
    return pd.DataFrame(rows)


def download_archive(
    url: str,
    destination: str | Path,
    expected_size_mb: float | None = None,
    timeout_seconds: int = 120,
) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and _size_is_plausible(target, expected_size_mb):
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        with temporary.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
    if not _size_is_plausible(temporary, expected_size_mb):
        temporary.unlink(missing_ok=True)
        raise IOError("Downloaded archive size differs materially from the OKX catalog")
    temporary.replace(target)
    return target


def audit_l2_archive(
    archive_path: str | Path,
    expected_symbol: str,
    expected_date: str,
    sample_interval_seconds: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    archive = Path(archive_path)
    asks: dict[float, tuple[float, int]] = {}
    bids: dict[float, tuple[float, int]] = {}
    counters = {
        "rows": 0,
        "snapshots": 0,
        "updates": 0,
        "unknown_actions": 0,
        "parse_errors": 0,
        "symbol_mismatches": 0,
        "out_of_order_rows": 0,
        "duplicate_timestamps": 0,
        "zero_size_deletions": 0,
        "gaps_over_100ms": 0,
        "gaps_over_1s": 0,
        "gaps_over_5s": 0,
    }
    first_ts: int | None = None
    last_ts: int | None = None
    max_gap_ms = 0
    initial_ask_levels: int | None = None
    initial_bid_levels: int | None = None
    samples: list[dict] = []
    next_sample_ts: int | None = None

    with tarfile.open(archive, mode="r:gz") as bundle:
        members = [member for member in bundle.getmembers() if member.isfile()]
        if len(members) != 1:
            raise ValueError(f"Expected one data member, found {len(members)}")
        stream = bundle.extractfile(members[0])
        if stream is None:
            raise ValueError("Could not open the archive data member")
        for raw_line in stream:
            counters["rows"] += 1
            try:
                row = json.loads(raw_line)
                timestamp_ms = int(row["ts"])
                action = str(row["action"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                counters["parse_errors"] += 1
                continue
            if row.get("instId") != expected_symbol:
                counters["symbol_mismatches"] += 1
            if first_ts is None:
                first_ts = timestamp_ms
            if last_ts is not None:
                gap_ms = timestamp_ms - last_ts
                if gap_ms < 0:
                    counters["out_of_order_rows"] += 1
                elif gap_ms == 0:
                    counters["duplicate_timestamps"] += 1
                else:
                    max_gap_ms = max(max_gap_ms, gap_ms)
                    counters["gaps_over_100ms"] += int(gap_ms > 100)
                    counters["gaps_over_1s"] += int(gap_ms > 1_000)
                    counters["gaps_over_5s"] += int(gap_ms > 5_000)
            last_ts = timestamp_ms

            if action == "snapshot":
                counters["snapshots"] += 1
                asks.clear()
                bids.clear()
            elif action == "update":
                counters["updates"] += 1
            else:
                counters["unknown_actions"] += 1
                continue
            counters["zero_size_deletions"] += _apply_levels(asks, row.get("asks", []))
            counters["zero_size_deletions"] += _apply_levels(bids, row.get("bids", []))
            if action == "snapshot" and initial_ask_levels is None:
                initial_ask_levels = len(asks)
                initial_bid_levels = len(bids)

            if next_sample_ts is None:
                interval_ms = sample_interval_seconds * 1_000
                next_sample_ts = (timestamp_ms // interval_ms) * interval_ms
            if timestamp_ms >= next_sample_ts:
                samples.append(_book_sample(timestamp_ms, asks, bids))
                interval_ms = sample_interval_seconds * 1_000
                next_sample_ts = ((timestamp_ms // interval_ms) + 1) * interval_ms

    if first_ts is None or last_ts is None:
        raise ValueError("The archive contains no valid L2 rows")
    day_start_ms = date_to_epoch_ms(expected_date)
    day_end_ms = day_start_ms + 86_400_000
    sample_frame = pd.DataFrame(samples)
    summary = {
        **counters,
        "archive_path": archive.name,
        "archive_size_bytes": archive.stat().st_size,
        "archive_member_size_bytes": members[0].size,
        "expected_symbol": expected_symbol,
        "expected_date": expected_date,
        "first_timestamp": pd.to_datetime(first_ts, unit="ms", utc=True),
        "last_timestamp": pd.to_datetime(last_ts, unit="ms", utc=True),
        "start_delay_ms": first_ts - day_start_ms,
        "end_early_ms": day_end_ms - last_ts,
        "observed_span_seconds": (last_ts - first_ts) / 1_000,
        "max_gap_ms": max_gap_ms,
        "initial_ask_levels": initial_ask_levels,
        "initial_bid_levels": initial_bid_levels,
        "sample_count": len(sample_frame),
        "sampled_crossed_books": int(sample_frame.get("crossed", pd.Series(dtype=bool)).sum()),
        "sampled_empty_books": int(sample_frame.get("empty", pd.Series(dtype=bool)).sum()),
    }
    return pd.DataFrame([summary]), sample_frame


def estimate_resource_requirements(catalog: pd.DataFrame, config: Mapping) -> pd.DataFrame:
    available = catalog.loc[catalog["available"] & catalog["size_mb"].notna()].copy()
    if available.empty:
        raise ValueError("No available catalog rows for resource estimation")
    daily = available.groupby("date", as_index=False)["size_mb"].sum()
    average_day_mb = float(daily["size_mb"].mean())
    median_day_mb = float(daily["size_mb"].median())
    return pd.DataFrame(
        [
            {
                "design": "sampled_catalog_day",
                "days": 1,
                "estimated_compressed_gb": average_day_mb / 1_000,
                "basis": f"mean of {len(daily)} sampled UTC dates",
            },
            {
                "design": "bounded_confirmatory_window",
                "days": int(config["audit"]["confirmatory_window_days"]),
                "estimated_compressed_gb": average_day_mb
                * int(config["audit"]["confirmatory_window_days"])
                / 1_000,
                "basis": "sample-date mean x days",
            },
            {
                "design": "full_two_year_window",
                "days": int(config["audit"]["full_history_days"]),
                "estimated_compressed_gb": average_day_mb
                * int(config["audit"]["full_history_days"])
                / 1_000,
                "basis": "sample-date mean x days",
            },
            {
                "design": "sampled_catalog_day_median",
                "days": 1,
                "estimated_compressed_gb": median_day_mb / 1_000,
                "basis": f"median of {len(daily)} sampled UTC dates",
            },
        ]
    )


def build_quality_decisions(
    catalog: pd.DataFrame,
    audit_summary: pd.DataFrame,
    config: Mapping,
) -> pd.DataFrame:
    row = audit_summary.iloc[0]
    thresholds = config["audit"]
    checks = [
        (
            "catalog_complete",
            bool(catalog["available"].all()),
            f"{int(catalog['available'].sum())}/{len(catalog)} symbol-date files available",
        ),
        ("initial_snapshot_present", int(row["snapshots"]) >= 1, f"snapshots={int(row['snapshots'])}"),
        (
            "initial_depth_sufficient",
            min(int(row["initial_ask_levels"]), int(row["initial_bid_levels"]))
            >= int(thresholds["minimum_initial_levels_per_side"]),
            f"asks={int(row['initial_ask_levels'])}, bids={int(row['initial_bid_levels'])}",
        ),
        (
            "full_day_boundary_coverage",
            int(row["start_delay_ms"]) <= int(thresholds["maximum_start_delay_ms"])
            and int(row["end_early_ms"]) <= int(thresholds["maximum_end_early_ms"]),
            f"start_delay_ms={int(row['start_delay_ms'])}, end_early_ms={int(row['end_early_ms'])}",
        ),
        (
            "rows_parse_cleanly",
            int(row["parse_errors"]) <= int(thresholds["maximum_parse_errors"]),
            f"parse_errors={int(row['parse_errors'])}",
        ),
        (
            "timestamps_ordered",
            int(row["out_of_order_rows"]) <= int(thresholds["maximum_out_of_order_rows"]),
            f"out_of_order_rows={int(row['out_of_order_rows'])}",
        ),
        (
            "sampled_books_not_crossed",
            int(row["sampled_crossed_books"])
            <= int(thresholds["maximum_sampled_crossed_books"]),
            f"sampled_crossed_books={int(row['sampled_crossed_books'])}",
        ),
    ]
    decisions = pd.DataFrame(checks, columns=["quality_gate", "passed", "evidence"])
    overall = bool(decisions["passed"].all())
    decisions.loc[len(decisions)] = [
        "bounded_l2_study_ready",
        overall,
        "all fixed availability and reconstruction gates passed" if overall else "one or more gates failed",
    ]
    return decisions


def build_audit_report(
    catalog: pd.DataFrame,
    audit_summary: pd.DataFrame,
    resources: pd.DataFrame,
    decisions: pd.DataFrame,
) -> str:
    row = audit_summary.iloc[0]
    ready = bool(
        decisions.loc[decisions["quality_gate"].eq("bounded_l2_study_ready"), "passed"].iloc[0]
    )
    bounded = resources.loc[resources["design"].eq("bounded_confirmatory_window")].iloc[0]
    full = resources.loc[resources["design"].eq("full_two_year_window")].iloc[0]
    failed = decisions.loc[~decisions["passed"], "quality_gate"].tolist()
    lines = [
        "# OKX L2 데이터 가용성·품질 감사",
        "",
        "## 목적",
        "",
        "체결 주문흐름 동조화와 공통 유동성 충격을 분리하기 전에, 공식 400레벨 L2 원자료가 연구에 사용할 수 있는지 확인했다. 이 단계는 수익률 또는 가설 결과를 탐색하지 않는 데이터 감사다.",
        "",
        "## 공식 파일 가용성",
        "",
        "- 출처: [OKX Historical Market Data](https://www.okx.com/historical-data)",
        f"- 고정 표본: {catalog['symbol'].nunique()}개 자산 × {catalog['date'].nunique()}개 UTC 날짜 = {len(catalog)}개 파일",
        f"- 확인된 파일: {int(catalog['available'].sum())}/{len(catalog)}",
        f"- 표본 날짜 하루 평균 압축 용량: {catalog.groupby('date')['size_mb'].sum().mean() / 1000:.2f} GB",
        "",
        "## 전체 파일 복원 감사",
        "",
        f"- 파일: {row['expected_symbol']} {row['expected_date']}",
        f"- JSONL 행: {int(row['rows']):,}개 (snapshot {int(row['snapshots']):,}, update {int(row['updates']):,})",
        f"- 최초 호가 깊이: ask {int(row['initial_ask_levels'])} / bid {int(row['initial_bid_levels'])} 레벨",
        f"- 시작 지연: {int(row['start_delay_ms'])} ms, 종료 여유: {int(row['end_early_ms'])} ms",
        f"- 파싱 오류: {int(row['parse_errors'])}, 역행 timestamp: {int(row['out_of_order_rows'])}",
        f"- 1분 표본: {int(row['sample_count']):,}개, 교차 호가: {int(row['sampled_crossed_books'])}개",
        "",
        "## 용량 판단",
        "",
        f"- 7개 자산 180일 추정 압축 다운로드: {bounded['estimated_compressed_gb']:.1f} GB",
        f"- 7개 자산 2년 추정 압축 다운로드: {full['estimated_compressed_gb']:.1f} GB",
        "- 원자료 전체를 상시 풀어두지 않고 날짜별 스트리밍 처리 후 요약 feature만 보존하는 방식이 적합하다.",
        "",
        "## 결론",
        "",
    ]
    if ready:
        lines.extend(
            [
                "고정 품질 기준을 모두 통과했다. 따라서 180일·7개 자산의 범위가 제한된 L2 확인 연구를 사전등록할 수 있다.",
                "다음 가설은 체결 주문흐름 동조화가 spread 확대, depth 고갈, book imbalance의 공통 성분으로 설명되는지 검정하는 것이다. 이 판정만으로 미래수익률 알파가 확인된 것은 아니다.",
            ]
        )
    else:
        lines.append(f"현재 L2 연구를 진행하지 않는다. 실패 기준: {', '.join(failed)}")
    return "\n".join(lines) + "\n"


def _apply_levels(book: dict[float, tuple[float, int]], levels: Iterable) -> int:
    deletions = 0
    for raw in levels:
        if len(raw) < 2:
            continue
        price = float(raw[0])
        size = float(raw[1])
        orders = int(raw[2]) if len(raw) > 2 else 0
        if size == 0:
            deletions += 1
            book.pop(price, None)
        else:
            book[price] = (size, orders)
    return deletions


def _book_sample(
    timestamp_ms: int,
    asks: Mapping[float, tuple[float, int]],
    bids: Mapping[float, tuple[float, int]],
) -> dict:
    if not asks or not bids:
        return {
            "timestamp": pd.to_datetime(timestamp_ms, unit="ms", utc=True),
            "empty": True,
            "crossed": False,
        }
    ask_prices = sorted(asks)
    bid_prices = sorted(bids, reverse=True)
    best_ask = ask_prices[0]
    best_bid = bid_prices[0]
    midpoint = (best_ask + best_bid) / 2
    result = {
        "timestamp": pd.to_datetime(timestamp_ms, unit="ms", utc=True),
        "empty": False,
        "crossed": best_bid >= best_ask,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": (best_ask - best_bid) / midpoint * 10_000,
        "ask_levels": len(asks),
        "bid_levels": len(bids),
    }
    for level_count in (1, 5, 10):
        ask_depth = sum(price * asks[price][0] for price in ask_prices[:level_count])
        bid_depth = sum(price * bids[price][0] for price in bid_prices[:level_count])
        total = ask_depth + bid_depth
        result[f"ask_depth_quote_{level_count}"] = ask_depth
        result[f"bid_depth_quote_{level_count}"] = bid_depth
        result[f"book_imbalance_{level_count}"] = (
            (bid_depth - ask_depth) / total if total > 0 else math.nan
        )
    return result


def _optional_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _size_is_plausible(path: Path, expected_size_mb: float | None) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    if expected_size_mb is None or not np.isfinite(expected_size_mb):
        return True
    observed_mb = path.stat().st_size / (1024 * 1024)
    return abs(observed_mb - expected_size_mb) / expected_size_mb <= 0.05


def _request_json_with_backoff(
    request: urllib.request.Request,
    timeout_seconds: int,
    maximum_attempts: int = 6,
) -> dict:
    for attempt in range(maximum_attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == maximum_attempts - 1:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2.0 ** attempt
            time.sleep(max(1.0, min(delay, 30.0)))
    raise RuntimeError("Catalog request retry loop exhausted")
