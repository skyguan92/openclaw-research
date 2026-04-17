"""
可视化脚本 — 为 Direction B（多轮 SWE-bench）数据生成对比图表。

用法:
    python -m analysis.visualize                     # 从 data/raw/ 加载
    python -m analysis.visualize --demo              # 使用 sample data
    python -m analysis.visualize --output results/   # 指定输出目录
    python -m analysis.visualize --run-id <substr>   # 只画某个 run group
"""

import argparse
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

# macOS 中文字体支持
matplotlib.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

from analysis.compare import AGENTS, load_data, filter_records, flatten_metrics

COLORS = {"openclaw": "#4CAF50", "hermes": "#FF9800", "claude-code": "#2196F3"}


def _agent_values(df, column):
    """Mean of `column` per agent, as a list ordered by AGENTS."""
    return [
        df[df["agent"] == agent][column].dropna().mean() if column in df.columns else np.nan
        for agent in AGENTS
    ]


def plot_token_bars(df, output_dir: Path) -> None:
    """分组柱状图：每个 agent 同时显示 runtime_tokens 与 provider_tokens。

    runtime_tokens 是三者可比口径（包含 cache_read）；
    provider_tokens 是计费口径。
    两者差距只有 hermes 显著（因为只有它独立追踪 cache_read）。
    """
    tok = df[df["dimension"] == "token_efficiency"]
    if tok.empty:
        print("  (无 token 数据)")
        return

    runtime_vals = _agent_values(tok, "runtime_tokens_total")
    provider_vals = _agent_values(tok, "provider_tokens_total")

    if all(np.isnan(v) for v in runtime_vals) and all(np.isnan(v) for v in provider_vals):
        print("  (无 token 数据)")
        return

    x = np.arange(len(AGENTS))
    width = 0.38

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.bar(x - width / 2, runtime_vals, width, label="runtime_tokens (含 cache_read)",
           color=[COLORS[a] for a in AGENTS])
    ax.bar(x + width / 2, provider_vals, width, label="provider_tokens (计费)",
           color=[COLORS[a] for a in AGENTS], alpha=0.45, edgecolor="black", linewidth=0.5)

    for i, (r, p) in enumerate(zip(runtime_vals, provider_vals)):
        if not np.isnan(r):
            ax.text(i - width / 2, r, f"{r:,.0f}", ha="center", va="bottom", fontsize=8)
        if not np.isnan(p):
            ax.text(i + width / 2, p, f"{p:,.0f}", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(AGENTS)
    ax.set_ylabel("平均 tokens / run")
    ax.set_title("Token 口径对比（apples-to-apples vs 计费视角）")
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_dir / "token_comparison.png", dpi=150)
    plt.close()
    print("  ✓ token_comparison.png")


def plot_memory_curve(df, output_dir: Path) -> None:
    """多轮曲线：tool_calls 和 runtime_tokens 随 round 的变化 —— Direction B 核心信号。"""
    tok = df[(df["dimension"] == "token_efficiency") & df["round"].notna()].copy()
    if tok.empty:
        print("  (无多轮数据，跳过 memory curve)")
        return

    tok["round"] = tok["round"].astype(int)
    rounds = sorted(tok["round"].unique())
    if len(rounds) < 2:
        print("  (少于 2 轮，跳过 memory curve)")
        return

    fig, (ax_tools, ax_tokens) = plt.subplots(1, 2, figsize=(13, 5))

    for agent in AGENTS:
        agent_data = tok[tok["agent"] == agent]
        if agent_data.empty:
            continue

        tools_by_round = [agent_data[agent_data["round"] == r]["tool_calls_count"].mean()
                          for r in rounds]
        tokens_by_round = [agent_data[agent_data["round"] == r]["runtime_tokens_total"].mean()
                           for r in rounds]

        ax_tools.plot(rounds, tools_by_round, "o-", label=agent, color=COLORS[agent], linewidth=2)
        ax_tokens.plot(rounds, tokens_by_round, "o-", label=agent, color=COLORS[agent], linewidth=2)

    ax_tools.set_xlabel("Round (复用同一 runtime state)")
    ax_tools.set_ylabel("tool_calls / round")
    ax_tools.set_title("多轮工具调用曲线")
    ax_tools.set_xticks(rounds)
    ax_tools.grid(alpha=0.3)
    ax_tools.legend()

    ax_tokens.set_xlabel("Round (复用同一 runtime state)")
    ax_tokens.set_ylabel("runtime_tokens / round")
    ax_tokens.set_title("多轮 token 消耗曲线")
    ax_tokens.set_xticks(rounds)
    ax_tokens.grid(alpha=0.3)
    ax_tokens.legend()

    fig.suptitle("Memory 行为曲线（第 1 轮前清空 state，之后复用）", fontsize=13)
    plt.tight_layout()
    plt.savefig(output_dir / "memory_curve.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  ✓ memory_curve.png")


def plot_resolved_heatmap(df, output_dir: Path) -> None:
    """热力图：每个 (agent, round) 的 resolved 情况 —— 看多轮是否真的带来成功率提升。"""
    tok = df[(df["dimension"] == "token_efficiency") & df["round"].notna()].copy()
    if tok.empty:
        print("  (无多轮数据，跳过 resolved heatmap)")
        return

    tok["round"] = tok["round"].astype(int)
    rounds = sorted(tok["round"].unique())

    matrix = []
    annot = []
    for agent in AGENTS:
        row_vals = []
        row_annot = []
        for r in rounds:
            cell = tok[(tok["agent"] == agent) & (tok["round"] == r)]
            if cell.empty or "resolved" not in cell.columns or cell["resolved"].isna().all():
                row_vals.append(np.nan)
                row_annot.append("·")
            else:
                rate = cell["resolved"].fillna(False).astype(bool).mean()
                row_vals.append(rate)
                row_annot.append("✓" if rate >= 0.5 else "✗")
        matrix.append(row_vals)
        annot.append(row_annot)

    if all(all(np.isnan(v) for v in row) for row in matrix):
        print("  (无 resolved 字段数据，跳过 resolved heatmap)")
        return

    fig, ax = plt.subplots(figsize=(min(10, 2 + len(rounds)), 3.2))
    sns.heatmap(
        matrix,
        annot=annot,
        fmt="",
        xticklabels=[f"R{r}" for r in rounds],
        yticklabels=AGENTS,
        cmap="RdYlGn",
        vmin=0,
        vmax=1,
        linewidths=0.5,
        cbar_kws={"label": "resolved rate"},
        ax=ax,
    )
    ax.set_title("每轮 harness resolved 情况（· = 未评测）")
    plt.tight_layout()
    plt.savefig(output_dir / "resolved_heatmap.png", dpi=150)
    plt.close()
    print("  ✓ resolved_heatmap.png")


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Research - 可视化")
    parser.add_argument("--demo", action="store_true", help="使用内置 sample data")
    parser.add_argument("--output", type=str, default="results", help="输出目录")
    parser.add_argument("--run-id", default=None, help="只可视化指定 run group / run_id 子串")
    parser.add_argument("--agent", choices=AGENTS + ["all"], default="all", help="只可视化指定 agent")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    records = load_data(use_demo=args.demo)
    records = filter_records(records, run_group=args.run_id, agent=args.agent)
    if not records:
        print("未找到数据。使用 --demo 查看演示。")
        return

    df = flatten_metrics(records)

    print("生成图表中...")
    plot_token_bars(df, output_dir)
    plot_memory_curve(df, output_dir)
    plot_resolved_heatmap(df, output_dir)
    print(f"\n所有图表已保存到 {output_dir}/")


if __name__ == "__main__":
    main()
