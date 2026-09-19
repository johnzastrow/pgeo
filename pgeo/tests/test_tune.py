"""pgeo-tune: profile validation, rendering, sizing (pure Python; no database)."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from pgeo import tune
from pgeo.settings import REPO_ROOT


def load_matrix():
    sys.path.insert(0, str(REPO_ROOT / "tests" / "load"))
    spec = importlib.util.spec_from_file_location("run_matrix_pgeo", REPO_ROOT / "tests/load/run_matrix_pgeo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_profiles_validate():
    names = tune.profile_names()
    assert {"tiny", "small", "medium", "large", "workstation"} <= set(names)
    for n in names:
        tune.load_profile(n)


def test_profiles_match_the_load_tested_configs():
    """A profile must carry exactly the values that were load-tested under its config."""
    configs = load_matrix().CONFIGS
    for n in tune.profile_names():
        p = tune.load_profile(n)
        cid = p["machine"].get("load_test_config")
        if not cid:
            continue
        c = configs[cid]
        assert p["postgres"]["shared_buffers"] == c["sb"], n
        assert p["postgres"]["effective_cache_size"] == c["ecs"], n
        assert p["postgres"]["work_mem"] == c["wm"], n
        assert p["frontend"]["rest_pool"] == c["conns"], n
        assert p["machine"]["cpus"] == (c["cpus"] or 0), n
        assert p["machine"]["db_memory_gb"] == (c["db_gb"] or 0), n


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("postgres", "archive_command", "rm -rf /"),
        ("postgres", "shared_preload_libraries", "evil"),
        ("postgres", "shared_buffers", "1GB; DROP"),
        ("postgres", "max_connections", 100000),
        ("frontend", "parse_mode", "shell"),
    ],
)
def test_rejects_unsafe_values(section, key, value):
    p = tune.load_profile("medium")
    p[section][key] = value
    with pytest.raises(tune.ProfileError):
        tune.validate(p)


def test_rejects_bad_profile_names():
    for bad in ("../secrets", "Medium", "a/b", ""):
        with pytest.raises(tune.ProfileError):
            tune.load_profile(bad)


def test_render_conf_and_vars():
    p = tune.load_profile("medium")
    conf = tune.render_conf("medium", p)
    assert "shared_buffers = '384MB'" in conf and "max_parallel_workers_per_gather = 0" in conf
    v = tune.render_vars("medium", p)
    assert "PGEO_TUNING_PROFILE=medium" in v and "PGEO_WORKERS=2" in v and "PGEO_REST_POOL=6" in v


def test_auto_matches_measured_medium_closely():
    a = tune.auto_profile(2, 2.8)
    assert a["postgres"]["shared_buffers"] == "384MB"
    assert a["frontend"]["api_workers"] == 2
    with pytest.raises(tune.ProfileError):
        tune.auto_profile(1, 1.5)  # below the smallest tested database memory


def test_size_comparison():
    assert tune.to_bytes("1GB") == tune.to_bytes("1024MB")
    eff = {"shared_buffers": ("49152", "8kB"), "work_mem": ("16384", "kB"), "jit": ("off", None)}
    p = {"postgres": {"shared_buffers": "384MB", "work_mem": "16MB", "jit": "off"}}
    assert tune.mismatches(p, eff) == []
    assert tune.mismatches(p | {"postgres": {"work_mem": "32MB"}}, eff)


def test_human_sizes():
    assert tune.human("131072", "8kB") == "1GB"
    assert tune.human("49152", "8kB") == "384MB"
    assert tune.human("40", None) == "40"
