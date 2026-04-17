"""Backfill harness resolution into existing swe_*.json token records."""

import json
from pathlib import Path

import pytest

from analysis.backfill import backfill_harness_outcomes


@pytest.fixture
def raw_dir_with_swe_records(tmp_path: Path, openclaw_swe_record) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()

    resolved = dict(openclaw_swe_record)
    resolved["task_id"] = "astropy__astropy-12907"
    resolved["run_group"] = "demo_r01"
    resolved["agent"] = "openclaw"
    (raw / "swe_astropy__astropy-12907_openclaw_demo_r01.json").write_text(
        json.dumps(resolved)
    )

    unresolved = dict(openclaw_swe_record)
    unresolved["task_id"] = "django__django-11099"
    unresolved["run_group"] = "demo_r01"
    unresolved["agent"] = "openclaw"
    unresolved["run_id"] = "swe_django__django-11099_openclaw_demo_r01"
    (raw / "swe_django__django-11099_openclaw_demo_r01.json").write_text(
        json.dumps(unresolved)
    )

    return raw


def test_backfill_marks_resolved_and_unresolved(raw_dir_with_swe_records, harness_report):
    updated = backfill_harness_outcomes(
        harness_report,
        agent="openclaw",
        run_group="demo_r01",
        raw_dir=raw_dir_with_swe_records,
    )
    assert len(updated) == 2

    resolved_path = raw_dir_with_swe_records / "swe_astropy__astropy-12907_openclaw_demo_r01.json"
    resolved_data = json.loads(resolved_path.read_text())
    assert resolved_data["metrics"]["resolved"] is True
    assert resolved_data["metrics"]["harness_status"] == "pass"

    unresolved_path = raw_dir_with_swe_records / "swe_django__django-11099_openclaw_demo_r01.json"
    unresolved_data = json.loads(unresolved_path.read_text())
    assert unresolved_data["metrics"]["resolved"] is False
    assert unresolved_data["metrics"]["harness_status"] == "fail"


def test_backfill_is_idempotent(raw_dir_with_swe_records, harness_report):
    backfill_harness_outcomes(
        harness_report,
        agent="openclaw",
        run_group="demo_r01",
        raw_dir=raw_dir_with_swe_records,
    )
    updated = backfill_harness_outcomes(
        harness_report,
        agent="openclaw",
        run_group="demo_r01",
        raw_dir=raw_dir_with_swe_records,
    )
    assert len(updated) == 2

    resolved_path = raw_dir_with_swe_records / "swe_astropy__astropy-12907_openclaw_demo_r01.json"
    resolved_data = json.loads(resolved_path.read_text())
    assert resolved_data["metrics"]["resolved"] is True


def test_backfill_skips_missing_records(raw_dir_with_swe_records, tmp_path):
    report = {
        "resolved_ids": ["nonexistent__repo-1"],
        "unresolved_ids": [],
        "error_ids": [],
        "empty_patch_ids": [],
        "submitted_ids": ["nonexistent__repo-1"],
    }
    updated = backfill_harness_outcomes(
        report,
        agent="openclaw",
        run_group="demo_r01",
        raw_dir=raw_dir_with_swe_records,
    )
    assert updated == []


def test_backfill_error_sets_harness_status_error(raw_dir_with_swe_records):
    report = {
        "resolved_ids": [],
        "unresolved_ids": [],
        "error_ids": ["astropy__astropy-12907"],
        "empty_patch_ids": [],
        "submitted_ids": ["astropy__astropy-12907"],
    }
    backfill_harness_outcomes(
        report,
        agent="openclaw",
        run_group="demo_r01",
        raw_dir=raw_dir_with_swe_records,
    )
    data = json.loads(
        (raw_dir_with_swe_records / "swe_astropy__astropy-12907_openclaw_demo_r01.json").read_text()
    )
    assert data["metrics"]["harness_status"] == "error"
    assert data["metrics"]["resolved"] is False
