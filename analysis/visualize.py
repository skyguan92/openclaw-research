"""
可视化脚本 — 生成三个维度的对比图表。

用法:
    python analysis/visualize.py                # 从 data/raw/ 加载
    python analysis/visualize.py --demo         # 使用 sample data
    python analysis/visualize.py --output results/  # 指定输出目录
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

from compare import AGENTS, load_data, filter_records, flatten_metrics

COLORS = {"openclaw": "#4CAF50", "hermes": "#FF9800", "claude-code": "#2196F3"}


def _token_column(df):
    if "provider_tokens_total" in df.columns:
        return "provider_tokens_total"
    if "tokens_total" in df.columns:
        return "tokens_total"
    return None


def plot_radar(df, output_dir: Path) -> None:
    """雷达图：三个 agent 在三个维度的综合表现。"""
    # 归一化各维度到 0-1
    scores = {}
    token_col = _token_column(df)
    for agent in AGENTS:
        agent_data = df[df["agent"] == agent]
        mem = agent_data[agent_data["dimension"] == "memory"]
        tok = agent_data[agent_data["dimension"] == "token_efficiency"]
        suc = agent_data[agent_data["dimension"] == "task_success"]

        memory_score = mem["recall_accuracy"].mean() if not mem.empty and "recall_accuracy" in mem else 0
        # Token 效率用倒数归一化（越少越好）
        token_val = tok[token_col].mean() if not tok.empty and token_col in tok else 5000
        token_score = max(0, 1 - token_val / 5000)
        success_score = (suc["result"] == "pass").mean() if not suc.empty and "result" in suc else 0

        scores[agent] = [memory_score, token_score, success_score]

    categories = ["记忆架构", "Token 效率", "任务成功率"]
    n = len(categories)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

    for agent in AGENTS:
        values = scores.get(agent, [0, 0, 0])
        values += values[:1]
        ax.plot(angles, values, "o-", linewidth=2, label=agent, color=COLORS[agent])
        ax.fill(angles, values, alpha=0.15, color=COLORS[agent])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=12)
    ax.set_ylim(0, 1)
    ax.set_title("Agent 综合能力对比", fontsize=16, pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))

    plt.tight_layout()
    plt.savefig(output_dir / "radar_comparison.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✓ radar_comparison.png")


def plot_token_bars(df, output_dir: Path) -> None:
    """柱状图：Token 消耗对比。"""
    tok = df[df["dimension"] == "token_efficiency"]
    if tok.empty:
        print("  (无 token 数据)")
        return
    token_col = _token_column(tok)
    if token_col is None:
        print("  (无 token 数据)")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    task_ids = sorted(tok["task_id"].unique())
    x = np.arange(len(task_ids))
    width = 0.25

    for i, agent in enumerate(AGENTS):
        agent_data = tok[tok["agent"] == agent]
        values = []
        for tid in task_ids:
            task_data = agent_data[agent_data["task_id"] == tid]
            values.append(task_data[token_col].mean() if not task_data.empty else 0)
        ax.bar(x + i * width, values, width, label=agent, color=COLORS[agent])

    ax.set_xlabel("任务 ID")
    ax.set_ylabel("Provider Token 总消耗" if token_col == "provider_tokens_total" else "Token 总消耗")
    ax.set_title("Token 效率对比")
    ax.set_xticks(x + width)
    ax.set_xticklabels(task_ids)
    ax.legend()

    plt.tight_layout()
    plt.savefig(output_dir / "token_comparison.png", dpi=150)
    plt.close()
    print(f"  ✓ token_comparison.png")


def plot_success_heatmap(df, output_dir: Path) -> None:
    """热力图：任务成功率矩阵。"""
    suc = df[df["dimension"] == "task_success"]
    if suc.empty:
        print("  (无 success 数据)")
        return

    task_ids = sorted(suc["task_id"].unique())
    matrix = []
    for agent in AGENTS:
        row = []
        for tid in task_ids:
            data = suc[(suc["agent"] == agent) & (suc["task_id"] == tid)]
            if not data.empty and "quality_score" in data and data["quality_score"].notna().any():
                row.append(data["quality_score"].mean())
            elif not data.empty and "result" in data:
                row.append((data["result"] == "pass").mean() * 5)
            else:
                row.append(0)
        matrix.append(row)

    fig, ax = plt.subplots(figsize=(10, 4))
    sns.heatmap(
        matrix,
        annot=True,
        fmt=".1f",
        xticklabels=task_ids,
        yticklabels=AGENTS,
        cmap="YlOrRd",
        vmin=0,
        vmax=5,
        ax=ax,
    )
    ax.set_title("任务质量评分热力图")

    plt.tight_layout()
    plt.savefig(output_dir / "success_heatmap.png", dpi=150)
    plt.close()
    print(f"  ✓ success_heatmap.png")


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
    plot_radar(df, output_dir)
    plot_token_bars(df, output_dir)
    plot_success_heatmap(df, output_dir)
    print(f"\n所有图表已保存到 {output_dir}/")


if __name__ == "__main__":
    main()
