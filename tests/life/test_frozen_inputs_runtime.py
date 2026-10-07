"""@brief 正式工厂的世界隔离和真实生产装配仍完整执行。"""

from tests.data_paths import ENVIRONMENT_ROOT

import json
from pathlib import Path

import pytest

from tests.integration.test_external_environment_lifecycle import _identity
from tests.engine_fixture import (_mutated_scenario)  # noqa: F401
from worldmock.errors import IntegrityError
from worldmock.life import world_setup
from worldmock.life.engine import LifeEngine


def test_formal_factory_worlds_and_storage_are_independent(engine_factory, frozen_inputs):
    first = engine_factory()
    count = len(frozen_inputs.records)
    second = engine_factory()
    original_energy = second.context.state.agents[1].body.energy_kcal
    first.context.state.agents[1].body.energy_kcal = 1
    first.context.state.accounts[next(iter(first.context.state.accounts))] = 99
    assert first.context.scenario is not second.context.scenario
    assert first.context.store.run_dir != second.context.store.run_dir
    assert first.context.state is not second.context.state
    assert first.context.scenario.body is not second.context.scenario.body
    assert second.context.state.agents[1].body.energy_kcal == original_energy
    assert len(frozen_inputs.records) == count


def test_mutated_factory_does_not_use_formal_input_cache(engine_factory, frozen_inputs):
    before = len(frozen_inputs.records)
    mutated = engine_factory(mutate=lambda document: document.update(activity_feedback_seconds=1800))
    assert mutated.context.scenario.activity_feedback_seconds == 1800
    assert len(frozen_inputs.records) == before
    path = mutated.context.store.run_dir.parent / "scenario-0/scenarios/single-person.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert not Path(document["corpus_root"]).is_absolute()


def test_direct_create_and_open_still_run_real_loaders_after_cache_use(engine_factory, monkeypatch, tmp_path):
    engine_factory()
    path = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    calls = []
    for name in ("load_runtime_scenario", "load_manifest"):
        original = getattr(world_setup, name)

        def checked(*arguments, _name=name, _original=original, **keywords):
            calls.append(_name)
            return _original(*arguments, **keywords)

        monkeypatch.setattr(world_setup, name, checked)
    engine = LifeEngine.create(path, tmp_path / "direct", _identity(path))
    engine.close()
    reopened = LifeEngine.open(path, tmp_path / "direct")
    reopened.close()
    assert calls.count("load_runtime_scenario") == 2
    assert calls.count("load_manifest") == 4
    invalid = _identity(path).model_copy(update={"agent_ids": (999,)})
    with pytest.raises(IntegrityError, match="agent ids disagree"):
        LifeEngine.create(path, tmp_path / "invalid", invalid)
    assert calls.count("load_runtime_scenario") == 3
    assert calls.count("load_manifest") == 6


def test_uncached_input_manifest_and_restore_identity_are_actually_validated(engine_factory, tmp_path):
    with pytest.raises(IntegrityError, match="manifest"):
        engine_factory(mutate=lambda document: document.update(corpus_manifest_sha256="0" * 64))
    engine = engine_factory()
    run_dir = engine.context.store.run_dir
    engine.close()
    path = _mutated_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json",
                             lambda document: document.update(activity_feedback_seconds=1800), tmp_path / "reopen")
    with pytest.raises(IntegrityError, match="scenario identity mismatch"):
        LifeEngine.open(path, run_dir)
