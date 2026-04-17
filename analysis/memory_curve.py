"""Round-aware memory behavior analysis.

Given a set of raw records from a memory-enabled multi-round experiment,
compute per-(agent, task) curves that expose how tokens, tool usage,
patch shape, and resolved-rate evolve as the runtime accumulates memory.

This is the replacement for the deprecated MemoryAgentBench quick-test
dimension: instead of measuring recall on synthetic QA, we observe the
runtime's *behavior* when it gets another shot at the same task with
retained state.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from analysis.metrics import provider_tokens, runtime_tokens

RAW_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "raw"


@dataclass
class MemoryCurve:
    agent: str
    task_id: str
    experiment_id: str
    rounds: list[int]
    provider_tokens_by_round: list[int]
    runtime_tokens_by_round: list[int]
    tools_by_round: list[int]
    patch_len_by_round: list[int]
    resolved_by_round: list[bool | None] = field(default_factory=list)

    @property
    def resolved_rate(self) -> float:
        scored = [r for r in self.resolved_by_round if r is not None]
        if not scored:
            return 0.0
        return sum(1 for r in scored if r) / len(scored)

    @property
    def delta_provider_tokens_r5_r1(self) -> int:
        if len(self.provider_tokens_by_round) < 2:
            return 0
        return self.provider_tokens_by_round[-1] - self.provider_tokens_by_round[0]

    @property
    def delta_tools_r5_r1(self) -> int:
        if len(self.tools_by_round) < 2:
            return 0
        return self.tools_by_round[-1] - self.tools_by_round[0]

    @property
    def patch_stability(self) -> float:
        """Fraction of round-pairs where patch_len stays identical.

        1.0 = every round produced an identically-sized patch
        0.0 = every round differs
        """
        pairs = list(zip(self.patch_len_by_round, self.patch_len_by_round[1:]))
        if not pairs:
            return 1.0
        identical = sum(1 for a, b in pairs if a == b)
        return identical / len(pairs)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["resolved_rate"] = self.resolved_rate
        payload["delta_provider_tokens_r5_r1"] = self.delta_provider_tokens_r5_r1
        payload["delta_tools_r5_r1"] = self.delta_tools_r5_r1
        payload["patch_stability"] = self.patch_stability
        return payload


def load_records(
    *,
    raw_dir: Path = RAW_DIR_DEFAULT,
    experiment_id: str | None = None,
    agents: tuple[str, ...] | None = None,
) -> list[dict]:
    records: list[dict] = []
    for path in sorted(raw_dir.glob("swe_*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if experiment_id and data.get("experiment_id") != experiment_id:
            continue
        if agents and data.get("agent") not in agents:
            continue
        records.append(data)
    return records


def _extract_patch_len(record: dict) -> int:
    notes = record.get("notes") or ""
    marker = "patch_len="
    if marker not in notes:
        return 0
    tail = notes.split(marker, 1)[1]
    digits = ""
    for ch in tail:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0


def compute_curves(records: list[dict]) -> list[MemoryCurve]:
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for r in records:
        key = (r.get("agent", ""), r.get("task_id", ""), r.get("experiment_id", ""))
        groups.setdefault(key, []).append(r)

    curves: list[MemoryCurve] = []
    for (agent, task_id, experiment_id), items in groups.items():
        items_sorted = sorted(items, key=lambda r: int(r.get("round") or 0))
        rounds = [int(r.get("round") or 0) for r in items_sorted]
        prov = [provider_tokens(r) for r in items_sorted]
        runt = [runtime_tokens(r) for r in items_sorted]
        tools = [int((r.get("metrics") or {}).get("tool_calls_count") or 0) for r in items_sorted]
        patches = [_extract_patch_len(r) for r in items_sorted]
        resolved = [
            (r.get("metrics") or {}).get("resolved") for r in items_sorted
        ]
        curves.append(
            MemoryCurve(
                agent=agent,
                task_id=task_id,
                experiment_id=experiment_id,
                rounds=rounds,
                provider_tokens_by_round=prov,
                runtime_tokens_by_round=runt,
                tools_by_round=tools,
                patch_len_by_round=patches,
                resolved_by_round=resolved,
            )
        )
    return sorted(curves, key=lambda c: (c.experiment_id, c.task_id, c.agent))


def _write_csv(curves: list[MemoryCurve], path: Path) -> None:
    fieldnames = [
        "experiment_id", "agent", "task_id", "round",
        "provider_tokens", "runtime_tokens", "tool_calls", "patch_len",
        "resolved", "resolved_rate_so_far",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for curve in curves:
            scored_so_far = 0
            passed_so_far = 0
            for i, rnd in enumerate(curve.rounds):
                if curve.resolved_by_round[i] is not None:
                    scored_so_far += 1
                    if curve.resolved_by_round[i]:
                        passed_so_far += 1
                writer.writerow({
                    "experiment_id": curve.experiment_id,
                    "agent": curve.agent,
                    "task_id": curve.task_id,
                    "round": rnd,
                    "provider_tokens": curve.provider_tokens_by_round[i],
                    "runtime_tokens": curve.runtime_tokens_by_round[i],
                    "tool_calls": curve.tools_by_round[i],
                    "patch_len": curve.patch_len_by_round[i],
                    "resolved": curve.resolved_by_round[i],
                    "resolved_rate_so_far": (passed_so_far / scored_so_far) if scored_so_far else None,
                })


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory curve analyzer")
    parser.add_argument("--experiment-id", required=True,
                        help="experiment_id filter (e.g. repo_mentioned_mem5_full_20260417)")
    parser.add_argument("--raw-dir", default=str(RAW_DIR_DEFAULT))
    parser.add_argument("--csv", default=None, help="write wide CSV to this path")
    args = parser.parse_args()

    records = load_records(raw_dir=Path(args.raw_dir), experiment_id=args.experiment_id)
    curves = compute_curves(records)
    if not curves:
        print(f"no records for experiment_id={args.experiment_id}")
        return

    for c in curves:
        print(
            f"{c.agent:<12} task={c.task_id:<30} "
            f"rounds={c.rounds} "
            f"tools={c.tools_by_round} "
            f"patch_len={c.patch_len_by_round} "
            f"resolved={c.resolved_by_round} "
            f"resolved_rate={c.resolved_rate:.0%} "
            f"\u0394tok={c.delta_provider_tokens_r5_r1:+d} "
            f"patch_stability={c.patch_stability:.0%}"
        )

    if args.csv:
        out = Path(args.csv)
        _write_csv(curves, out)
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
