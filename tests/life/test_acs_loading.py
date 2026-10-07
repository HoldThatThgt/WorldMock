"""@brief 动作与内容简化的世界、运行身份及包规则直接所有者验证。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import registered_world
from worldmock.errors import IntegrityError
from worldmock.life.models import ModelIdentity, RunIdentity, WorldState
from worldmock.life.run_identity import environment_identity, require_environment_identity
from worldmock.life.world_setup import _run_manifest
from worldmock.run_setup import runtime_code_sha256


def _identity(scenario):
    """@brief 形成只服务纯清单构造的合法运行身份。"""
    return RunIdentity(
        run_id="pure-loading", source_group_id="pure-loading-group", agent_ids=(1,),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(), timezone=scenario.timezone,
        code_sha256="2" * 64,
        model_identity=ModelIdentity(provider="openai_compatible", base_url="https://api.deepseek.com/beta",
                                     requested_model="deepseek-flash", thinking="enabled", tool_choice="auto",
                                     max_retries=0, prompt_sha256="0" * 64, tool_schema_sha256="1" * 64),
    )


def test_world_eleven_rejects_old_version_and_corpus_identity():
    """@brief 严格世界模型只受理当前结构，不补齐旧结构，也不保留语料钉。"""
    _, state = registered_world()
    payload = state.model_dump(mode="python")
    assert WorldState.model_validate(payload).schema_version == 11
    assert "content_manifest_sha256" not in payload
    with pytest.raises(ValidationError, match="Input should be 11"):
        WorldState.model_validate({**payload, "schema_version": 10})
    with pytest.raises(ValidationError, match="content_manifest_sha256"):
        WorldState.model_validate({**payload, "content_manifest_sha256": "0" * 64})


def test_run_manifest_has_five_environment_fields_without_corpus_pin():
    """@brief 真实清单构造绑定当前目录身份，语料不再属于运行身份。"""
    semantics, state = registered_world()
    manifest = _run_manifest(semantics, state.scenario_sha256, _identity(semantics.scenario))
    assert manifest["schema_version"] == 4
    assert set(manifest["environment_identity"]) == {
        "environment_id", "environment_version", "rules_version", "catalog_content_sha256", "map_sha256",
    }
    assert manifest["environment_identity"] == environment_identity(semantics)
    assert manifest["environment_identity"]["rules_version"] == 2
    assert "corpus_manifest_sha256" not in manifest
    require_environment_identity(manifest, semantics)


@pytest.mark.parametrize("damage", ["old_manifest", "old_rules", "changed_catalog", "extra_identity"])
def test_run_identity_rejects_old_manifest_and_different_environment(damage):
    """@brief 打开、恢复与重放共用的纯身份入口拒绝旧清单或任何环境漂移。"""
    semantics, _ = registered_world()
    identity = environment_identity(semantics)
    manifest = {"schema_version": 4, "environment_identity": identity}
    if damage == "old_manifest":
        manifest["schema_version"] = 3
    elif damage == "old_rules":
        identity["rules_version"] = 1
    elif damage == "changed_catalog":
        identity["catalog_content_sha256"] = "0" * 64
    else:
        identity["rules_sha256"] = "0" * 64
    with pytest.raises(IntegrityError, match="Runtime environment identity mismatch"):
        require_environment_identity(manifest, semantics)


def test_runtime_code_identity_binds_each_packaged_data_file(tmp_path):
    """@brief 在独立源码树逐个改规则字节，每次都使真实运行代码身份变化。"""
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "repository"
    root.mkdir()
    for name in ("src", "custom"):
        shutil.copytree(source / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(source / name, root / name)
    baseline = runtime_code_sha256(root)
    files = sorted(path for path in (root / "src/worldmock/data").rglob("*") if path.is_file())
    assert files
    for path in files:
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        assert runtime_code_sha256(root) != baseline, path.relative_to(root)
        path.write_bytes(original)
        assert runtime_code_sha256(root) == baseline
