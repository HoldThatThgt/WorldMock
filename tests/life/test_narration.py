"""观察投影与提示合同（worldmock.life.narration）的合同测试。

覆盖docs/dev/SPEC-life-runtime.md「观察投影与提示合同」：场景渲染内容与 800 汉字上限、驱动摘要无数字
权重、user 40000 / system 3000 字符预算边界、决策自述 200 汉字边界与空串、
黑名单字段递归校验（含 routes 白名单）、facts 确定性渲染与 user 消息
三段组装顺序。
"""

from __future__ import annotations

import pytest

from worldmock.life.narration import (
    DECISION_STATEMENT_HANZI_BUDGET,
    PERSONA_CARD_HANZI_BUDGET,
    SCENE_HANZI_BUDGET,
    SYSTEM_MESSAGE_CHAR_BUDGET,
    USER_MESSAGE_CHAR_BUDGET,
    ForbiddenObservationFieldError,
    DecisionStatementValidationError,
    PromptBudgetExceededError,
    RenderBudgetExceededError,
    compose_user_message,
    count_hanzi,
    enforce_budgets,
    enforce_field_blacklist,
    render_drive_summary,
    render_facts,
    render_scene,
    validate_decision_statement,
)


@pytest.fixture
def scene_observation() -> dict:
    """内容齐全的场景观察字段（键名与 narration 模块 docstring 约定一致）。"""
    return {
        "current_time": "2026-09-18T08:30:00+00:00",
        "location": "公司工位",
        "people_here": ["李雷", "韩梅梅"],
        "felt": {"hunger": "有点饿", "energy": "精力尚可"},
        "current_activity": {"title": "写周报", "progress": "已写将近一半"},
        "paused_activities": [
            {"title": "整理会议纪要", "progress": "已做二十分钟"},
        ],
        "day_plan": {
            "summary": "上午写代码、下午开评审会、晚上健身",
            "current_segment": "下午开评审会",
        },
        "inbox_head": ["张三：周报请在今天下班前提交", "人事部：下午茶活动报名"],
        "inbox_has_older_unread": True,
        "todos_summary": "还有两件待办：提交周报和预约体检",
        "reachable_places": [
            {"name": "公司食堂", "open": True},
            {"name": "楼下便利店", "open": False},
        ],
        "inventory": ["手机", "工卡", "钱包"],
        "recent_action_results": ["你完成了通勤，准时到达公司。"],
    }


class TestRenderScene:
    """场景段落渲染：关键内容存在性与合同顺序。"""

    def test_scene_contains_key_content(self, scene_observation: dict) -> None:
        """时间星期、地点、在场、体感、活动进度、计划、收件箱头等逐项在场。"""
        scene = render_scene(scene_observation)
        assert scene.startswith("现在是")
        assert "2026 年 9 月 18 日" in scene
        assert "星期五" in scene
        assert "08:30" in scene
        assert "公司工位" in scene
        assert "李雷" in scene and "韩梅梅" in scene
        assert "有点饿" in scene and "精力尚可" in scene
        assert "《写周报》" in scene and "已写将近一半" in scene
        assert "之前做到一半的《整理会议纪要》（已做二十分钟）还没收尾。" in scene
        assert "上午写代码、下午开评审会、晚上健身" in scene
        assert "「下午开评审会」这一段" in scene
        assert "收件箱最新消息" in scene
        assert "张三：周报请在今天下班前提交" in scene
        assert "更早还有未读消息。" in scene
        assert "还有两件待办：提交周报和预约体检" in scene
        assert "公司食堂" in scene and "营业中" in scene
        assert "楼下便利店" in scene and "未营业" in scene
        assert "手机" in scene and "工卡" in scene
        assert "你完成了通勤，准时到达公司" in scene
        assert scene.endswith("。")
        assert count_hanzi(scene) <= SCENE_HANZI_BUDGET

    def test_scene_skips_missing_optional_fields(self) -> None:
        """缺失的可选字段跳过对应句子，渲染器不虚构、不报错。"""
        scene = render_scene({
            "current_time": "2026-09-18T08:30:00+00:00",
            "location": "公司工位",
        })
        assert "公司工位" in scene
        assert "收件箱" not in scene
        assert "还没收尾" not in scene
        assert "更早还有未读消息" not in scene

    def test_scene_rejects_non_contract_language(self, scene_observation: dict) -> None:
        """合同只支持 zh-CN，其他语言值必须如实报错而非降级渲染。"""
        with pytest.raises(ValueError, match="scene language must be zh-CN"):
            render_scene(scene_observation, language="en-US")

    def test_scene_reports_oversized_input_without_truncation(self) -> None:
        """超 800 汉字是输入侧问题：抛错并在消息中如实报告实际字数。"""
        bloated = {
            "current_time": "2026-09-18T08:30:00+00:00",
            "location": "公司工位",
            "inventory": [f"很贵的重要物品编号甲乙丙丁{i}" for i in range(80)],
        }
        with pytest.raises(RenderBudgetExceededError) as excinfo:
            render_scene(bloated)
        message = str(excinfo.value)
        assert "exceeds 800 hanzi budget" in message
        reported = int(message.split("budget: ")[1].split(" hanzi")[0])
        assert reported > SCENE_HANZI_BUDGET

    def test_scene_exactly_at_hanzi_budget_passes(self) -> None:
        """恰好八百汉字的场景原样保留，不通过裁剪伪装通过。"""
        message = "事" * SCENE_HANZI_BUDGET
        assert render_scene({"recent_action_results": [message]}) == message + "。"

    def test_reachable_places_do_not_render_route_minutes(self) -> None:
        """场景保留地点名和营业状态，不重复事实清单中的路线分钟数。"""
        scene = render_scene({"reachable_places": [{"name": "书店", "open": True, "minutes": 13}]})
        assert scene == "可达地点有书店（营业中）。"

    def test_older_unread_hint_only_follows_the_explicit_flag(self) -> None:
        """当前头部存在消息不代表更早存在未读消息。"""
        scene = render_scene({"inbox_head": ["今晚见面"], "inbox_has_older_unread": False})
        assert scene == "收件箱最新消息：今晚见面。"


class TestDriveSummary:
    """驱动摘要：一句中文、无数字权重。"""

    def test_composes_one_sentence(self) -> None:
        """体感、工作缺口与待办拼成一句中文，与合同示例同构。"""
        text = render_drive_summary(
            {"hunger": "有点饿"},
            "今天的工作还差两小时",
            "晚上想放松一下",
        )
        assert text == "你有点饿，今天的工作还差两小时，晚上想放松一下。"
        assert not any(ch.isdigit() for ch in text)

    def test_joins_multiple_bands_without_digits(self) -> None:
        """多条体感分级以顿号连接，输出仍无阿拉伯数字。"""
        text = render_drive_summary(
            {"hunger": "有点饿", "fatigue": "略感疲惫"},
            "今天的工作还差两小时",
            "",
        )
        assert text == "你有点饿、略感疲惫，今天的工作还差两小时。"
        assert not any(ch.isdigit() for ch in text)

    def test_rejects_digit_weights(self) -> None:
        """含阿拉伯数字权重的输入是合同违反：如实抛错，不改写。"""
        with pytest.raises(ValueError, match="must not contain digit weights"):
            render_drive_summary({"hunger": "有点饿"}, "还差 2 小时", "")

    def test_all_empty_inputs_return_empty_string(self) -> None:
        """三段皆空返回空串，不虚构句子。"""
        assert render_drive_summary({}, "", "") == ""


class TestPromptBudgets:
    """字符预算执行器：恰好等于通过，超一即运行故障。"""

    def test_exactly_at_budgets_passes(self) -> None:
        """user=40000、system=3000 恰好等于预算：通过且不返回任何值。"""
        assert enforce_budgets(USER_MESSAGE_CHAR_BUDGET, SYSTEM_MESSAGE_CHAR_BUDGET) is None

    def test_user_over_by_one_raises(self) -> None:
        """user 超 1 字符即抛 PromptBudgetExceededError 并指明侧别。"""
        with pytest.raises(PromptBudgetExceededError, match="user message exceeds 40000"):
            enforce_budgets(USER_MESSAGE_CHAR_BUDGET + 1, SYSTEM_MESSAGE_CHAR_BUDGET)

    def test_system_over_by_one_raises(self) -> None:
        """system 超 1 字符即抛 PromptBudgetExceededError 并指明侧别。"""
        with pytest.raises(PromptBudgetExceededError, match="system message exceeds 3000"):
            enforce_budgets(USER_MESSAGE_CHAR_BUDGET, SYSTEM_MESSAGE_CHAR_BUDGET + 1)


class TestDecisionStatement:
    """决策自述协议：200 汉字边界与空串。"""

    def test_exactly_200_hanzi_passes(self) -> None:
        """恰好 200 汉字通过，且判定函数原样返回文本。"""
        text = "我" * DECISION_STATEMENT_HANZI_BUDGET
        assert validate_decision_statement(text) == text

    def test_201_hanzi_raises(self) -> None:
        """201 汉字是协议错误。"""
        with pytest.raises(DecisionStatementValidationError, match="exceeds 200 hanzi"):
            validate_decision_statement("我" * (DECISION_STATEMENT_HANZI_BUDGET + 1))

    def test_empty_string_raises(self) -> None:
        """空串是协议错误。"""
        with pytest.raises(DecisionStatementValidationError, match="decision_statement is empty"):
            validate_decision_statement("")

    def test_whitespace_only_raises(self) -> None:
        """纯空白视为为空，同样是协议错误。"""
        with pytest.raises(DecisionStatementValidationError, match="decision_statement is empty"):
            validate_decision_statement("   \n  ")

    def test_short_first_person_decision_statement_passes(self) -> None:
        """常规一到三句第一人称决策自述通过。"""
        text = "我想先喝杯水，再继续把周报写完。"
        assert validate_decision_statement(text) == text


class TestFieldBlacklist:
    """黑名单校验：每个禁键单独命中即抛，嵌套与 routes 白名单同样查出。"""

    @pytest.mark.parametrize("forbidden_key", [
        "world_state_version",
        "end_time",
        "affordance_contract",
        "energy_feasibility",
        "observation_event_id",
        "motivation_hints",
        "profile.motivation",
        "profile.entertainment_preferences",
        "game_catalog",
        "activity_snapshot",
        "activities",
    ])
    def test_each_forbidden_key_alone_raises(self, forbidden_key: str) -> None:
        """任一禁键单独出现在顶层即抛错。"""
        with pytest.raises(ForbiddenObservationFieldError):
            enforce_field_blacklist({forbidden_key: "leak"})

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
        payload = {"a": {"b": [{"energy_feasibility": {"levels": [1, 2]}}]}}
        with pytest.raises(
            ForbiddenObservationFieldError, match=r"observation\.a\.b\[0\]",
        ):
            enforce_field_blacklist(payload)

    @pytest.mark.parametrize("geometry_key", [
        "coordinates",
        "geometry",
        "waypoints",
    ])
    def test_route_geometry_fields_raise(self, geometry_key: str) -> None:
        """routes 条目混入坐标/几何字段即抛错（只允许合同五键）。"""
        route = {
            "destination": "office",
            "mode": "walk",
            "minutes": 12,
            "fare": 0,
            "feasible": True,
            geometry_key: [[0, 0], [1, 1]],
        }
        with pytest.raises(ForbiddenObservationFieldError, match="non-contract fields"):
            enforce_field_blacklist({"routes": [route]})

    def test_contract_fact_fields_pass(self) -> None:
        """docs/dev/SPEC-life-runtime.md「观察投影三部分」facts 合同字段与 routes 五键白名单整体通过。"""
        facts = {
            "current_time": "2026-09-18T08:30:00+00:00",
            "location_id": "office_desk",
            "people_here": ["李雷"],
            "inbox": [{"sender": "张三", "subject": "周报"}],
            "tasks": [{
                "task_id": "t-1", "title": "写周报", "due": "2026-09-18",
                "remaining_contract_hours": 2.0, "new_material_span": [120, 340],
            }],
            "routes": [{
                "destination": "canteen", "mode": "walk",
                "minutes": 5, "fare": 0, "feasible": True,
            }],
            "products": [{
                "product_id": "p-1", "name": "牛肉面",
                "price": 2800, "calories": 650, "eating_minutes": 20,
            }],
            "inventory": ["手机"],
            "day_plan": {"summary": "上午写代码", "current_segment": "上午写代码"},
            "felt": {"hunger": "有点饿"},
            "drive_summary": "你有点饿，今天的工作还差两小时。",
            "recent_action_results": [{"status": "accepted", "message": "开始写周报"}],
        }
        enforce_field_blacklist(facts)


class TestRenderFacts:
    """facts 确定性渲染。"""

    def test_deterministic_and_unicode_plain(self) -> None:
        """同一字典（任意插入顺序）渲染同一文本，中文不转义。"""
        first = render_facts({"location_id": "office", "people_here": ["李雷"]})
        second = render_facts({"people_here": ["李雷"], "location_id": "office"})
        assert first == second
        assert first == '{"location_id":"office","people_here":["李雷"]}'
        assert "李雷" in first

    def test_rejects_blacklist_before_render(self) -> None:
        """黑名单字段在渲染入口即被拦截。"""
        with pytest.raises(ForbiddenObservationFieldError):
            render_facts({"game_catalog": [{"game_id": "g-1"}]})


class TestComposeUserMessage:
    """user 消息三段组装：顺序、分隔与预算接线。"""

    def test_parts_in_contract_order(self) -> None:
        """persona_card → scene → facts，空行分隔，缺一不可乱序。"""
        persona = "你是张三，二十八岁，产品经理。"
        scene = "现在是二〇二六年九月十八日星期五 08:30。"
        facts = '{"location_id":"office"}'
        message = compose_user_message(persona, scene, facts)
        parts = message.split("\n\n")
        assert parts == [persona, scene, facts]
        assert message.index(persona) < message.index(scene) < message.index(facts)

    def test_persona_over_600_hanzi_raises(self) -> None:
        """persona_card 超 600 汉字如实抛错，不截断。"""
        with pytest.raises(RenderBudgetExceededError, match="persona card exceeds"):
            compose_user_message("我" * (PERSONA_CARD_HANZI_BUDGET + 1), "场景。", "{}")

    def test_persona_exactly_600_hanzi_passes(self) -> None:
        """persona_card 恰好 600 汉字通过。"""
        persona = "我" * PERSONA_CARD_HANZI_BUDGET
        message = compose_user_message(persona, "场景。", "{}")
        assert persona in message

    def test_overall_user_budget_enforced(self) -> None:
        """组装结果超 40000 字符触发运行故障（facts 为超长 JSON 文本）。"""
        with pytest.raises(PromptBudgetExceededError, match="user message exceeds"):
            compose_user_message("卡片", "场景。", "a" * (USER_MESSAGE_CHAR_BUDGET + 1))


class TestHanziCounter:
    """统一汉字计数口径。"""

    def test_non_hanzi_counts_zero(self) -> None:
        """标点、数字、拉丁字母不计入汉字。"""
        assert count_hanzi("abc 123，。！?") == 0

    def test_hanzi_counted(self) -> None:
        """只计 CJK 基本区汉字。"""
        assert count_hanzi("三个汉字a1，") == 4
