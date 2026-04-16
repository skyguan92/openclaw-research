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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("可用命令:")
        print("  swebench   — SWE-bench Verified 任务成功率评测")
        print("  memory     — 记忆架构评测")
        print("  compare    — 对比分析报告")
        print("  visualize  — 生成图表")
        print("  record     — 手动记录 benchmark 结果")
        print("  smoke      — 快速冒烟测试（验证环境可用）")
        return

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "swebench":
        subprocess.run(
            [sys.executable, "-m", "adapters.swebench_adapter"] + rest,
            cwd=str(ROOT),
        )
    elif cmd == "memory":
        subprocess.run(
            [sys.executable, "-m", "adapters.memory_adapter"] + rest,
            cwd=str(ROOT),
        )
    elif cmd == "compare":
        subprocess.run(
            [sys.executable, "analysis/compare.py"] + rest,
            cwd=str(ROOT),
        )
    elif cmd == "visualize":
        subprocess.run(
            [sys.executable, "analysis/visualize.py"] + rest,
            cwd=str(ROOT),
        )
    elif cmd == "record":
        subprocess.run(
            [sys.executable, "scripts/run_benchmark.py"] + rest,
            cwd=str(ROOT),
        )
    elif cmd == "smoke":
        run_smoke_test()
    else:
        print(f"未知命令: {cmd}")
        print("运行 python run.py 查看帮助")


def run_smoke_test():
    """快速冒烟测试：验证 API 连通 + 所有脚本可用。"""
    print("=" * 50)
    print("冒烟测试")
    print("=" * 50)

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
        else:
            print(f"  OK — {result.tokens_total} tokens, {result.latency_s:.1f}s")
    except Exception as e:
        print(f"  FAIL: {e}")

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

    # 4. Docker (SWE-bench 评测需要)
    print("\n[4/4] 测试 Docker...")
    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if r.returncode == 0:
        print("  OK — Docker 可用")
    else:
        print("  WARN — Docker 不可用（SWE-bench 评测需要 Docker）")

    print("\n" + "=" * 50)
    print("冒烟测试完成")


if __name__ == "__main__":
    main()
