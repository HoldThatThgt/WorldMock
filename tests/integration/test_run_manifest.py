"""运行 manifest 阶段 model_identity（stage_model_identity：action 与 work_review）冻结与防篡改测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import copy
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.errors import IntegrityError
from worldmock.life.models import ModelIdentity, RunIdentity
from worldmock.life.world_setup import load_scenario
from worldmock.run_setup import _model_identity, runtime_code_sha256, stage_model_identity
from worldmock.runtime import _require_runtime_identity
from worldmock.run_setup import validate_stage_model_identity
from tests.integration.as2_fixtures import use_fake_as2

_SCENARIO = ENVIRONMENT_ROOT / "scenarios/single-person.json"


@pytest.mark.parametrize("field,expected", [("transport_max_retries", 3), ("max_react_turns", 16)])
@pytest.mark.parametrize("change", ["missing", "incremented", "float", "string"])
def test_run_budgets_are_frozen_and_missing_or_changed_values_rejected(field, expected, change) -> None:
    """真实运行身份冻结纠错预算，旧身份缺字段或篡改时拒绝，不生成缺省值。"""
    identity = stage_model_identity()
    assert identity[field] == expected
    assert validate_stage_model_identity(identity) == identity
    if change == "missing":
        del identity[field]
    else:
        identity[field] = {"incremented": expected + 1, "float": float(expected), "string": str(expected)}[change]
    with pytest.raises(IntegrityError, match=field):
        validate_stage_model_identity(identity)


def _run_identity(scenario) -> RunIdentity:
    """形成与 conftest 工厂同口径的运行身份（隔离运行目录）。"""
    run_id = f"manifest-test-{scenario.scenario_id}"
    return RunIdentity(
        run_id=run_id,
        source_group_id=f"group-{run_id}",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(),
        end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone,
        code_sha256="3" * 64,
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


async def test_life_env_init_freezes_stage_identity_into_manifest(tmp_path) -> None:
    """生产接线：LifeEnv 新建运行把 action 与 work_review 两个阶段的身份冻结进运行 manifest。"""
    from custom.envs.life_env import LifeEnv

    scenario, _ = load_scenario(_SCENARIO)
    env = LifeEnv(scenario_path=str(_SCENARIO), run_dir=str(tmp_path / "run"),
                  run_identity=_run_identity(scenario).model_dump(mode="json"))
    await env.init(datetime.fromtimestamp(
        scenario.start_at.timestamp(), ZoneInfo(scenario.timezone)))
    try:
        manifest = env.engine.context.store.manifest
    finally:
        await env.close()

    identity = manifest["stage_model_identity"]
    assert "two_stage_model_identity" not in manifest
    assert identity["stages"] == {"action": {"thinking": "enabled", "tool_choice": "auto", "max_tokens": 8000,
                                             "same_second_continuation": True},
                                  "work_review": {"thinking": "disabled", "tool_choice": "required"}}
    # 与 runtime 单一事实源逐字节一致。
    assert identity == stage_model_identity()
    validate_stage_model_identity(identity)
    # model_identity 块保持恢复门可比形状（runtime._require_runtime_identity）。
    assert set(manifest["model_identity"]) == {
        "provider", "base_url", "requested_model", "thinking", "tool_choice",
        "max_retries", "prompt_sha256", "tool_schema_sha256"}


def test_manifest_stage_identity_tampering_is_rejected(tmp_path) -> None:
    """篡改拒绝：manifest 冻结值的任何阶段篡改被一致性校验拒绝。"""
    from worldmock.life.engine import LifeEngine
    scenario, _ = load_scenario(_SCENARIO)
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run", _run_identity(scenario),
                               stage_model_identity())
    try:
        frozen = copy.deepcopy(engine.context.store.manifest["stage_model_identity"])
    finally:
        engine.close()

    validate_stage_model_identity(frozen)
    tampered = copy.deepcopy(frozen)
    tampered["stages"]["action"]["tool_choice"] = "required"
    with pytest.raises(IntegrityError, match="stage action"):
        validate_stage_model_identity(tampered)
    wrong_endpoint = copy.deepcopy(frozen)
    wrong_endpoint["base_url"] = "https://elsewhere.example/v1"
    with pytest.raises(IntegrityError, match="base_url"):
        validate_stage_model_identity(wrong_endpoint)


@pytest.mark.parametrize("keep_current", [False, True])
def test_retired_stage_is_rejected_even_beside_the_current_stage(keep_current, caplog) -> None:
    """阶段名精确闭集：退役的 decision_statement 替换或混入当前清单都以退役错误拒绝；其他陌生阶段按闭集拒绝。"""
    identity = stage_model_identity()
    identity["stages"]["decision_statement"] = {"thinking": "disabled", "temperature": 1.0}
    if not keep_current:
        del identity["stages"]["action"]

    with pytest.raises(IntegrityError, match="Model identity stage decision_statement is retired"):
        validate_stage_model_identity(identity)
    unknown = stage_model_identity()
    unknown["stages"]["monologue"] = unknown["stages"].pop("action")
    with pytest.raises(IntegrityError, match="stages must be exactly action, work_review"):
        validate_stage_model_identity(unknown)


def test_engine_create_rejects_retired_stage_before_writing_run_artifacts(tmp_path) -> None:
    """真实新建入口拒绝退役阶段；独立校验通过不能替代持久化前校验。"""
    from worldmock.life.engine import LifeEngine
    scenario, _ = load_scenario(_SCENARIO)
    identity = stage_model_identity()
    identity["stages"]["decision_statement"] = {"thinking": "disabled"}
    run_dir = tmp_path / "old-stage"

    with pytest.raises(IntegrityError, match="stage decision_statement is retired"):
        LifeEngine.create(_SCENARIO, run_dir, _run_identity(scenario), identity)

    assert not (run_dir / "manifest.json").exists()
    assert not (run_dir / "footprints.sqlite").exists()


def test_runtime_restore_rejects_retired_stage_even_when_code_identity_matches(monkeypatch, tmp_path_factory) -> None:
    """恢复入口独立验证阶段身份，不能只依赖源码哈希间接拒绝旧清单（AS2 取锁定的临时包）。"""
    use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))
    root = Path(__file__).resolve().parents[2]
    stages = stage_model_identity()
    stages["stages"]["decision_statement"] = {"thinking": "disabled"}
    manifest = {"model_identity": _model_identity().model_dump(mode="json"),
                "stage_model_identity": stages, "code_sha256": runtime_code_sha256(root)}

    with pytest.raises(IntegrityError, match="stage decision_statement is retired"):
        _require_runtime_identity(manifest, root)
    old = {key: value for key, value in manifest.items() if key != "stage_model_identity"}
    old["two_stage_model_identity"] = stages
    with pytest.raises(IntegrityError, match="stage decision_statement is retired"):
        _require_runtime_identity(old, root)


def test_run_identity_records_git_state_as_a_pair() -> None:
    """git_commit 与 git_dirty 同时记录或在非 Git 检出时同时为 null；提交须为完整十六进制身份，标志须为布尔。"""
    scenario, _ = load_scenario(_SCENARIO)
    base = _run_identity(scenario).model_dump()
    assert (base["git_commit"], base["git_dirty"]) == (None, None)
    recorded = RunIdentity.model_validate({**base, "git_commit": "a" * 40, "git_dirty": False})
    assert (recorded.git_commit, recorded.git_dirty) == ("a" * 40, False)
    for fields in ({"git_commit": "a" * 40, "git_dirty": None}, {"git_commit": None, "git_dirty": True},
                   {"git_commit": "HEAD", "git_dirty": False}, {"git_commit": "a" * 40, "git_dirty": "yes"}):
        with pytest.raises(ValidationError):
            RunIdentity.model_validate({**base, **fields})


def test_engine_created_without_stage_identity_keeps_null_slot(tmp_path) -> None:
    """反例：未传阶段身份时 manifest 槽位为 null（既有测试工厂口径不变）。"""
    from worldmock.life.engine import LifeEngine
    scenario, _ = load_scenario(_SCENARIO)
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run", _run_identity(scenario))
    try:
        assert engine.context.store.manifest["stage_model_identity"] is None
    finally:
        engine.close()


def test_engine_direct_runs_keep_input_warnings_null_and_unrecorded(tmp_path) -> None:
    """运行输入警告只在新建运行时计算（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 ）：引擎直驱没有计算，清单的 input_warnings
    是 null 而不是空列表，质量报告据此把这一节记为未记录、计数为 null（不记为零）；新建运行算出的空列表照样冻结为 []。"""
    from worldmock.life.engine import LifeEngine
    from worldmock.footprints.quality import build_quality_report
    scenario, _ = load_scenario(_SCENARIO)
    direct = LifeEngine.create(_SCENARIO, tmp_path / "direct", _run_identity(scenario))
    created = LifeEngine.create(_SCENARIO, tmp_path / "created", _run_identity(scenario),
                                create_options={"input_warnings": []})
    try:
        frozen = (direct.context.store.manifest["input_warnings"], created.context.store.manifest["input_warnings"])
        section = build_quality_report(direct.context.store.read_events(direct.context.store.last_event_seq), [],
                                       direct.context.store.manifest)["input_warnings"]
    finally:
        direct.close()
        created.close()
    assert frozen == (None, [])
    assert (section["recorded"], section["warning_count"], section["code_counts"]) == (False, None, None)


def test_run_manifest_structure_three_freezes_environment_identity():
    """真实清单装配冻结五环境身份，删除旧顶层品类和地图来源字段。"""
    from worldmock.life.run_identity import environment_identity
    from worldmock.life.world_setup import _run_manifest, load_runtime_scenario

    semantics, digest = load_runtime_scenario(_SCENARIO)
    manifest = _run_manifest(semantics, digest, _run_identity(semantics.scenario))
    assert manifest["schema_version"] == 3
    assert manifest["environment_identity"] == environment_identity(semantics)
    assert set(manifest["environment_identity"]) == {
        "environment_id", "environment_version", "rules_version", "catalog_content_sha256", "map_sha256"}
    assert not {"category_contract_version", "category_contract_sha256", "exchange_rules_sha256",
                "catalog_content_sha256", "map_sha256", "map_provenance"} & manifest.keys()
