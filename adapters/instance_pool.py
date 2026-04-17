"""Declarative SWE-bench instance pools.

Pools live in benchmarks/swebench_instances.yaml. Each pool declares an
ordered list of SWE-bench Verified instance IDs. Loading a pool is the
canonical way to pick the instance set for an experiment — it replaces
ad-hoc --instance-ids lists and the implicit --limit N behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

POOL_FILE = (
    Path(__file__).resolve().parent.parent
    / "benchmarks"
    / "swebench_instances.yaml"
)


class UnknownInstanceSetError(KeyError):
    """Raised when a named instance set is not declared in the YAML."""


@dataclass(frozen=True)
class InstanceSet:
    name: str
    description: str
    instance_ids: tuple[str, ...]


def _load_pool_file() -> dict:
    with open(POOL_FILE) as f:
        return yaml.safe_load(f) or {}


def load_instance_set(name: str) -> InstanceSet:
    data = _load_pool_file()
    sets = data.get("sets", {})
    if name not in sets:
        raise UnknownInstanceSetError(
            f"instance set {name!r} not found in {POOL_FILE}; "
            f"known sets: {sorted(sets)}"
        )
    entry = sets[name]
    return InstanceSet(
        name=name,
        description=entry.get("description", ""),
        instance_ids=tuple(entry.get("instance_ids", [])),
    )


def list_instance_sets() -> list[str]:
    data = _load_pool_file()
    return sorted((data.get("sets") or {}).keys())
