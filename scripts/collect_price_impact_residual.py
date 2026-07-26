from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from price_impact_collection import (
    build_price_impact_jobs,
    collect_price_impact_features,
    validate_price_impact_collection_config,
)
from price_impact_study import validate_price_impact_config, verify_price_impact_seal
from utils import load_config, setup_logging


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="봉인된 H2 1분 L2·flow feature 수집")
    parser.add_argument(
        "--research-config",
        default=str(
            PROJECT_ROOT / "configs/research/price_impact_residual_v1.yaml"
        ),
    )
    parser.add_argument(
        "--collection-config",
        default=str(
            PROJECT_ROOT / "configs/collection/price_impact_residual_v1.yaml"
        ),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-new-files", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    research_path = Path(args.research_config).resolve()
    collection_path = Path(args.collection_config).resolve()
    research = load_config(research_path)
    collection = load_config(collection_path)
    setup_logging(collection.get("logging", {}).get("level", "INFO"))
    validate_price_impact_config(research)
    validate_price_impact_collection_config(research, collection)
    verify_price_impact_seal(
        PROJECT_ROOT / research["protocol"]["path"],
        research_path,
        PROJECT_ROOT / research["protocol"]["seal_path"],
    )
    jobs = build_price_impact_jobs(research)
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "files": len(jobs),
                    "days": int(jobs["date"].nunique()),
                    "symbols": int(jobs["symbol"].nunique()),
                    "raw_l2_deleted_after_cache": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    def log_progress(state: dict) -> None:
        LOGGER.info("Collection progress: %s", json.dumps(state, ensure_ascii=False))

    state = collect_price_impact_features(
        research,
        collection,
        PROJECT_ROOT,
        maximum_new_files=args.max_new_files,
        progress_callback=log_progress,
    )
    LOGGER.info("Collection stopped: %s", json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
