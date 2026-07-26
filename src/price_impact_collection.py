from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from okx_l2_audit import (
    audit_l2_archive,
    download_archive,
    fetch_archive_catalog_for_dates,
)
from okx_order_flow_external_validation import aggregate_okx_month_archive


def validate_price_impact_collection_config(
    research: Mapping,
    collection: Mapping,
) -> None:
    if int(research["data"]["interval_minutes"]) != 1:
        raise ValueError("Price-impact collection is fixed to one-minute features")
    if int(collection["collection"]["sample_interval_seconds"]) != 60:
        raise ValueError("L2 sampling interval has drifted")
    if collection["source"].get("delete_archive_after_cache") is not True:
        raise ValueError("Raw L2 archives must be deleted after verified cache creation")
    if list(collection["collection"]["sweep_quote_amounts"]) != list(
        research["execution"]["sweep_quote_amounts"]
    ):
        raise ValueError("Collection and sealed execution capacity tiers differ")


def build_price_impact_jobs(research: Mapping) -> pd.DataFrame:
    start = pd.Timestamp(research["data"]["start_inclusive"])
    end = pd.Timestamp(research["data"]["end_exclusive"])
    dates = pd.date_range(start.floor("D"), end.floor("D"), freq="D", inclusive="left")
    return pd.DataFrame(
        [
            {"date": day.strftime("%Y-%m-%d"), "symbol": symbol}
            for day in dates
            for symbol in research["data"]["symbols"]
        ]
    )


def extract_execution_minute_features(
    samples: pd.DataFrame,
    symbol: str,
    sample_date: str,
) -> pd.DataFrame:
    required = {
        "timestamp",
        "empty",
        "crossed",
        "best_bid",
        "best_ask",
        "midpoint",
        "spread_bps",
        "ask_depth_quote_10",
        "bid_depth_quote_10",
        "book_imbalance_10",
    }
    missing = sorted(required.difference(samples.columns))
    if missing:
        raise ValueError(f"L2 minute samples are missing: {', '.join(missing)}")
    frame = samples.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["bucket_start"] = frame["timestamp"].dt.floor("1min")
    if frame["bucket_start"].duplicated().any():
        raise ValueError(f"Duplicate minute samples: {symbol} {sample_date}")
    frame["symbol"] = symbol
    frame["sample_date"] = sample_date
    frame["top10_depth_quote"] = (
        frame["ask_depth_quote_10"] + frame["bid_depth_quote_10"]
    )
    frame["abs_book_imbalance_10"] = frame["book_imbalance_10"].abs()
    ordered = [
        "bucket_start",
        "timestamp",
        "symbol",
        "sample_date",
        "empty",
        "crossed",
        "best_bid",
        "best_ask",
        "midpoint",
        "spread_bps",
        "ask_depth_quote_10",
        "bid_depth_quote_10",
        "top10_depth_quote",
        "book_imbalance_10",
        "abs_book_imbalance_10",
    ]
    execution_columns = sorted(
        column
        for column in frame.columns
        if column.startswith(
            (
                "buy_vwap_",
                "sell_vwap_",
                "buy_fill_ratio_",
                "sell_fill_ratio_",
                "buy_impact_bps_",
                "sell_impact_bps_",
            )
        )
    )
    return frame[ordered + execution_columns].sort_values("bucket_start").reset_index(
        drop=True
    )


def collect_price_impact_features(
    research: Mapping,
    collection: Mapping,
    project_root: str | Path,
    maximum_new_files: int | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    root = Path(project_root)
    output_root = root / research["output"]["base_dir"]
    cache_root = output_root / "intermediate" / "l2_1m_daily"
    raw_root = root / collection["source"]["raw_data_dir"]
    progress_path = output_root / "collection_progress.csv"
    state_path = output_root / "collection_state.json"
    cache_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)

    jobs = build_price_impact_jobs(research)
    progress = _load_progress(progress_path)
    progress = _recover_completed_caches(progress, jobs, cache_root)
    _atomic_csv(progress, progress_path)
    completed = _valid_completed_keys(progress, cache_root)
    recorded_complete = _recorded_complete_keys(progress)
    terminal = _terminal_keys(progress)
    new_files = 0

    for sample_date, date_jobs in jobs.groupby("date", sort=True):
        pending = [
            symbol
            for symbol in date_jobs["symbol"]
            if (sample_date, symbol) not in terminal
            or (
                (sample_date, symbol) in recorded_complete
                and not _cache_valid(_cache_path(cache_root, symbol, sample_date))
            )
        ]
        if not pending:
            continue
        catalog = fetch_archive_catalog_for_dates(
            pending,
            [sample_date],
            endpoint=collection["source"]["catalog_endpoint"],
            module=str(research["data"]["l2_module"]),
            instrument_type=str(research["data"]["instrument_type"]),
            timeout_seconds=int(collection["source"]["timeout_seconds"]),
        )
        for row in catalog.loc[~catalog["available"]].itertuples(index=False):
            record = {
                "date": sample_date,
                "symbol": str(row.symbol),
                "status": "unavailable",
                "quality_passed": False,
                "quality_failures": "archive_unavailable",
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            progress = _upsert(progress, record)
            terminal.add((sample_date, str(row.symbol)))
            _atomic_csv(progress, progress_path)
            _write_state(jobs, progress, "running", state_path, progress_callback)

        for row in catalog.loc[catalog["available"]].sort_values("size_mb").itertuples(
            index=False
        ):
            if maximum_new_files is not None and new_files >= maximum_new_files:
                return _write_state(
                    jobs, progress, "paused", state_path, progress_callback
                )
            symbol = str(row.symbol)
            archive_path = raw_root / symbol / str(row.filename)
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            download_archive(
                str(row.url),
                archive_path,
                float(row.size_mb),
                timeout_seconds=int(collection["source"]["timeout_seconds"]),
            )
            archive_sha256 = _sha256(archive_path)
            summary_frame, samples = audit_l2_archive(
                archive_path,
                expected_symbol=symbol,
                expected_date=sample_date,
                sample_interval_seconds=int(
                    collection["collection"]["sample_interval_seconds"]
                ),
                sweep_quote_amounts=collection["collection"]["sweep_quote_amounts"],
            )
            summary = summary_frame.iloc[0].to_dict()
            quality_passed, failures = _quality_passed(summary, collection)
            features = extract_execution_minute_features(samples, symbol, sample_date)
            cache_path = _cache_path(cache_root, symbol, sample_date)
            metadata_path = cache_path.with_suffix(".metadata.json")
            _atomic_parquet(features, cache_path)
            metadata = {
                "date": sample_date,
                "symbol": symbol,
                "status": "complete",
                "quality_passed": quality_passed,
                "quality_failures": failures,
                "archive_filename": str(row.filename),
                "archive_size_bytes": archive_path.stat().st_size,
                "archive_sha256": archive_sha256,
                "raw_rows": int(summary["rows"]),
                "sample_count": int(summary["sample_count"]),
                "cache_rows": len(features),
                "cache_sha256": _sha256(cache_path),
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_json(metadata, metadata_path)
            if collection["source"]["delete_archive_after_cache"]:
                archive_path.unlink()
            progress = _upsert(progress, metadata)
            completed.add((sample_date, symbol))
            recorded_complete.add((sample_date, symbol))
            terminal.add((sample_date, symbol))
            new_files += 1
            _atomic_csv(progress, progress_path)
            _write_state(jobs, progress, "running", state_path, progress_callback)

    _assemble_l2_panel(cache_root, progress, output_root)
    _collect_flow_monthly_caches(research, root, output_root)
    return _write_state(jobs, progress, "complete", state_path, progress_callback)


def _collect_flow_monthly_caches(
    research: Mapping,
    root: Path,
    output_root: Path,
) -> None:
    start = pd.Timestamp(research["data"]["start_inclusive"])
    end = pd.Timestamp(research["data"]["end_exclusive"])
    archive_root = (root / research["data"]["flow_archive_root"]).resolve()
    cache_root = output_root / "intermediate" / "flow_1m_monthly"
    cache_root.mkdir(parents=True, exist_ok=True)
    months = pd.date_range(
        start.floor("D").replace(day=1),
        (end - pd.Timedelta(days=1)).replace(day=1),
        freq="MS",
    )
    frames = []
    for symbol in research["data"]["symbols"]:
        for month in months:
            month_key = month.strftime("%Y-%m")
            archive = archive_root / symbol / f"{symbol}-trades-{month_key}.zip"
            cache = cache_root / symbol / f"{symbol}_{month_key}_1m.parquet"
            if not cache.is_file():
                if not archive.is_file():
                    raise FileNotFoundError(f"Missing local flow archive: {archive}")
                frame, _ = aggregate_okx_month_archive(
                    archive, symbol, interval_minutes=1, chunksize=500_000
                )
                _atomic_parquet(frame, cache)
            frames.append(pd.read_parquet(cache))
    panel = pd.concat(frames, ignore_index=True)
    panel["bucket_start"] = pd.to_datetime(panel["bucket_start"], utc=True)
    panel = panel.loc[
        panel["bucket_start"].ge(start) & panel["bucket_start"].lt(end)
    ].copy()
    _atomic_parquet(
        panel.sort_values(["bucket_start", "symbol"]),
        output_root / "intermediate" / "flow_feature_panel_1m.parquet",
    )


def _assemble_l2_panel(cache_root: Path, progress: pd.DataFrame, output_root: Path) -> None:
    valid = progress.loc[
        progress["status"].eq("complete") & progress["quality_passed"].eq(True)
    ]
    frames = [
        pd.read_parquet(_cache_path(cache_root, row.symbol, row.date))
        for row in valid.itertuples(index=False)
    ]
    if not frames:
        raise ValueError("No quality-passing L2 caches available")
    panel = pd.concat(frames, ignore_index=True).sort_values(
        ["bucket_start", "symbol"]
    )
    _atomic_parquet(
        panel, output_root / "intermediate" / "l2_feature_panel_1m.parquet"
    )


def _quality_passed(summary: Mapping, collection: Mapping) -> tuple[bool, str]:
    settings = collection["collection"]
    checks = {
        "snapshot": int(summary["snapshots"]) >= 1,
        "initial_depth": min(
            int(summary["initial_ask_levels"]), int(summary["initial_bid_levels"])
        )
        >= int(settings["minimum_initial_levels_per_side"]),
        "start": int(summary["start_delay_ms"]) <= int(settings["maximum_start_delay_ms"]),
        "end": int(summary["end_early_ms"]) <= int(settings["maximum_end_early_ms"]),
        "parse": int(summary["parse_errors"]) == 0,
        "ordered": int(summary["out_of_order_rows"]) == 0,
        "crossed": int(summary["sampled_crossed_books"]) == 0,
        "empty": int(summary["sampled_empty_books"]) == 0,
        "minutes": int(summary["sample_count"]) == 1_440,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return not failed, ";".join(failed)


def _load_progress(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"date": str, "symbol": str, "status": str})
    if "quality_passed" in frame:
        frame["quality_passed"] = (
            frame["quality_passed"].astype(str).str.lower().eq("true")
        )
    return frame


def _upsert(progress: pd.DataFrame, record: dict) -> pd.DataFrame:
    if progress.empty:
        return pd.DataFrame([record])
    keep = ~(
        progress["date"].eq(record["date"])
        & progress["symbol"].eq(record["symbol"])
    )
    return pd.concat([progress.loc[keep], pd.DataFrame([record])], ignore_index=True)


def _terminal_keys(progress: pd.DataFrame) -> set[tuple[str, str]]:
    if progress.empty:
        return set()
    terminal = progress["status"].isin(["complete", "unavailable"])
    return set(zip(progress.loc[terminal, "date"], progress.loc[terminal, "symbol"]))


def _recorded_complete_keys(progress: pd.DataFrame) -> set[tuple[str, str]]:
    if progress.empty:
        return set()
    complete = progress["status"].eq("complete")
    return set(zip(progress.loc[complete, "date"], progress.loc[complete, "symbol"]))


def _valid_completed_keys(
    progress: pd.DataFrame,
    cache_root: Path,
) -> set[tuple[str, str]]:
    if progress.empty:
        return set()
    complete = progress.loc[progress["status"].eq("complete")]
    return {
        (str(row.date), str(row.symbol))
        for row in complete.itertuples(index=False)
        if _cache_valid(_cache_path(cache_root, str(row.symbol), str(row.date)))
    }


def _recover_completed_caches(
    progress: pd.DataFrame,
    jobs: pd.DataFrame,
    cache_root: Path,
) -> pd.DataFrame:
    recorded = _recorded_complete_keys(progress)
    for row in jobs.itertuples(index=False):
        key = (str(row.date), str(row.symbol))
        cache_path = _cache_path(cache_root, key[1], key[0])
        metadata_path = cache_path.with_suffix(".metadata.json")
        if key in recorded or not _cache_valid(cache_path):
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        progress = _upsert(progress, metadata)
    return progress


def _write_state(
    jobs: pd.DataFrame,
    progress: pd.DataFrame,
    status: str,
    path: Path,
    callback: Callable[[dict], None] | None,
) -> dict:
    terminal = progress["status"].isin(["complete", "unavailable"]) if not progress.empty else pd.Series(dtype=bool)
    payload = {
        "status": status,
        "total_files": len(jobs),
        "completed_files": int(terminal.sum()),
        "quality_passing_files": int(
            (
                progress.get("quality_passed", pd.Series(dtype=bool)).eq(True)
                & progress.get("status", pd.Series(dtype=str)).eq("complete")
            ).sum()
        ),
        "unavailable_files": int(
            progress.get("status", pd.Series(dtype=str)).eq("unavailable").sum()
        ),
        "progress_share": float(terminal.sum() / len(jobs)),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(payload, path)
    if callback is not None:
        callback(payload)
    return payload


def _cache_path(cache_root: Path, symbol: str, sample_date: str) -> Path:
    return cache_root / symbol / f"{symbol}_{sample_date}_1m.parquet"


def _cache_valid(path: Path) -> bool:
    metadata_path = path.with_suffix(".metadata.json")
    if not path.is_file() or not metadata_path.is_file():
        return False
    try:
        frame = pd.read_parquet(
            path, columns=["bucket_start", "symbol", "midpoint", "sample_date"]
        )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        len(frame) == 1_440
        and not frame["bucket_start"].duplicated().any()
        and frame["symbol"].eq(str(metadata.get("symbol"))).all()
        and frame["sample_date"].eq(str(metadata.get("date"))).all()
        and metadata.get("cache_sha256") == _sha256(path)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.sort_values(["date", "symbol"]).to_csv(temporary, index=False)
    temporary.replace(path)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _atomic_json(payload: Mapping, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
