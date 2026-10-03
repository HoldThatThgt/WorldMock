"""提示侧渲染与字段黑名单（worldmock.life.narration）的合同测试。

覆盖七层 JSON 的唯一规范序列化 render_layer_json（紧凑、键序即构建顺序、中文不转义），以及序列化前的字段黑名单：
内部身份、私有绑定与观察私有证据、数值身体状态、合同全表都不得进入提示（docs/dev/SPEC-persona-prompt.md 第一章）。
"""

from __future__ import annotations

import json

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
_PRIVATE_KEYS = ("interaction_bindings", "task_bindings", "goal_bindings", "activity_bindings", "session_bindings",
                 "message_bindings", "person_bindings", "persona_views", "progress_meta", "progress_event_ids", "presented_message_ids",
                 "continuation", "world_query_bindings", "world_query_page_size", "world_query_channels")


class TestFieldBlacklist:
    """黑名单校验：每个禁键单独命中即抛，嵌套与列表内同样查出。"""

    @pytest.mark.parametrize("forbidden_key", [
        "world_state_version",
        "end_time",
        "affordance_contract",
        "observation_event_id",
        "motivation_hints",
        "profile.motivation",
        "profile.entertainment_preferences",
        "game_catalog",
        "activity_snapshot",
        "activities",
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
        payload = [{"layer": "工具层", "targets": [{"affordance_contract": {"levels": [1, 2]}}]}]
        with pytest.raises(
            ForbiddenObservationFieldError, match=r"observation\[0\]\.targets\[0\]",
        ):
            enforce_field_blacklist(payload)

    @pytest.mark.parametrize("internal_key", [
        "session_id", "activity_id", "task_id", "route_id", "product_id",
        "supply_id", "order_id", "message_id", "contact_id", "price_version",
        "agent_id", "source_event_id", "event_seq", "location_id", "destination_id", "content_id",
    ])
    def test_internal_identity_cannot_hide_inside_a_progress_result(self, internal_key: str) -> None:
        """进度层调用结果的任意嵌套层不能重新泄漏引擎身份或报价版本。"""
        layer = {"layer": "进度层", "session_started_at": "2026-09-18T08:00:00+08:00", "events": [
            {"occurred_at": "2026-09-18T08:30:00+08:00", "type": "tool_call", "tool": "start_activity",
             "arguments": {}, "result": {"details": {internal_key: "private-7"}}}]}
        with pytest.raises(ForbiddenObservationFieldError, match=internal_key):
            render_layer_json(layer)

    @pytest.mark.parametrize("body_key", ["appetite", "sleepiness", "entertainment_need", "social_need",
                                          "mood_valence"])
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
            "lifestyle": [], "contents": [], "goods": [], "query_categories": []}}, {"layer": "人设层", "habits": []}]
        assert render_layer_json(layers) == (
            '[{"layer":"工具层","groups":[],"callable_index":'
            '{"lifestyle":[],"contents":[],"goods":[],"query_categories":[]}},{"layer":"人设层","habits":[]}]')
        assert render_layer_json(layers) == render_layer_json(json.loads(render_layer_json(layers)))

    def test_rejects_blacklist_before_render(self) -> None:
        """黑名单字段在渲染入口即被拦截，不产生文本。"""
        with pytest.raises(ForbiddenObservationFieldError, match="progress_meta"):
            render_layer_json([{"layer": "进度层", "events": []}, {"progress_meta": []}])


class TestHanziCounter:
    """统一汉字计数口径（人物卡片派生字段的 600 汉字上限所用）。"""

    def test_non_hanzi_counts_zero(self) -> None:
        """标点、数字、拉丁字母不计入汉字。"""
        assert count_hanzi("abc 123，。！?") == 0

    def test_hanzi_counted(self) -> None:
        """只计 CJK 基本区汉字。"""
        assert count_hanzi("三个汉字a1，") == 4
        assert PERSONA_CARD_HANZI_BUDGET == 600
