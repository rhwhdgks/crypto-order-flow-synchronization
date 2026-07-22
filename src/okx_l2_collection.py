from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from okx_l2_audit import (
    audit_l2_archive,
    download_archive,
    fetch_archive_catalog_for_dates,
)


FEATURE_COLUMNS = [
    "bucket_start",
    "symbol",
    "spread_bps",
    "top10_depth_quote",
    "book_imbalance_10",
    "abs_book_imbalance_10",
    "observed_minutes",
]


def validate_collection_config(research: Mapping, collection: Mapping) -> None:
    if collection["source"].get("delete_archive_after_cache") is not True:
        raise ValueError("Production collection must delete archives after verified cache creation")
    if int(collection["collection"]["sample_interval_seconds"]) != 60:
        raise ValueError("The sealed feature definition requires one-minute book samples")
    if float(collection["collection"]["minimum_valid_day_share"]) != 0.95:
        raise ValueError("Minimum valid-day share has drifted from the protocol")
    if int(research["data"]["bucket_minutes"]) != 15:
        raise ValueError("The sealed research bucket is 15 minutes")


def build_collection_jobs(research: Mapping) -> pd.DataFrame:
    start = pd.Timestamp(research["data"]["start_inclusive"])
    end = pd.Timestamp(research["data"]["end_exclusive"])
    dates = pd.date_range(start.floor("D"), end.floor("D"), freq="D", inclusive="left")
    rows = [
        {"date": day.strftime("%Y-%m-%d"), "symbol": symbol}
        for day in dates
        for symbol in research["data"]["symbols"]
    ]
    return pd.DataFrame(rows)


def aggregate_minute_book_features(
    samples: pd.DataFrame,
    symbol: str,
    bucket_minutes: int = 15,
) -> pd.DataFrame:
    required = {
        "timestamp",
        "empty",
        "crossed",
        "spread_bps",
        "ask_depth_quote_10",
        "bid_depth_quote_10",
        "book_imbalance_10",
    }
    missing = sorted(required.difference(samples.columns))
    if missing:
        raise ValueError(f"Minute book samples are missing columns: {', '.join(missing)}")
    valid = samples.loc[~samples["empty"] & ~samples["crossed"]].copy()
    if valid.empty:
        raise ValueError("No valid minute books remain")
    valid["timestamp"] = pd.to_datetime(valid["timestamp"], utc=True)
    valid["bucket_start"] = valid["timestamp"].dt.floor(f"{int(bucket_minutes)}min")
    valid["top10_depth_quote"] = (
        valid["ask_depth_quote_10"] + valid["bid_depth_quote_10"]
    )
    valid["abs_book_imbalance_10"] = valid["book_imbalance_10"].abs()
    aggregated = (
        valid.groupby("bucket_start", sort=True, as_index=False)
        .agg(
            spread_bps=("spread_bps", "median"),
            top10_depth_quote=("top10_depth_quote", "median"),
            book_imbalance_10=("book_imbalance_10", "median"),
            abs_book_imbalance_10=("abs_book_imbalance_10", "median"),
            observed_minutes=("timestamp", "size"),
        )
        .assign(symbol=symbol)
    )
    return aggregated[FEATURE_COLUMNS]


def day_quality_passed(summary: Mapping, collection: Mapping) -> tuple[bool, str]:
    settings = collection["collection"]
    checks = {
        "snapshot": int(summary["snapshots"]) >= 1,
        "initial_depth": min(
            int(summary["initial_ask_levels"]), int(summary["initial_bid_levels"])
        )
        >= int(settings["minimum_initial_levels_per_side"]),
        "start_boundary": int(summary["start_delay_ms"])
        <= int(settings["maximum_start_delay_ms"]),
        "end_boundary": int(summary["end_early_ms"])
        <= int(settings["maximum_end_early_ms"]),
        "parse": int(summary["parse_errors"]) == 0,
        "timestamp_order": int(summary["out_of_order_rows"]) == 0,
        "crossed": int(summary["sampled_crossed_books"]) == 0,
        "empty": int(summary["sampled_empty_books"]) == 0,
        "minutes": int(summary["sample_count"]) == 1_440,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return not failed, ";".join(failed)


def collect_l2_features(
    research: Mapping,
    collection: Mapping,
    project_root: str | Path,
    maximum_new_files: int | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    root = Path(project_root)
    output_root = root / research["output"]["base_dir"]
    cache_root = output_root / "intermediate" / "l2_15m_daily"
    raw_root = root / collection["source"]["raw_data_dir"]
    progress_path = output_root / "collection_progress.csv"
    state_path = output_root / "collection_state.json"
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)

    jobs = build_collection_jobs(research)
    progress = _load_progress(progress_path)
    progress = _recover_completed_caches(progress, jobs, cache_root)
    _atomic_csv(progress, progress_path)
    completed_keys = set(
        zip(
            progress.loc[progress["status"].eq("complete"), "date"],
            progress.loc[progress["status"].eq("complete"), "symbol"],
        )
    )
    new_files = 0
    for sample_date, date_jobs in jobs.groupby("date", sort=True):
        pending_symbols = [
            symbol
            for symbol in date_jobs["symbol"]
            if (sample_date, symbol) not in completed_keys
            or not _cache_is_valid(_cache_path(cache_root, symbol, sample_date), symbol, sample_date)
        ]
        if not pending_symbols:
            continue
        catalog = fetch_archive_catalog_for_dates(
            pending_symbols,
            [sample_date],
            endpoint=collection["source"]["catalog_endpoint"],
            module=str(research["data"]["l2_module"]),
            instrument_type=str(research["data"]["instrument_type"]),
            timeout_seconds=int(collection["source"]["timeout_seconds"]),
        )
        if not bool(catalog["available"].all()):
            missing = catalog.loc[~catalog["available"], "symbol"].tolist()
            raise FileNotFoundError(f"Missing OKX L2 archives for {sample_date}: {missing}")
        # Small files first make the initial smoke test and resumptions fast.
        for catalog_row in catalog.sort_values("size_mb").itertuples(index=False):
            if maximum_new_files is not None and new_files >= maximum_new_files:
                return _collection_state(jobs, progress, "paused", progress_callback, state_path)
            symbol = str(catalog_row.symbol)
            cache_path = _cache_path(cache_root, symbol, sample_date)
            metadata_path = cache_path.with_suffix(".metadata.json")
            archive_path = raw_root / symbol / str(catalog_row.filename)
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            download_archive(
                str(catalog_row.url),
                archive_path,
                float(catalog_row.size_mb),
                timeout_seconds=int(collection["source"]["timeout_seconds"]),
            )
            archive_sha256 = _sha256(archive_path)
            summary_frame, samples = audit_l2_archive(
                archive_path,
                expected_symbol=symbol,
                expected_date=sample_date,
                sample_interval_seconds=int(collection["collection"]["sample_interval_seconds"]),
            )
            summary = summary_frame.iloc[0].to_dict()
            quality_passed, quality_failures = day_quality_passed(summary, collection)
            features = aggregate_minute_book_features(
                samples,
                symbol=symbol,
                bucket_minutes=int(research["data"]["bucket_minutes"]),
            )
            _atomic_parquet(features, cache_path)
            metadata = {
                "date": sample_date,
                "symbol": symbol,
                "quality_passed": quality_passed,
                "quality_failures": quality_failures,
                "archive_filename": str(catalog_row.filename),
                "archive_size_bytes": archive_path.stat().st_size,
                "archive_sha256": archive_sha256,
                "raw_rows": int(summary["rows"]),
                "snapshots": int(summary["snapshots"]),
                "updates": int(summary["updates"]),
                "parse_errors": int(summary["parse_errors"]),
                "out_of_order_rows": int(summary["out_of_order_rows"]),
                "start_delay_ms": int(summary["start_delay_ms"]),
                "end_early_ms": int(summary["end_early_ms"]),
                "sample_count": int(summary["sample_count"]),
                "bucket_rows": len(features),
                "cache_sha256": _sha256(cache_path),
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_json(metadata, metadata_path)
            if collection["source"]["delete_archive_after_cache"]:
                archive_path.unlink()
            progress = _upsert_progress(progress, {**metadata, "status": "complete"})
            _atomic_csv(progress, progress_path)
            completed_keys.add((sample_date, symbol))
            new_files += 1
            _collection_state(jobs, progress, "running", progress_callback, state_path)

    panel, coverage = assemble_feature_panel(cache_root, jobs, collection)
    _atomic_parquet(panel, output_root / "intermediate" / "l2_feature_panel_15m.parquet")
    _atomic_csv(coverage, output_root / "l2_feature_coverage.csv")
    return _collection_state(jobs, progress, "complete", progress_callback, state_path)


def assemble_feature_panel(
    cache_root: Path,
    jobs: pd.DataFrame,
    collection: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    coverage_rows = []
    for row in jobs.itertuples(index=False):
        cache_path = _cache_path(cache_root, row.symbol, row.date)
        metadata_path = cache_path.with_suffix(".metadata.json")
        if not cache_path.is_file() or not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        coverage_rows.append(metadata)
        if metadata["quality_passed"]:
            frames.append(pd.read_parquet(cache_path))
    if not frames:
        raise ValueError("No quality-passing L2 feature caches are available")
    coverage = pd.DataFrame(coverage_rows).sort_values(["date", "symbol"])
    expected_days = jobs["date"].nunique()
    valid_days = coverage.loc[coverage["quality_passed"]].groupby("symbol")["date"].nunique()
    minimum_share = float(valid_days.reindex(jobs["symbol"].unique(), fill_value=0).min() / expected_days)
    if minimum_share < float(collection["collection"]["minimum_valid_day_share"]):
        raise ValueError(f"Minimum valid-day share {minimum_share:.4f} is below the sealed gate")
    panel = pd.concat(frames, ignore_index=True).sort_values(["symbol", "bucket_start"])
    panel["depth_depletion"] = -panel.groupby("symbol")["top10_depth_quote"].transform(
        lambda values: np.log(values).diff()
    )
    panel = panel.sort_values(["bucket_start", "symbol"]).reset_index(drop=True)
    return panel, coverage


def collection_status(project_root: str | Path, research: Mapping) -> dict:
    root = Path(project_root)
    output_root = root / research["output"]["base_dir"]
    state_path = output_root / "collection_state.json"
    progress_path = output_root / "collection_progress.csv"
    if state_path.is_file():
        return json.loads(state_path.read_text(encoding="utf-8"))
    jobs = build_collection_jobs(research)
    progress = _load_progress(progress_path)
    return _state_payload(jobs, progress, "not_started")


def _collection_state(
    jobs: pd.DataFrame,
    progress: pd.DataFrame,
    status: str,
    callback: Callable[[dict], None] | None,
    state_path: Path,
) -> dict:
    state = _state_payload(jobs, progress, status)
    _atomic_json(state, state_path)
    if callback is not None:
        callback(state)
    return state


def _state_payload(jobs: pd.DataFrame, progress: pd.DataFrame, status: str) -> dict:
    complete = progress.loc[progress["status"].eq("complete")] if not progress.empty else progress
    completed = len(complete)
    quality_passed = int(complete.get("quality_passed", pd.Series(dtype=bool)).sum())
    if "completed_at_utc" in complete and complete["completed_at_utc"].notna().any():
        latest = complete.loc[complete["completed_at_utc"].notna()].sort_values(
            "completed_at_utc"
        ).tail(1)
    else:
        latest = complete.sort_values(["date", "symbol"]).tail(1)
    return {
        "status": status,
        "completed_files": completed,
        "total_files": len(jobs),
        "progress_share": completed / len(jobs),
        "quality_passing_files": quality_passed,
        "quality_failing_files": completed - quality_passed,
        "last_completed_date": None if latest.empty else str(latest.iloc[0]["date"]),
        "last_completed_symbol": None if latest.empty else str(latest.iloc[0]["symbol"]),
    }


def _load_progress(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=["date", "symbol", "status", "quality_passed"])
    frame = pd.read_csv(path, dtype={"date": str, "symbol": str, "status": str})
    if "quality_passed" in frame:
        frame["quality_passed"] = frame["quality_passed"].astype(str).str.lower().eq("true")
    return frame


def _recover_completed_caches(
    progress: pd.DataFrame,
    jobs: pd.DataFrame,
    cache_root: Path,
) -> pd.DataFrame:
    completed_keys = set(
        zip(
            progress.loc[progress["status"].eq("complete"), "date"],
            progress.loc[progress["status"].eq("complete"), "symbol"],
        )
    )
    for row in jobs.itertuples(index=False):
        key = (row.date, row.symbol)
        cache_path = _cache_path(cache_root, row.symbol, row.date)
        if key in completed_keys or not _cache_is_valid(cache_path, row.symbol, row.date):
            continue
        metadata = json.loads(cache_path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
        progress = _upsert_progress(progress, {**metadata, "status": "complete"})
    return progress


def _upsert_progress(progress: pd.DataFrame, row: dict) -> pd.DataFrame:
    if not progress.empty:
        progress = progress.loc[
            ~progress["date"].eq(row["date"]) | ~progress["symbol"].eq(row["symbol"])
        ]
    return pd.concat([progress, pd.DataFrame([row])], ignore_index=True).sort_values(
        ["date", "symbol"]
    )


def _cache_path(cache_root: Path, symbol: str, sample_date: str) -> Path:
    return cache_root / symbol / f"{sample_date}.parquet"


def _cache_is_valid(path: Path, symbol: str, sample_date: str) -> bool:
    metadata_path = path.with_suffix(".metadata.json")
    if not path.is_file() or not metadata_path.is_file():
        return False
    try:
        frame = pd.read_parquet(path)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        list(frame.columns) == FEATURE_COLUMNS
        and len(frame) == 96
        and frame["symbol"].eq(symbol).all()
        and pd.to_datetime(frame["bucket_start"], utc=True).dt.strftime("%Y-%m-%d").eq(sample_date).all()
        and metadata.get("cache_sha256") == _sha256(path)
    )


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _atomic_json(content: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
