"""@brief 通用规则随引擎发布，并在无正式环境的安装目录中可用。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.catalog import load_catalog
from worldmock.catalog.affordances import engine_affordances, load_affordances
from worldmock.categories import refresh_manifest_identity
from worldmock.errors import IntegrityError
from worldmock.life.motivation import load_motivation_transforms
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.profile.mapper import load_mapping_config

RULE_SNAPSHOTS = ("affordances.toml", "rules/motivation.toml", "rules/persona-mapping.toml")


def test_engine_rules_ignore_working_directory_and_environment(monkeypatch, tmp_path):
    """@brief 通用闭集与人物映射不读取当前目录或环境变量指定的具体环境。"""
    directory = tmp_path / "catalog"
    directory.mkdir()
    text = files("worldmock.data").joinpath("affordances.toml").read_text(encoding="utf-8")
    (directory / "affordances.toml").write_text(
        text.replace('met_key = "wait"', 'met_key = "working-directory-wait"', 1), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORLDMOCK_PERSONA_MAPPING", "missing-persona-mapping.toml")
    engine_affordances.cache_clear()
    view = engine_affordances()
    assert set(view.kinds) == {
        "wait", "sleep", "work", "watch", "play_game", "travel", "read_mail",
        "browse", "consume", "lifestyle",
    }
    assert view.resource_claims("watch", content_kind="podcast_episode") == frozenset({"watch"})
    assert view.resource_claims("watch", content_kind="feature_film") == frozenset({"watch", "screen"})
    assert view.met_lookup_key("wait") == "wait"
    assert load_mapping_config()["world_dynamics"]["diversity_weight"]["slope_openness"] == 0.5


@pytest.fixture
def external_environment(tmp_path):
    """@brief 把人工最小环境复制到独立目录，通过真实外部加载入口验证。"""
    root = tmp_path / "external-environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    return root


def test_external_environment_loads_with_exact_packaged_rule_snapshots(external_environment):
    """@brief 环境三份通用规则均逐字节绑定包资源后，完整场景加载成功。"""
    directory = external_environment / "catalog"
    for relative in RULE_SNAPSHOTS:
        actual = (directory / relative).read_bytes()
        expected = files("worldmock.data").joinpath(Path(relative).name).read_bytes()
        assert actual == expected
    semantics, digest = load_runtime_scenario(external_environment / "scenarios/single-person.json", directory)
    assert semantics.scenario.schema_version == 6
    assert semantics.scenario.contents[0].title == "人工测试内容"
    assert semantics.affordances.met_lookup_key("wait") == "wait"
    assert len(digest) == 64


@pytest.mark.parametrize("relative", RULE_SNAPSHOTS)
def test_external_environment_rejects_missing_rule_snapshot(external_environment, relative, caplog):
    """@brief 重算目录身份后，缺规则仍由唯一包资源绑定门禁拒绝。"""
    directory = external_environment / "catalog"
    (directory / relative).unlink()
    identity = refresh_manifest_identity(directory)
    assert load_catalog(directory).catalog_content_sha256 == identity.catalog_content_sha256
    with pytest.raises(IntegrityError) as rejected:
        load_runtime_scenario(external_environment / "scenarios/single-person.json", directory)
    assert str(rejected.value) == f"Engine rule snapshot is missing: {relative}"
    assert isinstance(rejected.value.__cause__, FileNotFoundError)
    assert f"Engine rule snapshot is missing: {relative}" in caplog.text


@pytest.mark.parametrize("relative,previous,replacement", [
    ("affordances.toml", "feedback_boundary = true", "feedback_boundary = false"),
    ("rules/motivation.toml", "loss_exponent = 0.88", "loss_exponent = 0.9"),
    ("rules/persona-mapping.toml", "intercept = 55.0", "intercept = 60.0"),
])
def test_external_environment_rejects_changed_legal_rule_snapshot(
    external_environment, relative, previous, replacement,
):
    """@brief 合法参数变化与目录摘要一致时仍不能生成另一份人工通用规则。"""
    directory = external_environment / "catalog"
    path = directory / relative
    text = path.read_text(encoding="utf-8")
    assert previous in text
    changed = text.replace(previous, replacement, 1)
    assert tomllib.loads(changed) != tomllib.loads(text)
    path.write_text(changed, encoding="utf-8")
    _assert_rule_is_semantically_valid(directory, relative)
    _assert_changed_snapshot_rejected(external_environment, relative)


@pytest.mark.parametrize("field", ["target_corpus", "title_source"])
def test_external_environment_rejects_old_source_mapping_snapshot(external_environment, field):
    """@brief 旧来源映射即使语法合法且身份已刷新，也不能绕过包规则绑定。"""
    directory = external_environment / "catalog"
    path = directory / "affordances.toml"
    marker = 'target_discovery = "none"' if field == "target_corpus" else "attention_required = true"
    text = path.read_text(encoding="utf-8")
    changed = text.replace(marker, f'{marker}\n{field} = "obsolete"', 1)
    assert tomllib.loads(changed) != tomllib.loads(text)
    path.write_text(changed, encoding="utf-8")
    _assert_changed_snapshot_rejected(external_environment, "affordances.toml")


def _assert_rule_is_semantically_valid(directory, relative):
    """@brief 先证明各参数修改能通过原规则解析，避免用坏语法制造失败。"""
    if relative == "affordances.toml":
        assert load_affordances(directory / relative).kinds["wait"].feedback_boundary is False
    elif relative == "rules/motivation.toml":
        assert load_motivation_transforms(directory / "rules").loss_exponent == 0.9
    else:
        assert relative == "rules/persona-mapping.toml"
        config = load_mapping_config(directory / relative)
        assert config["world_dynamics"]["mood_halflife_minutes"]["intercept"] == 60.0


def _assert_changed_snapshot_rejected(root, relative):
    """@brief 真正目录加载通过后，精确断言失败来自包资源字节绑定。"""
    directory = root / "catalog"
    identity = refresh_manifest_identity(directory)
    assert load_catalog(directory).catalog_content_sha256 == identity.catalog_content_sha256
    with pytest.raises(IntegrityError) as rejected:
        load_runtime_scenario(root / "scenarios/single-person.json", directory)
    assert str(rejected.value) == f"Engine rule snapshot does not match the installed engine: {relative}"
    assert rejected.value.__cause__ is None


def test_persona_mapping_keeps_explicit_rule_path(tmp_path):
    """@brief 调用方显式传入规则文件时读取该文件，不覆盖包中的通用规则。"""
    text = files("worldmock.data").joinpath("persona-mapping.toml").read_text(encoding="utf-8")
    path = tmp_path / "persona-mapping.toml"
    path.write_text(text.replace("intercept = 55.0", "intercept = 60.0"), encoding="utf-8")
    assert load_mapping_config(path)["world_dynamics"]["mood_halflife_minutes"]["intercept"] == 60.0
    assert load_mapping_config()["world_dynamics"]["mood_halflife_minutes"]["intercept"] == 55.0


@pytest.mark.parametrize("field", ["target_corpus", "title_source"])
def test_registry_rejects_environment_source_fields(field, tmp_path):
    """@brief 旧来源专属声明不再受理，不能在规则中隐藏具体世界依赖。"""
    text = files("worldmock.data").joinpath("affordances.toml").read_text(encoding="utf-8")
    marker = 'target_discovery = "none"' if field == "target_corpus" else "attention_required = true"
    path = tmp_path / "affordances.toml"
    path.write_text(text.replace(marker, f'{marker}\n{field} = "obsolete"', 1), encoding="utf-8")
    with pytest.raises(IntegrityError, match=field):
        load_affordances(path)


def test_packaged_registry_contains_no_source_projection():
    """@brief 运行时视图只声明行为与注意力，不提供标题来源或语料路径接口。"""
    view = engine_affordances()
    assert not hasattr(view, "title_sources")
    assert all(not hasattr(spec, "target_corpus") for spec in view.kinds.values())
    for family in view.families.values():
        for kind in family.content_kinds:
            assert not hasattr(kind, "title_source")
            assert not hasattr(kind, "corpus_path")
            assert not hasattr(kind, "id_key")


def test_mapping_failure_is_logged(tmp_path, caplog):
    """@brief 显式文件缺失会留下英文错误日志并传播错误。"""
    with pytest.raises(FileNotFoundError):
        load_mapping_config(tmp_path / "missing.toml")
    assert "Failed to load persona mapping rules" in caplog.text


def test_built_wheel_reads_all_rules_after_installation(tmp_path):
    """@brief 构建真实 wheel 并独立安装，仓库外加载三份包规则。"""
    root = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(root / "pyproject.toml", project / "pyproject.toml")
    shutil.copytree(root / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"))
    build_code = "from setuptools.build_meta import build_wheel; build_wheel('dist')"
    subprocess.run([sys.executable, "-c", build_code], cwd=project, check=True, capture_output=True, text=True)
    wheel, = (project / "dist").glob("*.whl")
    installed = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(installed), str(wheel)],
        check=True, capture_output=True, text=True,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    environment = {**os.environ, "PYTHONPATH": str(installed),
                   "WORLDMOCK_PERSONA_MAPPING": "missing-persona-mapping.toml"}
    result = subprocess.run(
        [sys.executable, "-c", _installed_probe(), str(installed)],
        cwd=outside, env=environment, check=True, capture_output=True, text=True,
    )
    assert json.loads(result.stdout) == {"kind_count": 10, "persona_version": 2, "motivation_version": 1}


def _installed_probe() -> str:
    """@brief 子进程从独立安装包读取资源，并核对未导入源码树。"""
    return """
import json
import sys
import tomllib
from importlib.resources import files
from pathlib import Path
import worldmock
from worldmock.catalog.affordances import engine_affordances
from worldmock.profile.mapper import load_mapping_config
assert Path(worldmock.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
rules = files('worldmock.data')
motivation = tomllib.loads(rules.joinpath('motivation.toml').read_text(encoding='utf-8'))
print(json.dumps({'kind_count': len(engine_affordances().kinds),
                  'persona_version': load_mapping_config()['version'],
                  'motivation_version': motivation['version']}))
"""
