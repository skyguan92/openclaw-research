"""Unit tests for the mem-eval-v1 foundation (pricing + outcome classifier).

Covers the boring-but-load-bearing bits:
  - Pricing math matches the v1 Kimi table exactly
  - Currency conversion doesn't drift when we split buckets
  - Token-count extraction handles both breakdown shapes (openclaw legacy,
    hermes/claude-code with usage_details)
  - Classifier precedence: quota > telemetry > timeout > silent > crash > harness
  - Adapter-layer failures override a harness "fail" (quota shouldn't be
    tagged test_failed)
"""

from __future__ import annotations

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.kimi_pricing import (
    PRICE_TABLE_VERSION,
    TokenCounts,
    compute_cost,
    extract_token_counts,
    sanity_check_token_sum,
)
from adapters.outcome_classifier import Outcome, classify_outcome
from scripts.run_mem_eval import plan_cell  # noqa: E402


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_price_table_version_locked():
    assert PRICE_TABLE_VERSION == "v1-2026-04-20"


def test_compute_cost_rmb_matches_table():
    # 1M pure input + 1M cache read + 0 cache write + 1M output →
    # ¥4.00 + ¥1.00 + ¥0.00 + ¥16.00 = ¥21.00
    tokens = TokenCounts(pure_input=1_000_000, cache_read=1_000_000, cache_write=0, output=1_000_000)
    cost = compute_cost(tokens, currency="rmb")
    assert cost.pure_input == pytest.approx(4.00)
    assert cost.cache_read == pytest.approx(1.00)
    assert cost.cache_write == pytest.approx(0.00)
    assert cost.output == pytest.approx(16.00)
    assert cost.total == pytest.approx(21.00)
    assert cost.price_version == PRICE_TABLE_VERSION


def test_compute_cost_usd_matches_table():
    tokens = TokenCounts(pure_input=1_000_000, cache_read=1_000_000, cache_write=0, output=1_000_000)
    cost = compute_cost(tokens, currency="usd")
    # $0.60 + $0.15 + $0 + $2.50 = $3.25
    assert cost.total == pytest.approx(3.25)
    assert cost.currency == "usd"


def test_cache_write_charged_at_miss_rate():
    """cache_write is merged into the miss bucket in v1 (no separate rate)."""
    tokens = TokenCounts(pure_input=0, cache_read=0, cache_write=500_000, output=0)
    cost = compute_cost(tokens, currency="rmb")
    # 0.5M × ¥4/M = ¥2.00 — same rate as cache_miss_in
    assert cost.cache_write == pytest.approx(2.00)
    assert cost.pure_input == 0


def test_zero_tokens_zero_cost():
    tokens = TokenCounts(pure_input=0, cache_read=0, cache_write=0, output=0)
    cost = compute_cost(tokens)
    assert cost.total == 0


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def test_extract_token_counts_from_hermes_record(hermes_swe_record):
    tokens = extract_token_counts(hermes_swe_record)
    # Fixture: input=19000, cache_read=380000, cache_write=9500, output=4500
    assert tokens.pure_input == 19_000
    assert tokens.cache_read == 380_000
    assert tokens.cache_write == 9_500
    assert tokens.output == 4_500


def test_extract_token_counts_from_legacy_openclaw(openclaw_swe_record):
    """Legacy openclaw records have no usage_details breakdown — tokens_in
    is already the pure billable input in that shape."""
    tokens = extract_token_counts(openclaw_swe_record)
    assert tokens.pure_input == 1_000_000  # whole tokens_in
    assert tokens.cache_read == 0
    assert tokens.cache_write == 0
    assert tokens.output == 15_000


def test_extract_token_counts_from_claude_code_v2(claude_code_swe_record_v2):
    tokens = extract_token_counts(claude_code_swe_record_v2)
    assert tokens.pure_input == 10_000
    assert tokens.cache_read == 1_035_000
    assert tokens.output == 9_500


# ---------------------------------------------------------------------------
# Telemetry consistency check
# ---------------------------------------------------------------------------


def test_sanity_check_within_tolerance():
    """Anthropic convention: tokens_in = pure + cache_read + cache_write."""
    tokens = TokenCounts(pure_input=100, cache_read=200, cache_write=0, output=50)
    ok, _ = sanity_check_token_sum(tokens, reported_total_input=300)
    assert ok


def test_sanity_check_accepts_openclaw_convention():
    """openclaw reports tokens_in as pure_input only; cache_read is a
    sibling field. sanity_check should accept this as consistent."""
    tokens = TokenCounts(pure_input=254_752, cache_read=2_250_368, cache_write=0, output=10_590)
    ok, msg = sanity_check_token_sum(tokens, reported_total_input=254_752)
    assert ok, msg
    assert "runtime-style" in msg


def test_sanity_check_rejects_drift():
    """Neither convention matches → real drift."""
    tokens = TokenCounts(pure_input=100, cache_read=100, cache_write=0, output=50)
    ok, _ = sanity_check_token_sum(tokens, reported_total_input=500, tolerance=0.01)
    assert not ok


# ---------------------------------------------------------------------------
# Outcome classifier — precedence checks
# ---------------------------------------------------------------------------


def _base_ok_record(harness_status: str | None = "pass") -> dict:
    """A record that looks like a clean successful run."""
    record = {
        "metrics": {
            "tokens_in": 19_000,
            "tokens_out": 4_500,
            "task_completed": True,
        },
        "notes": "SWE-bench instance, mode=repo-mentioned, patch_len=1234",
        "usage_details": {
            "input_tokens": 19_000,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }
    if harness_status is not None:
        record["metrics"]["harness_status"] = harness_status
        record["metrics"]["resolved"] = (harness_status == "pass")
    return record


def test_classify_passed():
    result = classify_outcome(_base_ok_record("pass"))
    assert result.outcome == Outcome.PASSED
    assert result.harness_available


def test_classify_test_failed():
    result = classify_outcome(_base_ok_record("fail"))
    assert result.outcome == Outcome.TEST_FAILED


def test_classify_harness_pending():
    """Clean adapter run but no harness verdict yet → outcome=None."""
    result = classify_outcome(_base_ok_record(None))
    assert result.outcome is None
    assert not result.harness_available


def test_classify_quota_beats_harness_fail():
    """A 403 quota response shouldn't be bucketed as test_failed even if
    harness ran on an earlier attempt."""
    rec = _base_ok_record("fail")
    rec["error"] = "API Error: 403 insufficient_quota"
    result = classify_outcome(rec)
    assert result.outcome == Outcome.QUOTA_403


def test_classify_rate_limit_as_quota():
    rec = _base_ok_record(None)
    rec["error"] = "API Error: 429 rate limit exceeded"
    result = classify_outcome(rec)
    assert result.outcome == Outcome.QUOTA_403


def test_classify_timeout():
    rec = _base_ok_record(None)
    rec["error"] = "timeout"
    result = classify_outcome(rec)
    assert result.outcome == Outcome.TIMEOUT


def test_classify_silent_exit_explicit():
    rec = _base_ok_record(None)
    rec["error"] = "empty output (possible silent crash)"
    result = classify_outcome(rec)
    assert result.outcome == Outcome.SILENT_EXIT


def test_classify_silent_exit_no_patch_no_error():
    rec = _base_ok_record(None)
    rec["metrics"]["task_completed"] = False
    rec["notes"] = "SWE-bench instance, mode=repo-mentioned, patch_len=0"
    result = classify_outcome(rec)
    assert result.outcome == Outcome.SILENT_EXIT


def test_classify_crash_on_api_400():
    rec = _base_ok_record(None)
    rec["error"] = 'API Error: 400 {"error":{"message":"thinking ..."}}'
    result = classify_outcome(rec)
    assert result.outcome == Outcome.CRASH


def test_classify_telemetry_error_beats_success():
    """If our own counts don't add up, we can't trust the record even if
    the harness says pass."""
    rec = _base_ok_record("pass")
    rec["metrics"]["tokens_in"] = 10_000  # but usage_details says 19_000
    result = classify_outcome(rec)
    assert result.outcome == Outcome.TELEMETRY_ERROR


# ---------------------------------------------------------------------------
# Cell planning — isolation invariants
# ---------------------------------------------------------------------------


def test_plan_cell_always_uses_isolated_state():
    """Both mem profiles must pick `memory-enabled` so state dirs are
    isolated under data/runtime_state/, never the user's ~/.openclaw."""
    assert plan_cell("openclaw", "off", 1, 1).runtime_profile == "memory-enabled"
    assert plan_cell("openclaw", "on", 1, 1).runtime_profile == "memory-enabled"


def test_plan_cell_memoff_always_resets():
    plan = plan_cell("openclaw", "off", 1, 1)
    assert plan.reset_state is True


def test_plan_cell_memon_resets_only_on_round_1():
    assert plan_cell("openclaw", "on", 1, 1).reset_state is True
    for r in (2, 3, 4, 5):
        assert plan_cell("openclaw", "on", 1, r).reset_state is False


def test_plan_cell_memon_rounds_share_experiment_id():
    """5 rounds of one mem-on sequence must land in the same state dir,
    otherwise 'memory carries across rounds' isn't tested."""
    eids = {plan_cell("openclaw", "on", 1, r).experiment_id for r in range(1, 6)}
    assert len(eids) == 1


def test_plan_cell_different_seqs_are_isolated():
    """Two seqs under the same profile must use different experiment_ids,
    otherwise replicates contaminate each other."""
    eid1 = plan_cell("openclaw", "off", 1, 1).experiment_id
    eid2 = plan_cell("openclaw", "off", 2, 1).experiment_id
    assert eid1 != eid2


def test_plan_cell_different_agents_are_isolated():
    eid_openclaw = plan_cell("openclaw", "on", 1, 1).experiment_id
    eid_hermes = plan_cell("hermes", "on", 1, 1).experiment_id
    assert eid_openclaw != eid_hermes


def test_plan_cell_memon_round_out_of_range():
    with pytest.raises(ValueError):
        plan_cell("openclaw", "on", 1, 6)
    with pytest.raises(ValueError):
        plan_cell("openclaw", "on", 1, 0)


def test_plan_cell_memoff_round_must_be_1():
    with pytest.raises(ValueError):
        plan_cell("openclaw", "off", 1, 2)
