"""Classify a raw record into a single canonical Outcome.

Old flag `metrics.task_completed` just means "agent CLI exited cleanly and
produced a non-empty patch", which is not the same as "SWE-bench harness
resolved this instance". This module is the single source of truth for
success/failure bucketing across analysis scripts.
"""

from __future__ import annotations

from enum import Enum


class Outcome(Enum):
    HARNESS_PASSED = "harness_passed"
    HARNESS_FAILED = "harness_failed"
    HARNESS_ERROR = "harness_error"
    EMPTY_PATCH = "empty_patch"
    AGENT_TIMEOUT = "agent_timeout"
    AGENT_EMPTY_OUTPUT = "agent_empty_output"
    UNSCORED = "unscored"
    UNKNOWN = "unknown"

    def is_success(self) -> bool:
        return self is Outcome.HARNESS_PASSED

    def is_infra_failure(self) -> bool:
        return self in (Outcome.HARNESS_ERROR,)


def classify(record: dict) -> Outcome:
    metrics = record.get("metrics", {}) or {}
    harness_status = metrics.get("harness_status")
    resolved = metrics.get("resolved")

    if harness_status == "pass" or resolved is True:
        return Outcome.HARNESS_PASSED
    if harness_status == "error":
        return Outcome.HARNESS_ERROR
    if harness_status == "fail" or resolved is False:
        return Outcome.HARNESS_FAILED

    error_text = (record.get("error") or "").lower()
    if "timeout" in error_text:
        return Outcome.AGENT_TIMEOUT
    if "empty output" in error_text or "silent crash" in error_text:
        return Outcome.AGENT_EMPTY_OUTPUT

    notes = record.get("notes") or ""
    if "patch_len=0" in notes:
        return Outcome.EMPTY_PATCH

    if metrics.get("task_completed") is True:
        return Outcome.UNSCORED

    return Outcome.UNKNOWN
