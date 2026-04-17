"""Instance pool loader behavior."""

from pathlib import Path

import pytest

from adapters.instance_pool import (
    InstanceSet,
    UnknownInstanceSetError,
    load_instance_set,
    list_instance_sets,
)


def test_phase1_set_has_20_stratified_instances():
    pool = load_instance_set("phase1")
    assert isinstance(pool, InstanceSet)
    assert len(pool.instance_ids) == 20
    repos = {iid.split("__", 1)[0] for iid in pool.instance_ids}
    assert len(repos) >= 5, f"phase1 should span 5+ repos for stratification, got {repos}"


def test_pilot_set_has_single_known_instance():
    pool = load_instance_set("pilot")
    assert pool.instance_ids == ("astropy__astropy-12907",)


def test_unknown_set_raises():
    with pytest.raises(UnknownInstanceSetError):
        load_instance_set("does-not-exist")


def test_list_instance_sets_includes_defaults():
    names = list_instance_sets()
    assert "phase1" in names
    assert "pilot" in names


def test_phase1_instances_appear_in_swebench_verified_dataset_names():
    pool = load_instance_set("phase1")
    for iid in pool.instance_ids:
        assert "__" in iid and "-" in iid, f"invalid instance id shape: {iid}"
