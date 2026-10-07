"""@brief 正式工厂的世界隔离和真实生产装配仍完整执行。"""

from tests.data_paths import ENVIRONMENT_ROOT

import json

import pytest

from tests.engine_fixture import (_mutated_scenario)  # noqa: F401
from tests.life.scenario_fixtures import run_identity
from worldmock.errors import IntegrityError
from worldmock.life import world_setup


def _identity(path):
    """@brief 直接运行仍生成真实代码与模型身份，不导入另一个集成测试模块。"""
    return run_identity(json.loads(path.read_text(encoding="utf-8")), "direct-frozen-input")


def test_formal_factory_worlds_and_storage_are_independent(engine_factory, frozen_inputs):
    first = engine_factory(ENVIRONMENT_ROOT / "scenarios/single-person.json")
    count = len(frozen_inputs.records)
    second = engine_factory(ENVIRONMENT_ROOT / "scenarios/single-person.json")
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
    mutated = engine_factory(ENVIRONMENT_ROOT / "scenarios/single-person.json", mutate=lambda document: document.update(activity_feedback_seconds=1800))
    assert mutated.context.scenario.activity_feedback_seconds == 1800
    assert len(frozen_inputs.records) == before
    path = mutated.context.store.run_dir.parent / "scenario-0/scenarios/single-person.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "corpus_root" not in document
    assert (path.parent.parent / "catalog/manifest.json").is_file()
    assert not (path.parent.parent / "corpora").exists()


def test_direct_create_and_open_still_run_real_loaders_after_cache_use(engine_factory, monkeypatch, tmp_path):
    from worldmock.life.engine import LifeEngine

    engine_factory(ENVIRONMENT_ROOT / "scenarios/single-person.json")
    path = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    calls = []
    for name in ("load_runtime_scenario",):
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
    invalid = _identity(path).model_copy(update={"agent_ids": (999,)})
    with pytest.raises(IntegrityError, match="agent ids disagree"):
        LifeEngine.create(path, tmp_path / "invalid", invalid)
    assert calls.count("load_runtime_scenario") == 3


def test_uncached_input_manifest_and_restore_identity_are_actually_validated(engine_factory, tmp_path):
    from worldmock.life.engine import LifeEngine

    with pytest.raises(IntegrityError, match="ENVIRONMENT_PIN_MISMATCH"):
        engine_factory(ENVIRONMENT_ROOT / "scenarios/single-person.json",
                       mutate=lambda document: document["environment"].update(environment_version="unknown"))
    engine = engine_factory(ENVIRONMENT_ROOT / "scenarios/single-person.json")
    run_dir = engine.context.store.run_dir
    engine.close()
    path = _mutated_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json",
                             lambda document: document.update(activity_feedback_seconds=1800), tmp_path / "reopen")
    with pytest.raises(IntegrityError, match="scenario identity mismatch"):
        LifeEngine.open(path, run_dir)
