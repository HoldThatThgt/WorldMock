"""活动事件内嵌人物可读标题：内容、生活活动，以及工作、通话与交谈（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-时间线只出现中文名称 ）。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, stage_day_plan, start_activity, start_request, submit_round
from tests.life.test_work_protocol import at_workplace
from worldmock.life.models import ActivityKind


def _executable(action: dict) -> bool:
    """@brief 动作此刻没有未满足的条件：说明为空；内容对象的说明只写时长（「全长…」或「还剩…」），没有前缀原因。"""
    note = action["note"]
    return note is None or note.startswith(("全长", "还剩"))


def _start_visible(engine, round_id: str, kind: str):
    """@brief 从真实观察的私有绑定选一项当前可执行的对象与方式，按「动词＋对象＋方式」启动。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding, action = next((row, action) for row in observed["interaction_bindings"] if row["kind"] == kind
                           for action in row["actions"] if _executable(action))
    request = start_request(targets=[binding["object"]], actions=[action["verb"]], ways=[action["label"]])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return binding, engine.commit_decision_round(round_id)[0]


def test_app_and_content_titles_are_carried_into_activity_events(engine_factory) -> None:
    """@brief 观看活动的结算与进度使用冻结语料标题。"""
    engine = engine_factory()
    binding, started = _start_visible(engine, "app-title", "app")
    assert started.status == "in_progress", started.error_code
    expected = engine.context.semantics.catalog.apps[binding["object_id"]].name
    event = next(event for event in engine.context.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-app-title")
    assert event.data["data"]["activity"]["title"] == expected
    content = event.data["data"]["activity"]["data"]["content_id"]
    assert event.data["data"]["activity"]["content_title"] == engine.context.rules.content_titles[content]
    advance_next(engine)
    progress = [event for event in engine.context.store.read_agent_events("activity_progress", 1)
                if event.data["activity"]["target_id"] == binding["object_id"]]
    assert progress[-1].data["activity"]["title"] == expected


def test_wait_activity_has_no_media_title(engine_factory) -> None:
    """@brief 自身等待不借用内容或目录标题。"""
    engine = engine_factory()
    _, started = _start_visible(engine, "wait-title", "self")
    assert started.status == "in_progress", started.error_code
    event = next(event for event in engine.context.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-wait-title")
    assert "title" not in event.data["data"]["activity"]


def _titles(engine, agent_id: int, activity_id: str, action_id: str) -> tuple:
    """@brief 同一活动的标题：受理结果快照、推进后的最后一条进度快照、观察控制行（八键）的 title。"""
    accepted = next(event for event in engine.context.store.read_agent_events("action_result", agent_id)
                    if event.action_id == action_id)
    advance_next(engine)
    progress = [event for event in engine.context.store.read_agent_events("activity_progress", agent_id)
                if event.activity_id == activity_id]
    observed = engine.observe(context(engine, f"title-peek-{activity_id}", agent_id))
    rows = [row for row in observed["active_activities"] + observed["pending_conclusions"]
            if row["activity_id"] == activity_id]
    return accepted.data["data"]["activity"]["title"], progress[-1].data["activity"]["title"], rows[0]["title"]


def test_work_title_is_the_topic(engine_factory) -> None:
    """@brief 工作的标题是任务标题：进受理结果、进度快照与观察控制行。"""
    engine = engine_factory()
    at_workplace(engine)
    stage_day_plan(engine)
    binding, started = _start_visible(engine, "work-title", "work_topic")
    assert started.status == "in_progress", started.error_code
    title = engine.context.rules.work_topics[binding["object_id"]].topic
    assert _titles(engine, 1, started.activity_id, "action-work-title") == (title, title, title)


def test_call_title_is_the_contact_salutation(engine_factory) -> None:
    """@brief 通话的标题是联系人在本人通讯录里的称谓（通话对象记在活动数据的 contact_id）。"""
    def with_contact(document):
        document['agents'][0]['contacts'] = [{
            'contact_id': 'colleague', 'salutation': '同事', 'relation': '同事', 'closeness': 16,
            'channels': ['phone'], 'reply_policy': 'none',
        }]
    engine = engine_factory(mutate=with_contact)
    binding, started = _start_visible(engine, "call-title", "contact")
    assert started.status == "in_progress", started.error_code
    contact = next(item for item in engine.context.scenario.agents[0].contacts if item.contact_id == binding["object_id"])
    assert engine.context.state.activities[started.activity_id].data["contact_id"] == contact.contact_id
    salutation = contact.salutation
    assert _titles(engine, 1, started.activity_id, "action-call-title") == (salutation, salutation, salutation)


def test_chat_title_is_the_partner_name(engine_factory) -> None:
    """@brief 交谈的标题是对方姓名（交谈对象记在活动数据的 partner_id）。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    first, second = engine.context.state.agents[1].name, engine.context.state.agents[2].name
    submit_round(engine, {1: ("start_activity", start_activity("交谈", f"人物：{second}")),
                          2: ("start_activity", start_activity("等待", "自己"))}, "chat-title")
    submit_round(engine, {2: ("start_activity", start_activity("交谈", f"人物：{first}"))}, "chat-accept")
    chats = {item.agent_id: item for item in engine.context.state.activities.values() if item.kind == ActivityKind.CHAT}
    assert (chats[1].data["partner_id"], chats[2].data["partner_id"]) == ("agent:2", "agent:1")
    assert _titles(engine, 1, chats[1].activity_id, "action-chat-title-1") == (second, second, second)
    accepted = next(event for event in engine.context.store.read_agent_events("action_result", 2)
                    if event.action_id == "action-chat-accept-2")
    assert accepted.data["data"]["activity"]["title"] == first
