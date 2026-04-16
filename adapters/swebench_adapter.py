"""
SWE-bench Adapter — 把 agent_runner 接上 SWE-bench Verified 评测流程。

流程:
    1. 加载 SWE-bench instance（问题描述 + repo 信息）
    2. 构造 prompt 发给 agent
    3. 从 agent 回复中提取 unified diff patch
    4. 写入 JSONL 预测文件
    5. 调用 swebench harness 评测

用法:
    python -m adapters.swebench_adapter --agent openclaw --limit 5
    python -m adapters.swebench_adapter --agent all --instance-ids sympy__sympy-20590
    python -m adapters.swebench_adapter --evaluate predictions/openclaw.jsonl
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

PREDICTIONS_DIR = Path(__file__).resolve().parent.parent / "swebench_output"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
AGENTS = ["openclaw", "hermes", "claude-code"]


def load_instances(
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    split: str = "test",
    instance_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """加载 SWE-bench 数据集。"""
    from datasets import load_dataset

    dataset = load_dataset(dataset_name, split=split)
    instances = list(dataset)

    if instance_ids:
        instances = [i for i in instances if i["instance_id"] in instance_ids]
    if limit:
        instances = instances[:limit]

    return instances


def build_prompt(instance: dict) -> str:
    """从 SWE-bench instance 构造给 agent 的 prompt。"""
    return f"""You are a software engineer working on the repository: {instance['repo']}.

The following GitHub issue needs to be resolved:

{instance['problem_statement']}

Please analyze the issue and provide a fix as a unified diff patch (git diff format).
Your response MUST include the patch wrapped in a code block like:

```diff
<your patch here>
```

Only output the minimal patch needed to fix the issue. Do not include unrelated changes."""


def extract_patch(response: str) -> str:
    """从 agent 回复中提取 diff patch。"""
    # 尝试 ```diff ... ``` 代码块
    match = re.search(r"```diff\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 尝试任意代码块中包含 diff 内容
    match = re.search(r"```\s*\n(diff --git.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 尝试裸 diff 内容
    match = re.search(r"(diff --git .+)", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    return ""


def run_agent_on_instance(
    agent_name: str, instance: dict, mode: str = "api"
) -> dict:
    """对单个 instance 运行 agent，返回预测记录 + token 统计。"""
    from adapters.agent_runner import run_agent

    prompt = build_prompt(instance)
    result = run_agent(agent_name, prompt, mode=mode, max_tokens=8192)

    patch = extract_patch(result.response)

    prediction = {
        "instance_id": instance["instance_id"],
        "model_name_or_path": agent_name,
        "model_patch": patch,
    }

    # 额外的 token 统计（保存到 data/raw/ 用于分析）
    token_record = {
        "run_id": f"swe_{instance['instance_id']}_{agent_name}_{int(time.time())}",
        "agent": agent_name,
        "task_id": instance["instance_id"],
        "dimension": "token_efficiency",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "metrics": {
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "tokens_total": result.tokens_total,
            "task_completed": bool(patch),
            "latency_s": result.latency_s,
        },
        "notes": f"SWE-bench instance, patch_len={len(patch)}",
    }

    return {"prediction": prediction, "token_record": token_record}


def generate_predictions(
    agent_name: str,
    instances: list[dict],
    mode: str = "api",
) -> Path:
    """为指定 agent 生成所有 instance 的预测，写入 JSONL。"""
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    preds_file = PREDICTIONS_DIR / f"{agent_name}.jsonl"
    total = len(instances)

    with open(preds_file, "w") as f:
        for i, instance in enumerate(instances):
            iid = instance["instance_id"]
            print(f"  [{i + 1}/{total}] {agent_name} → {iid} ...", end=" ", flush=True)

            output = run_agent_on_instance(agent_name, instance, mode=mode)

            # 写预测
            f.write(json.dumps(output["prediction"]) + "\n")
            f.flush()

            # 写 token 统计到 data/raw/
            token_path = RESULTS_DIR / f"{output['token_record']['run_id']}.json"
            with open(token_path, "w") as tf:
                json.dump(output["token_record"], tf, indent=2, ensure_ascii=False)

            status = "✓" if output["prediction"]["model_patch"] else "✗"
            tokens = output["token_record"]["metrics"]["tokens_total"]
            print(f"{status} ({tokens} tokens)")

    print(f"\n预测文件: {preds_file}")
    return preds_file


def run_evaluation(preds_file: Path, run_id: str | None = None) -> None:
    """调用 SWE-bench harness 评测。"""
    if run_id is None:
        run_id = preds_file.stem

    swebench_dir = Path(__file__).resolve().parent.parent / "vendors" / "swe-bench"
    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        "princeton-nlp/SWE-bench_Verified",
        "--predictions_path",
        str(preds_file),
        "--max_workers",
        "4",
        "--run_id",
        run_id,
    ]

    print(f"\n运行 SWE-bench 评测: {run_id}")
    print(f"  预测文件: {preds_file}")
    subprocess.run(cmd, cwd=str(swebench_dir), check=True)
    print(f"\n评测完成。结果在: evaluation_results/{run_id}/")


def main():
    parser = argparse.ArgumentParser(description="SWE-bench Adapter")
    parser.add_argument(
        "--agent",
        choices=AGENTS + ["all"],
        help="运行的 agent（或 all）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只跑前 N 个 instance",
    )
    parser.add_argument(
        "--instance-ids",
        nargs="+",
        default=None,
        help="指定 instance ID",
    )
    parser.add_argument(
        "--dataset",
        default="princeton-nlp/SWE-bench_Verified",
        help="数据集名称",
    )
    parser.add_argument(
        "--mode",
        choices=["api", "cli"],
        default="api",
        help="agent 调用模式",
    )
    parser.add_argument(
        "--evaluate",
        type=str,
        default=None,
        help="直接评测已有的预测文件",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="评测 run ID",
    )
    args = parser.parse_args()

    # 仅评测模式
    if args.evaluate:
        run_evaluation(Path(args.evaluate), args.run_id)
        return

    if not args.agent:
        parser.print_help()
        return

    # 加载数据
    print("加载 SWE-bench 数据集...")
    instances = load_instances(
        dataset_name=args.dataset,
        instance_ids=args.instance_ids,
        limit=args.limit,
    )
    print(f"  共 {len(instances)} 个 instance\n")

    # 运行 agent
    agents_to_run = AGENTS if args.agent == "all" else [args.agent]
    for agent_name in agents_to_run:
        print(f"{'='*50}")
        print(f"Agent: {agent_name}")
        print(f"{'='*50}")
        generate_predictions(agent_name, instances, mode=args.mode)


if __name__ == "__main__":
    main()
