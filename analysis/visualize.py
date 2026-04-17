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

# Five components of token_breakdown, in stacking order bottom → top.
# Cache tiers share a hue; output/reasoning get distinct colors.
BREAKDOWN_LAYERS = [
    ("pure_input_tokens",  "input (计费)",    "#78909C"),
    ("cache_read_tokens",  "cache_read (0.1×)", "#B0BEC5"),
    ("cache_write_tokens", "cache_write (1.25×)", "#D7CCC8"),
    ("output_tokens",      "output (3×)",       "#EF5350"),
    ("reasoning_tokens",   "reasoning (3×)",    "#AB47BC"),
]


def _agent_values(df, column):
    """Mean of `column` per agent, as a list ordered by AGENTS."""
    return [
        df[df["agent"] == agent][column].dropna().mean() if column in df.columns else np.nan
        for agent in AGENTS
    ]


def plot_token_bars(df, output_dir: Path) -> None:
    """Stacked bar：每个 agent 按 token 类别分层展示。

    五个分层（从底到顶）：pure_input / cache_read / cache_write / output / reasoning。
    不同类别的计费权重不同（标签上标注了相对权重），肉眼能直接看出
    openclaw 的 input-heavy / hermes 的 cache-heavy / claude-code 的哪种结构。
    """
    tok = df[df["dimension"] == "token_efficiency"]
    if tok.empty:
        print("  (无 token 数据)")
        return

    agent_means = {
        agent: {col: (tok[tok["agent"] == agent][col].dropna().mean() if col in tok.columns else 0.0)
                for col, _, _ in BREAKDOWN_LAYERS}
        for agent in AGENTS
    }

    x = np.arange(len(AGENTS))
    width = 0.55
    fig, ax = plt.subplots(figsize=(9, 5.5))

    bottom = np.zeros(len(AGENTS))
    for col, label, color in BREAKDOWN_LAYERS:
        values = np.array([agent_means[a][col] or 0.0 for a in AGENTS])
        ax.bar(x, values, width, bottom=bottom, label=label, color=color,
               edgecolor="white", linewidth=0.6)
        bottom += values

    for i, total in enumerate(bottom):
        if total > 0:
            ax.text(i, total, f"{total:,.0f}", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(AGENTS)
    ax.set_ylabel("平均 tokens / run")
    ax.set_title("Token 结构分解（按类别堆叠；括号为相对计费权重）")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_dir / "token_breakdown.png", dpi=150)
    plt.close()
    print("  ✓ token_breakdown.png")


def plot_cost_comparison(df, output_dir: Path) -> None:
    """并排柱状图：cost_tokens（provider 无关口径）vs cost_usd（Kimi 定价）。

    cost_tokens = 各类 token 加权求和（等价 input-token 数），跨 provider 口径稳定。
    cost_usd    = 按 Kimi-for-coding 定价折算真美元 —— 便于 sanity check。
    """
    tok = df[df["dimension"] == "token_efficiency"]
    if tok.empty:
        print("  (无 cost 数据)")
        return

    ct_vals = _agent_values(tok, "cost_tokens")
    usd_vals = _agent_values(tok, "cost_usd")

    fig, (ax_tok, ax_usd) = plt.subplots(1, 2, figsize=(12, 5))

    bars_tok = ax_tok.bar(AGENTS, ct_vals, color=[COLORS[a] for a in AGENTS],
                          edgecolor="black", linewidth=0.5)
    ax_tok.set_title("cost_tokens（等价 input-token 数）")
    ax_tok.set_ylabel("equivalent input tokens / run")
    ax_tok.grid(axis="y", alpha=0.3)
    for bar, v in zip(bars_tok, ct_vals):
        if not np.isnan(v):
            ax_tok.text(bar.get_x() + bar.get_width() / 2, v, f"{v:,.0f}",
                        ha="center", va="bottom", fontsize=9)

    bars_usd = ax_usd.bar(AGENTS, usd_vals, color=[COLORS[a] for a in AGENTS],
                          edgecolor="black", linewidth=0.5)
    ax_usd.set_title("cost_usd（Kimi-for-coding 定价）")
    ax_usd.set_ylabel("USD / run")
    ax_usd.grid(axis="y", alpha=0.3)
    for bar, v in zip(bars_usd, usd_vals):
        if not np.isnan(v):
            ax_usd.text(bar.get_x() + bar.get_width() / 2, v, f"${v:.3f}",
                        ha="center", va="bottom", fontsize=9)

    fig.suptitle("成本对比：provider-agnostic vs real-dollar", fontsize=13)
    plt.tight_layout()
    plt.savefig(output_dir / "cost_comparison.png", dpi=150)
    plt.close()
    print("  ✓ cost_comparison.png")


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

    fig, (ax_tools, ax_cost) = plt.subplots(1, 2, figsize=(13, 5))

    for agent in AGENTS:
        agent_data = tok[tok["agent"] == agent]
        if agent_data.empty:
            continue

        tools_by_round = [agent_data[agent_data["round"] == r]["tool_calls_count"].mean()
                          for r in rounds]
        cost_by_round = [agent_data[agent_data["round"] == r]["cost_tokens"].mean()
                         for r in rounds]

        ax_tools.plot(rounds, tools_by_round, "o-", label=agent, color=COLORS[agent], linewidth=2)
        ax_cost.plot(rounds, cost_by_round, "o-", label=agent, color=COLORS[agent], linewidth=2)

    ax_tools.set_xlabel("Round (复用同一 runtime state)")
    ax_tools.set_ylabel("tool_calls / round")
    ax_tools.set_title("多轮工具调用曲线")
    ax_tools.set_xticks(rounds)
    ax_tools.grid(alpha=0.3)
    ax_tools.legend()

    ax_cost.set_xlabel("Round (复用同一 runtime state)")
    ax_cost.set_ylabel("cost_tokens / round")
    ax_cost.set_title("多轮成本曲线（等价 input-token 数）")
    ax_cost.set_xticks(rounds)
    ax_cost.grid(alpha=0.3)
    ax_cost.legend()

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
    plot_cost_comparison(df, output_dir)
    plot_memory_curve(df, output_dir)
    plot_resolved_heatmap(df, output_dir)
    print(f"\n所有图表已保存到 {output_dir}/")


if __name__ == "__main__":
    main()
