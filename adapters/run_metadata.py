"""Reproducibility metadata attached to every raw record.

Captures model id, prompt hash, timeout, mode, runtime profile, and host
details so future analysis can filter by or audit the exact conditions a
record was produced under. The env block goes into record["env"].
"""

from __future__ import annotations

import hashlib
import platform
import sys


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def build_env_block(
    *,
    agent_name: str,
    model_id: str,
    prompt: str,
    timeout: int | None,
    mode: str,
    runtime_profile: str,
) -> dict:
    return {
        "agent": agent_name,
        "model_id": model_id,
        "prompt_hash": prompt_hash(prompt),
        "timeout_s": timeout if (timeout is not None and timeout > 0) else None,
        "mode": mode,
        "runtime_profile": runtime_profile,
        "python_version": sys.version.split()[0],
        "host": platform.node(),
        "platform": f"{platform.system()}-{platform.release()}-{platform.machine()}",
    }
