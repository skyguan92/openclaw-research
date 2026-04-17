"""
Isolated runtime state helpers for repeatable multi-round experiments.

Goals:
  1. Preserve each runtime's native memory/session behavior.
  2. Start every experiment from a clean memory state.
  3. Avoid touching the user's default ~/.openclaw / ~/.hermes / ~/.claude.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_STATE_ROOT = ROOT / "data" / "runtime_state"


def _task_slug(task_id: str) -> str:
    return task_id.replace("/", "__")


def runtime_state_dir(
    agent_name: str,
    experiment_id: str,
    task_id: str,
    *,
    root: Path = DEFAULT_RUNTIME_STATE_ROOT,
) -> Path:
    return root / experiment_id / agent_name / _task_slug(task_id)


def build_runtime_env(agent_name: str, state_dir: Path | None) -> dict[str, str]:
    if state_dir is None:
        return {}
    state_dir = state_dir.resolve()

    if agent_name == "openclaw":
        return {
            "OPENCLAW_STATE_DIR": str(state_dir),
            "OPENCLAW_CONFIG_PATH": str(state_dir / "openclaw.json"),
        }
    if agent_name == "hermes":
        return {"HERMES_HOME": str(state_dir)}
    if agent_name == "claude-code":
        return {"CLAUDE_CONFIG_DIR": str(state_dir)}
    return {}


def resolve_openclaw_state_dir(state_dir: Path | None = None) -> Path:
    if state_dir is not None:
        return state_dir
    env_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".openclaw"


def prepare_runtime_state(
    agent_name: str,
    experiment_id: str,
    task_id: str,
    *,
    root: Path = DEFAULT_RUNTIME_STATE_ROOT,
    reset: bool = False,
) -> Path:
    state_dir = runtime_state_dir(agent_name, experiment_id, task_id, root=root)
    if reset and state_dir.exists():
        shutil.rmtree(state_dir)
    if state_dir.exists():
        return state_dir

    state_dir.mkdir(parents=True, exist_ok=True)

    if agent_name == "openclaw":
        _seed_openclaw_state(state_dir)
    elif agent_name == "hermes":
        _seed_hermes_home(state_dir)
    elif agent_name == "claude-code":
        _seed_openclaude_config(state_dir)
    else:
        raise ValueError(f"Unknown agent: {agent_name}")

    return state_dir


def _copy_file_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _seed_openclaw_state(state_dir: Path) -> None:
    source = Path.home() / ".openclaw"
    config_path = state_dir / "openclaw.json"

    config: dict = {}
    source_config = source / "openclaw.json"
    if source_config.exists():
        config = json.loads(source_config.read_text())

    if not config:
        kimi_base = os.getenv("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
        kimi_key = os.getenv("KIMI_API_KEY", "")
        kimi_model = os.getenv("KIMI_MODEL", "kimi-for-coding")
        config = {
            "auth": {
                "profiles": {
                    "kimi": {
                        "provider": "kimi",
                        "mode": "api_key",
                        "displayName": "Kimi for Coding",
                    }
                }
            },
            "models": {
                "mode": "merge",
                "providers": {
                    "kimi": {
                        "baseUrl": kimi_base,
                        "apiKey": kimi_key,
                        "models": [
                            {
                                "id": kimi_model,
                                "name": kimi_model,
                                "contextWindow": 262144,
                                "compat": {
                                    "supportsUsageInStreaming": True,
                                },
                            }
                        ],
                    }
                },
            },
        }

    _enable_openclaw_kimi_stream_usage(config)

    # Preserve provider/auth config but always start from a clean agent registry.
    config["agents"] = {"list": [{"id": "main"}]}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    for name in ("models.json", "auth-profiles.json", "exec-approvals.json", "aima-openclaw-managed.json"):
        _copy_file_if_exists(source / name, state_dir / name)
    _copy_tree_if_exists(source / "identity", state_dir / "identity")

    (state_dir / "workspace").mkdir(parents=True, exist_ok=True)
    (state_dir / "memory").mkdir(parents=True, exist_ok=True)


def _enable_openclaw_kimi_stream_usage(config: dict) -> None:
    """Force usage-in-streaming compat for isolated Kimi provider configs.

    Kimi's OpenAI-compatible endpoint supports `stream_options.include_usage`,
    but OpenClaw's auto-detection disables it for custom base URLs unless the
    model compat explicitly opts in. This only affects telemetry collection.
    """
    models_cfg = config.get("models")
    if not isinstance(models_cfg, dict):
        return
    providers = models_cfg.get("providers")
    if not isinstance(providers, dict):
        return

    for provider_id, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            continue
        provider_name = str(provider_id or "").strip().lower()
        base_url = str(provider_cfg.get("baseUrl") or "").strip().lower()
        is_kimi_provider = provider_name == "kimi" or "api.kimi.com" in base_url
        if not is_kimi_provider:
            continue

        models = provider_cfg.get("models")
        if not isinstance(models, list):
            continue

        for model in models:
            if not isinstance(model, dict):
                continue
            compat = model.get("compat")
            if not isinstance(compat, dict):
                compat = {}
                model["compat"] = compat
            compat["supportsUsageInStreaming"] = True


def _seed_hermes_home(state_dir: Path) -> None:
    source = Path.home() / ".hermes"

    copied = False
    for name in ("config.yaml", ".env", "auth.json", "models_dev_cache.json", ".skills_prompt_snapshot.json"):
        src = source / name
        if src.exists():
            _copy_file_if_exists(src, state_dir / name)
            copied = True

    if not copied:
        kimi_base = os.getenv("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
        kimi_key = os.getenv("KIMI_API_KEY", "")
        kimi_model = os.getenv("KIMI_MODEL", "kimi-for-coding")
        config = f"""model:
  default: {kimi_model}
  provider: custom:kimi

providers:
  kimi:
    name: Kimi for Coding
    base_url: {kimi_base}
    api_key: {kimi_key}
    model: {kimi_model}
    context_length: 131072
"""
        (state_dir / "config.yaml").write_text(config)

    for dirname in ("memories", "sessions", "logs", "workspace", "home", "cron", "skills"):
        (state_dir / dirname).mkdir(parents=True, exist_ok=True)


def _seed_openclaude_config(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
