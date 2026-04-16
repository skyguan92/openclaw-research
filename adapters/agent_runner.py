"""
统一 Agent Runner — 把三个 agent 的调用方式抽象为相同接口。

每个 agent 的 run() 返回:
    AgentResult(response, patch, tokens_in, tokens_out, tokens_total, tool_calls, latency_s)

支持两种模式:
    1. API 模式: 直接调用 OpenAI-compatible API（适用于 MemoryAgentBench）
    2. CLI 模式: 通过子进程调用 agent CLI（适用于 SWE-bench）
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from adapters.runtime_state import build_runtime_env

# ── Kimi API 配置（三个 agent 共用同一 LLM）──────────────────────────

KIMI_BASE_URL = os.getenv(
    "KIMI_BASE_URL", "https://api.kimi.com/coding/v1"
)
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-for-coding")


@dataclass
class AgentResult:
    """Agent 执行结果。"""

    response: str = ""
    patch: str = ""  # SWE-bench 用: unified diff
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    tool_calls: int = 0
    latency_s: float = 0.0
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def tefs(self) -> float:
        """Token Efficiency Function Score: 每 1k output token 的有效得分。
        需要外部设置 score 后计算，这里返回 tokens_per_1k_output。"""
        if self.tokens_out == 0:
            return 0.0
        return self.tokens_out / 1000


def _extract_usage(data: dict) -> tuple[int, int, int]:
    """从不同 API/CLI 返回结构中尽量提取 token 用量。"""
    usage = data.get("usage")
    if isinstance(usage, dict):
        tokens_in = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        tokens_out = usage.get("completion_tokens", usage.get("output_tokens", 0))
        tokens_total = usage.get("total_tokens", (tokens_in or 0) + (tokens_out or 0))
        return int(tokens_in or 0), int(tokens_out or 0), int(tokens_total or 0)

    model_usage = data.get("modelUsage")
    if isinstance(model_usage, dict):
        usage_entry = model_usage.get(KIMI_MODEL)
        if usage_entry is None and len(model_usage) == 1:
            usage_entry = next(iter(model_usage.values()))
        if isinstance(usage_entry, dict):
            tokens_in = usage_entry.get("inputTokens", 0)
            tokens_out = usage_entry.get("outputTokens", 0)
            return int(tokens_in or 0), int(tokens_out or 0), int((tokens_in or 0) + (tokens_out or 0))

    return 0, 0, 0


def _extract_tool_calls(data: dict) -> int:
    """从常见字段中提取工具调用次数。"""
    if isinstance(data.get("tool_calls"), list):
        return len(data["tool_calls"])
    if isinstance(data.get("tool_calls_count"), int):
        return max(0, data["tool_calls_count"])
    if isinstance(data.get("num_turns"), int):
        return max(0, data["num_turns"] - 1)
    return 0


# ── API 模式: 直接调用 Kimi API ──────────────────────────────────────


def call_kimi_api(
    messages: list[dict],
    *,
    model: str = KIMI_MODEL,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    user_agent: str = "claude-code/2.1.5",
) -> AgentResult:
    """直接调用 Kimi OpenAI-compatible API，返回 AgentResult。"""
    t0 = time.time()
    if not KIMI_API_KEY:
        return AgentResult(error="KIMI_API_KEY is not set", latency_s=0.0)
    try:
        session = requests.Session()
        session.trust_env = False
        resp = session.post(
            f"{KIMI_BASE_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {KIMI_API_KEY}",
                "User-Agent": user_agent,
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return AgentResult(error=str(e), latency_s=time.time() - t0)

    usage = data.get("usage", {})
    content = ""
    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")

    return AgentResult(
        response=content,
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
        tokens_total=usage.get("total_tokens", 0),
        latency_s=time.time() - t0,
        raw=data,
    )


# ── CLI 模式: 通过子进程调用 agent ───────────────────────────────────

def _build_clean_env(extra: dict | None = None) -> dict:
    """构建干净环境：继承系统 env，移除代理，合并额外变量。"""
    env = dict(os.environ)
    # Kimi 不走代理
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)
    # 移除可能干扰 OpenClaude 的 USER_TYPE=ant
    env.pop("USER_TYPE", None)
    if extra:
        env.update(extra)
    return env


def _run_openclaw_cli(
    prompt: str,
    *,
    cwd: str | None = None,
    timeout: int | None = 600,
    runtime_state_dir: Path | None = None,
    **_: object,
) -> AgentResult:
    """
    OpenClaw CLI 非交互模式。

    命令: openclaw infer model run --local --model kimi/kimi-for-coding --prompt "..." --json
    输出: JSON { ok, outputs: [{ text }], ... }
    """
    cmd = [
        "openclaw", "infer", "model", "run",
        "--local",
        "--model", f"kimi/{KIMI_MODEL}",
        "--prompt", prompt,
        "--json",
    ]
    env = _build_clean_env(build_runtime_env("openclaw", runtime_state_dir))
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env,
        )
        latency = time.time() - t0

        if result.returncode != 0:
            return AgentResult(error=result.stderr.strip(), latency_s=latency)

        # stdout 是干净 JSON（stderr 已分离），直接解析
        stdout = result.stdout.strip()
        if not stdout:
            return AgentResult(
                error=result.stderr.strip() or "empty output",
                latency_s=latency,
            )
        # 找到 JSON 起始（跳过可能的非 JSON 前缀行）
        json_start = stdout.find("{")
        if json_start < 0:
            return AgentResult(response=stdout, latency_s=latency)
        data = json.loads(stdout[json_start:])
        text = ""
        outputs = data.get("outputs", [])
        if outputs:
            text = outputs[0].get("text", "")
        tokens_in, tokens_out, tokens_total = _extract_usage(data)
        return AgentResult(
            response=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_total=tokens_total,
            tool_calls=_extract_tool_calls(data),
            latency_s=latency,
            raw=data,
        )

    except subprocess.TimeoutExpired:
        return AgentResult(error="timeout", latency_s=time.time() - t0)
    except Exception as e:
        return AgentResult(error=str(e), latency_s=time.time() - t0)


def _run_hermes_cli(
    prompt: str,
    *,
    cwd: str | None = None,
    timeout: int | None = 600,
    runtime_state_dir: Path | None = None,
    **_: object,
) -> AgentResult:
    """
    Hermes Agent CLI 非交互模式。

    命令: hermes chat -q "..." -Q
    -q: 单条消息   -Q: quiet 模式（只输出 AI 回复）
    config.yaml 中 provider: custom:kimi 指向 api.kimi.com
    """
    hermes_bin = str(Path.home() / ".hermes/hermes-agent/.venv/bin/hermes")
    cmd = [hermes_bin, "chat", "-q", prompt, "-Q"]
    env = _build_clean_env(build_runtime_env("hermes", runtime_state_dir))
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env,
        )
        latency = time.time() - t0

        # Hermes -Q 模式输出纯文本回复（可能有 rich formatting 残留，清理一下）
        stdout = result.stdout.strip()
        # 去掉 Hermes 的 rich box 装饰
        lines = []
        for line in stdout.splitlines():
            stripped = line.strip()
            # 跳过 box 装饰行和初始化行
            if stripped.startswith("╭") or stripped.startswith("╰") or stripped.startswith("│"):
                # 提取 box 内容
                if stripped.startswith("│"):
                    content = stripped.strip("│ ").strip()
                    if content:
                        lines.append(content)
            elif stripped.startswith(("🤖", "🔗", "🔑", "✅", "⚠️", "📊", "🛠️")):
                # 跳过初始化状态行
                continue
            elif stripped.startswith("[thinking]"):
                continue
            elif stripped.startswith("session_id:"):
                continue
            elif stripped:
                lines.append(stripped)
        response = "\n".join(lines).strip()

        if result.returncode != 0 and not response:
            return AgentResult(error=result.stderr.strip(), latency_s=latency)

        return AgentResult(response=response, latency_s=latency)

    except subprocess.TimeoutExpired:
        return AgentResult(error="timeout", latency_s=time.time() - t0)
    except Exception as e:
        return AgentResult(error=str(e), latency_s=time.time() - t0)


def _run_openclaude_cli(
    prompt: str,
    *,
    cwd: str | None = None,
    timeout: int | None = 300,
    runtime_state_dir: Path | None = None,
    runtime_memory_enabled: bool = False,
    **_: object,
) -> AgentResult:
    """
    OpenClaude (Claude Code fork) CLI 非交互模式。

    命令: bun cli.mjs --provider openai --model kimi-for-coding --bare
           --dangerously-skip-permissions --output-format json -p "..."
    输出: JSON { result, duration_ms, modelUsage: { model: { inputTokens, outputTokens } } }
    """
    bun = str(Path.home() / ".bun/bin/bun")
    cli = str(Path.home() / "projects/openclaude/dist/cli.mjs")
    cmd = [
        bun, cli,
        "--provider", "openai",
        "--model", KIMI_MODEL,
        # Kimi's OpenAI-compatible endpoint does not reliably emit reasoning
        # content in Claude Code's expected format, so adaptive thinking can
        # fail with invalid_request_error during headless runs.
        "--thinking", "disabled",
        "--dangerously-skip-permissions",
        "--output-format", "json",
    ]
    if not runtime_memory_enabled:
        cmd.extend(["--bare", "--system-prompt", "You are a helpful assistant. Answer concisely."])
    cmd.extend(["-p", prompt])
    env = _build_clean_env({
        "OPENAI_API_KEY": KIMI_API_KEY,
        "OPENAI_BASE_URL": KIMI_BASE_URL,
        "OPENAI_MODEL": KIMI_MODEL,
        **build_runtime_env("claude-code", runtime_state_dir),
    })

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env,
        )
        latency = time.time() - t0

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()
            return AgentResult(
                error=stderr or "empty output (possible silent crash)",
                latency_s=latency,
            )

        # 解析 JSON 输出
        data = json.loads(stdout)
        response = data.get("result", "")
        subtype = str(data.get("subtype", ""))
        errors = data.get("errors") if isinstance(data.get("errors"), list) else []
        error_text = ""
        if errors:
            error_text = "; ".join(str(item) for item in errors if item)
        elif response:
            error_text = str(response)
        # 提取 token 用量
        model_usage = data.get("modelUsage", {})
        usage = model_usage.get(KIMI_MODEL, {})
        tokens_in = usage.get("inputTokens", 0)
        tokens_out = usage.get("outputTokens", 0)
        duration_ms = data.get("duration_ms", 0)
        num_turns = data.get("num_turns", 0)
        is_error = bool(data.get("is_error")) or subtype.startswith("error")

        return AgentResult(
            response=response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_total=tokens_in + tokens_out,
            tool_calls=max(0, num_turns - 1),  # first turn is the user message
            latency_s=duration_ms / 1000 if duration_ms else latency,
            error=error_text if is_error else None,
            raw=data,
        )
    except json.JSONDecodeError:
        return AgentResult(
            response=result.stdout.strip(),
            latency_s=time.time() - t0,
        )
    except subprocess.TimeoutExpired:
        return AgentResult(error="timeout", latency_s=time.time() - t0)
    except Exception as e:
        return AgentResult(error=str(e), latency_s=time.time() - t0)


# 路由表
_CLI_RUNNERS = {
    "openclaw": _run_openclaw_cli,
    "hermes": _run_hermes_cli,
    "claude-code": _run_openclaude_cli,
}


def run_agent_cli(
    agent_name: str,
    prompt: str,
    *,
    cwd: str | None = None,
    timeout: int | None = 600,
    **kwargs,
) -> AgentResult:
    """通过 CLI 子进程调用 agent（路由到各 agent 专用函数）。"""
    runner = _CLI_RUNNERS.get(agent_name)
    if runner is None:
        return AgentResult(error=f"Unknown agent: {agent_name}")
    return runner(prompt, cwd=cwd, timeout=timeout, **kwargs)


# ── 统一入口 ─────────────────────────────────────────────────────────


def run_agent(
    agent_name: str,
    prompt: str,
    *,
    mode: str = "api",
    cwd: Optional[str] = None,
    **kwargs,
) -> AgentResult:
    """
    统一入口。

    mode="api"  → 直接调 Kimi API（快，适合记忆测试和 token 统计）
    mode="cli"  → 通过 agent CLI 子进程（适合 SWE-bench，agent 需要工具环境）
    """
    if mode == "api":
        messages = [{"role": "user", "content": prompt}]
        return call_kimi_api(messages, **kwargs)
    elif mode == "cli":
        return run_agent_cli(agent_name, prompt, cwd=cwd, **kwargs)
    else:
        return AgentResult(error=f"Unknown mode: {mode}")
