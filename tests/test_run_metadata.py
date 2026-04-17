"""Environment metadata block for reproducibility."""

import hashlib

from adapters.run_metadata import build_env_block, prompt_hash


def test_prompt_hash_is_stable():
    h1 = prompt_hash("hello world")
    h2 = prompt_hash("hello world")
    assert h1 == h2
    assert len(h1) == 12  # short sha


def test_prompt_hash_differs_by_content():
    assert prompt_hash("abc") != prompt_hash("abd")


def test_env_block_includes_core_fields():
    env = build_env_block(
        agent_name="openclaw",
        model_id="kimi-for-coding",
        prompt="resolve this issue",
        timeout=600,
        mode="repo-mentioned",
        runtime_profile="memory-enabled",
    )
    assert env["agent"] == "openclaw"
    assert env["model_id"] == "kimi-for-coding"
    assert env["timeout_s"] == 600
    assert env["mode"] == "repo-mentioned"
    assert env["runtime_profile"] == "memory-enabled"
    assert env["prompt_hash"] == prompt_hash("resolve this issue")
    assert env["python_version"]
    assert env["host"]


def test_env_block_timeout_zero_becomes_null():
    env = build_env_block(
        agent_name="openclaw",
        model_id="kimi-for-coding",
        prompt="x",
        timeout=0,
        mode="workspace",
        runtime_profile="default",
    )
    assert env["timeout_s"] is None


def test_prompt_hash_matches_sha256_prefix():
    expected = hashlib.sha256(b"xyz").hexdigest()[:12]
    assert prompt_hash("xyz") == expected
