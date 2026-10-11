"""共享真实单人生活引擎，模型服务使用明确离线测试输入，人物卡片来自夹具的人设表。"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
import tomlkit

from tests import data_paths
from tests.frozen_inputs import fixture_metadata


@contextmanager
def engine_factory_at(directory: Path, frozen_inputs):
    """在给定目录下建真实引擎的工厂；退出时关闭全部引擎。一份导出要被多条用例各自复制篡改时，由模块级夹具直接使用。"""
    from worldmock.environment import load_environment
    from worldmock.life.engine import LifeEngine
    from tests.integration.runtime_fixtures import current_run_identity
    from worldmock.run_setup import stage_model_identity

    created = []

    def create(environment_root: Path | None = None, mutate=None, create_options: dict | None = None):
        source = Path(environment_root) if environment_root is not None else data_paths.ENVIRONMENT_ROOT
        if mutate is not None:
            source = _mutated_environment(source, mutate, directory / f"environment-{len(created)}")
            inputs = load_environment(source, metadata_loader=fixture_metadata)
        else:
            inputs = frozen_inputs.record(source)["inputs"]
        run_id = f"test-{len(created)}"
        config = inputs.config
        identity = current_run_identity(inputs, run_id)
        engine = LifeEngine.create(inputs, directory / f"run-{len(created)}", identity,
                                   stage_model_identity(config.model, inputs.model_metadata), dict(create_options or {}))
        created.append(engine)
        return engine

    try:
        yield create
    finally:
        for engine in created:
            engine.close()


@pytest.fixture
def engine_factory(tmp_path: Path, frozen_inputs):
    with engine_factory_at(tmp_path, frozen_inputs) as create:
        yield create


def _mutated_environment(source: Path, mutate, target: Path) -> Path:
    shutil.copytree(source, target)
    persona = tomlkit.parse((target / "persona.toml").read_text(encoding="utf-8"))
    config = tomlkit.parse((target / "config.toml").read_text(encoding="utf-8"))
    mutate(persona, config)
    for name, document in (("persona.toml", persona), ("config.toml", config)):
        (target / name).write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    return target
