from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = (
    PROJECT_ROOT
    / "outputs/v2/price_impact_residual_v1/collection_state.json"
)


def main() -> None:
    if not STATE_PATH.is_file():
        print(json.dumps({"status": "not_started"}, ensure_ascii=False, indent=2))
        return
    print(STATE_PATH.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    main()
