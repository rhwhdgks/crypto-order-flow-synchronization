from __future__ import annotations

import json
from pathlib import Path

from okx_l2_collection import collection_status
from utils import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = load_config(PROJECT_ROOT / "configs/research/common_liquidity_order_flow_v1.yaml")
    state = collection_status(PROJECT_ROOT, config)
    print(json.dumps(state, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
