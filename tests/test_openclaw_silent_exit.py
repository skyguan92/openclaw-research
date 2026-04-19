"""Regression tests for openclaw silent-exit telemetry honesty.

Covers the post-fix behavior of `_run_openclaw_agent`: when a memory-enabled
round exits quickly with no new API activity, the adapter must NOT inherit
cumulative session-meta token counts from prior rounds. Records must show
zero tokens and an explicit silent-exit error.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from adapters import openclaw_workspace as ocw


class _StubCompleted:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_with_stubs(
    *,
    session_summary: dict,
    before_session: dict,
    after_session: dict,
    payload: dict | None,
    returncode: int = 0,
    stdout: str = "{}",
    stderr: str = "",
):
    """Drive `_run_openclaw_agent` with collaborator doubles."""
    def fake_ensure(*_, **__):
        return None

    def fake_subprocess_run(*_, **__):
        return _StubCompleted(returncode=returncode, stdout=stdout, stderr=stderr)

    def fake_read_events(*_, **__):
        return []

    def fake_snapshot(*_, **__):
        return before_session

    def fake_wait(*_, **__):
        return after_session

    def fake_summarize(_events):
        return session_summary

    def fake_extract(*_):
        return payload

    def fake_clean_env(**_):
        return {}

    with patch.object(ocw, "ensure_openclaw_agent", fake_ensure), \
         patch.object(ocw, "_snapshot_session_state", fake_snapshot), \
         patch.object(ocw, "_wait_for_session_state", fake_wait), \
         patch.object(ocw, "_read_appended_session_events", fake_read_events), \
         patch.object(ocw, "_summarize_session_events", fake_summarize), \
         patch.object(ocw, "_extract_json_blob", fake_extract), \
         patch.object(ocw, "_clean_env", fake_clean_env), \
         patch.object(ocw.subprocess, "run", fake_subprocess_run):
        return ocw._run_openclaw_agent(
            workspace=Path("/tmp/ws"),
            agent_id="swebench-test",
            prompt="do something",
            timeout=30,
            model="kimi-for-coding",
        )


def _empty_summary() -> dict:
    return {
        "response": "",
        "tool_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "reported_tokens_out": 0,
        "estimated_tokens_out": 0,
        "counted_tokens_out": 0,
        "approx_tokens_out": 0,
        "tokens_total": 0,
        "reported_tokens_total": 0,
        "effective_tokens_total": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "count_tokens_calls": 0,
        "count_tokens_cache_hits": 0,
        "last_usage": {},
    }


def test_silent_exit_does_not_inherit_cumulative_meta():
    """Memory-enabled round that exits silently must report zeros, not
    the agent's lifetime session totals."""
    before = {
        "meta": {"inputTokens": 93568, "outputTokens": 11294, "totalTokens": 3480286},
        "session_id": "s1",
        "session_file": Path("/tmp/sess.jsonl"),
        "size": 1000,
        "updated_at": 100,
    }
    after = {
        "meta": {"inputTokens": 93568, "outputTokens": 11294, "totalTokens": 3480286},
        "session_id": "s1",
        "session_file": Path("/tmp/sess.jsonl"),
        "size": 1000,
        "updated_at": 101,
    }
    payload = {"meta": {"agentMeta": {}, "toolSummary": {"calls": 0}}, "payloads": []}

    result = _run_with_stubs(
        session_summary=_empty_summary(),
        before_session=before,
        after_session=after,
        payload=payload,
    )

    assert result.tokens_in == 0, "silent exit must not carry prior-round input tokens"
    assert result.tokens_out == 0, "silent exit must not carry prior-round output tokens"
    assert result.tokens_total == 0
    assert result.tool_calls == 0
    assert result.error == "silent_exit_no_api_activity"
    usage = result.raw["usage_details"]
    assert usage["has_new_activity"] is False
    assert usage["input_tokens"] == 0
    assert usage["runtime_tokens_total"] == 0


def test_call_with_events_uses_session_summary():
    """A normal call with assistant events should report session-derived
    tokens — unchanged behavior."""
    summary = _empty_summary()
    summary.update(
        tokens_in=5000,
        tokens_out=800,
        reported_tokens_out=800,
        effective_tokens_total=6000,
        reported_tokens_total=6000,
        tool_calls=7,
        cache_read_tokens=200,
    )
    before = {
        "meta": {"inputTokens": 93568, "outputTokens": 11294, "totalTokens": 3480286},
        "session_id": "s1",
        "session_file": Path("/tmp/sess.jsonl"),
        "size": 900,
        "updated_at": 100,
    }
    after = {
        "meta": {"inputTokens": 98568, "outputTokens": 12094, "totalTokens": 3486286},
        "session_id": "s1",
        "session_file": Path("/tmp/sess.jsonl"),
        "size": 1500,
        "updated_at": 102,
    }
    payload = {
        "meta": {"agentMeta": {"lastCallUsage": {"input": 5000, "output": 800, "total": 5800}},
                 "toolSummary": {"calls": 7}},
        "payloads": [{"text": "ok"}],
    }

    result = _run_with_stubs(
        session_summary=summary,
        before_session=before,
        after_session=after,
        payload=payload,
    )

    assert result.tokens_in == 5000
    assert result.tokens_out == 800
    assert result.tool_calls == 7
    assert result.error is None
    assert result.raw["usage_details"]["has_new_activity"] is True


def test_final_call_only_activity_still_trusted():
    """If session events are empty but payload reports a final-call usage
    (some openclaw flows only surface tokens via agentMeta), trust the
    final call rather than marking silent exit."""
    before = {
        "meta": {"inputTokens": 93568, "outputTokens": 11294, "totalTokens": 3480286},
        "session_id": "s1",
        "session_file": Path("/tmp/sess.jsonl"),
        "size": 1000,
        "updated_at": 100,
    }
    after = {
        "meta": {"inputTokens": 93568, "outputTokens": 11294, "totalTokens": 3480286},
        "session_id": "s1",
        "session_file": Path("/tmp/sess.jsonl"),
        "size": 1000,
        "updated_at": 101,
    }
    payload = {
        "meta": {"agentMeta": {"lastCallUsage": {"input": 120, "output": 40, "total": 160}},
                 "toolSummary": {"calls": 0}},
        "payloads": [{"text": "tiny reply"}],
    }

    result = _run_with_stubs(
        session_summary=_empty_summary(),
        before_session=before,
        after_session=after,
        payload=payload,
    )

    assert result.tokens_in == 120
    assert result.tokens_out == 40
    assert result.error is None
    assert result.raw["usage_details"]["has_new_activity"] is True


if __name__ == "__main__":  # pragma: no cover - manual invocation
    pytest.main([__file__, "-v"])
