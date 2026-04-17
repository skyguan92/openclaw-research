"""Stitch SWE-bench harness results back into existing swe_*.json records.

The existing swebench_adapter.import_evaluation_summary writes standalone
suc_*.json records (one per instance), but downstream analysis wants
resolved/harness_status on the matching swe_*.json token record. This
module does that join.
"""

from __future__ import annotations

import json
from pathlib import Path

RAW_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "raw"


def _status_map(report: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for iid in report.get("resolved_ids", []) or []:
        mapping[iid] = "pass"
    for iid in report.get("unresolved_ids", []) or []:
        mapping[iid] = "fail"
    for iid in report.get("error_ids", []) or []:
        mapping[iid] = "error"
    for iid in report.get("empty_patch_ids", []) or []:
        mapping.setdefault(iid, "fail")
    return mapping


def _swe_record_path(raw_dir: Path, instance_id: str, agent: str, run_group: str) -> Path:
    return raw_dir / f"swe_{instance_id}_{agent}_{run_group}.json"


def backfill_harness_outcomes(
    report: dict,
    *,
    agent: str,
    run_group: str,
    raw_dir: Path = RAW_DIR_DEFAULT,
) -> list[Path]:
    """Add metrics.resolved + metrics.harness_status to every matching record.

    Returns the list of record paths that were updated.
    """
    statuses = _status_map(report)
    updated: list[Path] = []

    for instance_id, status in statuses.items():
        record_path = _swe_record_path(raw_dir, instance_id, agent, run_group)
        if not record_path.exists():
            continue

        data = json.loads(record_path.read_text())
        metrics = dict(data.get("metrics") or {})
        metrics["resolved"] = status == "pass"
        metrics["harness_status"] = status
        data["metrics"] = metrics
        record_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        updated.append(record_path)

    return updated
