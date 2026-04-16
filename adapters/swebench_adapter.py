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
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from adapters.openclaw_workspace import (
    DEFAULT_OPENCLAW_MODEL,
    DEFAULT_REPO_CACHE_DIR,
    DEFAULT_REPO_MENTIONED_ROOT,
    DEFAULT_WORKSPACE_ROOT,
    build_repo_mentioned_prompt,
    capture_repo_mentioned_patch,
    materialize_repo_mentioned_workspace,
    run_openclaw_repo_mentioned,
    run_openclaw_workspace,
)
from adapters.runtime_state import DEFAULT_RUNTIME_STATE_ROOT, prepare_runtime_state

PREDICTIONS_DIR = Path(__file__).resolve().parent.parent / "swebench_output"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
AGENTS = ["openclaw", "hermes", "claude-code"]


def _infer_run_id(preds_file: Path) -> str:
    parts = preds_file.stem.split(".", 1)
    return parts[1] if len(parts) == 2 else preds_file.stem


def _load_prediction_agent(preds_file: Path) -> str | None:
    with open(preds_file) as f:
        first_line = f.readline().strip()
    if not first_line:
        return None
    return json.loads(first_line).get("model_name_or_path")


def _docker_env_for_harness() -> dict[str, str]:
    """
    让 docker-py 跟随当前 Docker CLI context。

    Docker CLI 可以从 ~/.docker context 配置里解析 colima 等 endpoint，
    但 docker.from_env() 只看环境变量。这里把当前 context 的 Host 注入子进程。
    """
    env = dict(os.environ)
    docker_host = env.get("DOCKER_HOST")
    if docker_host:
        if not docker_host.startswith("unix://"):
            return env
        socket_path = Path(docker_host.removeprefix("unix://"))
        if socket_path.exists():
            return env
        env.pop("DOCKER_HOST", None)

    try:
        current_host = subprocess.run(
            ["docker", "context", "show"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not current_host:
            return env
    except Exception:
        return env

    try:
        payload = subprocess.run(
            ["docker", "context", "inspect", current_host],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        inspect = json.loads(payload)
        host = inspect[0].get("Endpoints", {}).get("docker", {}).get("Host")
        if host:
            env["DOCKER_HOST"] = host
    except Exception:
        pass

    return env


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
    agent_name: str,
    instance: dict,
    mode: str = "api",
    run_group: str | None = None,
    experiment_id: str | None = None,
    round_index: int | None = None,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    repo_cache_dir: Path = DEFAULT_REPO_CACHE_DIR,
    repo_mentioned_root: Path = DEFAULT_REPO_MENTIONED_ROOT,
    timeout: int | None = 1800,
    openclaw_model: str = DEFAULT_OPENCLAW_MODEL,
    runtime_profile: str = "default",
    runtime_state_root: Path = DEFAULT_RUNTIME_STATE_ROOT,
    reset_runtime_state: bool = False,
) -> dict:
    """对单个 instance 运行 agent，返回预测记录 + token 统计。"""
    run_group = run_group or time.strftime("%Y%m%d_%H%M%S")
    experiment_id = experiment_id or run_group
    runtime_memory_enabled = runtime_profile == "memory-enabled"
    runtime_state_dir = None
    if runtime_memory_enabled:
        runtime_state_dir = prepare_runtime_state(
            agent_name,
            experiment_id,
            instance["instance_id"],
            root=runtime_state_root,
            reset=reset_runtime_state,
        )
    prompt = build_prompt(instance)

    if mode == "workspace":
        if agent_name != "openclaw":
            raise ValueError("workspace mode is only supported for agent=openclaw")
        result = run_openclaw_workspace(
            instance,
            run_group=run_group,
            experiment_id=experiment_id if runtime_memory_enabled else None,
            timeout=timeout,
            workspace_root=workspace_root,
            repo_cache_dir=repo_cache_dir,
            model=openclaw_model,
            state_dir=runtime_state_dir,
        )
        patch = result.patch
    elif mode == "repo-mentioned":
        if agent_name == "openclaw":
            result = run_openclaw_repo_mentioned(
                instance,
                run_group=run_group,
                experiment_id=experiment_id if runtime_memory_enabled else None,
                timeout=timeout,
                workspace_root=repo_mentioned_root,
                repo_cache_dir=repo_cache_dir,
                model=openclaw_model,
                state_dir=runtime_state_dir,
            )
            patch = result.patch
        else:
            from adapters.agent_runner import run_agent

            workspace = materialize_repo_mentioned_workspace(
                instance,
                run_group=run_group,
                workspace_root=repo_mentioned_root,
                repo_cache_dir=repo_cache_dir,
            )
            prompt = build_repo_mentioned_prompt(instance)
            result = run_agent(
                agent_name,
                prompt,
                mode="cli",
                cwd=str(workspace),
                timeout=timeout,
                runtime_state_dir=runtime_state_dir,
                runtime_memory_enabled=runtime_memory_enabled,
            )
            result.raw["workspace"] = str(workspace)
            patch = capture_repo_mentioned_patch(workspace, instance, result.response)
    else:
        from adapters.agent_runner import run_agent

        run_kwargs = {"max_tokens": 8192} if mode == "api" else {"timeout": timeout}
        if mode == "cli":
            run_kwargs["runtime_state_dir"] = runtime_state_dir
            run_kwargs["runtime_memory_enabled"] = runtime_memory_enabled
        result = run_agent(agent_name, prompt, mode=mode, **run_kwargs)
        patch = extract_patch(result.response)

    prediction = {
        "instance_id": instance["instance_id"],
        "model_name_or_path": agent_name,
        "model_patch": patch,
    }

    # 额外的 token 统计（保存到 data/raw/ 用于分析）
    token_record = {
        "run_id": f"swe_{instance['instance_id']}_{agent_name}_{run_group}",
        "run_group": run_group,
        "agent": agent_name,
        "task_id": instance["instance_id"],
        "dimension": "token_efficiency",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "metrics": {
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "tokens_total": result.tokens_total,
            "task_completed": bool(patch),
            "tool_calls_count": result.tool_calls,
            "latency_s": result.latency_s,
        },
        "notes": f"SWE-bench instance, mode={mode}, patch_len={len(patch)}",
    }
    token_record["experiment_id"] = experiment_id
    token_record["round"] = round_index or 1
    token_record["runtime_profile"] = runtime_profile
    if result.error:
        token_record["error"] = result.error
    if mode in {"workspace", "repo-mentioned"}:
        token_record["notes"] += f", workspace={result.raw.get('workspace', '')}"
        if result.raw.get("session_id"):
            token_record["notes"] += f", session={result.raw.get('session_id', '')}"
    if runtime_state_dir:
        token_record["runtime_state_dir"] = str(runtime_state_dir)

    return {"prediction": prediction, "token_record": token_record}


def generate_predictions(
    agent_name: str,
    instances: list[dict],
    mode: str = "api",
    run_group: str | None = None,
    experiment_id: str | None = None,
    round_index: int | None = None,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    repo_cache_dir: Path = DEFAULT_REPO_CACHE_DIR,
    repo_mentioned_root: Path = DEFAULT_REPO_MENTIONED_ROOT,
    timeout: int | None = 1800,
    openclaw_model: str = DEFAULT_OPENCLAW_MODEL,
    runtime_profile: str = "default",
    runtime_state_root: Path = DEFAULT_RUNTIME_STATE_ROOT,
    reset_runtime_state: bool = False,
) -> Path:
    """为指定 agent 生成所有 instance 的预测，写入 JSONL。"""
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    run_group = run_group or time.strftime("%Y%m%d_%H%M%S")
    preds_file = PREDICTIONS_DIR / f"{agent_name}.{run_group}.jsonl"
    total = len(instances)

    with open(preds_file, "w") as f:
        for i, instance in enumerate(instances):
            iid = instance["instance_id"]
            print(f"  [{i + 1}/{total}] {agent_name} → {iid} ...", end=" ", flush=True)

            output = run_agent_on_instance(
                agent_name,
                instance,
                mode=mode,
                run_group=run_group,
                experiment_id=experiment_id,
                round_index=round_index,
                workspace_root=workspace_root,
                repo_cache_dir=repo_cache_dir,
                repo_mentioned_root=repo_mentioned_root,
                timeout=timeout,
                openclaw_model=openclaw_model,
                runtime_profile=runtime_profile,
                runtime_state_root=runtime_state_root,
                reset_runtime_state=reset_runtime_state,
            )

            # 写预测
            f.write(json.dumps(output["prediction"]) + "\n")
            f.flush()

            # 写 token 统计到 data/raw/
            token_path = RESULTS_DIR / f"{output['token_record']['run_id']}.json"
            with open(token_path, "w") as tf:
                json.dump(output["token_record"], tf, indent=2, ensure_ascii=False)

            error = output["token_record"].get("error")
            if error:
                status = "ERR"
            else:
                status = "✓" if output["prediction"]["model_patch"] else "✗"
            tokens = output["token_record"]["metrics"]["tokens_total"]
            suffix = f"{status} ({tokens} tokens)"
            if error:
                suffix += f" error={error}"
            print(suffix)

    print(f"\n预测文件: {preds_file}")
    return preds_file


def import_evaluation_summary(report_file: Path, *, agent_name: str, run_group: str) -> list[Path]:
    """把 SWE-bench 评测摘要导入 data/raw/，让 compare 能统计 task_success。"""
    with open(report_file) as f:
        report = json.load(f)

    statuses = {}
    for instance_id in report.get("resolved_ids", []):
        statuses[instance_id] = "pass"
    for instance_id in report.get("unresolved_ids", []):
        statuses[instance_id] = "fail"
    for instance_id in report.get("error_ids", []):
        statuses[instance_id] = "error"
    for instance_id in report.get("empty_patch_ids", []):
        statuses.setdefault(instance_id, "fail")

    saved_paths = []
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for instance_id, status in sorted(statuses.items()):
        record = {
            "run_id": f"suc_{instance_id}_{agent_name}_{run_group}",
            "run_group": run_group,
            "agent": agent_name,
            "task_id": instance_id,
            "dimension": "task_success",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "metrics": {
                "result": status,
                "error_encountered": status == "error",
            },
            "notes": f"SWE-bench evaluation imported from {report_file.name}",
        }
        path = RESULTS_DIR / f"{record['run_id']}.json"
        with open(path, "w") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        saved_paths.append(path)

    return saved_paths


def run_evaluation(preds_file: Path, run_id: str | None = None) -> None:
    """调用 SWE-bench harness 评测。"""
    preds_file = preds_file.resolve()
    if run_id is None:
        run_id = _infer_run_id(preds_file)

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
    subprocess.run(
        cmd,
        cwd=str(swebench_dir),
        check=True,
        env=_docker_env_for_harness(),
    )
    print(f"\n评测完成。结果在: evaluation_results/{run_id}/")

    agent_name = _load_prediction_agent(preds_file)
    if not agent_name:
        print("  WARN — 无法从预测文件识别 agent，跳过导入 task_success 记录")
        return

    report_file = swebench_dir / f"{agent_name.replace('/', '__')}.{run_id}.json"
    if not report_file.exists():
        print(f"  WARN — 未找到评测摘要文件: {report_file}")
        return

    saved = import_evaluation_summary(report_file, agent_name=agent_name, run_group=run_id)
    print(f"  已导入 {len(saved)} 条 task_success 记录到 data/raw/")


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
        choices=["api", "cli", "workspace", "repo-mentioned"],
        default="api",
        help="agent 调用模式；workspace=绑定本地 repo，repo-mentioned=prompt 里只显式说明 repo",
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
        help="本次预测/评测的 run ID；不传则自动生成",
    )
    parser.add_argument(
        "--workspace-root",
        default=str(DEFAULT_WORKSPACE_ROOT),
        help="workspace mode 下实例工作区根目录",
    )
    parser.add_argument(
        "--repo-mentioned-root",
        default=str(DEFAULT_REPO_MENTIONED_ROOT),
        help="repo-mentioned mode 下的空白工作区根目录",
    )
    parser.add_argument(
        "--repo-cache-dir",
        default=str(DEFAULT_REPO_CACHE_DIR),
        help="workspace mode 下 Git mirror 缓存目录",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="agent 超时时间（秒）；传 0 表示不限时",
    )
    parser.add_argument(
        "--openclaw-model",
        default=DEFAULT_OPENCLAW_MODEL,
        help="workspace mode 下 OpenClaw agent 使用的 model id",
    )
    parser.add_argument(
        "--runtime-profile",
        choices=["default", "memory-enabled"],
        default="default",
        help="runtime 协议；memory-enabled 会启用隔离持久 state，并按轮次复用",
    )
    parser.add_argument(
        "--runtime-state-root",
        default=str(DEFAULT_RUNTIME_STATE_ROOT),
        help="memory-enabled 协议下的隔离 runtime state 根目录",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="重复运行轮数；memory-enabled 下第 1 轮清空 memory，后续轮次继承",
    )
    parser.add_argument(
        "--keep-runtime-state",
        action="store_true",
        help="不要在第 1 轮前重置隔离 runtime state（默认会清空）",
    )
    args = parser.parse_args()

    # 仅评测模式
    if args.evaluate:
        run_evaluation(Path(args.evaluate), args.run_id)
        return

    if not args.agent:
        parser.print_help()
        return

    if args.mode == "workspace" and args.agent != "openclaw":
        raise SystemExit("workspace mode 目前只支持 --agent openclaw")
    if args.rounds < 1:
        raise SystemExit("--rounds 必须 >= 1")

    # 加载数据
    print("加载 SWE-bench 数据集...")
    instances = load_instances(
        dataset_name=args.dataset,
        instance_ids=args.instance_ids,
        limit=args.limit,
    )
    print(f"  共 {len(instances)} 个 instance\n")
    experiment_id = args.run_id or time.strftime("%Y%m%d_%H%M%S")
    timeout = None if args.timeout <= 0 else args.timeout

    # 运行 agent
    agents_to_run = AGENTS if args.agent == "all" else [args.agent]
    for round_index in range(1, args.rounds + 1):
        round_run_group = (
            experiment_id if args.rounds == 1 else f"{experiment_id}_r{round_index:02d}"
        )
        if args.rounds > 1:
            print(f"{'#'*50}")
            print(f"Round {round_index}/{args.rounds} — experiment={experiment_id}")
            print(f"{'#'*50}")
        for agent_name in agents_to_run:
            print(f"{'='*50}")
            print(f"Agent: {agent_name} (mode={args.mode}, run_id={round_run_group})")
            print(f"{'='*50}")
            generate_predictions(
                agent_name,
                instances,
                mode=args.mode,
                run_group=round_run_group,
                experiment_id=experiment_id,
                round_index=round_index,
                workspace_root=Path(args.workspace_root),
                repo_cache_dir=Path(args.repo_cache_dir),
                repo_mentioned_root=Path(args.repo_mentioned_root),
                timeout=timeout,
                openclaw_model=args.openclaw_model,
                runtime_profile=args.runtime_profile,
                runtime_state_root=Path(args.runtime_state_root),
                reset_runtime_state=(
                    args.runtime_profile == "memory-enabled"
                    and round_index == 1
                    and not args.keep_runtime_state
                ),
            )


if __name__ == "__main__":
    main()
