from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from okx_l2_audit import validate_common_liquidity_config, verify_common_liquidity_seal
from okx_l2_collection import (
    build_collection_jobs,
    collect_l2_features,
    validate_collection_config,
)
from utils import load_config, setup_logging


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="봉인된 OKX L2 180일 feature 수집")
    parser.add_argument(
        "--research-config",
        default=str(PROJECT_ROOT / "configs/research/common_liquidity_order_flow_v1.yaml"),
    )
    parser.add_argument(
        "--collection-config",
        default=str(PROJECT_ROOT / "configs/collection/okx_l2_common_liquidity_v1.yaml"),
    )
    parser.add_argument("--execute", action="store_true", help="실제 다운로드와 복원을 실행")
    parser.add_argument("--max-new-files", type=int, default=None, help="smoke/resume용 신규 파일 제한")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    research_path = Path(args.research_config).resolve()
    collection_path = Path(args.collection_config).resolve()
    research = load_config(research_path)
    collection = load_config(collection_path)
    setup_logging(collection.get("logging", {}).get("level", "INFO"))
    validate_common_liquidity_config(research)
    validate_collection_config(research, collection)
    verify_common_liquidity_seal(
        PROJECT_ROOT / research["protocol"]["path"],
        research_path,
        PROJECT_ROOT / research["protocol"]["seal_path"],
    )
    jobs = build_collection_jobs(research)
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "files": len(jobs),
                    "days": int(jobs["date"].nunique()),
                    "symbols": int(jobs["symbol"].nunique()),
                    "raw_archives_deleted_after_verified_cache": True,
                    "run_command": "PYTHONPATH=src python scripts/collect_common_liquidity_l2.py --execute",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    def log_progress(state: dict) -> None:
        LOGGER.info("Collection progress: %s", json.dumps(state, ensure_ascii=False))

    state = collect_l2_features(
        research,
        collection,
        PROJECT_ROOT,
        maximum_new_files=args.max_new_files,
        progress_callback=log_progress,
    )
    LOGGER.info("Collection stopped: %s", json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
