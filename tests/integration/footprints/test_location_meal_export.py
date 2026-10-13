"""带地点内容完成效果的运行：导出并通过认证，改写完成效果证据的导出过不了；在地点吃的一餐不是收货，不进购买投影、
食品效率样本、「收到」经历和食品多样性（docs/design/05-recording-engine.md「事件与来源」）。"""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from tests.engine_fixture import engine_factory_at
from tests.footprints.recording import artifact_rows, export_completed, read_export, reproject
from tests.life.conftest import conclude_request, do_at
from tests.life.environment_inputs import catalog_rows, meal_environment, write_catalog_rows
from tests.life.environment_runtime import act, one_hour, until_ended
from worldmock.errors import IntegrityError
from worldmock.evaluation.behavior_metrics import behavior_metrics_report
from worldmock.footprints.validation import validate_export

#: 完成效果证据的形状检查只有这一句英文错误。
COMPLETION_ERROR = "Activity completion effects must use the current location completion contract"


def _hungry_hour(persona, config):
    """一小时的运行，初始能量 1,000。"""
    one_hour(persona, config)
    persona["body"]["initial_energy_kcal"] = 1000.0


def _finish(engine, label, unit):
    """在家点名一件事，推进到它做完再收尾。"""
    _, started = act(engine, label, do_at("家", unit))
    assert started.status == "in_progress", started.error_code
    until_ended(engine, started.activity_id)
    name = engine.context.state.activities[started.activity_id].name
    _, concluded = act(engine, f"{label}-conclude", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code


@pytest.fixture(scope="module")
def meal_export(tmp_path_factory, frozen_inputs):
    """整份导出只跑一次：meal_environment 里「测试闲聊」再写上两项缓解；做完供一餐的「测试休息」和只缓解的「测试闲聊」，
    跑到截止、导出并认证。每条用例复制一份再改写。"""
    root = tmp_path_factory.mktemp("meal-day")
    environment = meal_environment(root / "input")
    rows = catalog_rows(environment, "content-catalog.jsonl")
    next(row for row in rows if row["content_id"] == "c-chat")["completion_effects"] = {
        "relaxation_relief": "low", "social_relief": "medium"}
    write_catalog_rows(environment, "content-catalog.jsonl", rows)
    with engine_factory_at(root, frozen_inputs) as create:
        engine = create(environment, mutate=_hungry_hour)
        _finish(engine, "rest", "测试休息")
        _finish(engine, "chat", "测试闲聊")
        manifest, events, _ = export_completed(engine, root / "export")
    return {"directory": root / "export", "manifest": manifest, "events": events}


def completions(events):
    """导出事件里结算了完成效果的 activity_progress，按发生先后。"""
    return [row for row in events if row["event_type"] == "activity_progress"
            and (row["data"].get("content") or {}).get("completion_effects") is not None]


def test_a_run_with_completion_effects_exports_and_certifies(meal_export):
    """导出认证通过，导出的事件里恰有两条结算了完成效果的 activity_progress：先是「测试休息」的一餐，后是「测试闲聊」的
    两项缓解。"""
    meal, relief = completions(meal_export["events"])
    assert meal["data"]["content"]["content_id"] == "c-rest"
    assert meal["data"]["content"]["completion_effects"]["food"]["tier"] == "meal"
    assert relief["data"]["content"]["content_id"] == "c-chat"
    effects = relief["data"]["content"]["completion_effects"]
    assert effects["food"] is None and (effects["relaxation_relief"]["tier"], effects["social_relief"]["tier"]) == (
        "low", "medium")
    validate_export(meal_export["directory"])


def test_a_location_meal_is_not_a_purchase_or_a_food_sample(meal_export):
    """只在家吃过这一餐的运行：purchases.jsonl 没有行，质量报告的食品效率仍报 no_food_receipt，已核验经历里没有「收到」，
    行为指标的食品样本数为零。"""
    directory = meal_export["directory"]
    assert artifact_rows(directory, "purchases") == []
    quality = json.loads((directory / "quality.json").read_bytes())
    assert quality["conversion_efficiency"]["food"]["unavailable_or_incomplete_reason"] == "no_food_receipt"
    assert not [row for row in artifact_rows(directory, "verified_experiences") if row["kind"] == "received"]
    [report] = behavior_metrics_report(artifact_rows(directory, "events")).values()
    diversity = report["reports"]["meal_diversity"]
    assert (diversity["receipt_count"], diversity["distinct_products"]) == (0, 0)


def _drop_member(effects):
    """completion_effects 少一个成员键。"""
    del effects["social_relief"]


def _extra_food_key(effects):
    """food 的成员证据多一个键。"""
    effects["food"]["content_id"] = "c-rest"


def _food_tier_outside(effects):
    """food 的 tier 不在它的档位表内。"""
    effects["food"]["tier"] = "dinner"


def _relief_on_the_meal(effects):
    """缓解成员写成 food 的档位。"""
    effects["relaxation_relief"] = {"tier": "meal", "provided": 0.1, "applied": 0.1}


@pytest.mark.parametrize("tamper", [_drop_member, _extra_food_key, _food_tier_outside, _relief_on_the_meal],
                         ids=["member-key-missing", "member-evidence-extra-key", "tier-outside-the-table",
                              "relief-with-a-food-tier"])
def test_tampered_completion_evidence_is_refused(meal_export, tmp_path, tamper):
    """改写已导出那一餐的完成效果证据后重新投影、签名再认证：报同一句英文错误。"""
    directory = tmp_path / "export"
    shutil.copytree(meal_export["directory"], directory)
    manifest, events = read_export(directory)
    events = copy.deepcopy(events)
    meal = completions(events)[0]
    tamper(meal["data"]["content"]["completion_effects"])
    with pytest.raises(IntegrityError, match=f"^{COMPLETION_ERROR}$"):
        reproject(directory, manifest, events)
        validate_export(directory)
