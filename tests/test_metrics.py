"""Token normalization and TEFS calculation."""

import math

import pytest

from analysis.metrics import provider_tokens, runtime_tokens, tefs


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
