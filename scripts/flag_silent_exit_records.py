"""Retroactively flag openclaw token records where a silent-exit call
inherited the prior round's cumulative session-meta tokens.

The root-cause bug was in `openclaw_workspace._run_openclaw_agent`: when
`session_summary`/`final_call_*` were all zero (silent exit), token totals
fell back to `after_session["meta"]` which is cumulative across the agent's
entire session lifetime. Fixed in the commit that added
`tests/test_openclaw_silent_exit.py`.

This script patches already-saved records in-place: zeros out the metrics
for suspect runs, stashes the original numbers under
`usage_details.inherited_cumulative`, and sets a clear error marker so the
analysis pipeline can exclude them.

Heuristic for "silent exit":
  - tool_calls_count == 0
  - task_completed is False
  - usage_details.final_call_input_tokens == 0
  - usage_details.final_call_output_tokens == 0
  - usage_details.cache_read_tokens == 0
  - latency_s < 60s

Usage:
    python scripts/flag_silent_exit_records.py data/raw/*_openclaw_*.json [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SILENT_ERROR = "silent_exit_no_api_activity (backfilled)"


def _is_silent_exit(record: dict) -> bool:
    metrics = record.get("metrics") or {}
    usage = record.get("usage_details") or {}
    if record.get("agent") != "openclaw":
        return False
    if metrics.get("tool_calls_count", 0) != 0:
        return False
    if metrics.get("task_completed"):
        return False
    if usage.get("final_call_input_tokens", 0) != 0:
        return False
    if usage.get("final_call_output_tokens", 0) != 0:
        return False
    if usage.get("cache_read_tokens", 0) != 0:
        return False
    if metrics.get("latency_s", 0) >= 60:
        return False
    if metrics.get("tokens_in", 0) == 0 and metrics.get("tokens_out", 0) == 0:
        # Already zero — nothing to flag (probably already backfilled or a
        # genuine fresh run that produced nothing).
        return False
    return True


def flag(record: dict) -> bool:
    """Return True if the record was modified."""
    if not _is_silent_exit(record):
        return False

    metrics = record["metrics"]
    usage = record.setdefault("usage_details", {})

    inherited = {
        "tokens_in": metrics.get("tokens_in", 0),
        "tokens_out": metrics.get("tokens_out", 0),
        "tokens_total": metrics.get("tokens_total", 0),
        "provider_tokens_total": metrics.get("provider_tokens_total", 0),
        "runtime_tokens_total": metrics.get("runtime_tokens_total", 0),
        "output_tokens_reported": usage.get("output_tokens_reported", 0),
        "runtime_tokens_total_reported": usage.get("runtime_tokens_total_reported", 0),
    }
    usage["inherited_cumulative"] = inherited
    usage["has_new_activity"] = False

    for key in (
        "tokens_in",
        "tokens_out",
        "tokens_total",
        "provider_tokens_total",
        "runtime_tokens_total",
    ):
        metrics[key] = 0

    for key in (
        "input_tokens",
        "output_tokens",
        "output_tokens_reported",
        "output_tokens_estimated",
        "output_tokens_counted",
        "output_tokens_approx",
        "provider_tokens_total",
        "provider_tokens_total_reported",
        "runtime_tokens_total",
        "runtime_tokens_total_reported",
    ):
        if key in usage:
            usage[key] = 0
    usage["output_tokens_source"] = "missing"

    existing_error = record.get("error")
    if existing_error and SILENT_ERROR not in existing_error:
        record["error"] = f"{existing_error}; {SILENT_ERROR}"
    else:
        record["error"] = SILENT_ERROR
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path, help="record JSON files to process")
    ap.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = ap.parse_args()

    flagged: list[Path] = []
    for path in args.paths:
        if not path.exists():
            print(f"SKIP (missing): {path}")
            continue
        record = json.loads(path.read_text())
        if flag(record):
            flagged.append(path)
            if not args.dry_run:
                path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
            status = "WOULD-FLAG" if args.dry_run else "FLAGGED"
            print(f"{status}: {path.name}")

    print(f"\n{len(flagged)} record(s) {'would be ' if args.dry_run else ''}flagged")


if __name__ == "__main__":
    main()
