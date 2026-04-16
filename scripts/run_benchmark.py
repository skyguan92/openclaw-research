#!/usr/bin/env python3
"""
Benchmark Runner — 记录测试结果到 data/raw/。

支持两种模式:
  1. 交互模式（默认）: 手动输入测试结果
  2. 批量导入: 从 JSON 文件导入

用法:
    python scripts/run_benchmark.py --agent openclaw --task mem-01
    python scripts/run_benchmark.py --agent hermes --task tok-01
    python scripts/run_benchmark.py --import results.json
    python scripts/run_benchmark.py --list                          # 列出所有任务
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "benchmarks" / "tasks.yaml"
DATA_DIR = ROOT / "data" / "raw"

# ── 维度到 metrics 的映射 ──────────────────────────────────────────────

DIMENSION_METRICS = {
    "memory": {
        "recall_accuracy": {"type": float, "prompt": "召回准确率 (0-1)"},
        "hallucination_count": {"type": int, "prompt": "幻觉次数 (int)"},
    },
    "token_efficiency": {
        "tokens_in": {"type": int, "prompt": "输入 token 数"},
        "tokens_out": {"type": int, "prompt": "输出 token 数"},
        "tokens_total": {"type": int, "prompt": "总 token 数"},
        "task_completed": {"type": bool, "prompt": "任务是否完成 (y/n)"},
        "tool_calls_count": {"type": int, "prompt": "工具调用次数"},
        "redundant_calls": {"type": int, "prompt": "冗余调用次数"},
    },
    "task_success": {
        "result": {"type": str, "prompt": "结果 (pass/partial/fail)"},
        "quality_score": {"type": int, "prompt": "质量评分 (1-5)"},
        "rounds_used": {"type": int, "prompt": "使用轮数"},
        "time_minutes": {"type": float, "prompt": "耗时(分钟)"},
        "error_encountered": {"type": bool, "prompt": "是否遇到错误 (y/n)"},
        "self_recovered": {"type": bool, "prompt": "是否自主恢复 (y/n)"},
    },
}

TASK_PREFIX_TO_DIMENSION = {
    "mem": "memory",
    "tok": "token_efficiency",
    "suc": "task_success",
}


def load_tasks() -> dict:
    with open(TASKS_FILE) as f:
        return yaml.safe_load(f)


def get_dimension(task_id: str) -> str:
    prefix = task_id.split("-")[0]
    dim = TASK_PREFIX_TO_DIMENSION.get(prefix)
    if not dim:
        sys.exit(f"无法识别任务维度: {task_id} (prefix={prefix})")
    return dim


def prompt_value(name: str, meta: dict):
    """交互式提示用户输入一个 metric 值。"""
    raw = input(f"  {meta['prompt']} [{name}]: ").strip()
    if not raw:
        return None
    if meta["type"] == bool:
        return raw.lower() in ("y", "yes", "true", "1")
    if meta["type"] == float:
        return float(raw)
    if meta["type"] == int:
        return int(raw)
    return raw


def generate_run_id(agent: str, task_id: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{task_id}_{agent}_{ts}"


def interactive_record(agent: str, task_id: str) -> dict:
    """交互式收集一条测试记录。"""
    dimension = get_dimension(task_id)
    metrics_def = DIMENSION_METRICS[dimension]

    print(f"\n{'='*50}")
    print(f"记录测试: agent={agent}  task={task_id}  dimension={dimension}")
    print(f"{'='*50}")

    metrics = {}
    for name, meta in metrics_def.items():
        val = prompt_value(name, meta)
        if val is not None:
            metrics[name] = val

    notes = input("\n  备注 (可选): ").strip()

    record = {
        "run_id": generate_run_id(agent, task_id),
        "agent": agent,
        "task_id": task_id,
        "dimension": dimension,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "notes": notes,
    }
    return record


def save_record(record: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{record['run_id']}.json"
    path = DATA_DIR / filename
    with open(path, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    return path


def list_tasks():
    tasks = load_tasks()
    for section in ["memory_tasks", "token_tasks", "success_tasks"]:
        print(f"\n{section}:")
        for t in tasks.get(section, []):
            print(f"  {t['id']:10s}  [{t['difficulty']:6s}]  {t['name']}")


def import_file(filepath: str):
    with open(filepath) as f:
        data = json.load(f)
    records = data if isinstance(data, list) else [data]
    for r in records:
        path = save_record(r)
        print(f"  导入: {path.name}")
    print(f"\n共导入 {len(records)} 条记录")


def main():
    parser = argparse.ArgumentParser(description="OpenClaw Benchmark Runner")
    parser.add_argument("--agent", choices=["openclaw", "hermes", "claude-code"], help="Agent 名称")
    parser.add_argument("--task", help="任务 ID (如 mem-01, tok-02, suc-03)")
    parser.add_argument("--list", action="store_true", help="列出所有可用任务")
    parser.add_argument("--import-file", dest="import_path", help="从 JSON 文件批量导入")
    args = parser.parse_args()

    if args.list:
        list_tasks()
        return

    if args.import_path:
        import_file(args.import_path)
        return

    if not args.agent or not args.task:
        parser.print_help()
        print("\n示例:")
        print("  python scripts/run_benchmark.py --agent openclaw --task mem-01")
        print("  python scripts/run_benchmark.py --list")
        return

    record = interactive_record(args.agent, args.task)

    print(f"\n记录预览:")
    print(json.dumps(record, indent=2, ensure_ascii=False))

    confirm = input("\n保存? (y/n): ").strip().lower()
    if confirm in ("y", "yes", ""):
        path = save_record(record)
        print(f"\n✓ 已保存到 {path}")
    else:
        print("已取消")


if __name__ == "__main__":
    main()
