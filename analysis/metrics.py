"""Normalized token accounting, cost estimation, and TEFS calculation.

Three runners report tokens differently:
  - openclaw: tokens_total already excludes cache (provider-only).
  - hermes:   tokens_total is runtime-inclusive (input + output +
              cache_read + cache_write + reasoning).
  - claude-code: tokens_total = input + output (cache tracked
              separately in usage_details if present). Note: the
              upstream modelUsage.inputTokens already contains
              cacheReadInputTokens; the CLI runner subtracts it back
              out so usage_details.input_tokens here is the pure
              non-cache input, matching hermes semantics.

This module is the single place that resolves these differences so
every downstream chart uses the same definitions.
"""

from __future__ import annotations

from typing import Literal, Optional

Basis = Literal["provider", "runtime", "cost_tokens", "cost_usd"]

# Kimi-for-coding public pricing (per 1M tokens, USD) as of 2026-04.
# Used as fallback when a record doesn't carry provider_cost_usd.
KIMI_PRICING_PER_M = {
    "input": 0.15,
    "cache_read": 0.015,
    "cache_write": 0.1875,  # = input × 1.25
    "output": 2.50,
    "reasoning": 2.50,
}

# Provider-agnostic "equivalent input token" weights, used for the
# cost_tokens cross-runtime metric. Mirrors Anthropic's relative pricing
# so the number stays stable even if we benchmark against a different
# provider later.
EQUIV_WEIGHTS = {
    "input": 1.0,
    "cache_read": 0.1,
    "cache_write": 1.25,
    "output": 3.0,
    "reasoning": 3.0,
}


def _coerce_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def provider_tokens(record: dict) -> int:
    """Tokens that were actually billed by the LLM provider.

    Preference order:
      1. usage_details.provider_tokens_total (most accurate, set by runner)
      2. metrics.provider_tokens_total (older records)
      3. metrics.tokens_in + metrics.tokens_out
    """
    usage = record.get("usage_details") or {}
    if "provider_tokens_total" in usage:
        return _coerce_int(usage["provider_tokens_total"])

    metrics = record.get("metrics") or {}
    if "provider_tokens_total" in metrics:
        return _coerce_int(metrics["provider_tokens_total"])

    return _coerce_int(metrics.get("tokens_in")) + _coerce_int(metrics.get("tokens_out"))


def runtime_tokens(record: dict) -> int:
    """All tokens consumed by the runtime, including cache and reasoning.

    Preference order:
      1. usage_details.runtime_tokens_total
      2. metrics.runtime_tokens_total
      3. metrics.tokens_total
      4. provider_tokens (as best effort)
    """
    usage = record.get("usage_details") or {}
    if "runtime_tokens_total" in usage:
        return _coerce_int(usage["runtime_tokens_total"])

    metrics = record.get("metrics") or {}
    if "runtime_tokens_total" in metrics:
        return _coerce_int(metrics["runtime_tokens_total"])
    if "tokens_total" in metrics and metrics["tokens_total"]:
        return _coerce_int(metrics["tokens_total"])

    return provider_tokens(record)


TokenBreakdown = dict[str, int]
_BREAKDOWN_KEYS = ("pure_input", "cache_read", "cache_write", "output", "reasoning")


def token_breakdown(record: dict) -> TokenBreakdown:
    """Decompose usage into five comparable buckets.

    All three runners populate these under usage_details with a common
    vocabulary (input_tokens = non-cache input, cache_read_tokens,
    cache_write_tokens, output_tokens, reasoning_tokens). For legacy
    records without usage_details we fall back to metrics.tokens_in/out
    which means pure_input will be overstated on agents that cached (the
    old openclaw/claude-code rows), but that's the best we can do.
    """
    usage = record.get("usage_details") or {}
    metrics = record.get("metrics") or {}

    def _get(name: str, *fallbacks: str) -> int:
        for key in (name, *fallbacks):
            if key in usage and usage[key] is not None:
                return _coerce_int(usage[key])
        return 0

    if usage:
        return {
            "pure_input": _get("input_tokens"),
            "cache_read": _get("cache_read_tokens"),
            "cache_write": _get("cache_write_tokens"),
            "output": _get("output_tokens"),
            "reasoning": _get("reasoning_tokens"),
        }

    # Legacy path: only tokens_in/tokens_out survived.
    return {
        "pure_input": _coerce_int(metrics.get("tokens_in")),
        "cache_read": 0,
        "cache_write": 0,
        "output": _coerce_int(metrics.get("tokens_out")),
        "reasoning": 0,
    }


def cost_tokens(record: dict, weights: dict = EQUIV_WEIGHTS) -> float:
    """Provider-agnostic "equivalent input tokens" cost.

    Same shape as token_breakdown but weighted. Safe to compare across
    runtimes as long as they all ran on the same model family (we do —
    everything is Kimi-for-coding in this benchmark).
    """
    bd = token_breakdown(record)
    return (
        bd["pure_input"] * weights["input"]
        + bd["cache_read"] * weights["cache_read"]
        + bd["cache_write"] * weights["cache_write"]
        + bd["output"] * weights["output"]
        + bd["reasoning"] * weights["reasoning"]
    )


def cost_usd(record: dict, pricing_per_m: dict = KIMI_PRICING_PER_M) -> Optional[float]:
    """Real-dollar cost.

    Prefers the runner-reported value (openclaude already emits it);
    falls back to a per-breakdown calculation against Kimi pricing for
    runners that don't populate a cost field. Returns None only when
    there are truly no tokens to price.
    """
    usage = record.get("usage_details") or {}
    reported = usage.get("provider_cost_usd")
    if reported is not None:
        return float(reported)

    bd = token_breakdown(record)
    total = (
        bd["pure_input"] * pricing_per_m["input"]
        + bd["cache_read"] * pricing_per_m["cache_read"]
        + bd["cache_write"] * pricing_per_m["cache_write"]
        + bd["output"] * pricing_per_m["output"]
        + bd["reasoning"] * pricing_per_m["reasoning"]
    ) / 1_000_000

    if total <= 0 and all(bd[k] == 0 for k in _BREAKDOWN_KEYS):
        return None
    return total


def tefs(
    record: dict,
    *,
    score: Optional[float],
    basis: Basis = "provider",
) -> Optional[float]:
    """Token Efficiency Function Score.

    Unit varies by basis — DO NOT compare values across bases:
      - provider    → score per 1k billable tokens
      - runtime     → score per 1k runtime tokens (includes cache)
      - cost_tokens → score per 1k equivalent-input tokens (provider-agnostic)
      - cost_usd    → score per USD (different dimension; use for
                      dollar-level efficiency only, never mix with others)

    Returns None when TEFS is undefined:
      - score is None (harness has not scored this record yet)
      - score is 0   (avoids conflating "expensive but close to passing"
                      with "cheap and wildly wrong")
      - tokens/cost are 0 or unknown
    """
    if basis not in ("provider", "runtime", "cost_tokens", "cost_usd"):
        raise ValueError(
            f"tefs basis must be provider|runtime|cost_tokens|cost_usd, got {basis!r}"
        )
    if score is None or score == 0:
        return None

    if basis == "provider":
        denom = float(provider_tokens(record))
    elif basis == "runtime":
        denom = float(runtime_tokens(record))
    elif basis == "cost_tokens":
        denom = cost_tokens(record)
    else:  # cost_usd — units are score/$, NOT score/k-tokens
        dollars = cost_usd(record)
        if dollars is None or dollars <= 0:
            return None
        return float(score) / dollars

    if denom <= 0:
        return None
    return float(score) / (denom / 1000.0)
