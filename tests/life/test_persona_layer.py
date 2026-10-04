"""人设层（docs/dev/SPEC-persona-prompt.md 第一章「人设层」，裁决 #33、#35、#36、#38）。

结构化卡片在新建运行时存进 profile["persona_card_struct"]（不含当下处境）并在恢复时校验；宿主据此渲染身份、作息（刻钟
写对）、习惯、口味与应对，卡片关系与场景联系人按名称精确合并，只来自联系人的项不带亲密度与渠道；看法与偏好只来自成功的
conclude_activity 写下的带 subject 的记忆，同一对象后写的替换先写的，事件里两条都保留；600 汉字上限只约束卡片派生的
字段；只读问答用最近一次生活决策保存的快照；购买本身不写偏好，巩固摘要不进提示。
"""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402
from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig  # noqa: E402

from custom.agents.life_agent import LifeAgent, _conclusion_memories, _validate_prepared_action  # noqa: E402
from custom.agents.life_memory import WorldMockMemoryRuntime  # noqa: E402
from custom.agents.life_persona import (PROMPT_SNAPSHOT_FILE, card_struct, known_persons, persona_layer,  # noqa: E402
                                        readonly_layers, write_prompt_snapshot)
from custom.agents.life_prompt import empty_memory_layer, readonly_messages  # noqa: E402
from tests.integration.test_life_agent_protocol import _context, _observations  # noqa: E402
from tests.life.conftest import (activity_name, advance_next, advance_until_ready, stage_day_plan, start_activity,
                                 conclude_request)
from tests.life.test_memory_layer import (_act, _jump, _memo, _next_morning, _observe,  # noqa: E402
                                          _remote_order, _view, _walk)
from tests.life.test_dayrun_restaurants import _keep_waiting, _stage  # noqa: E402
from tests.profile.conftest import make_card, make_window  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.life.activity_control import slot_activities  # noqa: E402
from worldmock.life.control_items import control_items  # noqa: E402
from worldmock.life.models import ConclusionMemory, ConclusionView  # noqa: E402
from worldmock.life.narration import RenderBudgetExceededError  # noqa: E402
from worldmock.life.prompt_schemas import validate_layer  # noqa: E402
from worldmock.life.readable import local_date  # noqa: E402
from worldmock.profile.models import Tastes, TimeAnchors, persona_card_struct  # noqa: E402
from worldmock.profile.textgen import hour_text, render_card  # noqa: E402

_CONTACTS = [{"contact_id": "contact-zhou", "salutation": "老周", "relation": "朋友", "closeness": 20,
              "channels": ["wechat"], "reply_policy": "none"},
             {"contact_id": "contact-shen", "salutation": "沈昊", "relation": "老友", "closeness": 25,
              "channels": ["phone"], "reply_policy": "model"}]
_NO_VIEWS = {"relationships": [], "preferences": []}
#: 人设层 rhythm 的九键与键序（docs/dev/SPEC-errands-workday.md 4.1）。
_RHYTHM_KEYS = ("wake", "leave_home", "lunch", "leave_work", "arrive_home", "dinner", "bedtime", "sleep_midpoint",
                "sleep_need")
#: 人设层的八个顶层键（键序即渲染顺序）。
_PERSONA_KEYS = ["layer", "identity", "rhythm", "habits", "tastes", "coping", "relationships", "preferences"]


def _profile(card=None) -> dict:
    """带两位场景联系人的人物档案；有卡片时另存结构化字段。"""
    profile = {"id": 1, "name": "陈曦", "contacts": copy.deepcopy(_CONTACTS)}
    if card is not None:
        profile["persona_card_struct"] = persona_card_struct(card)
    return profile


def _work_card(leave_work, arrive_home):
    """工厂卡片换上给定的下班、到家锚点（经 TimeAnchors 校验重建，其余五个锚点不变）。"""
    anchors = make_card().time_anchors.model_dump()
    return make_card(time_anchors=TimeAnchors.model_validate(
        {**anchors, "leave_work": leave_work, "arrive_home": arrive_home}))


def test_card_path_renders_card_fields_and_merges_relationships_by_exact_name() -> None:
    """卡片路径：身份、作息、习惯、口味与应对取自卡片；卡片「老周」与同名联系人合并为一项（带卡片的亲密度与渠道），
    只来自联系人的「沈昊」亲密度为 null、渠道为空；看法写到对应的人；不含当下处境；过人设层 Schema。"""
    card = make_card(chronotype_midsleep=3.25)
    views = {"relationships": [{"person": "老周", "view": "最近很忙"}, {"person": "沈昊", "view": "靠谱"}],
             "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": "2026-01-05"}]}
    layer = persona_layer(_profile(card), "陈曦", views)
    validate_layer(layer)
    assert layer["identity"] == {"name": "陈曦", "age": 32, "occupation": "产品经理",
                                 "living_arrangement": "与伴侣合租两居室"}
    assert layer["rhythm"] == {"wake": "7点到7点半", "leave_home": "8点到8点半", "lunch": "12点到13点",
                               "leave_work": None, "arrive_home": None,
                               "dinner": "18点半到19点半", "bedtime": "23点到0点", "sleep_midpoint": "3点一刻",
                               "sleep_need": "7.5 小时"}
    assert list(layer["rhythm"]) == list(_RHYTHM_KEYS)
    assert layer["habits"] == [{"trigger": "下班到家", "response": "公园慢跑半小时", "time_window": "19点到20点"},
                               {"trigger": "睡前", "response": "读几页小说", "time_window": "22点半到23点"}]
    assert layer["tastes"] == {"content_genres": ["悬疑", "纪录片"], "food_preferences": ["川菜", "面食"],
                               "media": ["长视频", "短视频"]}
    assert layer["coping"] == "拼高达模型"
    assert layer["relationships"] == [
        {"person": "母亲", "role": "家人", "closeness": "很亲近", "channels": ["电话", "微信"], "view": None},
        {"person": "老周", "role": "老友", "closeness": "较亲近", "channels": ["微信", "见面"], "view": "最近很忙"},
        {"person": "沈昊", "role": "老友", "closeness": None, "channels": [], "view": "靠谱"}]
    assert layer["preferences"] == views["preferences"]
    text = json.dumps(layer, ensure_ascii=False)
    assert "新版上线收尾" not in text and "父母的体检结果" not in text and "situation" not in text


def test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent() -> None:
    """人设层 rhythm（docs/dev/SPEC-errands-workday.md 4.1）：九键依次为 wake, leave_home, lunch, leave_work, arrive_home,
    dinner, bedtime, sleep_midpoint, sleep_need；卡片写明下班与到家时两键是可读短语，没有固定时刻（null）时为 null，只写明
    到家时下班仍为 null；三种卡片都过人设层 Schema，其余七键不随两个新键变化。Schema 要求九键齐全，只有两个新键可为
    null，写出的短语不能为空。"""
    plain = persona_layer(_profile(make_card()), "陈曦", _NO_VIEWS)["rhythm"]
    cases = {(make_window(18.0, 18.5), make_window(19.0, 19.5)): ("18点到18点半", "19点到19点半"),
             (None, make_window(19.0, 19.5)): (None, "19点到19点半"), (None, None): (None, None)}
    for (leave_work, arrive_home), shown in cases.items():
        layer = persona_layer(_profile(_work_card(leave_work, arrive_home)), "陈曦", _NO_VIEWS)
        validate_layer(layer)
        rhythm = layer["rhythm"]
        assert list(rhythm) == list(_RHYTHM_KEYS)
        assert (rhythm["leave_work"], rhythm["arrive_home"]) == shown
        assert {**rhythm, "leave_work": None, "arrive_home": None} == plain
    layer = persona_layer(_profile(make_card()), "陈曦", _NO_VIEWS)
    broken = [{key: value for key, value in plain.items() if key != "leave_work"}, {**plain, "wake": None},
              {**plain, "arrive_home": ""}]
    for rhythm in broken:
        with pytest.raises(ValueError):
            validate_layer({**layer, "rhythm": rhythm})


@pytest.mark.parametrize("missing", _RHYTHM_KEYS)
def test_rhythm_schema_requires_each_of_the_nine_keys(missing: str) -> None:
    """人设层 rhythm 非空时九键齐全（docs/dev/SPEC-errands-workday.md 4.1；接口合同「七层字段」）：下班与到家的值可以
    为 null，键不能省。由写明下班与到家的卡片渲染出的人设层通过 Schema、九键俱在；从 rhythm 里去掉其中任意一个键，
    就在 rhythm 处不合 Schema。"""
    card = _work_card(make_window(18.0, 18.5), make_window(19.0, 19.5))
    layer = persona_layer(_profile(card), "陈曦", _NO_VIEWS)
    validate_layer(layer)
    assert list(layer["rhythm"]) == list(_RHYTHM_KEYS)
    rhythm = {key: value for key, value in layer["rhythm"].items() if key != missing}
    with pytest.raises(ValueError, match=r"Prompt layer 人设层 violates its schema at \['rhythm'\]"):
        validate_layer({**layer, "rhythm": rhythm})


def test_a_declared_work_schedule_is_not_rendered_in_the_persona_layer() -> None:
    """宿主不渲染人物档案里的工作安排（docs/dev/SPEC-errands-workday.md 4.4）：profile 带场景人物声明的 work_schedule 与
    下班、到家窗口时，人设层仍只有八个键、过 Schema，且与不带这些键的同一档案逐值相同——作息只取卡片（场景窗口经作息覆盖
    落进卡片后才出现在人设层），序列化后既没有 work_schedule，也没有工作地点的身份或场景窗口的写法。"""
    shift = {"starts": 540, "ends": 1080, "breaks": [[720, 810]]}
    schedule = {"workplace_location_id": "office-zhangjiang",
                "weekly": {str(day): shift if day < 5 else None for day in range(7)},
                "exceptions": {"2026-02-14": shift, "2026-02-17": None}}
    for card in (make_card(), None):
        plain = _profile(card)
        declared = {**plain, "work_schedule": schedule, "leave_work_window": [18.0, 18.5],
                    "arrive_home_window": [19.5, 20.0]}
        layer = persona_layer(declared, "陈曦", _NO_VIEWS)
        validate_layer(layer)
        assert list(layer) == _PERSONA_KEYS
        assert layer == persona_layer(plain, "陈曦", _NO_VIEWS)
        text = json.dumps(layer, ensure_ascii=False)
        assert all(term not in text for term in ("work_schedule", "office-zhangjiang", "18点到18点半", "19点半到20点"))


def test_quarter_hour_times_are_written_as_quarters() -> None:
    """刻钟写对：睡眠中点 3.25 与 3.75 写「3点一刻」「3点三刻」（旧写法只认半点，会写成「3点」）；render_card 不动，
    卡片文本仍是旧写法「3点」（刻钟写法只属于人设层）。"""
    assert [hour_text(value) for value in (7.0, 7.25, 7.5, 7.75)] == ["7点", "7点一刻", "7点半", "7点三刻"]
    card = make_card(chronotype_midsleep=3.75)
    assert persona_layer(_profile(card), "陈曦", _NO_VIEWS)["rhythm"]["sleep_midpoint"] == "3点三刻"
    assert "睡眠中点约3点，" in render_card(card)


def test_no_card_person_keeps_the_name_only_path_and_view_only_people_get_an_item() -> None:
    """没有结构化卡片：只填姓名，作息、口味、应对为 null，习惯为空；关系按场景联系人逐项（亲密度 null、渠道为空）；
    看法对象不在联系人中的已知人物另占一项，角色、亲密度为 null、渠道为空（不填编造的值），过人设层 Schema。"""
    views = {"relationships": [{"person": "李凡", "view": "新同事"}], "preferences": []}
    layer = persona_layer(_profile(), "陈曦", views)
    validate_layer(layer)
    assert layer == {"layer": "人设层",
                     "identity": {"name": "陈曦", "age": None, "occupation": None, "living_arrangement": None},
                     "rhythm": None, "habits": [], "tastes": None, "coping": None,
                     "relationships": [
                         {"person": "老周", "role": "朋友", "closeness": None, "channels": [], "view": None},
                         {"person": "沈昊", "role": "老友", "closeness": None, "channels": [], "view": None},
                         {"person": "李凡", "role": None, "closeness": None, "channels": [], "view": "新同事"}],
                     "preferences": []}
    assert card_struct(_profile()) is None


def test_six_hundred_hanzi_cap_checks_only_card_derived_fields() -> None:
    """600 汉字上限只约束卡片派生的字段：卡片口味超长时如实报错；人物写下的长看法与联系人不计入。"""
    long_taste = make_card(tastes=Tastes(content_genres=("悬" * 600,), food_preferences=(), media=()))
    with pytest.raises(RenderBudgetExceededError, match="exceed budget"):
        persona_layer(_profile(long_taste), "陈曦", _NO_VIEWS)
    views = {"relationships": [{"person": "沈昊", "view": "靠" * 700}],
             "preferences": [{"subject": "午饭", "preference": "面" * 700, "noted_on": "2026-01-05"}]}
    layer = persona_layer(_profile(make_card()), "陈曦", views)
    assert layer["relationships"][-1]["view"] == "靠" * 700


def test_persona_card_struct_is_saved_on_new_runs_and_validated_on_restore(tmp_path) -> None:
    """新建运行：卡片渲染文本与结构化字段（去掉当下处境）都存进 profile，两者取经场景作息覆盖后的卡片
    （docs/dev/SPEC-dayrun-fidelity.md 2.1）；恢复时结构化字段按 PersonaCardStruct 校验，带当下处境或格点不对的字段
    阻止分支发布；没有卡片的人物不带该键。"""
    from tests.integration.test_host_branch_coverage import (ROOT, SCENARIO, ResumeRequest, _agent_workspace,
                                                             _parent_store, _prepare_resume)
    from worldmock.life.scenario import load_scenario
    from worldmock.profile.injection import apply_scenario_rhythm
    from worldmock.runtime import _agent_specs

    scenario, _ = load_scenario(SCENARIO)
    card = make_card()
    overridden = apply_scenario_rhythm(card, scenario.agents[0]).card
    profile = _agent_specs(scenario, persona_compiler=lambda agent: card)[0]["profile"]
    assert profile["persona_card"] == render_card(overridden)
    assert profile["persona_card_struct"] == persona_card_struct(overridden) and "situation" not in profile[
        "persona_card_struct"]
    assert "persona_card_struct" not in _agent_specs(scenario, persona_compiler=lambda agent: None)[0]["profile"]
    broken = {**persona_card_struct(card), "situation": {"busy_this_week": "赶工", "cares_about": "体检"}}
    agent = {"schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
             "profile": {"id": 1, "same_second_continuation": True, "persona_card_struct": broken}}
    parent, checkpoint = _parent_store(tmp_path, workspace_builder=lambda path: _agent_workspace(
        path, agent_document=agent))
    try:
        with pytest.raises(IntegrityError, match="persona card structure is invalid"):
            _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "parent", checkpoint.checkpoint_id,
                                          tmp_path / "child"))
    finally:
        parent.close()


def test_structured_card_without_work_anchor_keys_is_rejected_on_restore(tmp_path) -> None:
    """卡片结构 3（docs/dev/SPEC-errands-workday.md 4.1）：新建运行冻结的结构化卡片带下班、到家两个必填键（这里写明了窗口），
    在恢复路径照常恢复；同一张卡片去掉这两个键（旧结构的卡片）被 PersonaCardStruct 校验拒绝，阻止分支发布、不建子运行
    目录（没有带版本的卡片读者，也不补 null）。"""
    from tests.integration.test_host_branch_coverage import (ROOT, SCENARIO, ResumeRequest, _agent_workspace,
                                                             _parent_store, _prepare_resume)
    from worldmock.life.scenario import load_scenario
    from worldmock.profile.injection import apply_scenario_rhythm

    scenario, _ = load_scenario(SCENARIO)
    card = _work_card(make_window(18.0, 18.5), make_window(19.0, 19.5))
    frozen = persona_card_struct(apply_scenario_rhythm(card, scenario.agents[0]).card)
    assert frozen["time_anchors"]["arrive_home"] == {"start_hour": 19.0, "end_hour": 19.5}
    anchors = {key: value for key, value in frozen["time_anchors"].items() if key not in ("leave_work", "arrive_home")}
    for name, struct in (("frozen", frozen), ("old", {**frozen, "time_anchors": anchors})):
        agent = {"schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
                 "profile": {"id": 1, "same_second_continuation": True, "persona_card_struct": struct}}
        root = tmp_path / name
        root.mkdir()
        parent, checkpoint = _parent_store(root, workspace_builder=lambda path, document=agent: _agent_workspace(
            path, agent_document=document))
        try:
            request = ResumeRequest(ROOT, SCENARIO, root / "parent", checkpoint.checkpoint_id, root / "child")
            if name == "frozen":
                assert _prepare_resume(request).workspace_mode == "restored"
            else:
                with pytest.raises(IntegrityError, match="persona card structure is invalid"):
                    _prepare_resume(request)
                assert not (root / "child").exists()
        finally:
            parent.close()


def test_subject_is_required_for_relationship_and_preference_and_stripped_before_memory() -> None:
    """看法与偏好写在 views，对象 about 必填（请求里叫 about、存储里叫 subject，指同一件事）；普通记忆没有对象字段，类型也不能
    是 relationship 或 preference；relationship 的对象须是已知人物（卡片关系名、联系人称谓或本轮列出的同世界人物）；宿主把
    about 转为 subject、写入主观记忆前去掉（docs/dev/SPEC-dayrun-fidelity.md 第六章）。"""
    view = _view("preference", "爱吃面", "午饭")
    with pytest.raises(ValueError, match="about"):
        ConclusionView.model_validate({key: value for key, value in view.items() if key != "about"})
    with pytest.raises(ValueError, match="about"):
        ConclusionView.model_validate({**view, "about": " "})
    with pytest.raises(ValueError, match="subject"):
        ConclusionMemory.model_validate({**_memo("goal", "明天早起"), "subject": "早起"})
    with pytest.raises(ValueError, match="type"):
        ConclusionMemory.model_validate(_memo("preference", "爱吃面"))
    known = known_persons(_profile(make_card()), ["李凡"])
    assert known == {"母亲", "老周", "沈昊", "李凡"}
    names = {"activity_names": ("等待",), "known_persons": known, "errand_bindings": (), "errand_item_bindings": ()}
    request = conclude_request("等待", summary="收尾", views=[[_view("relationship", "新认识的", "王五"), view]])
    assert _validate_prepared_action("conclude_activity", request, names) == (
        "SUBJECT_NOT_KNOWN: The relationship subject 王五 is not a known person.")
    fine = conclude_request("等待", summary="收尾", views=[[_view("relationship", "靠谱", "沈昊")]])
    assert _validate_prepared_action("conclude_activity", fine, names) == ""
    assert control_items("conclude_activity", fine)[0].memories == ({
        "type": "relationship", "text": "靠谱", "importance": 0.5, "keywords": ["靠谱"], "subject": "沈昊"},)
    written = _conclusion_memories("conclude_activity", fine.model_dump(mode="json"))
    assert written == [{"type": "relationship", "text": "靠谱", "importance": 0.5, "keywords": ["靠谱"],
                        "source": "activity_conclusion", "epistemic_status": "subjective"}]


def test_later_view_replaces_earlier_while_events_keep_both(engine_factory) -> None:
    """同一对象后写的看法替换先写的，事件里两条都保留；preference 按写下的本地日期记 noted_on；被拒请求写下的看法不算；
    这两类记忆不在进度层参数与记忆层中。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    for index, (view, taste) in enumerate((("靠谱", "爱吃面"), ("最近很忙", None))):
        _, waited = _act(engine, f"v-wait-{index}", "start_activity", start_activity("等待", "自己"))
        assert advance_until_ready(engine)
        views = [_view("relationship", view, friend)]
        views += [_view("preference", taste, "午饭")] if taste else []
        _act(engine, f"v-conclude-{index}", "conclude_activity", conclude_request(
            activity_name(engine, waited.activity_id), summary="收尾", views=[views]))
    rejected = _act(engine, "v-rejected", "conclude_activity", conclude_request(
        "不存在的活动", summary="收尾", views=[[_view("relationship", "骗子", friend)]]))[1]
    assert rejected.status == "rejected"
    observed = _observe(engine, "v-seen")
    day = local_date(engine.state.current_time, engine.scenario.timezone)
    assert observed["persona_views"] == {"relationships": [{"person": friend, "view": "最近很忙"}],
                                         "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": day}]}
    written = [memory["text"] for event in engine.store.read_agent_events("action_requested", 1)
               if event.data["tool_name"] == "conclude_activity"
               for item in control_items("conclude_activity", event.data["request"]) for memory in item.views]
    assert written == ["靠谱", "爱吃面", "最近很忙", "骗子"]
    _walk(engine, "v-walk")
    road = _observe(engine, "v-road")
    shown = json.dumps([road["memory_layer"], road["progress_layer"]], ensure_ascii=False)
    assert all(text not in shown for text in ("靠谱", "爱吃面", "最近很忙", "骗子"))


def _record_conclusion(engine, memories: list[dict], views: list[dict]) -> None:
    """经 FootprintStore 补登一条成功的 conclude_activity 记录：照人物 1 最近一次收尾的请求与结果，逐项内容换成给定的
    memories 与 views，同一决策、动作身份另起，请求摘要按同一算法重算；世界状态不变、版本加一。请求模型不接受类型为
    relationship、preference 的普通记忆，引擎自己写不出这种记录，只用于核对读取方（persona_views）对它的处理。"""
    import hashlib

    from worldmock.footprints.models import EventDraft

    requested = [event for event in engine.store.read_agent_events("action_requested", 1)
                 if event.data["tool_name"] == "conclude_activity"][-1]
    result = next(event for event in engine.store.read_agent_events("action_result", 1)
                  if event.action_id == requested.action_id)
    item = {**requested.data["request"]["items"][0], "memories": memories, "views": views}
    request = {"items": [item], "errands": []}
    content = json.dumps({"tool_name": "conclude_activity", "request": request}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    data = {**requested.data, "request": request, "request_sha256": hashlib.sha256(content).hexdigest()}
    staged = engine.state.model_copy(deep=True)
    staged.version += 1
    shared = {"occurred_at": staged.current_time, "agent_id": 1, "decision_id": requested.decision_id,
              "action_id": f"{requested.action_id}-recorded"}
    engine.store.commit(staged.model_dump(mode="json"), [
        EventDraft(event_type="action_requested", source_kind="model", data=data, **shared),
        EventDraft(event_type="action_result", data={**result.data, "action_id": shared["action_id"]}, **shared)])
    engine.state = staged


def test_a_recorded_view_typed_memory_without_a_subject_is_not_a_persona_view(engine_factory) -> None:
    """看法与偏好只来自带对象的记忆：请求模型不接受类型为 relationship、preference 的普通记忆（它们没有 about），引擎写不出
    带这种记忆的成功收尾；经存储补登的成功收尾记录里出现这两类不带对象的记忆时，人设层的看法与偏好跳过它们，只取同一条
    记录里带对象的偏好与此前真实写下的看法，不把 null 当作对象，观察照常完成。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    _, waited = _act(engine, "s-wait", "start_activity", start_activity("等待", "自己"))
    assert advance_until_ready(engine)
    _act(engine, "s-conclude", "conclude_activity", conclude_request(
        activity_name(engine, waited.activity_id), summary="收尾", views=[[_view("relationship", "靠谱", friend)]]))
    loose = [_memo("relationship", "没有对象的看法"), _memo("preference", "没有对象的偏好")]
    for memory in loose:
        with pytest.raises(ValueError, match="type"):
            ConclusionMemory.model_validate(memory)
    _record_conclusion(engine, loose, [_view("preference", "爱吃面", "午饭")])
    recorded = engine.store.read_agent_events("action_requested", 1)[-1]
    assert [memory["subject"] for item in control_items("conclude_activity", recorded.data["request"])
            for memory in item.views] == [None, None, "午饭"]
    observed = _observe(engine, "s-seen")
    day = local_date(engine.state.current_time, engine.scenario.timezone)
    assert observed["persona_views"] == {"relationships": [{"person": friend, "view": "靠谱"}],
                                         "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": day}]}
    assert "没有对象" not in json.dumps(observed["persona_views"], ensure_ascii=False)


def test_first_purchase_writes_no_preference_and_leaves_no_trace_after_delivery_and_midnight(engine_factory) -> None:
    """反例：首次购买后人物只写了「明天还想吃」这类非偏好记忆时 preferences 为空；送达并跨零点后记忆层没有这次购买。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 1500)
    order = _remote_order(engine, "p-order")
    product = engine.rules.items[order["product_id"]].name
    for step in range(64):
        if engine.state.deliveries[order["order_id"]].status != "pending":
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"p-wait-{step}", *_keep_waiting(engine))
        else:
            advance_next(engine)
    names = [activity_name(engine, item.activity_id) for item in slot_activities(engine.state, 1)]
    _act(engine, "p-conclude", "conclude_activity", conclude_request(
        *names, summary="吃过了", memories=[[_memo("commitment", "明天还想吃")] for _ in names]))
    walk = _walk(engine, "p-walk")
    assert advance_until_ready(engine)
    _act(engine, "p-arrive", "conclude_activity", conclude_request(
        activity_name(engine, walk.activity_id), summary="到了", memories=[[]]))
    today = _observe(engine, "p-today")
    assert today["persona_views"]["preferences"] == []
    assert product in json.dumps(today["memory_layer"], ensure_ascii=False)
    moment = engine.state.current_time
    _jump(engine, _next_morning(moment))
    stage_day_plan(engine, submitted=True)
    later = _observe(engine, "p-next-day")
    assert later["memory_layer"] == {"layer": "记忆层", "earlier_days": [], "earlier_today": []}
    assert later["persona_views"]["preferences"] == []


def test_consolidation_is_kept_but_its_summary_never_reaches_the_prompt(tmp_path) -> None:
    """阈值巩固保留（摘要照旧写进主观存储），记忆上下文与生活决策、只读问答的请求都不含摘要。"""
    agent = LifeAgent()
    agent._id, agent._name, agent._profile = 7, "Alice", {"id": 7, "name": "Alice", "contacts": []}
    agent._enable_memory = True
    agent._logger = SimpleNamespace(warning=lambda *args: None, error=lambda *args: None)
    agent.trace_span = None
    runtime = WorldMockMemoryRuntime(agent, MemoryRuntimeConfig())
    runtime.ensure_store(tmp_path)
    runtime.store.mark_subjective_consolidated("我最爱吃楼下的牛肉面。", step_count=1, tick=60)
    agent._memory_runtime = runtime
    assert runtime.store.read_subjective_summary() == "我最爱吃楼下的牛肉面。"
    assert "牛肉面" not in json.dumps(runtime.build_context(), ensure_ascii=False)
    life = ReactLoopRequest(0, _context().observed_at, _observations())
    agent.prepare_react_loop(life)
    messages = agent.build_react_messages(life)
    readonly = ReactLoopRequest(0, _context().observed_at, [], "你爱吃什么？", True)
    agent.prepare_react_loop(readonly)
    messages += agent.build_react_messages(readonly)
    assert len(messages) == 5 and "牛肉面" not in json.dumps(messages, ensure_ascii=False)


def test_readonly_questions_use_the_latest_decision_snapshot(tmp_path) -> None:
    """只读问答：尚无生活决策时人设层来自卡片、记忆层两个列表为空；每次生活决策构建请求后把人设层与记忆层存进工作区
    快照，只读问答取最近一份。"""
    agent = LifeAgent()
    agent._id, agent._name = 7, "Alice"
    agent._profile = _profile(make_card())
    agent._enable_memory = False
    agent._workspace_root = tmp_path
    question = "今天过得怎么样？"
    readonly = ReactLoopRequest(0, _context().observed_at, [], question, True)
    agent.prepare_react_loop(readonly)
    persona, memory = agent._life_budget.layers
    assert memory == empty_memory_layer() and persona == persona_layer(agent._profile, "Alice", _NO_VIEWS)
    observations = _observations()
    remembered = {"layer": "记忆层", "earlier_days": [], "earlier_today": [
        {"recorded_at": "2026-01-04T23:00:00+00:00", "settled": ["22:30 给沈昊发消息：到家了"], "self_notes": []}]}
    observations[0]["data"]["memory_layer"] = remembered
    observations[0]["data"]["persona_views"] = {"relationships": [{"person": "沈昊", "view": "靠谱"}],
                                                "preferences": []}
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
    snapshot = json.loads((tmp_path / PROMPT_SNAPSHOT_FILE).read_text(encoding="utf-8"))
    assert snapshot == {"persona": agent._life_budget.layers[1], "memory": remembered}
    agent.prepare_react_loop(readonly)
    assert agent.build_react_messages(readonly) == readonly_messages(snapshot["persona"], remembered, question)
    assert readonly_layers(None, agent._profile, "Alice")[1] == empty_memory_layer()
    write_prompt_snapshot(tmp_path, {"layer": "人设层"}, remembered)
    assert readonly_layers(tmp_path, agent._profile, "Alice") == ({"layer": "人设层"}, remembered)
