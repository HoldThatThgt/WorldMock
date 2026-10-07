"""@brief 全仓共享的真实生活引擎夹具；装配时才导入生产运行模块。"""

from __future__ import annotations

import json
import shutil
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import pytest

from tests.frozen_inputs import relative_corpus
from tests import data_paths


@pytest.fixture
def engine_factory(tmp_path: Path, frozen_inputs):
    """建立绑定人工环境但隔离存储的 LifeEngine；给出 mutate(document) 时先改写场景文档的副本；create_options 原样交给
    LifeEngine.create（例如 persona_habit_responses，缺省为引擎直驱：不带新建运行的附加输入）。"""
    from worldmock.life.engine import LifeEngine
    from worldmock.life.models import ModelIdentity, RunIdentity

    created = []

    def create(scenario_name: str | Path = "single-person.json", mutate=None,
               create_options: dict | None = None) -> LifeEngine:
        scenario_path = (scenario_name if isinstance(scenario_name, Path)
                         else data_paths.ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios" / scenario_name)
        if mutate is not None:
            scenario_path = _mutated_scenario(scenario_path, mutate, tmp_path / f"scenario-{len(created)}")
        document = json.loads(scenario_path.read_text(encoding="utf-8"))
        run_id = f"test-{len(created)}-{document['scenario_id']}"
        identity = RunIdentity(
            run_id=run_id,
            source_group_id=f"group-{run_id}",
            agent_ids=tuple(item["agent_id"] for item in document["agents"]),
            start_at=datetime.fromisoformat(document["start_at"]).isoformat(),
            end_at=datetime.fromisoformat(document["end_at"]).isoformat(),
            timezone=document["timezone"],
            code_sha256="2" * 64,
            model_identity=ModelIdentity(
                provider="openai_compatible",
                base_url="https://api.deepseek.com/beta",
                requested_model="deepseek-flash",
                thinking="enabled",
                tool_choice="auto",
                max_retries=0,
                prompt_sha256="0" * 64,
                tool_schema_sha256="1" * 64,
            ),
        )
        selected = scenario_path.resolve().parent in (
            data_paths.ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios", data_paths.SCENARIOS_ROOT,
        )
        cache = frozen_inputs.use(scenario_path) if mutate is None and selected else nullcontext()
        with cache:
            engine = LifeEngine.create(scenario_path, tmp_path / f"run-{len(created)}", identity, None, create_options)
        created.append(engine)
        return engine

    yield create
    for engine in created:
        engine.close()



def _mutated_scenario(source: Path, mutate, root: Path) -> Path:
    """把人工场景副本交给 mutate(document) 改写，目录保持隔离，语料用相对路径引用。"""
    document = json.loads(source.read_text(encoding="utf-8"))
    mutate(document)
    corpus = (source.parent / document["corpus_root"]).resolve()
    document["corpus_root"] = relative_corpus(corpus, root / "scenarios")
    (root / "scenarios").mkdir(parents=True)
    shutil.copytree(source.resolve().parent.parent / "catalog", root / "catalog")
    path = root / "scenarios" / source.name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path
