"""Shared fixtures for research-infra tests."""

import pytest


@pytest.fixture
def openclaw_swe_record():
    """Representative openclaw SWE-bench record (no cache tokens)."""
    return {
        "run_id": "swe_astropy__astropy-12907_openclaw_demo_r01",
        "run_group": "demo_r01",
        "experiment_id": "demo",
        "agent": "openclaw",
        "task_id": "astropy__astropy-12907",
        "dimension": "token_efficiency",
        "round": 1,
        "runtime_profile": "memory-enabled",
        "metrics": {
            "tokens_in": 1000000,
            "tokens_out": 15000,
            "tokens_total": 1015000,
            "provider_tokens_total": 1015000,
            "runtime_tokens_total": 1015000,
            "task_completed": True,
            "tool_calls_count": 30,
            "latency_s": 400.0,
        },
        "notes": "SWE-bench instance, mode=repo-mentioned, patch_len=4291",
        "usage_details": {
            "provider_tokens_total": 1015000,
            "runtime_tokens_total": 1015000,
            "telemetry_source": "openclaw_session_events",
        },
    }


@pytest.fixture
def hermes_swe_record():
    """Representative hermes record (includes cache + reasoning tokens)."""
    return {
        "run_id": "swe_astropy__astropy-12907_hermes_demo_r01",
        "run_group": "demo_r01",
        "experiment_id": "demo",
        "agent": "hermes",
        "task_id": "astropy__astropy-12907",
        "dimension": "token_efficiency",
        "round": 1,
        "runtime_profile": "memory-enabled",
        "metrics": {
            "tokens_in": 19000,
            "tokens_out": 4500,
            "tokens_total": 413000,
            "task_completed": True,
            "tool_calls_count": 20,
            "latency_s": 350.0,
        },
        "notes": "SWE-bench instance, mode=repo-mentioned, patch_len=1606",
        "usage_details": {
            "tool_call_count": 20,
            "input_tokens": 19000,
            "output_tokens": 4500,
            "cache_read_tokens": 380000,
            "cache_write_tokens": 9500,
            "reasoning_tokens": 0,
            "provider_tokens_total": 23500,
            "runtime_tokens_total": 413000,
            "session_count": 1,
            "telemetry_source": "hermes_state_db",
        },
    }


@pytest.fixture
def claude_code_swe_record():
    """Representative claude-code record (cache tracked but excluded from tokens_total)."""
    return {
        "run_id": "swe_astropy__astropy-12907_claude-code_demo_r01",
        "run_group": "demo_r01",
        "experiment_id": "demo",
        "agent": "claude-code",
        "task_id": "astropy__astropy-12907",
        "dimension": "token_efficiency",
        "round": 1,
        "runtime_profile": "memory-enabled",
        "metrics": {
            "tokens_in": 1045000,
            "tokens_out": 9500,
            "tokens_total": 1054500,
            "task_completed": True,
            "tool_calls_count": 37,
            "latency_s": 321.0,
        },
        "notes": "SWE-bench instance, mode=repo-mentioned, patch_len=2157",
    }


@pytest.fixture
def harness_report():
    """Representative SWE-bench harness summary JSON."""
    return {
        "total_instances": 2,
        "submitted_instances": 2,
        "completed_instances": 2,
        "resolved_instances": 1,
        "unresolved_instances": 1,
        "empty_patch_instances": 0,
        "error_instances": 0,
        "submitted_ids": ["astropy__astropy-12907", "django__django-11099"],
        "resolved_ids": ["astropy__astropy-12907"],
        "unresolved_ids": ["django__django-11099"],
        "error_ids": [],
        "empty_patch_ids": [],
        "schema_version": 2,
    }
