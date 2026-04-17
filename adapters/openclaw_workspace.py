"""
OpenClaw SWE-bench workspace runner.

这不是 text-only patch 生成，而是在本地 git workspace 中运行 OpenClaw agent，
让它直接读写仓库文件，然后从工作区导出 patch。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from adapters.agent_runner import AgentResult
from adapters.runtime_state import build_runtime_env, resolve_openclaw_state_dir

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO_CACHE_DIR = ROOT / "data" / "cache" / "swebench_repos"
DEFAULT_WORKSPACE_ROOT = ROOT / "data" / "workspaces" / "swebench"
DEFAULT_REPO_MENTIONED_ROOT = ROOT / "data" / "workspaces" / "swebench_unbound"
DEFAULT_OPENCLAW_MODEL = "kimi/kimi-for-coding"
OPENCLAW_BOOTSTRAP_FILES = {
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
}
OPENCLAW_IGNORED_PATH_PREFIXES = {
    ".openclaw",
    "memory",
}


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        details = stderr or stdout or f"exit={result.returncode}"
        raise RuntimeError(f"{' '.join(cmd)} failed: {details}")
    return result


def _extract_json_blob(*texts: str) -> dict:
    """从 OpenClaw stdout/stderr 混合日志中提取最终 JSON payload。"""
    decoder = json.JSONDecoder()
    for text in texts:
        if not text:
            continue
        brace_positions = [idx for idx, char in enumerate(text) if char == "{"]
        for start in reversed(brace_positions):
            try:
                payload, _ = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and ("payloads" in payload or "meta" in payload):
                return payload
    raise ValueError("JSON payload not found")


def _approx_token_count(text: str) -> int:
    """Cheap fallback token estimator for runtimes that omit output usage."""
    if not text:
        return 0
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def _agent_id(run_group: str, instance_id: str) -> str:
    digest = hashlib.sha1(f"{run_group}:{instance_id}".encode("utf-8")).hexdigest()[:12]
    return f"swebench-{digest}"


def _mirror_path(repo: str, repo_cache_dir: Path) -> Path:
    return repo_cache_dir / f"{repo.replace('/', '__')}.git"


def _clean_env(*, state_dir: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)
    env.pop("USER_TYPE", None)
    env.update(build_runtime_env("openclaw", state_dir))
    return env


def ensure_repo_mirror(repo: str, *, repo_cache_dir: Path = DEFAULT_REPO_CACHE_DIR) -> Path:
    """缓存裸仓库镜像，避免每个 instance 都重新 clone GitHub。"""
    repo_cache_dir.mkdir(parents=True, exist_ok=True)
    mirror = _mirror_path(repo, repo_cache_dir)
    repo_url = f"https://github.com/{repo}.git"

    if mirror.exists():
        try:
            _run(["git", "remote", "update", "--prune"], cwd=mirror)
        except RuntimeError:
            shutil.rmtree(mirror)
            _run(["git", "clone", "--mirror", repo_url, str(mirror)])
    else:
        _run(["git", "clone", "--mirror", repo_url, str(mirror)])

    return mirror


def _prune_future_refs(workspace: Path) -> None:
    """删除远端/分支/tag refs，尽量把工作区限制在 base commit 视角。"""
    refs = _run(
        ["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes", "refs/tags"],
        cwd=workspace,
    ).stdout.splitlines()

    for ref in refs:
        ref = ref.strip()
        if ref:
            _run(["git", "update-ref", "-d", ref], cwd=workspace)

    _run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=workspace)
    _run(["git", "gc", "--prune=now"], cwd=workspace)


def materialize_instance_workspace(
    instance: dict,
    *,
    run_group: str,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    repo_cache_dir: Path = DEFAULT_REPO_CACHE_DIR,
) -> Path:
    """
    为指定 instance 创建一个真实本地工作区。

    过程：
      1. 从本地 mirror clone
      2. checkout 到 base_commit
      3. 去掉未来 refs，降低信息泄露
    """
    workspace = workspace_root / run_group / instance["instance_id"]
    mirror = ensure_repo_mirror(instance["repo"], repo_cache_dir=repo_cache_dir)

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)

    _run(["git", "clone", "--quiet", str(mirror), str(workspace)])
    _run(
        ["git", "-c", "advice.detachedHead=false", "checkout", "--quiet", instance["base_commit"]],
        cwd=workspace,
    )
    _run(["git", "remote", "remove", "origin"], cwd=workspace)
    _run(["git", "config", "user.email", "openclaw@swebench.local"], cwd=workspace)
    _run(["git", "config", "user.name", "OpenClaw SWE-bench"], cwd=workspace)
    _prune_future_refs(workspace)

    return workspace


def ensure_openclaw_agent(
    agent_id: str,
    *,
    workspace: Path,
    model: str = DEFAULT_OPENCLAW_MODEL,
    state_dir: Path | None = None,
) -> None:
    """确保存在一个指向目标 workspace 的 OpenClaw agent。"""
    env = _clean_env(state_dir=state_dir)
    result = _run(["openclaw", "agents", "list", "--json"], check=True, env=env)
    agents = json.loads(result.stdout)
    for agent in agents:
        if agent.get("id") != agent_id:
            continue
        configured_workspace = agent.get("workspace")
        if configured_workspace and Path(configured_workspace) != workspace:
            raise RuntimeError(
                f"OpenClaw agent {agent_id} already exists with workspace {configured_workspace}, "
                f"expected {workspace}"
            )
        return

    _run(
        [
            "openclaw",
            "agents",
            "add",
            agent_id,
            "--workspace",
            str(workspace),
            "--model",
            model,
            "--non-interactive",
            "--json",
        ],
        env=env,
    )


def build_workspace_prompt(instance: dict) -> str:
    return "\n".join(
        [
            "Please fix the following issue in the current repository.",
            "",
            instance["problem_statement"].strip(),
        ]
    )


def build_repo_mentioned_prompt(instance: dict) -> str:
    project_name = instance["repo"].split("/")[-1]
    return "\n".join(
        [
            f"There is a project folder on this machine named `{project_name}`.",
            "Please fix the following issue in that project.",
            "",
            instance["problem_statement"].strip(),
        ]
    )


def materialize_repo_mentioned_workspace(
    instance: dict,
    *,
    run_group: str,
    workspace_namespace: str | None = None,
    workspace_root: Path = DEFAULT_REPO_MENTIONED_ROOT,
    repo_cache_dir: Path = DEFAULT_REPO_CACHE_DIR,
) -> Path:
    """为 repo-mentioned 模式创建工作区，并放一个同名本地项目目录。"""
    workspace = workspace_root / (workspace_namespace or run_group) / instance["instance_id"]
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    project_name = instance["repo"].split("/")[-1]
    project_dir = workspace / project_name
    mirror = ensure_repo_mirror(instance["repo"], repo_cache_dir=repo_cache_dir)

    _run(["git", "clone", "--quiet", str(mirror), str(project_dir)])
    _run(
        ["git", "-c", "advice.detachedHead=false", "checkout", "--quiet", instance["base_commit"]],
        cwd=project_dir,
    )
    _run(["git", "remote", "remove", "origin"], cwd=project_dir)
    _run(["git", "config", "user.email", "openclaw@swebench.local"], cwd=project_dir)
    _run(["git", "config", "user.name", "OpenClaw SWE-bench"], cwd=project_dir)
    _prune_future_refs(project_dir)

    return workspace


def _session_key(agent_id: str) -> str:
    return f"agent:{agent_id}:main"


def _session_dir(agent_id: str, *, state_dir: Path | None = None) -> Path:
    return resolve_openclaw_state_dir(state_dir) / "agents" / agent_id / "sessions"


def _load_session_index(agent_id: str, *, state_dir: Path | None = None) -> dict:
    index_file = _session_dir(agent_id, state_dir=state_dir) / "sessions.json"
    if not index_file.exists():
        return {}
    try:
        payload = json.loads(index_file.read_text())
    except json.JSONDecodeError:
        return {}
    return payload.get(_session_key(agent_id), {})


def _snapshot_session_state(agent_id: str, *, state_dir: Path | None = None) -> dict:
    meta = _load_session_index(agent_id, state_dir=state_dir)
    session_id = meta.get("sessionId")
    session_file = meta.get("sessionFile")
    if session_file:
        session_path = Path(session_file)
    elif session_id:
        session_path = _session_dir(agent_id, state_dir=state_dir) / f"{session_id}.jsonl"
    else:
        session_path = None
    size = session_path.stat().st_size if session_path and session_path.exists() else 0
    return {
        "meta": meta,
        "session_id": session_id,
        "session_file": session_path,
        "size": size,
        "updated_at": meta.get("updatedAt"),
    }


def _wait_for_session_state(
    agent_id: str,
    previous_updated_at: int | None,
    *,
    state_dir: Path | None = None,
    timeout_s: float = 3.0,
) -> dict:
    deadline = time.time() + timeout_s
    latest = _snapshot_session_state(agent_id, state_dir=state_dir)
    while time.time() < deadline:
        latest = _snapshot_session_state(agent_id, state_dir=state_dir)
        if latest["session_id"] and (
            previous_updated_at is None or latest["updated_at"] != previous_updated_at
        ):
            return latest
        time.sleep(0.1)
    return latest


def _read_appended_session_events(before: dict, after: dict) -> list[dict]:
    session_path = after.get("session_file")
    if not session_path or not session_path.exists():
        return []

    start = 0
    if (
        before.get("session_id")
        and before.get("session_id") == after.get("session_id")
        and before.get("session_file") == session_path
    ):
        start = before.get("size", 0)

    with open(session_path) as f:
        if start:
            f.seek(start)
        lines = f.read().splitlines()

    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _summarize_session_events(events: list[dict]) -> dict:
    response = ""
    tool_calls = 0
    usage = {}
    tokens_in = 0
    reported_tokens_out = 0
    cache_read = 0
    cache_write = 0
    estimated_tokens_out = 0

    for event in events:
        if event.get("type") != "message":
            continue
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            continue

        if isinstance(message.get("usage"), dict):
            usage = message["usage"]
            tokens_in += int(usage.get("input", 0) or 0)
            reported_tokens_out += int(usage.get("output", 0) or 0)
            cache_read += int(usage.get("cacheRead", 0) or 0)
            cache_write += int(usage.get("cacheWrite", 0) or 0)

        output_parts: list[str] = []
        text_parts = []
        for item in message.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "toolCall":
                tool_calls += 1
                if item.get("name"):
                    output_parts.append(str(item["name"]))
                if item.get("id"):
                    output_parts.append(str(item["id"]))
                arguments = item.get("arguments", item.get("input", item.get("args")))
                if arguments is not None:
                    try:
                        output_parts.append(
                            json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                        )
                    except TypeError:
                        output_parts.append(str(arguments))
            elif item.get("type") == "text" and item.get("text"):
                text_parts.append(item["text"].strip())
                output_parts.append(item["text"])
        if text_parts:
            response = "\n\n".join(part for part in text_parts if part)
        if output_parts:
            estimated_tokens_out += _approx_token_count("\n".join(output_parts))

    effective_tokens_out = reported_tokens_out or estimated_tokens_out
    reported_tokens_total = tokens_in + reported_tokens_out + cache_read + cache_write
    effective_tokens_total = tokens_in + effective_tokens_out + cache_read + cache_write
    return {
        "response": response,
        "tool_calls": tool_calls,
        "tokens_in": tokens_in,
        "tokens_out": effective_tokens_out,
        "reported_tokens_out": reported_tokens_out,
        "estimated_tokens_out": estimated_tokens_out,
        "tokens_total": effective_tokens_total,
        "reported_tokens_total": reported_tokens_total,
        "effective_tokens_total": effective_tokens_total,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "last_usage": usage,
    }


def _capture_untracked_file_patch(workspace: Path, relpath: str) -> str:
    result = _run(
        ["git", "diff", "--binary", "--no-index", "--", "/dev/null", relpath],
        cwd=workspace,
        check=False,
    )
    if result.returncode not in (0, 1):
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"failed to diff new file {relpath}: {stderr}")
    return result.stdout.strip()


def _extract_patch_from_response(response: str) -> str:
    import re

    match = re.search(r"```diff\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"

    match = re.search(r"```\s*\n(diff --git.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"

    match = re.search(r"(diff --git .+)", response, re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"

    return ""


def capture_workspace_patch(workspace: Path) -> str:
    """导出 tracked diff + 非 bootstrap 的新增文件。"""
    exclude_args = [f":(exclude){name}" for name in sorted(OPENCLAW_BOOTSTRAP_FILES)]
    for prefix in sorted(OPENCLAW_IGNORED_PATH_PREFIXES):
        exclude_args.extend([f":(exclude){prefix}", f":(exclude){prefix}/**"])
    tracked = _run(
        [
            "git",
            "-c",
            "core.fileMode=false",
            "diff",
            "--binary",
            "--relative",
            "HEAD",
            "--",
            ".",
            *exclude_args,
        ],
        cwd=workspace,
    ).stdout.strip()

    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"], cwd=workspace).stdout.splitlines()
    new_file_patches = []
    for relpath in sorted(untracked):
        relpath = relpath.strip()
        if not relpath:
            continue
        rel = Path(relpath)
        if rel.name in OPENCLAW_BOOTSTRAP_FILES:
            continue
        if rel.parts and rel.parts[0] in OPENCLAW_IGNORED_PATH_PREFIXES:
            continue
        new_file_patches.append(_capture_untracked_file_patch(workspace, relpath))

    chunks = [chunk for chunk in [tracked, *new_file_patches] if chunk]
    patch = "\n\n".join(chunks)
    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch


def _find_git_repos(root: Path) -> list[Path]:
    repos = {git_dir.parent for git_dir in root.rglob(".git")}
    return sorted(repos, key=lambda repo: (len(repo.parts), str(repo)))


def _capture_repo_mentioned_patch(workspace: Path, instance: dict, response: str) -> str:
    repo_name = instance["repo"].split("/")[-1]
    repos = _find_git_repos(workspace)
    preferred = [repo for repo in repos if repo.name == repo_name]
    candidates = preferred or repos

    for repo in candidates:
        try:
            patch = capture_workspace_patch(repo)
        except Exception:
            continue
        if patch:
            return patch

    return _extract_patch_from_response(response)


def capture_repo_mentioned_patch(workspace: Path, instance: dict, response: str) -> str:
    """导出 repo-mentioned 场景下的 patch。"""
    return _capture_repo_mentioned_patch(workspace, instance, response)


def _run_openclaw_agent(
    *,
    workspace: Path,
    agent_id: str,
    prompt: str,
    timeout: int | None,
    model: str,
    state_dir: Path | None = None,
) -> AgentResult:
    env = _clean_env(state_dir=state_dir)
    ensure_openclaw_agent(agent_id, workspace=workspace, model=model, state_dir=state_dir)
    before_session = _snapshot_session_state(agent_id, state_dir=state_dir)
    cmd = [
        "openclaw",
        "agent",
        "--local",
        "--agent",
        agent_id,
        "--json",
        "--thinking",
        "off",
        "--verbose",
        "off",
        "-m",
        prompt,
    ]
    if timeout:
        cmd[10:10] = ["--timeout", str(timeout)]
    wall_t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=(timeout + 60) if timeout else None,
        )
        process_error = None
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        result = None
        process_error = "timeout"
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""

    elapsed_s = time.time() - wall_t0
    after_session = _wait_for_session_state(
        agent_id,
        before_session.get("updated_at"),
        state_dir=state_dir,
    )
    session_events = _read_appended_session_events(before_session, after_session)
    session_summary = _summarize_session_events(session_events)

    payload = None
    payload_error = None
    try:
        payload = _extract_json_blob(stdout, stderr)
    except Exception as exc:
        payload_error = str(exc)

    meta = payload.get("meta", {}) if payload else {}
    agent_meta = meta.get("agentMeta", {})
    usage = agent_meta.get("lastCallUsage") or agent_meta.get("usage") or {}
    response = ""
    if payload:
        response = "\n\n".join(
            p.get("text", "").strip()
            for p in payload.get("payloads", [])
            if p.get("text")
        ).strip()
    if not response:
        response = session_summary["response"]
    final_call_in = int(usage.get("input", agent_meta.get("promptTokens", 0)) or 0)
    final_call_out = int(usage.get("output", 0) or 0)
    final_call_total = int(usage.get("total", usage.get("totalTokens", final_call_in + final_call_out)) or (final_call_in + final_call_out))
    tokens_in = session_summary["tokens_in"] or final_call_in or int(after_session["meta"].get("inputTokens", 0) or 0)
    reported_tokens_out = (
        session_summary.get("reported_tokens_out", 0)
        or final_call_out
        or int(after_session["meta"].get("outputTokens", 0) or 0)
    )
    estimated_tokens_out = session_summary.get("estimated_tokens_out", 0)
    tokens_out = reported_tokens_out or estimated_tokens_out
    cache_read = session_summary.get("cache_read_tokens", 0)
    cache_write = session_summary.get("cache_write_tokens", 0)
    reported_tokens_total = (
        session_summary.get("reported_tokens_total", 0)
        or final_call_total
        or int(after_session["meta"].get("totalTokens", 0) or (tokens_in + reported_tokens_out + cache_read + cache_write))
    )
    tokens_total = session_summary.get("effective_tokens_total", 0) or int(
        after_session["meta"].get("totalTokens", 0) or (tokens_in + tokens_out + cache_read + cache_write)
    )
    tool_calls = int(meta.get("toolSummary", {}).get("calls", 0) or session_summary["tool_calls"] or 0)
    duration_ms = meta.get("durationMs", 0) or 0
    latency_s = duration_ms / 1000 if duration_ms else elapsed_s
    error = process_error
    if not error and result and result.returncode != 0:
        error = stderr.strip() or stdout.strip() or f"exit={result.returncode}"

    return AgentResult(
        response=response,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_total=tokens_total,
        tool_calls=tool_calls,
        latency_s=latency_s,
        error=error,
        raw={
            "workspace": str(workspace),
            "agent_id": agent_id,
            "session_id": after_session.get("session_id"),
            "session_file": str(after_session["session_file"]) if after_session.get("session_file") else "",
            "session_event_count": len(session_events),
            "stdout": stdout,
            "stderr": stderr,
            "output_parse_error": payload_error,
            "payload": payload,
            "usage_details": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "output_tokens_reported": reported_tokens_out,
                "output_tokens_estimated": estimated_tokens_out,
                "output_tokens_source": (
                    "reported"
                    if reported_tokens_out > 0
                    else "estimated_from_session_content"
                    if estimated_tokens_out > 0
                    else "missing"
                ),
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "provider_tokens_total": tokens_in + tokens_out,
                "provider_tokens_total_reported": tokens_in + reported_tokens_out,
                "runtime_tokens_total": tokens_total,
                "runtime_tokens_total_reported": reported_tokens_total,
                "final_call_input_tokens": final_call_in,
                "final_call_output_tokens": final_call_out,
                "final_call_total_tokens": final_call_total,
                "output_tokens_estimation_method": "approx_chars_div_4_from_text_and_tool_calls",
                "telemetry_source": "openclaw_session_events",
            },
        },
    )


def run_openclaw_workspace(
    instance: dict,
    *,
    run_group: str,
    experiment_id: str | None = None,
    timeout: int | None = 1800,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    repo_cache_dir: Path = DEFAULT_REPO_CACHE_DIR,
    model: str = DEFAULT_OPENCLAW_MODEL,
    state_dir: Path | None = None,
) -> AgentResult:
    """运行真实 workspace agent，并从 git diff 导出 patch。"""
    workspace_group = experiment_id or run_group
    workspace = materialize_instance_workspace(
        instance,
        run_group=workspace_group,
        workspace_root=workspace_root,
        repo_cache_dir=repo_cache_dir,
    )
    agent_id = _agent_id(workspace_group, instance["instance_id"])
    result = _run_openclaw_agent(
        workspace=workspace,
        agent_id=agent_id,
        prompt=build_workspace_prompt(instance),
        timeout=timeout,
        model=model,
        state_dir=state_dir,
    )
    patch = capture_workspace_patch(workspace)
    error = result.error
    if not error and not result.raw.get("payload") and not result.response and not patch:
        error = f"failed to parse OpenClaw JSON output: {result.raw.get('output_parse_error')}"
    return AgentResult(
        response=result.response,
        patch=patch,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        tokens_total=result.tokens_total,
        tool_calls=result.tool_calls,
        latency_s=result.latency_s,
        error=error,
        raw=result.raw,
    )


def run_openclaw_repo_mentioned(
    instance: dict,
    *,
    run_group: str,
    experiment_id: str | None = None,
    timeout: int | None = 1800,
    workspace_root: Path = DEFAULT_REPO_MENTIONED_ROOT,
    repo_cache_dir: Path = DEFAULT_REPO_CACHE_DIR,
    model: str = DEFAULT_OPENCLAW_MODEL,
    state_dir: Path | None = None,
) -> AgentResult:
    """运行不绑定目标 repo 的 OpenClaw agent，只在 prompt 中显式说明仓库名。"""
    workspace_group = experiment_id or run_group
    workspace = materialize_repo_mentioned_workspace(
        instance,
        run_group=workspace_group,
        workspace_namespace=f"{workspace_group}__openclaw",
        workspace_root=workspace_root,
        repo_cache_dir=repo_cache_dir,
    )
    agent_id = _agent_id(workspace_group, instance["instance_id"])
    result = _run_openclaw_agent(
        workspace=workspace,
        agent_id=agent_id,
        prompt=build_repo_mentioned_prompt(instance),
        timeout=timeout,
        model=model,
        state_dir=state_dir,
    )
    patch = _capture_repo_mentioned_patch(workspace, instance, result.response)
    error = result.error
    if not error and not result.raw.get("payload") and not result.response and not patch:
        error = f"failed to parse OpenClaw JSON output: {result.raw.get('output_parse_error')}"
    return AgentResult(
        response=result.response,
        patch=patch,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        tokens_total=result.tokens_total,
        tool_calls=result.tool_calls,
        latency_s=result.latency_s,
        error=error,
        raw=result.raw,
    )
