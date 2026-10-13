"""提示侧渲染与字段黑名单（worldmock.life.narration）的合同测试。

覆盖七层 JSON 的唯一规范序列化 render_layer_json（紧凑、键序即构建顺序、中文不转义），以及序列化前的字段黑名单：
内部身份、私有绑定与观察私有证据、数值身体状态、合同全表都不得进入提示（docs/design/06-agent.md 第一章）。
"""

from __future__ import annotations

import json
import re

import pytest


from worldmock.life.narration import (
    FORBIDDEN_OBSERVATION_KEYS,
    PERSONA_CARD_HANZI_BUDGET,
    ForbiddenObservationFieldError,
    count_hanzi,
    enforce_field_blacklist,
    render_layer_json,
)

#: 观察私有证据与私有绑定：只给宿主与读者，绝不渲染进提示。
_PRIVATE_KEYS = ("interaction_bindings", "goal_bindings", "activity_bindings", "session_bindings",
                 "persona_views", "progress_meta", "progress_event_ids",
                 "continuation", "errand_bindings", "errand_item_bindings")


class TestFieldBlacklist:
    """黑名单校验：每个禁键单独命中即抛，嵌套与列表内同样查出。"""

    @pytest.mark.parametrize("forbidden_key", [
        "world_state_version",
        "end_time",
        "observation_event_id",
        "motivation_hints",
        "profile.motivation",
        "profile.entertainment_preferences",
        "activity_snapshot",
        "activities",
        "home_location_id",
        *_PRIVATE_KEYS,
    ])
    def test_each_forbidden_key_alone_raises(self, forbidden_key: str) -> None:
        """任一禁键单独出现在顶层即抛错。"""
        with pytest.raises(ForbiddenObservationFieldError):
            enforce_field_blacklist({forbidden_key: "leak"})

    def test_private_observation_keys_are_registered_as_forbidden(self) -> None:
        """私有绑定、看法偏好原始证据、进度层私有元数据与续接缓冲都在禁键表中。"""
        assert set(_PRIVATE_KEYS) <= FORBIDDEN_OBSERVATION_KEYS

    def test_profile_nested_keys_raise(self) -> None:
        """profile 的 motivation 与 entertainment_preferences 嵌套子键命中。"""
        with pytest.raises(ForbiddenObservationFieldError, match="profile.motivation"):
            enforce_field_blacklist({"profile": {"motivation": "leak"}})
        with pytest.raises(
            ForbiddenObservationFieldError,
            match="profile.entertainment_preferences",
        ):
            enforce_field_blacklist(
                {"profile": {"entertainment_preferences": ["leak"]}})

    def test_deeply_nested_key_inside_list_raises(self) -> None:
        """列表内多层数据包藏的禁键同样被递归查出，路径含索引。"""
        payload = [{"layer": "工具层", "targets": [{"activity_snapshot": {"levels": [1, 2]}}]}]
        with pytest.raises(
            ForbiddenObservationFieldError, match=r"observation\[0\]\.targets\[0\]",
        ):
            enforce_field_blacklist(payload)

    @pytest.mark.parametrize("internal_key", [
        "session_id", "activity_id", "task_id", "app_id", "goods_id", "store_id", "product_id",
        "payment_app_id", "order_id", "push_id", "trip_id", "path",
        "agent_id", "source_event_id", "event_seq", "location_id", "destination_id", "content_id", "errand_id",
    ])
    def test_internal_identity_cannot_hide_inside_a_progress_result(self, internal_key: str) -> None:
        """进度层调用结果的任意嵌套层不能重新泄漏引擎身份或报价版本。"""
        layer = {"layer": "进度层", "session_started_at": "2026-09-18T08:00:00+08:00", "events": [
            {"occurred_at": "2026-09-18T08:30:00+08:00", "type": "tool_call", "tool": "start_main_activity",
             "arguments": {}, "result": {"details": {internal_key: "private-7"}}}]}
        with pytest.raises(ForbiddenObservationFieldError, match=internal_key):
            render_layer_json(layer)

    @pytest.mark.parametrize("body_key", ["appetite", "sleepiness", "relaxation_need", "social_need",
                                          "social_need"])
    def test_numeric_body_state_is_rejected_but_felt_text_passes(self, body_key: str) -> None:
        """体感分级文字可以进入提示，同名的数值身体状态不行。"""
        enforce_field_blacklist({"body": {body_key: "有点饿"}})
        with pytest.raises(ForbiddenObservationFieldError, match="numeric body state"):
            enforce_field_blacklist({"body": {body_key: 0.6}})

    def test_activity_names_in_memory_notes_and_outcomes_pass(self) -> None:
        """七层字段 activity 是可读活动名（记忆层 self_notes、结果 outcome），不是完整活动快照。"""
        enforce_field_blacklist({"layer": "记忆层", "earlier_days": [], "earlier_today": [
            {"recorded_at": "2026-09-18T09:00:00+08:00", "settled": [],
             "self_notes": [{"activity": "写周报（9月18日 08:30 起）", "text": "写完一半。"}]}]})
        with pytest.raises(ForbiddenObservationFieldError, match="activities"):
            enforce_field_blacklist({"layer": "待办层", "day_plan": {"activities": []}})


class TestRenderLayerJson:
    """七层 JSON 的唯一规范序列化。"""

    def test_compact_key_order_preserved_and_unicode_plain(self) -> None:
        """紧凑分隔、中文不转义，键序即构建顺序（不排序）。"""
        layer = {"layer": "环境层", "observed_at": "2026-09-18T08:30:00+08:00",
                 "body": {"mood": "平静", "appetite": "有点饿"}}
        text = render_layer_json(layer)
        assert text == ('{"layer":"环境层","observed_at":"2026-09-18T08:30:00+08:00",'
                        '"body":{"mood":"平静","appetite":"有点饿"}}')
        assert json.loads(text) == layer

    def test_layer_arrays_render_in_the_given_order(self) -> None:
        """user 的层数组按给定层序渲染，同一输入逐字节相同。"""
        layers = [{"layer": "工具层", "targets": [], "callable_index": {
            "goods": [], "navigation_apps": [], "payment_apps": []}},
                  {"layer": "人设层", "habits": []}]
        assert render_layer_json(layers) == (
            '[{"layer":"工具层","groups":[],"callable_index":'
            '{"goods":[],"navigation_apps":[],"payment_apps":[]}},{"layer":"人设层","habits":[]}]')
        assert render_layer_json(layers) == render_layer_json(json.loads(render_layer_json(layers)))

    def test_rejects_blacklist_before_render(self) -> None:
        """黑名单字段在渲染入口即被拦截，不产生文本。"""
        with pytest.raises(ForbiddenObservationFieldError, match="progress_meta"):
            render_layer_json([{"layer": "进度层", "events": []}, {"progress_meta": []}])

    def test_parameter_error_path_is_visible_but_graph_path_is_private(self) -> None:
        """真实错误指出待修正参数；内部地图路径不能借同名字段进入提示。"""
        from worldmock.life.error_contract import ErrorItem, host_report

        item = ErrorItem(("tool_calls", 0, "function", "arguments", "target", 1), "不存在的对象", "NAME_NOT_FOUND")
        report = host_report("NAME_NOT_FOUND", items=(item,)).payload()
        assert json.loads(render_layer_json({"details": report})) == {"details": report}
        for value in (["home", "office"], "private-map-path"):
            report["items"][0]["path"] = value
            with pytest.raises(ForbiddenObservationFieldError, match="path"):
                render_layer_json({"details": report})


class TestHanziCounter:
    """统一汉字计数口径（人物卡片派生字段的 600 汉字上限所用）。"""

    def test_non_hanzi_counts_zero(self) -> None:
        """标点、数字、拉丁字母不计入汉字。"""
        assert count_hanzi("abc 123，。！?") == 0

    def test_hanzi_counted(self) -> None:
        """只计 CJK 基本区汉字。"""
        assert count_hanzi("三个汉字a1，") == 4
        assert PERSONA_CARD_HANZI_BUDGET == 600


#: 人物可见层禁止泄漏的内部身份、私有绑定与身体数值字段。
_FORBIDDEN_KEYS = frozenset({
    'account_id', 'accounts', 'action_id', 'activities',
    'activity_bindings', 'activity_id', 'activity_snapshot',
    'agent_id', 'app_id', 'balance_account_id', 'balance_fen',
    'chronotype_midsleep', 'content_id', 'continuation',
    'destination_id', 'end_time', 'energy_kcal', 'errand_bindings',
    'errand_id', 'errand_item_bindings', 'event_id', 'event_seq',
    'goal_bindings', 'goods_id', 'home_location_id', 'input_path',
    'interaction_bindings', 'location_id', 'motivation_hints',
    'observation_event_id', 'oracle', 'oracle_path', 'order_id',
    'path', 'payment_app_id', 'persona_views',
    'product_id', 'profile.entertainment_preferences', 'profile.motivation',
    'progress_event_ids', 'progress_meta', 'push_id', 'reference_assessment',
    'reference_assessments', 'reference_id', 'resource_changes', 'session_bindings',
    'session_id', 'sleep_deficit_hours', 'sleep_need_hours', 'sleep_pressure',
    'source_event_id', 'store_id', 'task_id', 'trip_id',
    'world_state_version',
})




class TestForbiddenKeys:
    """@brief 私有字段在人物可见层的顶层与嵌套位置均不能泄漏。"""

    def test_private_keys_are_rejected_alone_and_inside_a_layer(self) -> None:
        """@brief 独立列出的私有字段经实际渲染入口逐键拒绝，不从实现常量生成测试输入。"""
        keys = _FORBIDDEN_KEYS
        assert len(keys) >= 55
        assert {"home_location_id", "location_id", "errand_id", "goods_id", "app_id",
                "payment_app_id", "interaction_bindings"} <= keys
        assert keys <= FORBIDDEN_OBSERVATION_KEYS, sorted(keys - FORBIDDEN_OBSERVATION_KEYS)
        for key in sorted(keys):
            with pytest.raises(ForbiddenObservationFieldError, match=re.escape(key)):
                enforce_field_blacklist({key: "leak"})
            with pytest.raises(ForbiddenObservationFieldError, match=re.escape(key)):
                render_layer_json([{"layer": "环境层", "location": {"name": "自己家", key: "leak"}}])

    @pytest.mark.parametrize("removed", ["world_query_bindings", "world_query_page_size"])
    def test_removed_world_query_keys_are_no_longer_forbidden(self, removed: str) -> None:
        """@brief 已删除的查询私有键不再是禁键：经禁键检查与真实渲染入口都照常通过。"""
        assert removed not in FORBIDDEN_OBSERVATION_KEYS
        enforce_field_blacklist({removed: "value"})
        layer = {"layer": "环境层", "location": {"name": "自己家", removed: "value"}}
        assert json.loads(render_layer_json([layer])) == [layer]

    def test_home_identity_cannot_enter_any_layer(self) -> None:
        """@brief 本人住处的地点身份（「家」「住处」按它解析，只留在观察顶层）混进环境层或工具层目标即被拒绝，路径写到
        该键；不含它的同一层照常渲染。"""
        layer = {"layer": "环境层", "location": {"session_kind": "stay", "name": "自己家"}}
        assert json.loads(render_layer_json(layer)) == layer
        with pytest.raises(ForbiddenObservationFieldError, match=r"observation\.home_location_id"):
            render_layer_json({**layer, "home_location_id": "home"})
        target = {"name": "地点：自己家", "kind": "location", "home_location_id": "home"}
        with pytest.raises(ForbiddenObservationFieldError, match=r"observation\[0\]\.targets\[0\]\.home_location_id"):
            enforce_field_blacklist([{"layer": "工具层", "targets": [target]}])
