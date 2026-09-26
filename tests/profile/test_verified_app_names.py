"""@brief verified_app_names 直接补测：最小场景投影与临时语料根，不依赖 LabelKit。

函数只按鸭子类型读取场景的手机配置钉扎、人物、路线、内容与供给，因此用 SimpleNamespace
构造场景；语料根只含手机配置与登记它的 manifest。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldmock.errors import IntegrityError
from worldmock.profile.coverage_apps import verified_app_names

_RELATIVE = "scenario-phone/phone-simulation.json"
_CONFIG = {
    "schema_version": 1, "scenario_id": "zhangjiang-day", "agent_id": 7, "timezone": "Asia/Shanghai",
    "activity_apps": {"driving_travel": "ride", "watch": "video"},
    "purchase_apps_by_seller": {"seller-stocked": "food"},
    "app_display_names": {"ride": ["滴滴出行"], "video": ["哔哩哔哩", "B站"], "food": ["美团外卖"],
                          "idle": ["未接入应用"]},
    "unrelated_setting": {"ignored": True},
}


def _corpus(root: Path, config: dict | bytes = _CONFIG, *, listed: bool = True, pinned: str | None = None) -> str:
    """@brief 写出手机配置与登记它的 manifest，返回配置内容哈希。"""
    content = config if isinstance(config, bytes) else json.dumps(config, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    (root / _RELATIVE).parent.mkdir(parents=True, exist_ok=True)
    (root / _RELATIVE).write_bytes(content)
    entry = {"entry_id": "scenario-phone:phone-simulation.json", "path": _RELATIVE, "sha256": pinned or digest,
             "size_bytes": len(content), "media_type": "application/json", "purpose": "phone simulation",
             "source": {"kind": "scenario", "license": "WorldMock-project", "selector": "complete frozen file"}}
    manifest = {"schema_version": 1, "corpus_id": "phone-fixture", "created_at": "2026-09-15T08:00:00+08:00",
                "transformer_version": "fixture-1", "entries": [entry] if listed else []}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return digest


def _scenario(digest: str, **overrides) -> SimpleNamespace:
    """@brief 覆盖核验读取的最小场景投影：钉扎、人物、路线、内容与供给。"""
    values = {
        "phone_simulation_config": _RELATIVE, "phone_simulation_config_sha256": digest,
        "scenario_id": "zhangjiang-day", "timezone": "Asia/Shanghai",
        "agents": [SimpleNamespace(agent_id=7)],
        "routes": [SimpleNamespace(mode="walking", status="reachable"),
                   SimpleNamespace(mode="driving", status="reachable")],
        "contents": ["sintel"],
        "supplies": [SimpleNamespace(seller_account_id="seller-stocked", initial_stock=2),
                     SimpleNamespace(seller_account_id="seller-empty", initial_stock=0)],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_returns_sorted_aliases_of_apps_backed_by_scenario_actions(tmp_path):
    digest = _corpus(tmp_path)

    # "idle" 没有任何活动或卖家映射，其别名不得出现。
    assert verified_app_names(_scenario(digest), tmp_path) == ("B站", "哔哩哔哩", "滴滴出行", "美团外卖")
    assert verified_app_names(_scenario(digest, phone_simulation_config=None), tmp_path) == ()


@pytest.mark.parametrize("corpus, scenario_pin", [
    ({"listed": False}, None),
    ({"pinned": "f" * 64}, None),
    ({}, "0" * 64),
], ids=["not-in-manifest", "manifest-pins-other-content", "scenario-pins-other-content"])
def test_rejects_config_identity_not_pinned_by_corpus_and_scenario(tmp_path, corpus, scenario_pin):
    digest = _corpus(tmp_path, **corpus)

    with pytest.raises(IntegrityError, match="identity is invalid"):
        verified_app_names(_scenario(scenario_pin or digest), tmp_path)


@pytest.mark.parametrize("content", [
    b"{not json",
    json.dumps({**_CONFIG, "schema_version": 2}).encode("utf-8"),
], ids=["malformed-json", "unsupported-schema"])
def test_rejects_pinned_config_with_invalid_schema(tmp_path, content):
    digest = _corpus(tmp_path, content)

    with pytest.raises(IntegrityError, match="schema is invalid") as caught:
        verified_app_names(_scenario(digest), tmp_path)

    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize("override", [
    {"scenario_id": "other-day"}, {"timezone": "UTC"}, {"agent_id": 8},
], ids=["scenario", "timezone", "person"])
def test_rejects_config_bound_to_another_scenario_or_person(tmp_path, override):
    digest = _corpus(tmp_path, {**_CONFIG, **override})

    with pytest.raises(IntegrityError, match="scenario identity is invalid"):
        verified_app_names(_scenario(digest), tmp_path)


@pytest.mark.parametrize("names", [
    {"ride": [" "], "video": ["哔哩哔哩"], "food": ["美团外卖"]},
    {"ride": ["滴滴出行"], "video": ["美团外卖"], "food": ["美团外卖"]},
], ids=["blank-alias", "duplicate-alias"])
def test_rejects_blank_or_duplicate_app_names(tmp_path, names):
    digest = _corpus(tmp_path, {**_CONFIG, "app_display_names": names})

    with pytest.raises(IntegrityError, match="app names are invalid"):
        verified_app_names(_scenario(digest), tmp_path)


@pytest.mark.parametrize("scenario_override, config_override, message", [
    ({"routes": [SimpleNamespace(mode="driving", status="unreachable")]}, {}, "activity app is not executable"),
    ({"contents": []}, {}, "activity app is not executable"),
    ({}, {"purchase_apps_by_seller": {"seller-empty": "food"}}, "purchase app is not executable"),
    ({}, {"app_display_names": {"ride": ["滴滴出行"], "video": ["哔哩哔哩"]}}, "display name is missing"),
], ids=["no-reachable-driving-route", "nothing-to-watch", "seller-out-of-stock", "app-without-name"])
def test_rejects_app_mappings_without_scenario_evidence(tmp_path, scenario_override, config_override, message):
    digest = _corpus(tmp_path, {**_CONFIG, **config_override})

    with pytest.raises(IntegrityError, match=message):
        verified_app_names(_scenario(digest, **scenario_override), tmp_path)


def test_rejects_config_path_outside_the_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    digest = _corpus(corpus)
    (tmp_path / "outside.json").write_bytes((corpus / _RELATIVE).read_bytes())

    with pytest.raises(IntegrityError, match="path is invalid"):
        verified_app_names(_scenario(digest, phone_simulation_config="../outside.json"), corpus)
