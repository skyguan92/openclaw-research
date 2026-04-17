"""Token normalization, breakdown, cost, and TEFS calculation."""

import math

import pytest

from analysis.metrics import (
    EQUIV_WEIGHTS,
    KIMI_PRICING_PER_M,
    cost_tokens,
    cost_usd,
    provider_tokens,
    runtime_tokens,
    tefs,
    token_breakdown,
)


def test_openclaw_provider_equals_runtime(openclaw_swe_record):
    """openclaw: no separate cache accounting → both totals are equal."""
    assert provider_tokens(openclaw_swe_record) == 1015000
    assert runtime_tokens(openclaw_swe_record) == 1015000


def test_hermes_runtime_includes_cache(hermes_swe_record):
    """hermes: provider is tiny, runtime includes cache_read + cache_write."""
    assert provider_tokens(hermes_swe_record) == 23500
    assert runtime_tokens(hermes_swe_record) == 413000


def test_claude_code_falls_back_to_metrics(claude_code_swe_record):
    """claude-code fixture has no usage_details → fallback path uses metrics block."""
    assert provider_tokens(claude_code_swe_record) == 1054500
    assert runtime_tokens(claude_code_swe_record) == 1054500


def test_tefs_requires_score(openclaw_swe_record):
    """TEFS is undefined when the task has not been scored yet."""
    assert tefs(openclaw_swe_record, score=None) is None


def test_tefs_zero_score_returns_none_not_zero(openclaw_swe_record):
    """Score=0 and tokens>0 should be None (undefined), NOT 0. Prevents
    masking 'vastly overspent then failed' as 'zero efficiency'."""
    assert tefs(openclaw_swe_record, score=0.0) is None


def test_tefs_provider_basis(openclaw_swe_record):
    """TEFS with score=1 and 1,015,000 provider tokens → 1 / 1015 ≈ 0.000985."""
    result = tefs(openclaw_swe_record, score=1.0, basis="provider")
    assert result is not None
    assert math.isclose(result, 1.0 / (1015000 / 1000), rel_tol=1e-9)


def test_tefs_runtime_basis_differs_for_hermes(hermes_swe_record):
    """hermes TEFS on runtime tokens is ~18× lower than on provider tokens."""
    prov = tefs(hermes_swe_record, score=1.0, basis="provider")
    run = tefs(hermes_swe_record, score=1.0, basis="runtime")
    assert prov is not None and run is not None
    assert prov > run
    assert math.isclose(prov / run, 413000 / 23500, rel_tol=1e-3)


def test_tefs_rejects_bad_basis(openclaw_swe_record):
    with pytest.raises(ValueError):
        tefs(openclaw_swe_record, score=1.0, basis="nonsense")


# ── token_breakdown ─────────────────────────────────────────────────


def test_breakdown_openclaw_uses_usage_details(openclaw_swe_record):
    """openclaw has usage_details but no cache fields — breakdown zeros cache."""
    bd = token_breakdown(openclaw_swe_record)
    # The fixture's usage_details omits input_tokens/output_tokens, so they
    # default to 0. We only assert on fields the fixture actually sets.
    assert bd["cache_read"] == 0
    assert bd["cache_write"] == 0
    assert bd["reasoning"] == 0


def test_breakdown_hermes_full_five_buckets(hermes_swe_record):
    """hermes is the gold standard: all five buckets come from usage_details."""
    bd = token_breakdown(hermes_swe_record)
    assert bd["pure_input"] == 19000
    assert bd["cache_read"] == 380000
    assert bd["cache_write"] == 9500
    assert bd["output"] == 4500
    assert bd["reasoning"] == 0


def test_breakdown_claude_code_v2_has_pure_input(claude_code_swe_record_v2):
    """Post-fix claude-code: input_tokens is pure (cache_read subtracted out)."""
    bd = token_breakdown(claude_code_swe_record_v2)
    assert bd["pure_input"] == 10000
    assert bd["cache_read"] == 1035000
    assert bd["output"] == 9500
    # Sanity: five buckets should reconstruct runtime_tokens_total.
    assert sum(bd.values()) == 1054500


def test_breakdown_legacy_claude_code_fallback(claude_code_swe_record):
    """Legacy shape has no usage_details → fallback to metrics.tokens_in/out only."""
    bd = token_breakdown(claude_code_swe_record)
    assert bd["pure_input"] == 1045000
    assert bd["output"] == 9500
    assert bd["cache_read"] == 0  # best we can do without breakdown


# ── cost_tokens (provider-agnostic equivalent) ──────────────────────


def test_cost_tokens_hermes_weighted(hermes_swe_record):
    """cost_tokens = 19000×1 + 380000×0.1 + 9500×1.25 + 4500×3 + 0
                   = 19000 + 38000 + 11875 + 13500 = 82375."""
    expected = (
        19000 * EQUIV_WEIGHTS["input"]
        + 380000 * EQUIV_WEIGHTS["cache_read"]
        + 9500 * EQUIV_WEIGHTS["cache_write"]
        + 4500 * EQUIV_WEIGHTS["output"]
    )
    assert math.isclose(cost_tokens(hermes_swe_record), expected)


def test_cost_tokens_claude_code_v2_lower_than_raw_runtime(claude_code_swe_record_v2):
    """With cache_read weighted at 0.1×, cost_tokens should be far below runtime_tokens_total."""
    ct = cost_tokens(claude_code_swe_record_v2)
    assert ct < runtime_tokens(claude_code_swe_record_v2)
    # Specifically: 10000 + 103500 (cache×0.1) + 28500 (output×3) = 142000
    assert math.isclose(ct, 10000 + 1035000 * 0.1 + 9500 * 3.0)


# ── cost_usd ────────────────────────────────────────────────────────


def test_cost_usd_ignores_upstream_reported_for_openclaude(claude_code_swe_record_v2):
    """openclaude's upstream_reported_cost_usd is Anthropic-priced and
    must NOT poison the metric — cost_usd should compute from the
    Kimi-priced breakdown instead."""
    # 10000 × 0.15 + 1035000 × 0.015 + 9500 × 2.50 = (per-M)
    expected_usd = (
        10000 * KIMI_PRICING_PER_M["input"]
        + 1035000 * KIMI_PRICING_PER_M["cache_read"]
        + 9500 * KIMI_PRICING_PER_M["output"]
    ) / 1_000_000
    result = cost_usd(claude_code_swe_record_v2)
    assert result is not None
    assert math.isclose(result, expected_usd, rel_tol=1e-9)
    # Should be ~$0.04, not the $2.71 reported upstream.
    assert result < 0.1


def test_cost_usd_fallback_uses_kimi_pricing(hermes_swe_record):
    """hermes fixture has no provider_cost_usd → compute from breakdown × Kimi pricing."""
    # 19000 × 0.15 + 380000 × 0.015 + 9500 × 0.1875 + 4500 × 2.50 = (all per-M)
    expected_usd = (
        19000 * KIMI_PRICING_PER_M["input"]
        + 380000 * KIMI_PRICING_PER_M["cache_read"]
        + 9500 * KIMI_PRICING_PER_M["cache_write"]
        + 4500 * KIMI_PRICING_PER_M["output"]
    ) / 1_000_000
    result = cost_usd(hermes_swe_record)
    assert result is not None
    assert math.isclose(result, expected_usd, rel_tol=1e-9)


def test_cost_usd_none_when_no_tokens():
    """Empty record → None, not 0 (preserves "unknown" semantics)."""
    assert cost_usd({"metrics": {}}) is None


# ── tefs on new bases ───────────────────────────────────────────────


def test_tefs_cost_tokens_basis(hermes_swe_record):
    result = tefs(hermes_swe_record, score=1.0, basis="cost_tokens")
    assert result is not None
    expected = 1.0 / (cost_tokens(hermes_swe_record) / 1000.0)
    assert math.isclose(result, expected)


def test_tefs_cost_usd_basis(hermes_swe_record):
    result = tefs(hermes_swe_record, score=1.0, basis="cost_usd")
    expected = 1.0 / cost_usd(hermes_swe_record)
    assert math.isclose(result, expected)
