"""Closed-enum outcome classifier for SWE-bench run records.

We look at the `swe_*.json` record produced by `swebench_adapter` (possibly
after `backfill_harness_outcomes` has stitched in the harness verdict) and
assign one of seven terminal outcomes — or `None` if the run is still
waiting for the harness to report.

Enum (closed):
  passed           — harness says resolved
  test_failed      — agent produced a patch, harness says unresolved/error
  timeout          — adapter hit wall-clock timeout
  silent_exit      — adapter ended with no patch and no usable output
  crash            — adapter surfaced an explicit error (API 4xx/5xx, parse)
  quota_403        — billing/quota/rate-limit response from the provider
  telemetry_error  — adapter token accounting is internally inconsistent

Adapter-layer failures take precedence over the harness verdict: a run that
hit its quota cap never got a real chance to pass, so tagging it
`test_failed` would mislead downstream analyses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adapters.kimi_pricing import TokenCounts, extract_token_counts, sanity_check_token_sum


class Outcome(str, Enum):
    PASSED = "passed"
    TEST_FAILED = "test_failed"
    TIMEOUT = "timeout"
    SILENT_EXIT = "silent_exit"
    CRASH = "crash"
    QUOTA_403 = "quota_403"
    TELEMETRY_ERROR = "telemetry_error"


@dataclass(frozen=True)
class ClassifyResult:
    outcome: Outcome | None  # None = harness hasn't scored the patch yet
    reason: str              # human-readable justification
    harness_available: bool  # did the record have harness_status?


_QUOTA_MARKERS = (
    "API Error: 403",
    "API Error: 429",
    "rate limit",
    "rate_limit",
    "quota",
    "insufficient_quota",
    "billing",
)

_CRASH_API_PREFIX = "API Error:"

_SILENT_MARKERS = (
    "empty output",
    "silent crash",
    "JSON payload not found",
    "failed to parse",
)


def _error_text(record: dict) -> str:
    err = record.get("error") or ""
    return str(err)


def _has_patch(record: dict) -> bool:
    notes = str(record.get("notes") or "")
    if "patch_len=0" in notes:
        return False
    if "patch_len=" in notes:
        return True
    metrics = record.get("metrics") or {}
    return bool(metrics.get("task_completed"))


def _token_counts(record: dict) -> TokenCounts | None:
    """Return normalized token counts, or None if the record has no tokens
    at all (too early to evaluate telemetry consistency)."""
    usage = record.get("usage_details") or {}
    metrics = record.get("metrics") or {}
    if not usage and not metrics.get("tokens_in"):
        return None
    return extract_token_counts(record)


def classify_outcome(record: dict) -> ClassifyResult:
    """Classify a single `swe_*.json` record.

    The record may or may not have `metrics.harness_status`; if it doesn't,
    we still return an adapter-layer failure when one is present. Otherwise
    we return `outcome=None` so the caller knows the run is pending.
    """
    err = _error_text(record)
    err_lower = err.lower()
    metrics = record.get("metrics") or {}
    harness_status = metrics.get("harness_status")  # "pass" / "fail" / "error" / None
    harness_available = harness_status is not None

    # 1. Quota / rate limit — check before generic "API Error:" so quota
    #    doesn't get bucketed as a crash.
    if any(marker.lower() in err_lower for marker in _QUOTA_MARKERS):
        return ClassifyResult(Outcome.QUOTA_403, f"quota marker in error: {err[:80]}", harness_available)

    # 2. Telemetry inconsistency — our own bookkeeping is broken; nothing
    #    downstream of this is trustworthy, so flag it before any
    #    success/failure decision.
    tc = _token_counts(record)
    if tc is not None:
        reported_in = int(metrics.get("tokens_in") or 0)
        ok, msg = sanity_check_token_sum(tc, reported_in)
        if not ok:
            return ClassifyResult(Outcome.TELEMETRY_ERROR, f"token sum drift: {msg}", harness_available)

    # 3. Explicit timeout.
    if err.strip().lower() == "timeout" or "timeout" in err_lower:
        return ClassifyResult(Outcome.TIMEOUT, f"timeout error: {err[:80]}", harness_available)

    # 4. Silent exit — runner gave up with no patch/no structured output.
    if any(marker.lower() in err_lower for marker in _SILENT_MARKERS):
        return ClassifyResult(Outcome.SILENT_EXIT, f"silent-exit marker: {err[:80]}", harness_available)

    # 5. Generic adapter crash — API 4xx/5xx that isn't quota, or any other
    #    non-empty error string that didn't match above.
    if err and err.startswith(_CRASH_API_PREFIX):
        return ClassifyResult(Outcome.CRASH, f"adapter crash: {err[:80]}", harness_available)
    if err:
        return ClassifyResult(Outcome.CRASH, f"adapter error: {err[:80]}", harness_available)

    # 6. No explicit error, but also no patch produced — treat as silent exit
    #    so it doesn't get confused with test_failed (which requires a patch).
    if not _has_patch(record):
        return ClassifyResult(Outcome.SILENT_EXIT, "no patch and no error", harness_available)

    # 7. Adapter succeeded; defer to harness.
    if harness_status == "pass":
        return ClassifyResult(Outcome.PASSED, "harness resolved", True)
    if harness_status in ("fail", "error"):
        return ClassifyResult(Outcome.TEST_FAILED, f"harness status={harness_status}", True)

    # 8. Harness not scored yet — caller should re-run classify after
    #    import_evaluation_summary + backfill_harness_outcomes.
    return ClassifyResult(None, "harness pending", False)
