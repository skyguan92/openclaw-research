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


def flatten_metrics(records: list[dict]) -> pd.DataFrame:
    """将 nested metrics 展平为 DataFrame。"""
    rows = []
    for r in records:
        row = {
            "run_id": r["run_id"],
            "agent": r["agent"],
            "task_id": r["task_id"],
            "dimension": r["dimension"],
        }
        row.update(r.get("metrics", {}))
        rows.append(row)
    return pd.DataFrame(rows)


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
        recall = agent_data["recall_accuracy"].mean() if "recall_accuracy" in agent_data.columns and agent_data["recall_accuracy"].notna().any() else None
        halluc = agent_data["hallucination_count"].mean() if "hallucination_count" in agent_data.columns and agent_data["hallucination_count"].notna().any() else None
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
        total = agent_data["tokens_total"].mean() if "tokens_total" in agent_data else "N/A"
        calls = agent_data["tool_calls_count"].mean() if "tool_calls_count" in agent_data else "N/A"
        print(f"  {agent:15s}  avg_tokens={total:.0f}  avg_tool_calls={calls:.1f}")


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
        pass_rate = (agent_data["result"] == "pass").mean() if "result" in agent_data else "N/A"
        quality = agent_data["quality_score"].mean() if "quality_score" in agent_data else "N/A"
        rounds = agent_data["rounds_used"].mean() if "rounds_used" in agent_data else "N/A"
        print(f"  {agent:15s}  pass_rate={pass_rate:.0%}  quality={quality:.1f}  avg_rounds={rounds:.0f}")


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Research - 对比分析")
    parser.add_argument("--demo", action="store_true", help="使用内置 sample data 演示")
    args = parser.parse_args()

    records = load_data(use_demo=args.demo)
    if not records:
        print("未找到测试数据。使用 --demo 查看演示输出。")
        sys.exit(1)

    df = flatten_metrics(records)

    print("=" * 60)
    print("OpenClaw Research — 对比分析报告")
    print(f"数据来源: {'demo' if args.demo else str(DATA_DIR)}")
    print(f"总记录数: {len(records)}")
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

    print("\n" + "=" * 60)
    print("详细数据请查看 results/ 目录的可视化图表")


if __name__ == "__main__":
    main()
