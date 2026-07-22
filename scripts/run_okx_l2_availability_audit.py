from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from okx_l2_audit import (
    audit_l2_archive,
    build_audit_report,
    build_quality_decisions,
    download_archive,
    estimate_resource_requirements,
    fetch_archive_catalog,
    validate_audit_config,
)
from utils import (
    load_config,
    prepare_output_dirs,
    save_config_snapshot,
    save_dataframe,
    save_input_manifest,
    save_json,
    save_provenance_manifest,
    save_text,
    setup_logging,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OKX 400레벨 L2 데이터 가용성·품질 감사")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs/research/okx_l2_availability_audit_v1.yaml"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    setup_logging(config.get("logging", {}).get("level", "INFO"))
    validate_audit_config(config)
    output = prepare_output_dirs(PROJECT_ROOT, config)

    LOGGER.info("Querying fixed OKX L2 archive catalog sample")
    catalog = fetch_archive_catalog(config)
    pilot = catalog.loc[
        catalog["symbol"].eq(config["data"]["pilot_symbol"])
        & catalog["date"].eq(config["data"]["pilot_date"])
    ]
    if len(pilot) != 1 or not bool(pilot.iloc[0]["available"]):
        raise RuntimeError("The fixed pilot archive is unavailable")
    pilot_row = pilot.iloc[0]
    archive_path = PROJECT_ROOT / config["data"]["archive_dir"] / pilot_row["filename"]
    LOGGER.info("Ensuring pilot archive exists: %s", archive_path.name)
    download_archive(pilot_row["url"], archive_path, float(pilot_row["size_mb"]))

    LOGGER.info("Streaming and reconstructing the full pilot order book")
    audit_summary, samples = audit_l2_archive(
        archive_path,
        expected_symbol=config["data"]["pilot_symbol"],
        expected_date=config["data"]["pilot_date"],
        sample_interval_seconds=int(config["audit"]["sample_interval_seconds"]),
    )
    resources = estimate_resource_requirements(catalog, config)
    decisions = build_quality_decisions(catalog, audit_summary, config)
    report = build_audit_report(catalog, audit_summary, resources, decisions)

    save_dataframe(catalog, output["base"] / "archive_catalog_sample.csv", index=False)
    save_dataframe(audit_summary, output["base"] / "pilot_audit_summary.csv", index=False)
    save_dataframe(samples, output["base"] / "pilot_minute_book_features.csv", index=False)
    save_dataframe(resources, output["base"] / "resource_estimates.csv", index=False)
    save_dataframe(decisions, output["base"] / "quality_decisions.csv", index=False)
    save_text(report, output["base"] / "okx_l2_availability_audit_report.md")
    save_config_snapshot(config, output["base"] / "config_snapshot.yaml")
    manifest = save_input_manifest([archive_path], output["base"] / "input_manifest.json")
    save_provenance_manifest(
        config,
        output["base"] / "provenance.json",
        schema_version=1,
        pipeline_version="okx_l2_availability_audit_v1",
        statistical_method="fixed-date catalog audit; full-day snapshot and delta reconstruction",
        input_manifest_path=manifest,
    )
    ready = bool(
        decisions.loc[decisions["quality_gate"].eq("bounded_l2_study_ready"), "passed"].iloc[0]
    )
    run_summary = {
        "study": "okx_l2_availability_audit_v1",
        "catalog_files_available": int(catalog["available"].sum()),
        "catalog_files_expected": len(catalog),
        "pilot_rows": int(audit_summary.iloc[0]["rows"]),
        "bounded_l2_study_ready": ready,
        "directional_alpha_tested": False,
    }
    save_json(run_summary, output["base"] / "run_summary.json")
    LOGGER.info("Audit complete: %s", json.dumps(run_summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
