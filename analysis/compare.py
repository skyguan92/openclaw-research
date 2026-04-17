"""
对比分析脚本 — 加载 data/raw/ 中的测试数据，按维度和 agent 聚合输出统计摘要。

用法:
    python analysis/compare.py                # 分析 data/raw/ 中的真实数据
    python analysis/compare.py --demo         # 使用内置 sample data 演示
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from analysis.metrics import provider_tokens, runtime_tokens, tefs
from analysis.outcome import Outcome, classify

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

AGENTS = ["openclaw", "hermes", "claude-code"]
DIMENSIONS = ["memory", "token_efficiency", "task_success"]

# ── Sample data for --demo mode ──────────────────────────────────────────

SAMPLE_DATA = [
    # Memory tasks
    {"run_id": "mem-01_openclaw_demo_1", "agent": "openclaw", "task_id": "mem-01", "dimension": "memory",
     "metrics": {"recall_accuracy": 0.8, "hallucination_count": 0}},
    {"run_id": "mem-01_hermes_demo_1", "agent": "hermes", "task_id": "mem-01", "dimension": "memory",
     "metrics": {"recall_accuracy": 0.6, "hallucination_count": 1}},
    {"run_id": "mem-01_claude-code_demo_1", "agent": "claude-code", "task_id": "mem-01", "dimension": "memory",
     "metrics": {"recall_accuracy": 0.9, "hallucination_count": 0}},
    # Token tasks
    {"run_id": "tok-01_openclaw_demo_1", "agent": "openclaw", "task_id": "tok-01", "dimension": "token_efficiency",
     "metrics": {"tokens_total": 2500, "task_completed": True, "tool_calls_count": 4}},
    {"run_id": "tok-01_hermes_demo_1", "agent": "hermes", "task_id": "tok-01", "dimension": "token_efficiency",
     "metrics": {"tokens_total": 3200, "task_completed": True, "tool_calls_count": 6}},
    {"run_id": "tok-01_claude-code_demo_1", "agent": "claude-code", "task_id": "tok-01", "dimension": "token_efficiency",
     "metrics": {"tokens_total": 1800, "task_completed": True, "tool_calls_count": 3}},
    # Success tasks
    {"run_id": "suc-01_openclaw_demo_1", "agent": "openclaw", "task_id": "suc-01", "dimension": "task_success",
     "metrics": {"result": "pass", "quality_score": 4, "rounds_used": 15}},
    {"run_id": "suc-01_hermes_demo_1", "agent": "hermes", "task_id": "suc-01", "dimension": "task_success",
     "metrics": {"result": "partial", "quality_score": 3, "rounds_used": 25}},
    {"run_id": "suc-01_claude-code_demo_1", "agent": "claude-code", "task_id": "suc-01", "dimension": "task_success",
     "metrics": {"result": "pass", "quality_score": 5, "rounds_used": 10}},
]


def load_data(use_demo: bool = False) -> list[dict]:
    """加载测试数据，返回展平后的 records 列表。"""
    if use_demo:
        return SAMPLE_DATA

    records = []
    if not DATA_DIR.exists():
        print(f"[warn] 数据目录不存在: {DATA_DIR}")
        return records

    for f in sorted(DATA_DIR.glob("*.json")):
        with open(f) as fp:
            records.append(json.load(fp))
    return records


def filter_records(
    records: list[dict],
    *,
    run_group: str | None = None,
    agent: str | None = None,
) -> list[dict]:
    """按 run_group / agent 过滤记录。"""
    filtered = records
    if run_group:
        filtered = [
            r for r in filtered
            if r.get("run_group") == run_group or run_group in r.get("run_id", "")
        ]
    if agent and agent != "all":
        filtered = [r for r in filtered if r.get("agent") == agent]
    return filtered


def flatten_metrics(records: list[dict]) -> pd.DataFrame:
    """将 nested metrics 展平为 DataFrame。"""
    rows = []
    for r in records:
        usage_details = r.get("usage_details", {})
        metrics = dict(r.get("metrics", {}))
        tokens_in = metrics.get("tokens_in", 0) or 0
        tokens_out = metrics.get("tokens_out", 0) or 0
        provider_total = metrics.get("provider_tokens_total")
        if provider_total is None:
            provider_total = usage_details.get("provider_tokens_total")
        if provider_total is None:
            provider_total = (tokens_in or 0) + (tokens_out or 0)
        runtime_total = metrics.get("runtime_tokens_total")
        if runtime_total is None:
            runtime_total = usage_details.get("runtime_tokens_total")
        if runtime_total is None:
            runtime_total = metrics.get("tokens_total", provider_total)
        row = {
            "run_id": r["run_id"],
            "run_group": r.get("run_group"),
            "experiment_id": r.get("experiment_id"),
            "round": r.get("round"),
            "runtime_profile": r.get("runtime_profile"),
            "agent": r["agent"],
            "task_id": r["task_id"],
            "dimension": r["dimension"],
            "error": r.get("error"),
            "provider_tokens_total": provider_total,
            "runtime_tokens_total": runtime_total,
        }
        row.update(metrics)
        if isinstance(usage_details, dict):
            row.update(usage_details)
        rows.append(row)
    return pd.DataFrame(rows)


def _mean_if_present(df: pd.DataFrame, column: str):
    if column not in df.columns:
        return None
    series = df[column].dropna()
    if series.empty:
        return None
    return series.mean()


def summarize_memory(records: list[dict]) -> None:
    """记忆维度 — 多轮复用时的行为变化（基于 analysis.memory_curve）。"""
    from analysis.memory_curve import compute_curves

    mem_records = [
        r for r in records
        if r.get("runtime_profile") == "memory-enabled" and r.get("round") is not None
    ]
    if not mem_records:
        print("  (无 memory-enabled 多轮数据；用 --runtime-profile memory-enabled --rounds N 采集)")
        return

    curves = compute_curves(mem_records)
    for c in curves:
        print(
            f"  {c.agent:<12} task={c.task_id:<30} "
            f"rounds={len(c.rounds)} "
            f"Δtools={c.delta_tools_r5_r1:+d} "
            f"Δprov_tok={c.delta_provider_tokens_r5_r1:+d} "
            f"patch_stable={c.patch_stability:.0%} "
            f"resolved_rate={c.resolved_rate:.0%}"
        )


def summarize_tokens(records: list[dict]) -> None:
    """Token 效率维度摘要（走 analysis.metrics 统一口径）。"""
    tok_records = [r for r in records if r.get("dimension") == "token_efficiency"]
    if not tok_records:
        print("  (无数据)")
        return

    by_agent: dict[str, list[dict]] = {}
    for r in tok_records:
        by_agent.setdefault(r["agent"], []).append(r)

    print("  (runtime_tokens = all tokens processed by runtime incl. cache;")
    print("   provider_tokens = billable non-cache tokens — only hermes tracks this separately)")
    for agent in AGENTS:
        rows = by_agent.get(agent)
        if not rows:
            continue
        prov = sum(provider_tokens(r) for r in rows) / len(rows)
        runt = sum(runtime_tokens(r) for r in rows) / len(rows)
        cache_reads = [
            (r.get("usage_details") or {}).get("cache_read_tokens")
            for r in rows
            if (r.get("usage_details") or {}).get("cache_read_tokens") is not None
        ]
        avg_cache = (sum(cache_reads) / len(cache_reads)) if cache_reads else None

        tool_calls = [
            (r.get("metrics") or {}).get("tool_calls_count")
            for r in rows
            if (r.get("metrics") or {}).get("tool_calls_count") is not None
        ]
        avg_tools = (sum(tool_calls) / len(tool_calls)) if tool_calls else None

        resolved_scores = [
            1.0 if (r.get("metrics") or {}).get("resolved") else 0.0
            for r in rows
            if (r.get("metrics") or {}).get("resolved") is not None
        ]
        resolved_rate = (sum(resolved_scores) / len(resolved_scores)) if resolved_scores else None

        tefs_values = [
            tefs(r, score=(r.get("metrics") or {}).get("resolved") and 1.0 or None, basis="runtime")
            for r in rows
        ]
        tefs_values = [v for v in tefs_values if v is not None]
        avg_tefs_resolved = (sum(tefs_values) / len(tefs_values)) if tefs_values else None

        parts = [f"  {agent:15s}",
                 f"avg_runtime_tokens={runt:.0f}"]
        if abs(runt - prov) >= 1:
            parts.append(f"avg_provider_tokens={prov:.0f}")
            if avg_cache is not None:
                parts.append(f"avg_cache_read={avg_cache:.0f}")
        if avg_tools is not None:
            parts.append(f"avg_tool_calls={avg_tools:.1f}")
        if resolved_rate is not None:
            parts.append(f"resolved_rate={resolved_rate:.0%}")
        if avg_tefs_resolved is not None:
            parts.append(f"TEFS_resolved(runtime)={avg_tefs_resolved:.4f}")
        print("  ".join(parts))


def summarize_success(records: list[dict]) -> None:
    """任务成功率 — 基于 analysis.outcome，包含 harness pass 与失败分类。"""
    suc_records = [
        r for r in records
        if r.get("dimension") in ("task_success", "token_efficiency")
    ]
    if not suc_records:
        print("  (无数据)")
        return

    by_agent: dict[str, list[Outcome]] = {}
    for r in suc_records:
        by_agent.setdefault(r["agent"], []).append(classify(r))

    for agent in AGENTS:
        outcomes = by_agent.get(agent)
        if not outcomes:
            continue
        total = len(outcomes)
        counts: dict[Outcome, int] = {}
        for o in outcomes:
            counts[o] = counts.get(o, 0) + 1
        scored = sum(1 for o in outcomes if o in (Outcome.HARNESS_PASSED, Outcome.HARNESS_FAILED))
        passed = counts.get(Outcome.HARNESS_PASSED, 0)
        pass_rate = (passed / scored) if scored else None

        parts = [f"  {agent:15s}", f"n={total}"]
        if pass_rate is not None:
            parts.append(f"harness_pass_rate={pass_rate:.0%} ({passed}/{scored})")
        else:
            parts.append("harness_pass_rate=n/a (no scored records)")
        details = ", ".join(f"{o.value}={c}" for o, c in sorted(counts.items(), key=lambda kv: kv[0].value))
        parts.append(f"outcomes=[{details}]")
        print("  ".join(parts))


def summarize_rounds(df: pd.DataFrame) -> None:
    """按轮次输出 token / completion 的变化。"""
    if "round" not in df.columns:
        print("  (无轮次数据)")
        return

    round_df = df[df["round"].notna()].copy()
    if round_df.empty:
        print("  (无轮次数据)")
        return

    round_df["round"] = round_df["round"].astype(int)
    tok = round_df[round_df["dimension"] == "token_efficiency"]
    if tok.empty:
        print("  (无 token 轮次数据)")
        return

    rounds = sorted(tok["round"].unique())
    for round_index in rounds:
        print(f"  Round {round_index}")
        round_data = tok[tok["round"] == round_index]
        for agent in AGENTS:
            agent_data = round_data[round_data["agent"] == agent]
            if agent_data.empty:
                continue
            provider_total = _mean_if_present(agent_data, "provider_tokens_total")
            runtime_total = _mean_if_present(agent_data, "runtime_tokens_total")
            completion = _mean_if_present(agent_data, "task_completed")
            latency = _mean_if_present(agent_data, "latency_s")
            parts = [f"    {agent:13s}"]
            if runtime_total is not None:
                parts.append(f"runtime_tokens={runtime_total:.0f}")
            if provider_total is not None and (
                runtime_total is None or abs(runtime_total - provider_total) >= 1
            ):
                parts.append(f"provider_tokens={provider_total:.0f}")
            if completion is not None:
                parts.append(f"completion_rate={completion:.0%}")
            if latency is not None:
                parts.append(f"avg_latency_s={latency:.1f}")
            print("  ".join(parts))


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Research - 对比分析")
    parser.add_argument("--demo", action="store_true", help="使用内置 sample data 演示")
    parser.add_argument("--run-id", default=None, help="只分析指定 run group / run_id 子串")
    parser.add_argument("--agent", choices=AGENTS + ["all"], default="all", help="只分析指定 agent")
    args = parser.parse_args()

    records = load_data(use_demo=args.demo)
    records = filter_records(records, run_group=args.run_id, agent=args.agent)
    if not records:
        print("未找到测试数据。使用 --demo 查看演示输出。")
        sys.exit(1)

    print("=" * 60)
    print("OpenClaw Research — 对比分析报告")
    print(f"数据来源: {'demo' if args.demo else str(DATA_DIR)}")
    print(f"总记录数: {len(records)}")
    if args.run_id:
        print(f"Run 过滤: {args.run_id}")
    if args.agent != "all":
        print(f"Agent 过滤: {args.agent}")
    print("=" * 60)

    print("\n📊 记忆架构 (Memory — multi-round behavior)")
    print("-" * 40)
    summarize_memory(records)

    print("\n📊 Token 效率 (Token Efficiency)")
    print("-" * 40)
    summarize_tokens(records)

    print("\n📊 任务成功率 (Task Success — harness-resolved)")
    print("-" * 40)
    summarize_success(records)

    print("\n📊 轮次变化 (Round Progression)")
    print("-" * 40)
    df = flatten_metrics(records)
    summarize_rounds(df)

    print("\n" + "=" * 60)
    print("详细数据请查看 results/ 目录的可视化图表")


if __name__ == "__main__":
    main()
