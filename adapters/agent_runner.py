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

# ── Kimi API 配置（三个 agent 共用同一 LLM）──────────────────────────

KIMI_BASE_URL = os.getenv(
    "KIMI_BASE_URL", "https://api.kimi.com/coding/v1"
)
KIMI_API_KEY = os.getenv(
    "KIMI_API_KEY",
    "***REDACTED***",
)
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
    try:
        resp = requests.post(
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

AGENT_CLI = {
    "openclaw": {
        "cmd": ["openclaw"],
        "env_extra": {},
    },
    "hermes": {
        "cmd": [str(Path.home() / ".hermes/hermes-agent/.venv/bin/hermes")],
        "env_extra": {},
    },
    "claude-code": {
        "cmd": [
            str(Path.home() / ".bun/bin/bun"),
            str(Path.home() / "projects/openclaude/dist/cli.mjs"),
        ],
        "env_extra": {
            "CLAUDE_CONFIG_DIR": str(Path.home() / ".openclaude"),
            "CLAUDE_CODE_USE_OPENAI": "1",
            "OPENAI_BASE_URL": KIMI_BASE_URL,
            "OPENAI_API_KEY": KIMI_API_KEY,
            "OPENAI_MODEL": KIMI_MODEL,
            "ANTHROPIC_CUSTOM_HEADERS": "User-Agent: claude-code/2.1.5",
        },
    },
}


def run_agent_cli(
    agent_name: str,
    prompt: str,
    *,
    cwd: Optional[str] = None,
    timeout: int = 600,
) -> AgentResult:
    """
    通过 CLI 子进程调用 agent。

    注意: 这是非交互模式，适用于支持 stdin/pipe 输入的 agent。
    对于需要交互式终端的场景，请用 call_kimi_api() 代替。
    """
    if agent_name not in AGENT_CLI:
        return AgentResult(error=f"Unknown agent: {agent_name}")

    config = AGENT_CLI[agent_name]
    env = {**os.environ, **config["env_extra"]}
    # Kimi 不走代理
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("http_proxy", None)
    env.pop("https_proxy", None)

    cmd = config["cmd"] + ["--print", "-p", prompt]

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return AgentResult(
            response=result.stdout,
            latency_s=time.time() - t0,
            error=result.stderr if result.returncode != 0 else None,
        )
    except subprocess.TimeoutExpired:
        return AgentResult(error="timeout", latency_s=timeout)
    except Exception as e:
        return AgentResult(error=str(e), latency_s=time.time() - t0)


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
