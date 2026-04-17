#!/usr/bin/env python3
"""
OpenClaw Research — 统一运行入口。

用法:
    python run.py swebench  --agent openclaw --limit 5
    python run.py memory    --agent all --quick-test
    python run.py compare   --demo
    python run.py visualize --demo

快速冒烟测试:
    python run.py smoke
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run_child(args: list[str]) -> int:
    """运行子命令并透传退出码。"""
    result = subprocess.run(args, cwd=str(ROOT))
    return result.returncode


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("可用命令:")
        print("  swebench   — SWE-bench Verified 评测（成功率 + token + memory 多轮）")
        print("  curve      — 多轮 memory 行为曲线分析")
        print("  compare    — 对比分析报告")
        print("  visualize  — 生成图表")
        print("  record     — 手动记录 benchmark 结果")
        print("  smoke      — 快速冒烟测试（验证环境可用）")
        print("  memory     — (deprecated) MemoryAgentBench quick-test")
        return 0

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "swebench":
        return _run_child([sys.executable, "-m", "adapters.swebench_adapter"] + rest)
    elif cmd == "curve":
        return _run_child([sys.executable, "-m", "analysis.memory_curve"] + rest)
    elif cmd == "memory":
        print("WARN: `memory` command is deprecated. Memory effects are now measured")
        print("      as multi-round deltas via `python run.py curve`. See README.")
        return _run_child([sys.executable, "-m", "adapters.memory_adapter"] + rest)
    elif cmd == "compare":
        return _run_child([sys.executable, "analysis/compare.py"] + rest)
    elif cmd == "visualize":
        return _run_child([sys.executable, "analysis/visualize.py"] + rest)
    elif cmd == "record":
        return _run_child([sys.executable, "scripts/run_benchmark.py"] + rest)
    elif cmd == "smoke":
        return run_smoke_test()
    else:
        print(f"未知命令: {cmd}")
        print("运行 python run.py 查看帮助")
        return 1


def run_smoke_test():
    """快速冒烟测试：验证 API 连通 + 所有脚本可用。"""
    print("=" * 50)
    print("冒烟测试")
    print("=" * 50)
    failures = 0

    # 1. API 连通性
    print("\n[1/4] 测试 Kimi API 连通性...")
    try:
        from adapters.agent_runner import call_kimi_api

        result = call_kimi_api(
            [{"role": "user", "content": "Reply with exactly: SMOKE_OK"}],
            max_tokens=20,
        )
        if result.error:
            print(f"  FAIL: {result.error}")
            failures += 1
        else:
            print(f"  OK — {result.tokens_total} tokens, {result.latency_s:.1f}s")
    except Exception as e:
        print(f"  FAIL: {e}")
        failures += 1

    # 2. SWE-bench 数据集
    print("\n[2/4] 测试 SWE-bench 数据加载...")
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "princeton-nlp/SWE-bench_Verified",
            split="test",
            streaming=True,
        )
        first = next(iter(ds))
        print(f"  OK — 首条 instance: {first['instance_id']}")
    except Exception as e:
        print(f"  FAIL: {e}")
        print("  提示: pip install datasets")
        failures += 1

    # 3. 分析脚本
    print("\n[3/4] 测试分析脚本...")
    r = subprocess.run(
        [sys.executable, "analysis/compare.py", "--demo"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if r.returncode == 0:
        print("  OK — compare.py --demo 正常")
    else:
        print(f"  FAIL: {r.stderr[:200]}")
        failures += 1

    # 4. Docker (SWE-bench 评测需要)
    print("\n[4/4] 测试 Docker...")
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if r.returncode == 0:
            print("  OK — Docker 可用")
        else:
            print("  WARN — Docker 不可用（SWE-bench 评测需要 Docker）")
    except FileNotFoundError:
        print("  WARN — 未找到 Docker 命令（SWE-bench 评测需要 Docker）")

    print("\n" + "=" * 50)
    print("冒烟测试完成")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
