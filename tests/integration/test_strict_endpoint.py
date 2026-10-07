"""strict 端点切换（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-strict-端点  规则 2，裁决 D14、S6；验收台账「strict 端点」）。

模型端点常量是 DeepSeek 的 strict（Beta）端点 https://api.deepseek.com/beta：validate（validate_project 的真实路径，
进程配置走真实的 _configure_process）核对的阶段身份与写进 AS2 的默认、CODER 端点都是它；将冻结的阶段身份或进程里已导入的
AS2 配置仍是主站即拒绝。父运行清单冻结在主站的运行，经真实恢复准备路径（_prepare_resume，普通恢复与反事实重放共用）
被既有身份核对拒绝，不写子运行。每次运行不做在线自检（规则 3），这里不发任何模型请求。
"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
import worldmock.run_setup as worldmock_run_setup_module

import os
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import worldmock.runtime as runtime  # noqa: E402
import worldmock.validation as validation  # noqa: E402
from tests.integration.as2_fixtures import use_fake_as2, write_project  # noqa: E402
from tests.integration.runtime_fixtures import write_agent_workspace  # noqa: E402
from tests.profile.conftest import make_card, make_window  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.footprints.checkpoint import snapshot_directory  # noqa: E402
from worldmock.footprints.store import FootprintStore  # noqa: E402
from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.models import RunIdentity  # noqa: E402
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.model_endpoint import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL  # noqa: E402
from worldmock.profile.models import HabitSignature, Tastes  # noqa: E402
from worldmock.runtime import ResumeRequest, _prepare_resume  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ENVIRONMENT_ROOT / "scenarios" / "single-person.json"
STRICT_ENDPOINT = "https://api.deepseek.com/beta"
MAIN_SITE = "https://api.deepseek.com"
#: 主站地址被阶段身份核对拒绝时的英文原因。
_MAIN_SITE_REFUSAL = (r"base_url='https://api\.deepseek\.com' must be the DeepSeek strict endpoint "
                      r"'https://api\.deepseek\.com/beta'")
#: 真实 _configure_process 改写的进程环境变量（测试结束时由 monkeypatch 复原）。
_PROCESS_VARIABLES = ("WORKSPACE_PATH", "AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_LLM_API_BASE", "AGENTSOCIETY_LLM_MODEL",
                      "AGENTSOCIETY_CODER_LLM_API_KEY", "AGENTSOCIETY_CODER_LLM_API_BASE",
                      "AGENTSOCIETY_CODER_LLM_MODEL", "AGENTSOCIETY_EMBEDDING_MODEL", "LITELLM_LOCAL_MODEL_COST_MAP",
                      "AGENTSOCIETY_LLM_REQUEST_TIMEOUT", "MEM0_TELEMETRY", "ANONYMIZED_TELEMETRY", "PYTHONPATH")


@pytest.fixture(autouse=True)
def _locked_fake_as2(monkeypatch, tmp_path_factory):
    """运行身份与 AS2 锁定核对解析到临时 AS2 包并以其摘要为锁定值，不依赖本机实际安装的 AS2。"""
    return use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))


def _covered(story: str) -> SimpleNamespace:
    """确定性画像编译桩：任意背景故事都给全部命中真实目录目标的卡片（紫薇公园、悬疑、牛肉炒面、长视频）。"""
    return SimpleNamespace(card=make_card(
        habits=(HabitSignature(trigger="下班到家", response="去紫薇公园散步", time_window=make_window(19.0, 20.0)),),
        tastes=Tastes(content_genres=("悬疑",), food_preferences=("牛肉炒面",), media=("长视频",))))


def _validate_root(tmp_path: Path) -> Path:
    """validate 的工程根：真实 _configure_process 要求的 custom/agents/life_agent.py、空的 labelkit-projects 与 LabelKit
    命令占位（这里核对的是模型身份，不执行 LabelKit）。"""
    root = write_project(tmp_path / "project")
    (root / "labelkit-projects").mkdir()
    command = root / "vendor/labelkit/.venv" / ("Scripts/labelkit.exe" if os.name == "nt" else "bin/labelkit")
    command.parent.mkdir(parents=True)
    command.write_bytes(b"")
    return root


def _configure_as(monkeypatch, tmp_path: Path, as2_base: str) -> None:
    """真实 _configure_process 的前提：隔离 HOME 下的假密钥池，改写的环境变量在测试结束时复原；进程里已导入的 AS2 配置
    按 as2_base 与配置的模型给出（模拟 AS2 在进程配置之后导入）。"""
    from agentsociety2.config.config import Config

    home = tmp_path / "home"
    secret = home / ".config" / "worldmock" / "deepseek.env"
    secret.parent.mkdir(parents=True)
    secret.write_text("DEEPSEEK_NODE_KEYS=endpoint-secret-a,endpoint-secret-b\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in _PROCESS_VARIABLES:
        monkeypatch.setenv(name, os.environ.get(name, ""))
    for name, value in (("LLM_API_BASE", as2_base), ("CODER_LLM_API_BASE", as2_base), ("LLM_MODEL", DEEPSEEK_MODEL),
                        ("CODER_LLM_MODEL", DEEPSEEK_MODEL), ("EMBEDDING_MODEL", "")):
        monkeypatch.setattr(Config, name, value)


def test_validate_checks_a_stage_identity_on_the_strict_endpoint(tmp_path, monkeypatch) -> None:
    """验收：validate 的真实路径（真实 _configure_process 写 AS2 环境并核对已导入的 AS2 配置）核对的阶段身份就是新运行
    将冻结的身份，其 base_url 是 strict 端点 https://api.deepseek.com/beta；核对时写进 AS2 的默认与 CODER 端点也是它；
    校验通过并报告两个阶段。"""
    _configure_as(monkeypatch, tmp_path, STRICT_ENDPOINT)
    checked = []
    original = worldmock_run_setup_module.validate_manifest_stage_identity

    def spy(manifest=None):
        """记录 validate 核对的阶段身份与此刻 AS2 的两个端点环境变量。"""
        identity = original(manifest)
        checked.append((manifest, identity, os.environ["AGENTSOCIETY_LLM_API_BASE"],
                        os.environ["AGENTSOCIETY_CODER_LLM_API_BASE"]))
        return identity

    monkeypatch.setattr(worldmock_run_setup_module, 'validate_manifest_stage_identity', spy)
    result = validation.validate_project(_validate_root(tmp_path), SCENARIO, persona_compiler=_covered,
                                         coverage_out=tmp_path / "coverage.json")

    assert DEEPSEEK_BASE_URL == STRICT_ENDPOINT
    [(manifest, identity, default_base, coder_base)] = checked
    assert manifest is None and identity == worldmock_run_setup_module.stage_model_identity()
    assert identity["base_url"] == STRICT_ENDPOINT and identity["strict"] is True
    assert (default_base, coder_base) == (STRICT_ENDPOINT, STRICT_ENDPOINT)
    assert worldmock_run_setup_module._model_identity().base_url == STRICT_ENDPOINT
    assert result["valid"] is True
    assert result["model_identity"] == {"stages": ["action"], "consistent": True}


@pytest.mark.parametrize(("where", "error", "message"), [
    ("stage_identity", IntegrityError, _MAIN_SITE_REFUSAL),
    ("as2_process", RuntimeError, "AgentSociety2 was imported before the required model configuration"),
], ids=["identity-on-main-site", "as2-imported-for-main-site"])
def test_validate_refuses_the_main_site(tmp_path, monkeypatch, where: str, error: type, message: str) -> None:
    """validate 拒绝主站：新运行将冻结的阶段身份记着主站地址即以 base_url 拒绝；进程里已导入的 AS2 配置仍指向主站时，
    真实 _configure_process 在核对阶段身份之前拒绝。"""
    _configure_as(monkeypatch, tmp_path, MAIN_SITE if where == "as2_process" else STRICT_ENDPOINT)
    if where == "stage_identity":
        current = worldmock_run_setup_module.stage_model_identity()
        monkeypatch.setattr(worldmock_run_setup_module, 'stage_model_identity', lambda *_args: {**current, 'base_url': MAIN_SITE})

    with pytest.raises(error, match=message):
        validation.validate_project(_validate_root(tmp_path), SCENARIO, persona_compiler=_covered,
                                    coverage_out=tmp_path / "coverage.json")


def _parent_run(base: Path, base_url: str, *, stage: bool) -> str:
    """@brief 真实引擎创建完整世界和当前环境身份，检查点明确冻结端点差异供恢复核对。"""
    semantics, _ = load_runtime_scenario(SCENARIO)
    scenario = semantics.scenario
    identity = RunIdentity(run_id="parent", source_group_id="parent", agent_ids=(1,),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(), timezone=scenario.timezone,
        model_identity=worldmock_run_setup_module._model_identity(),
        code_sha256=worldmock_run_setup_module.runtime_code_sha256(ROOT))
    engine = LifeEngine.create(SCENARIO, base / "parent", identity)
    parent = engine.context.store
    stage_identity = worldmock_run_setup_module.stage_model_identity()
    if stage:
        stage_identity["base_url"] = base_url
    parent.manifest["stage_model_identity"] = stage_identity
    parent.manifest["model_identity"]["base_url"] = base_url
    (parent.run_dir / "manifest.json").write_text(json.dumps(parent.manifest))
    state = engine.context.state.model_dump(mode="json")
    try:
        workspace = write_agent_workspace(base / "agent")
        snapshots = {"agent:1": snapshot_directory(workspace, parent.assets),
                     "life": parent.assets.put_json({"state_version": state["version"]}, {"kind": "life_checkpoint"})}
        return parent.checkpoint(state, snapshots).checkpoint_id
    finally:
        engine.close()


@pytest.mark.parametrize(("stage", "message"), [
    (True, _MAIN_SITE_REFUSAL),
    (False, "Checkpoint model identity differs from the current LifeAgent contract"),
], ids=["manifest-on-main-site", "model-identity-on-main-site"])
def test_resume_refuses_a_parent_run_frozen_on_the_main_site(tmp_path, stage: bool, message: str) -> None:
    """父运行冻结当前身份（strict 端点）时经真实恢复准备路径照常恢复；同样建立、只是冻结在主站的父运行被同一路径经既有
    身份核对拒绝：阶段身份记着主站以 base_url 拒绝，只有 model_identity 记着主站以模型身份不符拒绝；都不写子运行。"""
    current = _parent_run(tmp_path / "current", STRICT_ENDPOINT, stage=True)
    restored = _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "current" / "parent", current,
                                             tmp_path / "current" / "child"))
    assert restored.workspace_mode == "restored"
    old = _parent_run(tmp_path / "old", MAIN_SITE, stage=stage)

    with pytest.raises(IntegrityError, match=message):
        _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "old" / "parent", old, tmp_path / "old" / "child"))
    assert not (tmp_path / "old" / "child").exists()
