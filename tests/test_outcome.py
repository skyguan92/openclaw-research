"""Outcome classification from raw records."""

from analysis.outcome import Outcome, classify


def test_harness_resolved():
    record = {
        "metrics": {"resolved": True, "harness_status": "pass"},
        "notes": "SWE-bench instance, mode=workspace, patch_len=4291",
    }
    assert classify(record) is Outcome.HARNESS_PASSED


def test_harness_unresolved():
    record = {
        "metrics": {"resolved": False, "harness_status": "fail"},
        "notes": "patch_len=1000",
    }
    assert classify(record) is Outcome.HARNESS_FAILED


def test_harness_error():
    record = {
        "metrics": {"resolved": False, "harness_status": "error"},
        "notes": "patch_len=1000",
    }
    assert classify(record) is Outcome.HARNESS_ERROR


def test_empty_patch():
    record = {
        "metrics": {"task_completed": False},
        "notes": "patch_len=0",
    }
    assert classify(record) is Outcome.EMPTY_PATCH


def test_agent_timeout():
    record = {
        "metrics": {"task_completed": False},
        "error": "timeout",
        "notes": "patch_len=0",
    }
    assert classify(record) is Outcome.AGENT_TIMEOUT


def test_agent_empty_output():
    record = {
        "metrics": {"task_completed": False},
        "error": "empty output (possible silent crash)",
        "notes": "patch_len=0",
    }
    assert classify(record) is Outcome.AGENT_EMPTY_OUTPUT


def test_unscored_patch_present():
    """Patch was produced, but harness has not run yet."""
    record = {
        "metrics": {"task_completed": True},
        "notes": "patch_len=2157",
    }
    assert classify(record) is Outcome.UNSCORED


def test_unknown_fallback():
    assert classify({}) is Outcome.UNKNOWN


def test_outcome_bool_helpers():
    assert Outcome.HARNESS_PASSED.is_success()
    assert not Outcome.HARNESS_FAILED.is_success()
    assert Outcome.AGENT_TIMEOUT.is_infra_failure() is False
    assert Outcome.HARNESS_ERROR.is_infra_failure() is True
