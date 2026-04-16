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
        }
        row.update(r.get("metrics", {}))
        rows.append(row)
    return pd.DataFrame(rows)


def _mean_if_present(df: pd.DataFrame, column: str):
    if column not in df.columns:
        return None
    series = df[column].dropna()
    if series.empty:
        return None
    return series.mean()


def summarize_memory(df: pd.DataFrame) -> None:
    """记忆架构维度摘要。"""
    mem = df[df["dimension"] == "memory"]
    if mem.empty:
        print("  (无数据)")
        return

    for agent in AGENTS:
        agent_data = mem[mem["agent"] == agent]
        if agent_data.empty:
            continue
        recall = _mean_if_present(agent_data, "recall_accuracy")
        halluc = _mean_if_present(agent_data, "hallucination_count")
        parts = [f"  {agent:15s}"]
        if recall is not None:
            parts.append(f"recall_accuracy={recall:.2f}")
        if halluc is not None:
            parts.append(f"hallucinations={halluc:.1f}")
        print("  ".join(parts))


def summarize_tokens(df: pd.DataFrame) -> None:
    """Token 效率维度摘要。"""
    tok = df[df["dimension"] == "token_efficiency"]
    if tok.empty:
        print("  (无数据)")
        return

    for agent in AGENTS:
        agent_data = tok[tok["agent"] == agent]
        if agent_data.empty:
            continue
        total = _mean_if_present(agent_data, "tokens_total")
        calls = _mean_if_present(agent_data, "tool_calls_count")
        completion = _mean_if_present(agent_data, "task_completed")
        parts = [f"  {agent:15s}"]
        if total is not None:
            parts.append(f"avg_tokens={total:.0f}")
        if calls is not None:
            parts.append(f"avg_tool_calls={calls:.1f}")
        if completion is not None:
            parts.append(f"completion_rate={completion:.0%}")
        if len(parts) == 1:
            parts.append("(无可聚合字段)")
        print("  ".join(parts))


def summarize_success(df: pd.DataFrame) -> None:
    """任务成功率维度摘要。"""
    suc = df[df["dimension"] == "task_success"]
    if suc.empty:
        print("  (无数据)")
        return

    for agent in AGENTS:
        agent_data = suc[suc["agent"] == agent]
        if agent_data.empty:
            continue
        parts = [f"  {agent:15s}"]
        if "result" in agent_data.columns and agent_data["result"].notna().any():
            pass_rate = (agent_data["result"] == "pass").mean()
            error_rate = (agent_data["result"] == "error").mean()
            parts.append(f"pass_rate={pass_rate:.0%}")
            parts.append(f"error_rate={error_rate:.0%}")
        quality = _mean_if_present(agent_data, "quality_score")
        rounds = _mean_if_present(agent_data, "rounds_used")
        if quality is not None:
            parts.append(f"quality={quality:.1f}")
        if rounds is not None:
            parts.append(f"avg_rounds={rounds:.0f}")
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
            total = _mean_if_present(agent_data, "tokens_total")
            completion = _mean_if_present(agent_data, "task_completed")
            latency = _mean_if_present(agent_data, "latency_s")
            parts = [f"    {agent:13s}"]
            if total is not None:
                parts.append(f"avg_tokens={total:.0f}")
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

    df = flatten_metrics(records)

    print("=" * 60)
    print("OpenClaw Research — 对比分析报告")
    print(f"数据来源: {'demo' if args.demo else str(DATA_DIR)}")
    print(f"总记录数: {len(records)}")
    if args.run_id:
        print(f"Run 过滤: {args.run_id}")
    if args.agent != "all":
        print(f"Agent 过滤: {args.agent}")
    print("=" * 60)

    print("\n📊 记忆架构 (Memory)")
    print("-" * 40)
    summarize_memory(df)

    print("\n📊 Token 效率 (Token Efficiency)")
    print("-" * 40)
    summarize_tokens(df)

    print("\n📊 任务成功率 (Task Success)")
    print("-" * 40)
    summarize_success(df)

    print("\n📊 轮次变化 (Round Progression)")
    print("-" * 40)
    summarize_rounds(df)

    print("\n" + "=" * 60)
    print("详细数据请查看 results/ 目录的可视化图表")


if __name__ == "__main__":
    main()
