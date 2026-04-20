#!/usr/bin/env python3
"""Single-cell runner for the mem-eval-v1 benchmark.

One invocation = one atomic (task, agent, mem profile, seq, round) cell.
The runner is idempotent: if the expected output file already exists it
skips, unless --force is given. This lets the controller fire cells in
any order and resume after budget exhaustion.

Cell geometry
-------------
For each (task, agent) pair we record runs under two memory profiles:

  mem=off  Each seq is an independent replicate. round is always 1; the
           runtime state dir gets wiped before every call.
  mem=on   Each seq is a 5-round conversation. state dir is wiped only
           on round 1; rounds 2..5 inherit whatever the runtime chose to
           persist.

Output layout
-------------
    data/raw/mem_eval_v1/<task_id>/
        <agent>__memoff__seq01__r01.json
        <agent>__memon__seq01__r01.json
        <agent>__memon__seq01__r02.json
        ...

Each record is the standard swe_*.json token record plus:

    outcome              — classifier verdict (or "pending" pre-harness)
    outcome_reason       — classifier justification string
    modeled_cost_rmb     — CostBreakdown under the v1 Kimi price table (RMB)
    modeled_cost_usd     — CostBreakdown under the v1 Kimi price table (USD)
    price_version        — pricing table version tag
    mem_eval             — block { profile, seq, round, experiment_id }

The harness is NOT invoked per cell (Docker spin-up would dominate cost).
After a batch of cells completes, run `adapters.swebench_adapter
--evaluate` on the collected predictions to backfill harness_status, then
re-run `scripts/classify_mem_eval.py` to update `outcome` in place.

Usage
-----
    python scripts/run_mem_eval.py run \
        --task astropy__astropy-14096 --agent openclaw \
        --mem on --seq 1 --round 1

    python scripts/run_mem_eval.py plan --task astropy__astropy-14096
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.instance_pool import load_instance_set
from adapters.kimi_pricing import (
    PRICE_TABLE_VERSION,
    compute_cost,
    extract_token_counts,
)
from adapters.outcome_classifier import classify_outcome

MEM_EVAL_POOL = "mem-eval-v1"
MEM_EVAL_DATA_ROOT = ROOT / "data" / "raw" / "mem_eval_v1"
AGENTS = ("openclaw", "hermes", "claude-code")


def _output_path(task_id: str, agent: str, mem: str, seq: int, round_idx: int) -> Path:
    filename = f"{agent}__mem{mem}__seq{seq:02d}__r{round_idx:02d}.json"
    return MEM_EVAL_DATA_ROOT / task_id / filename


def _experiment_id(agent: str, mem: str, seq: int) -> str:
    # runtime_state is keyed by (agent, experiment_id, task_id); using the
    # same experiment_id across rounds of one mem-on sequence is what makes
    # the 5-round conversation persist.
    return f"mem_eval_v1__{agent}__mem{mem}__seq{seq:02d}"


def _load_instance(task_id: str) -> dict:
    pool = load_instance_set(MEM_EVAL_POOL)
    if task_id not in pool.instance_ids:
        raise SystemExit(
            f"task {task_id} not in pool {MEM_EVAL_POOL}. "
            f"Valid ids: {', '.join(pool.instance_ids)}"
        )
    # Import here so --plan doesn't pay the `datasets` import cost.
    from adapters.swebench_adapter import load_instances

    instances = load_instances(instance_ids=[task_id])
    if not instances:
        raise SystemExit(f"instance {task_id} not found in SWE-bench Verified")
    return instances[0]


def _augment_record(record: dict, *, mem: str, seq: int, round_idx: int, experiment_id: str) -> dict:
    tokens = extract_token_counts(record)
    cost_rmb = compute_cost(tokens, currency="rmb")
    cost_usd = compute_cost(tokens, currency="usd")
    result = classify_outcome(record)

    record["mem_eval"] = {
        "profile": mem,
        "seq": seq,
        "round": round_idx,
        "experiment_id": experiment_id,
    }
    record["modeled_cost_rmb"] = asdict(cost_rmb)
    record["modeled_cost_usd"] = asdict(cost_usd)
    record["price_version"] = PRICE_TABLE_VERSION
    record["outcome"] = result.outcome.value if result.outcome else "pending"
    record["outcome_reason"] = result.reason
    record["outcome_harness_available"] = result.harness_available
    return record


def cmd_run(args: argparse.Namespace) -> int:
    task_id: str = args.task
    agent: str = args.agent
    mem: str = args.mem
    seq: int = args.seq
    round_idx: int = args.round

    if mem == "off" and round_idx != 1:
        raise SystemExit("mem=off only has round 1 (each replicate is independent)")
    if mem == "on" and not (1 <= round_idx <= 5):
        raise SystemExit("mem=on rounds must be in 1..5")
    if agent not in AGENTS:
        raise SystemExit(f"agent must be one of {AGENTS}")

    out_path = _output_path(task_id, agent, mem, seq, round_idx)
    if out_path.exists() and not args.force:
        print(f"[skip] {out_path.relative_to(ROOT)} already exists — pass --force to overwrite")
        return 0

    instance = _load_instance(task_id)
    experiment_id = _experiment_id(agent, mem, seq)
    runtime_profile = "memory-enabled" if mem == "on" else "default"
    # For mem=off: always reset. For mem=on: reset only on round 1 of the
    # sequence; rounds 2..5 must inherit state.
    reset_state = (mem == "off") or (round_idx == 1)

    from adapters.swebench_adapter import run_agent_on_instance

    print(f"[run] task={task_id} agent={agent} mem={mem} seq={seq} round={round_idx}")
    print(f"      experiment_id={experiment_id} reset_state={reset_state} profile={runtime_profile}")
    t0 = time.time()
    output = run_agent_on_instance(
        agent,
        instance,
        mode="repo-mentioned",
        run_group=experiment_id,
        experiment_id=experiment_id,
        round_index=round_idx,
        timeout=args.timeout,
        runtime_profile=runtime_profile,
        reset_runtime_state=reset_state,
    )
    elapsed = time.time() - t0

    token_record = output["token_record"]
    token_record = _augment_record(
        token_record, mem=mem, seq=seq, round_idx=round_idx, experiment_id=experiment_id
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(token_record, indent=2, ensure_ascii=False))

    # Also mirror the prediction to a per-cell predictions.jsonl so the harness
    # batch step can pick them up later.
    preds_path = out_path.with_suffix(".prediction.jsonl")
    preds_path.write_text(json.dumps(output["prediction"], ensure_ascii=False) + "\n")

    outcome = token_record["outcome"]
    cost_rmb = token_record["modeled_cost_rmb"]["total"]
    cost_usd = token_record["modeled_cost_usd"]["total"]
    print(
        f"[done] outcome={outcome} elapsed={elapsed:.0f}s "
        f"cost=¥{cost_rmb:.2f}/${cost_usd:.3f} → {out_path.relative_to(ROOT)}"
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Print a shuffled execution plan for one task (or the whole pool)."""
    pool = load_instance_set(MEM_EVAL_POOL)
    tasks = [args.task] if args.task else list(pool.instance_ids)
    for tid in tasks:
        if tid not in pool.instance_ids:
            raise SystemExit(f"task {tid} not in {MEM_EVAL_POOL}")

    cells: list[tuple[str, str, str, int, int]] = []
    for tid in tasks:
        for agent in AGENTS:
            for seq in range(1, args.mem_off_replicates + 1):
                cells.append((tid, agent, "off", seq, 1))
            for seq in range(1, args.mem_on_sequences + 1):
                for r in range(1, 6):
                    cells.append((tid, agent, "on", seq, r))

    # Shuffle blocks (= atomic scheduling units), but keep mem-on rounds
    # of the same sequence contiguous and in order — they have to run
    # serially because they share a state dir.
    blocks: dict[tuple, list[tuple]] = {}
    for cell in cells:
        tid, agent, mem, seq, r = cell
        key = (tid, agent, mem, seq)
        blocks.setdefault(key, []).append(cell)
    for key, rounds in blocks.items():
        rounds.sort(key=lambda c: c[4])  # round order within block

    rng = random.Random(args.seed)
    block_keys = list(blocks.keys())
    rng.shuffle(block_keys)

    for key in block_keys:
        for tid, agent, mem, seq, r in blocks[key]:
            print(
                f"python scripts/run_mem_eval.py run "
                f"--task {tid} --agent {agent} --mem {mem} --seq {seq} --round {r}"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="mem-eval-v1 single-cell runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="execute a single cell")
    p_run.add_argument("--task", required=True, help="SWE-bench instance id")
    p_run.add_argument("--agent", required=True, choices=AGENTS)
    p_run.add_argument("--mem", required=True, choices=("on", "off"))
    p_run.add_argument("--seq", type=int, required=True, help="sequence/replicate id, ≥1")
    p_run.add_argument("--round", type=int, required=True, help="1 for mem=off; 1..5 for mem=on")
    p_run.add_argument("--timeout", type=int, default=1800)
    p_run.add_argument("--force", action="store_true", help="overwrite existing output")
    p_run.set_defaults(func=cmd_run)

    p_plan = sub.add_parser("plan", help="print a shuffled cell execution plan")
    p_plan.add_argument("--task", default=None, help="if omitted, plan the whole pool")
    p_plan.add_argument("--mem-off-replicates", type=int, default=3)
    p_plan.add_argument("--mem-on-sequences", type=int, default=3)
    p_plan.add_argument("--seed", type=int, default=20260420)
    p_plan.set_defaults(func=cmd_plan)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
