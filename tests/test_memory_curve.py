"""Round-aware memory curve analysis."""

import json
from pathlib import Path

import pytest

from analysis.memory_curve import MemoryCurve, compute_curves, load_records


def _write_round_record(
    raw_dir: Path,
    *,
    agent: str,
    task: str,
    experiment: str,
    round_index: int,
    provider_tokens: int,
    runtime_tokens: int,
    tool_calls: int,
    patch_len: int,
    resolved: bool | None,
) -> None:
    run_group = f"{experiment}_r{round_index:02d}"
    record = {
        "run_id": f"swe_{task}_{agent}_{run_group}",
        "run_group": run_group,
        "experiment_id": experiment,
        "agent": agent,
        "task_id": task,
        "dimension": "token_efficiency",
        "round": round_index,
        "runtime_profile": "memory-enabled",
        "metrics": {
            "tokens_in": provider_tokens - 1000,
            "tokens_out": 1000,
            "tokens_total": runtime_tokens,
            "provider_tokens_total": provider_tokens,
            "runtime_tokens_total": runtime_tokens,
            "task_completed": True,
            "tool_calls_count": tool_calls,
            "latency_s": 200.0,
            **({"resolved": resolved, "harness_status": "pass" if resolved else "fail"}
               if resolved is not None else {}),
        },
        "notes": f"SWE-bench instance, mode=repo-mentioned, patch_len={patch_len}",
    }
    (raw_dir / f"{record['run_id']}.json").write_text(json.dumps(record))


@pytest.fixture
def mem5_raw_dir(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()

    # openclaw: stabilizes by round 2, all rounds resolve
    for r, tools in [(1, 34), (2, 10), (3, 10), (4, 10), (5, 10)]:
        _write_round_record(
            raw,
            agent="openclaw",
            task="astropy__astropy-12907",
            experiment="mem5",
            round_index=r,
            provider_tokens=1_100_000 + r * 10_000,
            runtime_tokens=1_100_000 + r * 10_000,
            tool_calls=tools,
            patch_len=4291,
            resolved=True,
        )

    # hermes: grows each round, resolves only rounds 1 and 3
    for r, tools, resolved in [(1, 20, True), (2, 37, False), (3, 40, True),
                                (4, 80, False), (5, 59, False)]:
        _write_round_record(
            raw,
            agent="hermes",
            task="astropy__astropy-12907",
            experiment="mem5",
            round_index=r,
            provider_tokens=400_000 * r,
            runtime_tokens=400_000 * r * 2,
            tool_calls=tools,
            patch_len=1500 + r * 200,
            resolved=resolved,
        )
    return raw


def test_load_records_filters_by_experiment(mem5_raw_dir: Path):
    records = load_records(raw_dir=mem5_raw_dir, experiment_id="mem5")
    assert len(records) == 10


def test_compute_curves_one_per_agent_task_pair(mem5_raw_dir: Path):
    curves = compute_curves(load_records(raw_dir=mem5_raw_dir, experiment_id="mem5"))
    assert len(curves) == 2
    keys = {(c.agent, c.task_id) for c in curves}
    assert ("openclaw", "astropy__astropy-12907") in keys
    assert ("hermes", "astropy__astropy-12907") in keys


def test_openclaw_curve_shows_stable_tools_and_patch(mem5_raw_dir: Path):
    curves = compute_curves(load_records(raw_dir=mem5_raw_dir, experiment_id="mem5"))
    oc = next(c for c in curves if c.agent == "openclaw")

    assert oc.rounds == [1, 2, 3, 4, 5]
    assert oc.tools_by_round == [34, 10, 10, 10, 10]
    assert oc.delta_tools_r5_r1 == 10 - 34
    assert oc.resolved_rate == 1.0
    assert oc.patch_stability == 1.0


def test_hermes_curve_shows_tool_growth_and_low_resolved(mem5_raw_dir: Path):
    curves = compute_curves(load_records(raw_dir=mem5_raw_dir, experiment_id="mem5"))
    hermes = next(c for c in curves if c.agent == "hermes")

    assert hermes.tools_by_round == [20, 37, 40, 80, 59]
    assert hermes.resolved_rate == pytest.approx(2 / 5)
    assert hermes.patch_stability < 1.0


def test_curve_exports_to_dict(mem5_raw_dir: Path):
    curves = compute_curves(load_records(raw_dir=mem5_raw_dir, experiment_id="mem5"))
    payload = curves[0].as_dict()
    required_keys = {
        "agent", "task_id", "rounds", "provider_tokens_by_round",
        "runtime_tokens_by_round", "tools_by_round", "patch_len_by_round",
        "resolved_by_round", "resolved_rate", "delta_provider_tokens_r5_r1",
        "delta_tools_r5_r1", "patch_stability",
    }
    assert required_keys.issubset(payload.keys())
