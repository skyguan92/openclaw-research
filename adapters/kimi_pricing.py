"""Kimi K2.5 / K2.6 thinking tier pricing — modeled cost computation.

This module produces a *modeled* cost number from adapter-reported token
counts. It is NOT a billing oracle: we do not have billing API access on
the coding-plan account, so the only use for these numbers is to compare
agents against each other under the same price assumptions.

Price table v1 (locked 2026-04-20):
  Source: https://platform.kimi.com/docs/pricing/chat-k2 (RMB page).
  Applies to `kimi-k2-0905/0711/thinking` tier, which is what
  `kimi-for-coding` currently routes to (K2.5 / K2.6 thinking, non-turbo).

  RMB per 1M tokens:
    cache_hit      = ¥1.00
    cache_miss_in  = ¥4.00
    output         = ¥16.00
    cache_write    = 0  (not billed separately in current docs; merged
                         into cache_miss on first call)

  USD per 1M tokens (derived from vendor USD page):
    cache_hit      = $0.15
    cache_miss_in  = $0.60
    output         = $2.50
    cache_write    = 0

If Moonshot changes pricing, bump `PRICE_TABLE_VERSION` and add a new
entry; never mutate an old row — analyses downstream rely on the version
tag to know which numbers were used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PRICE_TABLE_VERSION = "v1-2026-04-20"

# All prices are per 1M tokens.
_PRICES_RMB = {
    "cache_hit": 1.00,
    "cache_miss_in": 4.00,
    "output": 16.00,
    "cache_write": 0.00,
}
_PRICES_USD = {
    "cache_hit": 0.15,
    "cache_miss_in": 0.60,
    "output": 2.50,
    "cache_write": 0.00,
}

Currency = Literal["rmb", "usd"]


@dataclass(frozen=True)
class TokenCounts:
    """Per-call token breakdown as reported by the agent adapter.

    `pure_input` is the non-cached portion of the input (the "cache miss"
    bucket). `cache_read` is cached input that got re-served from
    Moonshot's context cache. `cache_write` is input that was freshly
    stored into the cache on this call; current price table treats it as
    part of `cache_miss_in` (so we charge it at the miss rate unless
    overridden).
    """

    pure_input: int
    cache_read: int
    cache_write: int
    output: int


@dataclass(frozen=True)
class CostBreakdown:
    """Modeled cost for a single call, broken out by bucket."""

    pure_input: float
    cache_read: float
    cache_write: float
    output: float
    total: float
    currency: Currency
    price_version: str


def extract_token_counts(record: dict) -> TokenCounts:
    """Pull the pure/cache/output breakdown from a `swe_*.json` record.

    The adapter stores the decomposed counts under `usage_details.input_tokens`
    (non-cached input) + `cache_read_tokens` + `cache_write_tokens`. For
    legacy openclaw records without a `usage_details` breakdown, we fall
    back to treating `metrics.tokens_in` as fully non-cached — which is
    what those records actually mean (openclaw without cache telemetry
    reported `tokens_in` as the full billable input).
    """
    usage = record.get("usage_details") or {}
    metrics = record.get("metrics") or {}
    tokens_in_total = int(metrics.get("tokens_in") or 0)
    tokens_out = int(metrics.get("tokens_out") or usage.get("output_tokens") or 0)

    # `usage_details` may be present but carry only aggregate counters
    # (e.g. `provider_tokens_total`) without the input/cache_read/cache_write
    # breakdown. Treat that as "no breakdown available" and fall back to
    # trusting metrics.tokens_in as fully non-cached.
    has_breakdown = any(
        key in usage for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
    )
    if has_breakdown:
        pure = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_tokens") or 0)
        cache_write = int(usage.get("cache_write_tokens") or 0)
    else:
        pure = tokens_in_total
        cache_read = 0
        cache_write = 0

    return TokenCounts(
        pure_input=pure,
        cache_read=cache_read,
        cache_write=cache_write,
        output=tokens_out,
    )


def compute_cost(tokens: TokenCounts, currency: Currency = "rmb") -> CostBreakdown:
    table = _PRICES_RMB if currency == "rmb" else _PRICES_USD

    # cache_write is not billed separately; we charge it at the miss rate
    # because on a cache-creating call Moonshot still counts those tokens
    # as fresh input. If the price table is ever changed to bill
    # cache_write separately, flip to `table["cache_write"]`.
    write_rate = table["cache_miss_in"]

    per_m = 1_000_000
    pure = tokens.pure_input * table["cache_miss_in"] / per_m
    read = tokens.cache_read * table["cache_hit"] / per_m
    write = tokens.cache_write * write_rate / per_m
    out = tokens.output * table["output"] / per_m

    return CostBreakdown(
        pure_input=pure,
        cache_read=read,
        cache_write=write,
        output=out,
        total=pure + read + write + out,
        currency=currency,
        price_version=PRICE_TABLE_VERSION,
    )


def sanity_check_token_sum(
    tokens: TokenCounts,
    reported_total_input: int,
    tolerance: float = 0.01,
) -> tuple[bool, str]:
    """Internal consistency check on `metrics.tokens_in`.

    The three runtimes disagree on what `tokens_in` means, so we accept
    either of the two conventions and flag drift only if neither matches:

      - Anthropic-style (hermes, claude-code):
          tokens_in == pure_input + cache_read + cache_write
      - Runtime-style (openclaw):
          tokens_in == pure_input   (cache_read reported as a sibling field)

    Returns `(ok, message)`. On mismatch against both conventions, callers
    should tag the record's outcome as `telemetry_error` and keep the raw
    numbers for audit.
    """
    full_sum = tokens.pure_input + tokens.cache_read + tokens.cache_write
    pure_only = tokens.pure_input

    if reported_total_input == 0 and full_sum == 0:
        return True, "both zero"
    if reported_total_input == 0:
        return False, f"reported_total_input=0 but parts sum to {full_sum}"

    drift_full = abs(full_sum - reported_total_input) / reported_total_input
    drift_pure = abs(pure_only - reported_total_input) / reported_total_input
    best_drift = min(drift_full, drift_pure)

    if best_drift > tolerance:
        return False, (
            f"token parts {full_sum} (anthropic-style) / {pure_only} (runtime-style) "
            f"vs reported {reported_total_input} "
            f"(best drift {best_drift:.2%}, tolerance {tolerance:.1%})"
        )
    convention = "anthropic-style" if drift_full <= drift_pure else "runtime-style"
    return True, f"within {best_drift:.2%} ({convention})"
