from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from price_impact_study import (
    validate_price_impact_config,
    verify_price_impact_seal,
)
from utils import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="H2 가격충격 잔차 산출물 검증")
    parser.add_argument(
        "--config",
        default=str(
            PROJECT_ROOT / "configs/research/price_impact_residual_v1.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_price_impact_config(config)
    verify_price_impact_seal(
        PROJECT_ROOT / config["protocol"]["path"],
        config_path,
        PROJECT_ROOT / config["protocol"]["seal_path"],
    )
    output = PROJECT_ROOT / config["output"]["base_dir"]
    required = [
        "analysis_coverage.csv",
        "development_coefficients.csv",
        "event_ledger.csv",
        "event_returns.csv",
        "inferential_summary.csv",
        "decisions.csv",
        "price_impact_residual_report.md",
        "analysis_input_manifest.json",
        "analysis_provenance.json",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing H2 outputs: {', '.join(missing)}")
    decisions = pd.read_csv(output / "decisions.csv")
    inference = pd.read_csv(output / "inferential_summary.csv")
    checks = {
        "sealed_protocol": True,
        "all_horizons_reported": set(inference["horizon_minutes"]) == {1, 5, 15, 30},
        "primary_gate_present": decisions["gate"].eq("primary_supported").any(),
        "fdr_finite": inference["q_value"].notna().all(),
    }
    checks = {name: bool(passed) for name, passed in checks.items()}
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
