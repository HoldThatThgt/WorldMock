"""@brief 阶段1保留包规则原字节，环境副本始终逐字节绑定。"""

from importlib.resources import files
from pathlib import Path

import pytest

from worldmock.catalog.affordances import RESOURCE_CLAIM_SLOTS, engine_affordances
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.catalog.loader import load_catalog
from worldmock.corpora.manifest import load_manifest
from worldmock.exchange.motivation import load_motivation_transforms

RULE_SNAPSHOTS = ("affordances.toml", "rules/motivation.toml", "rules/persona-mapping.toml")


def test_artificial_rule_snapshots_equal_engine_package(catalog_dir):
    """@brief 三份TOML和清单真实加载，阶段1不提前收紧旧包声明。"""
    for relative in RULE_SNAPSHOTS:
        assert (catalog_dir / relative).read_bytes() == files("worldmock.data").joinpath(Path(relative).name).read_bytes()
    catalog = load_catalog(catalog_dir)
    assert len(catalog.manifest.files) == 9
    assert len(RESOURCE_CLAIM_SLOTS) == 11
    assert engine_affordances().met_lookup_key("wait") == "wait"
    assert load_motivation_transforms(catalog_dir / "rules").loss_exponent == 0.88


@pytest.mark.parametrize("relative", RULE_SNAPSHOTS)
def test_changed_rule_snapshot_is_rejected_after_identity_refresh(catalog_dir, relative):
    """@brief 即使环境摘要重新发布，包规则副本字节漂移仍拒绝。"""
    with (catalog_dir / relative).open("ab") as handle:
        handle.write(b"\n")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("relative", RULE_SNAPSHOTS)
def test_missing_rule_snapshot_is_rejected(catalog_dir, relative):
    """@brief 必需规则没有缺文件回退或关闭路径。"""
    (catalog_dir / relative).unlink()
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        load_catalog(catalog_dir)


def test_missing_motivation_rules_are_rejected(tmp_path):
    """@brief 动机纯加载器对缺文件直接报英文完整性错误。"""
    from worldmock.errors import IntegrityError
    with pytest.raises(IntegrityError, match="Motivation rule snapshot is missing"):
        load_motivation_transforms(tmp_path)


def test_artificial_corpus_only_registers_lifestyle():
    """@brief 已删除地图和字幕语料不再留在清单引用里。"""
    root = Path(__file__).resolve().parents[1] / "fixtures" / "environment" / "corpora"
    assert [entry.path for entry in load_manifest(root).entries] == ["lifestyle/items.json"]
