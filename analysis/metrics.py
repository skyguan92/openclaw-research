"""Normalized token accounting and TEFS calculation.

Three runners report tokens differently:
  - openclaw: tokens_total already excludes cache (provider-only).
  - hermes:   tokens_total is runtime-inclusive (input + output +
              cache_read + cache_write + reasoning).
  - claude-code: tokens_total = input + output (cache tracked
              separately in usage_details if present).

This module is the single place that resolves these differences so
every downstream chart uses the same definitions.
"""

from __future__ import annotations

from typing import Literal, Optional

Basis = Literal["provider", "runtime"]


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


def tefs(
    record: dict,
    *,
    score: Optional[float],
    basis: Basis = "provider",
) -> Optional[float]:
    """Token Efficiency Function Score = score / (tokens / 1000).

    Returns None when TEFS is undefined:
      - score is None (harness has not scored this record yet)
      - score is 0   (avoids conflating "expensive but close to passing"
                      with "cheap and wildly wrong")
      - tokens are 0
    """
    if basis not in ("provider", "runtime"):
        raise ValueError(f"tefs basis must be 'provider' or 'runtime', got {basis!r}")
    if score is None or score == 0:
        return None

    tokens = provider_tokens(record) if basis == "provider" else runtime_tokens(record)
    if tokens <= 0:
        return None

    return float(score) / (tokens / 1000.0)
