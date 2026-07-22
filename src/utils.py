from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml


def load_config(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def prepare_output_dirs(project_root: Path, config: dict) -> dict[str, Path]:
    base = project_root / config.get("output", {}).get("base_dir", "outputs")
    paths = {
        "base": base,
        "intermediate": base / "intermediate",
        "plots": base / "plots",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _ensure_parent(path: str | Path) -> Path:
    result = Path(path)
    result.parent.mkdir(parents=True, exist_ok=True)
    return result


def save_dataframe(frame: pd.DataFrame | pd.Series, path: str | Path, index: bool = True) -> None:
    destination = _ensure_parent(path)
    if isinstance(frame, pd.Series):
        frame.to_frame().to_csv(destination, index=index)
    else:
        frame.to_csv(destination, index=index)


def save_text(content: str, path: str | Path) -> None:
    _ensure_parent(path).write_text(content, encoding="utf-8")


def save_json(content: dict, path: str | Path) -> None:
    with _ensure_parent(path).open("w", encoding="utf-8") as handle:
        json.dump(content, handle, indent=2, ensure_ascii=False)


def save_config_snapshot(config: dict, path: str | Path) -> None:
    with _ensure_parent(path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)


def save_input_manifest(paths: Iterable[str | Path], path: str | Path) -> Path:
    entries = []
    for raw_path in paths:
        source = Path(raw_path)
        entries.append(
            {
                "path": _display_path(source),
                "size": source.stat().st_size,
                "sha256": _sha256(source),
            }
        )
    destination = _ensure_parent(path)
    save_json({"files": entries}, destination)
    return destination


def save_provenance_manifest(
    config: dict,
    path: str | Path,
    schema_version: int,
    pipeline_version: str,
    statistical_method: str,
    input_manifest_path: str | Path | None = None,
    random_seed: int | None = None,
    train_start: str | None = None,
    train_end: str | None = None,
    oos_start: str | None = None,
    oos_end: str | None = None,
) -> None:
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        git_commit = "unavailable"
    input_hash = (
        _sha256(Path(input_manifest_path))
        if input_manifest_path is not None and Path(input_manifest_path).is_file()
        else None
    )
    save_json(
        {
            "schema_version": int(schema_version),
            "pipeline_version": pipeline_version,
            "git_commit": git_commit,
            "config_sha256": hashlib.sha256(
                yaml.safe_dump(config, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "input_manifest_sha256": input_hash,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "train_start": train_start,
            "train_end": train_end,
            "oos_start": oos_start,
            "oos_end": oos_end,
            "statistical_method": statistical_method,
            "random_seed": random_seed,
        },
        path,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name
