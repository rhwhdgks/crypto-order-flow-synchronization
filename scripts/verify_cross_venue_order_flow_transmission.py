from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from cross_venue_order_flow import (
    build_decisions,
    validate_frozen_config,
    verify_preregistration_seal,
)
from utils import load_config, save_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="교차거래소 주문흐름 결과 검증")
    parser.add_argument(
        "--config",
        default=str(
            PROJECT_ROOT
            / "configs"
            / "research"
            / "cross_venue_order_flow_transmission_v1.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_frozen_config(config)
    seal = verify_preregistration_seal(
        PROJECT_ROOT / config["protocol"]["path"],
        config_path,
        PROJECT_ROOT / config["protocol"]["seal_path"],
    )
    output = PROJECT_ROOT / config["output"]["base_dir"]
    required = [
        "source_coverage.csv",
        "synchronization_summary.csv",
        "synchronization_null_draws.parquet",
        "lead_lag_summary.csv",
        "lead_lag_direction_bootstrap.csv",
        "decisions.csv",
        "cross_venue_order_flow_report.md",
        "run_summary.json",
        "config_snapshot.yaml",
        "protocol_snapshot.md",
        "input_manifest.json",
        "provenance.json",
        "plots/cross_venue_common_flow.png",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing result artifacts: {', '.join(missing)}")

    synchronization = pd.read_csv(output / "synchronization_summary.csv")
    lead_lag = pd.read_csv(output / "lead_lag_summary.csv")
    bootstrap = pd.read_csv(output / "lead_lag_direction_bootstrap.csv")
    stored = pd.read_csv(output / "decisions.csv")
    recomputed = build_decisions(synchronization, lead_lag, bootstrap, config)
    pd.testing.assert_frame_equal(stored, recomputed, check_dtype=False)

    if len(synchronization) != 2 or synchronization["q_value_bh_fdr"].isna().any():
        raise ValueError("Synchronization family is incomplete")
    if len(lead_lag) != 6 or lead_lag["q_value_bh_fdr"].isna().any():
        raise ValueError("Lead-lag family is incomplete")
    if len(bootstrap) != int(config["analysis"]["bootstrap_repetitions"]):
        raise ValueError("Bootstrap repetitions do not match frozen config")
    nulls = pd.read_parquet(output / "synchronization_null_draws.parquet")
    if len(nulls) != int(config["analysis"]["null_repetitions"]):
        raise ValueError("Null repetitions do not match frozen config")
    if not stored.loc[
        stored["decision"].eq("intentional_herding_identification"), "passed"
    ].eq(False).all():
        raise ValueError("Intentional-herding limitation was not preserved")
    if not stored.loc[stored["decision"].eq("directional_alpha"), "classification"].eq(
        "not_tested"
    ).all():
        raise ValueError("Directional alpha must remain untested")

    run_summary = json.loads((output / "run_summary.json").read_text(encoding="utf-8"))
    if run_summary.get("news_reddit_sentiment_used") is not False:
        raise ValueError("Forbidden external text data were marked as used")
    verification = {
        "verified": True,
        "preregistration_seal_verified": bool(seal["verified"]),
        "artifact_count_checked": len(required),
        "decision_recomputation_match": True,
        "synchronization_family_size": len(synchronization),
        "lead_lag_family_size": len(lead_lag),
        "null_repetitions": len(nulls),
        "bootstrap_repetitions": len(bootstrap),
        "intentional_herding_identified": False,
        "directional_alpha_tested": False,
        "news_reddit_sentiment_used": False,
    }
    save_json(verification, output / "verification.json")
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
