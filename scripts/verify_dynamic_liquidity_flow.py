from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from dynamic_liquidity_flow import (
    build_dynamic_decisions,
    validate_dynamic_config,
    verify_dynamic_seal,
)
from stats_utils import benjamini_hochberg
from utils import load_config, save_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="동적 L2→주문흐름 결과 검증")
    parser.add_argument(
        "--config",
        default=str(
            PROJECT_ROOT / "configs/research/dynamic_liquidity_flow_v1.yaml"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    validate_dynamic_config(config)
    seal = verify_dynamic_seal(
        PROJECT_ROOT / config["protocol"]["path"],
        config_path,
        PROJECT_ROOT / config["protocol"]["seal_path"],
    )
    output = PROJECT_ROOT / config["output"]["base_dir"]
    required = [
        "analysis_coverage.csv",
        "dynamic_test_summary.csv",
        "bootstrap_draws.csv",
        "development_coefficients.csv",
        "decisions.csv",
        "dynamic_liquidity_flow_report.md",
        "plots/dynamic_mse_improvement.png",
        "config_snapshot.yaml",
        "protocol_snapshot.md",
        "preregistration_seal_verification.json",
        "analysis_input_manifest.json",
        "analysis_provenance.json",
        "analysis_run_summary.json",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing dynamic artifacts: {', '.join(missing)}")
    summary = pd.read_csv(output / "dynamic_test_summary.csv")
    draws = pd.read_csv(output / "bootstrap_draws.csv")
    expected_draws = len(summary) * int(config["analysis"]["bootstrap_repetitions"])
    if len(summary) != 6 or len(draws) != expected_draws:
        raise ValueError("Dynamic test family or bootstrap size differs from seal")
    recomputed_p = []
    recomputed_lower = []
    recomputed_upper = []
    for row in summary.itertuples(index=False):
        group = draws.loc[
            draws["direction"].eq(row.direction)
            & draws["horizon_minutes"].eq(row.horizon_minutes),
            "mse_improvement",
        ]
        recomputed_p.append((1 + group.le(0).sum()) / (len(group) + 1))
        recomputed_lower.append(group.quantile(0.025))
        recomputed_upper.append(group.quantile(0.975))
    if not np.allclose(summary["p_value_one_sided"], recomputed_p):
        raise ValueError("Dynamic p-values do not match public bootstrap draws")
    if not np.allclose(summary["bootstrap_ci_lower"], recomputed_lower):
        raise ValueError("Dynamic lower intervals do not match public draws")
    if not np.allclose(summary["bootstrap_ci_upper"], recomputed_upper):
        raise ValueError("Dynamic upper intervals do not match public draws")
    recomputed_q = benjamini_hochberg(pd.Series(recomputed_p))
    if not np.allclose(summary["q_value_bh_fdr"], recomputed_q):
        raise ValueError("Dynamic q-values do not match the six-test family")
    recomputed_decisions = build_dynamic_decisions(summary, config)
    stored_decisions = pd.read_csv(output / "decisions.csv")
    pd.testing.assert_frame_equal(
        stored_decisions, recomputed_decisions, check_dtype=False
    )
    verification = {
        "verified": True,
        "preregistration_seal_verified": bool(seal["verified"]),
        "artifact_count_checked": len(required),
        "test_family_size": len(summary),
        "bootstrap_draws": len(draws),
        "decision_recomputation_match": True,
        "primary_supported": bool(stored_decisions.iloc[0]["passed"]),
        "directional_return_alpha_tested": False,
    }
    save_json(verification, output / "analysis_verification.json")
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
