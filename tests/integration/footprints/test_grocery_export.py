"""买菜做饭的运行：导出并通过认证，导出的事件里有 consume_on_start 与带完成效果的内容证据；改写这两样的导出过不了，
各报对应的英文错误（docs/design/05-recording-engine.md「事件与来源」）。"""

from __future__ import annotations

import copy
import shutil
from datetime import timedelta

import pytest
from pydantic import ValidationError

from tests.engine_fixture import engine_factory_at
from tests.footprints.recording import export_completed, read_export, reproject
from tests.life.conftest import action_identity, conclude_request, do_at
from tests.life.environment_inputs import grocery_environment
from tests.life.environment_runtime import act, until_ended
from worldmock.errors import IntegrityError
from worldmock.footprints.validation import validate_export

#: 完成效果证据的形状检查只有这一句英文错误。
COMPLETION_ERROR = "Activity completion effects must use the current location completion contract"


def _hungry_two_hours(persona, config):
    """两小时的运行，初始能量 1,000。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=2)
    persona["body"]["initial_energy_kcal"] = 1000.0


@pytest.fixture(scope="module")
def grocery_export(tmp_path_factory, frozen_inputs):
    """整份导出只跑一次：家里已有食材（groceries 为 1），点名「做饭」做完再收尾，跑到截止、导出并认证。
    每条用例复制一份再改写。"""
    root = tmp_path_factory.mktemp("grocery-day")
    environment = grocery_environment(root / "input", initial=1)
    with engine_factory_at(root, frozen_inputs) as create:
        engine = create(environment, mutate=_hungry_two_hours)
        _, started = act(engine, "cook", do_at("家", "做饭"))
        assert started.status == "in_progress", started.error_code
        until_ended(engine, started.activity_id)
        name = engine.context.state.activities[started.activity_id].name
        _, concluded = act(engine, "cook-conclude", conclude_request(name), "conclude_activity")
        assert concluded.status == "completed", concluded.error_code
        export_completed(engine, root / "export")
    return root / "export"


def consumed(events):
    """导出事件里的 consume_on_start。"""
    return [row for row in events if row["event_type"] == "environment_event"
            and row["data"].get("type") == "consume_on_start"]


def completed(events):
    """导出事件里结算了完成效果的 activity_progress。"""
    return [row for row in events if row["event_type"] == "activity_progress"
            and (row["data"].get("content") or {}).get("completion_effects") is not None]


def test_a_grocery_run_exports_and_certifies(grocery_export):
    """导出认证通过；事件里恰有一条 consume_on_start，回链这次开始调用的 action_result；恰有一条带完成效果的
    activity_progress，是「做饭」做完的那一条。"""
    validate_export(grocery_export)
    _, events = read_export(grocery_export)
    [consume] = consumed(events)
    [cause] = [row for row in events if row["event_id"] in consume["caused_by"]]
    assert (cause["event_type"], cause["action_id"]) == ("action_result", action_identity("cook"))
    assert consume["data"] == {"kind": "callback_effect", "type": "consume_on_start", "rule_id": None,
                               "state": "groceries", "content_id": "cook", "before": 1, "after": 0}
    [meal] = completed(events)
    assert meal["data"]["content"]["content_id"] == "cook"
    assert meal["data"]["content"]["completion_effects"]["food"]["tier"] == "meal"


def _consume_extra_key(events):
    """consume_on_start 多一个键。"""
    consumed(events)[0]["data"]["goods_id"] = "playing-cards"


def _consume_negative_before(events):
    """consume_on_start 的 before 为负。"""
    consumed(events)[0]["data"]["before"] = -1


def _consume_two_causes(events):
    """consume_on_start 的 caused_by 有两项。"""
    row = consumed(events)[0]
    row["caused_by"] = [*row["caused_by"], events[0]["event_id"]]


@pytest.mark.parametrize("tamper,error", [
    (_consume_extra_key, "Extra inputs are not permitted"),
    (_consume_negative_before, "Input should be greater than or equal to 0"),
    (_consume_two_causes, "Callback effect requires one actual environment trigger"),
], ids=["extra-key", "negative-before", "two-causes"])
def test_tampered_consume_on_start_is_refused(grocery_export, tmp_path, tamper, error):
    """改写已导出的 consume_on_start 后重新投影、签名再认证：报对应的英文错误。"""
    directory = tmp_path / "export"
    shutil.copytree(grocery_export, directory)
    manifest, events = read_export(directory)
    events = copy.deepcopy(events)
    tamper(events)
    with pytest.raises(ValidationError, match=error):
        reproject(directory, manifest, events)
        validate_export(directory)


def _drop_member(effects):
    """completion_effects 少一个成员键。"""
    del effects["relaxation_relief"]


def _extra_food_key(effects):
    """food 的成员证据多一个键。"""
    effects["food"]["content_id"] = "cook"


def _food_tier_outside(effects):
    """food 的 tier 不在它的档位表内。"""
    effects["food"]["tier"] = "dinner"


@pytest.mark.parametrize("tamper", [_drop_member, _extra_food_key, _food_tier_outside],
                         ids=["member-key-missing", "member-evidence-extra-key", "tier-outside-the-table"])
def test_tampered_completion_evidence_of_the_cook_is_refused(grocery_export, tmp_path, tamper):
    """改写「做饭」做完那条的完成效果证据后重新投影、签名再认证：报同一句英文错误。"""
    directory = tmp_path / "export"
    shutil.copytree(grocery_export, directory)
    manifest, events = read_export(directory)
    events = copy.deepcopy(events)
    tamper(completed(events)[0]["data"]["content"]["completion_effects"])
    with pytest.raises(IntegrityError, match=f"^{COMPLETION_ERROR}$"):
        reproject(directory, manifest, events)
        validate_export(directory)
