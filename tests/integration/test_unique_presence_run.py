"""唯一出现原则的性质测试（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章「唯一出现原则」「通用格式」、验收台账「唯一出现」）。

真实 LifeEngine、SQLite、LifeEnv 与启用记忆的 LifeAgent（从工作区装配、经 AS2 step），只替换远程模型传输：双人场景
10:00 起，陈曦先查一次记忆再浏览商品目录，李凡同一秒发来一条直接消息；浏览结束后陈曦收尾并写下对李凡的看法与一条偏好，
接着看内容；邮件到达时暂停观看、读邮件；读完后步行去办公室（在家这段停留随出发结束），到达后收尾并开始等待。当日任务
一直可见。对两人发出的每一个行动请求（system、六层 user、原生 tools 与同秒续接的 assistant、tool 消息）检查：
- 直接消息全文、邮件正文、记忆查询返回的条目各至多出现一次；消息在同秒续接的 tool 消息、进度层与（停留结束后）记忆层
  各被看到过，邮件正文在阅读结果中被看到过，记忆条目只在查询的那一秒出现，时间推进后再不出现；
- 人设层的每条看法与偏好、待办层每项暂停活动的客观进度、任务目标的合同对象与其中的剩余要求，在整次请求中只在它自己
  那一处出现（恰好一次；字面相同的两项各算一处）；截止时刻只作为合同的 deadline 出现（别的记录恰好发生在那一刻时的
  *_at 不算）；工具层每个门店商品目标（浏览得到的供给）的完整对象恰好一处，名称作为 name 只在那一处，别处只作名称引用；
- 各层与 tool 消息的 JSON 中没有以 _seconds、_fen、_id、_ids 结尾的键，数值只有人设层的年龄与记忆查询的条数，没有
  写成纯数字、「N 秒」或超过两位小数的字符串；原生工具的参数名同样没有这些后缀；整次请求中没有任何内部身份。
同一批检查也作用于 tests/integration/test_dayrun_complete_days.py 的双人全天运行的每个行动请求。
计数时，同秒续接里被后续 tool 消息 changes 按名称取代（updated、added）或移除（removed）的链首旧条目不算另一处呈现
（第二章「同秒续接」：changes 相对于模型当前所见，以最新为准）；assistant 回合只计 content 与 reasoning_content，
它的 tool_calls 是模型自己那次调用的原样回放，不是环境呈现的信息。
个人事务另有一段单人运行（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 「唯一出现的口径」）：记下（被宿主纠正一次）、更新、取消、
办成与一次绕过宿主被环境拒绝的变更都在其中，事务的完整行只在待办层，事务名在别处只作为 errand 键，只有规格的三处
回显位置例外。
"""

from __future__ import annotations

from worldmock.life.tool_groups import compact_render, expand_tool_layer

import copy
import json
import os
import re
from collections import Counter
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.society import DirectToolCall  # noqa: E402
from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.agents.life_continuation import _earlier_changes, _overlay, _view  # noqa: E402
from custom.agents.life_persona import persona_layer  # noqa: E402
from custom.agents.life_prompt import life_user_layers, system_message, user_message  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.integration.test_life_agent_protocol import (_ModelResponse, _TextResponse, _conclude_args,  # noqa: E402
                                                        _memory, _stored_memories, _view as _opinion)
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (context, stage_day_plan, start_arguments)  # noqa: F401
from tests.life.conftest import errand_add, errand_cancel, errand_update, wake_by_mail  # noqa: E402
from tests.life.routing_fixtures import stage  # noqa: E402
from tests.life.test_errand_completion import KIT, KIT_NAME, MILK, MILK_ID, home  # noqa: E402
from tests.life.test_errand_reminder import TEA  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.activity_control import PHASE_ACTIVE  # noqa: E402
from worldmock.life.error_contract import ITEM_KEYS, ErrorItem, ErrorReport  # noqa: E402
from worldmock.life.errand_state import ERRAND_EVENT_KINDS  # noqa: E402
from worldmock.life.models import REQUEST_MODELS, ActivityProgressItem  # noqa: E402
from worldmock.life.prompt_schemas import validate_layer  # noqa: E402

#: 李凡发给陈曦的直接消息（全文不含 JSON 需要转义的字符）。
_MESSAGE = "周四下午的评审挪到三点半了，记得把第二版原型和用户访谈的纪要一起带上，会议室换成七楼东侧那间。"
#: 10:20 到达的邮件。
_MAIL = {"message_id": "unique-presence-mail", "thread_id": "unique-presence-mail", "recipient_agent_id": 1,
         "sent_at": "2026-01-05T10:20:00+08:00", "subject": "季度复盘材料确认",
         "body": "请在本周五中午前把季度复盘的三页材料发给我，重点写清楚留存下降的原因和下个季度准备做的两个实验。",
         "related_task_id": None}
#: 预先写进陈曦主观记忆的一条旧条目（不在记忆层里：记忆层由已提交事件派生）。
_EPISODE = "上周六傍晚沿着川杨河散步，风很大，走到桥边才折回来。"
#: 浏览收尾时写下的看法与偏好。
_VIEW = "李凡做事很靠谱，这周的评审可以放心交给他。"
_PREFERENCE = "下午更想喝不加糖的热拿铁。"
#: 陈曦的剧本阶段，依次推进。
_PHASES = ("query", "browse", "browsing", "watch", "watching", "read", "reading", "leave", "walking", "office", "done")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定上下文窗口，只验证请求内容。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


def _current(request: dict) -> dict:
    """模型此刻所见：链首六层依次叠加此前各续接 tool 消息的 changes（与宿主组装续接时同一视图）。"""
    messages = request["messages"]
    seen = _view(json.loads(messages[1]["content"]))
    for message in messages[2:]:
        changes = _earlier_changes(message) if message["role"] == "tool" else None
        if changes:
            seen = _overlay(seen, changes)
    return seen


class _Script:
    """只替换远程模型传输：按本次请求中模型此刻所见决定唯一一个工具调用，保存实际发送的每个请求。"""

    model_name = "deepseek-flash"

    def __init__(self, agent_id: int) -> None:
        self.agent_id, self.requests, self.phase = agent_id, [], "query" if agent_id == 1 else "message"

    async def call(self, **request):
        """保存请求；行动请求（工具中有 start_activity，docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-就寝反思经严格工具提交 ）按剧本返回一个工具调用，
        记忆运行时不带工具的请求返回一句话。这段运行到不了就寝边界：带工具而不是行动请求的请求（就寝反思）出现即失败，
        不被当作行动请求作答。"""
        names = [item["function"]["name"] for item in request.get("tools") or []]
        if not names:
            return _TextResponse("我按眼前的情况决定接下来做什么。")
        assert "start_activity" in names, f"unexpected non-action request with tools {names}"
        self.requests.append(copy.deepcopy(request))
        tool, arguments = self._first(_current(request)) if self.agent_id == 1 else self._second(_current(request))
        return _ModelResponse(f"call-{self.agent_id}-{len(self.requests)}", tool,
                              json.dumps(arguments, ensure_ascii=False), reasoning_content="按此刻所见决定下一步。")

    def _advance(self) -> None:
        """进入下一阶段。"""
        self.phase = _PHASES[_PHASES.index(self.phase) + 1]

    def _first(self, seen: dict) -> tuple[str, dict]:
        """陈曦：刚结束的先收尾，否则按阶段行动。"""
        pending, running = _activities(seen, "刚结束"), _activities(seen, "进行中")
        if pending:
            views = [_opinion("李凡", _VIEW), _opinion("咖啡", _PREFERENCE, kind="preference")] if (
                self.phase == "browsing") else []
            if self.phase in {"browsing", "reading", "walking"}:
                self._advance()
            return "conclude_activity", _items("conclude_activity", pending, "这件事先到这里。", views)
        phase = self.phase
        if phase == "query":
            self._advance()
            return "memory_recent", {"limit": 5}
        if phase in {"browse", "watch", "read", "leave", "office"}:
            self._advance()
            return "start_activity", _start(seen, phase)
        if phase == "watching" and running and any(name.startswith("邮件：") for name in seen["targets"]):
            self._advance()
            return "pause_activity", _items("pause_activity", running, "先看邮件。")
        if running:
            return "report_status", _items("report_status", running, "接着做。")
        return "start_activity", _start(seen, "office")

    def _second(self, seen: dict) -> tuple[str, dict]:
        """李凡：先给陈曦发消息，之后刚结束的收尾、进行中的播报，否则等待。"""
        if self.phase == "message":
            self.phase = "busy"
            return "send_message", {"recipient_name": "人物：陈曦", "text": _MESSAGE}
        pending, running = _activities(seen, "刚结束"), _activities(seen, "进行中")
        if pending:
            return "conclude_activity", _items("conclude_activity", pending, "收尾。")
        if running:
            return "report_status", _items("report_status", running, "接着做。")
        return "start_activity", _start(seen, "office")


def _items(tool: str, names: list[str], summary: str, views: list[dict] | None = None) -> dict:
    """控制工具参数：一项一个对象、写同一句自述；结束工具每项另带空的普通记忆，看法只写在第一项；没有个人事务变更。"""
    rows = []
    for index, name in enumerate(names):
        row: dict = {"activity_name": name, "summary": summary}
        if tool == "conclude_activity":
            row.update(memories=[], views=list(views or []) if index == 0 else [])
        rows.append(row)
    return {"items": rows, "errands": []}


def _activities(seen: dict, phase: str) -> list[str]:
    """所见工具层中处于给定阶段的活动目标名。"""
    return [name for name, target in seen["targets"].items() if target["kind"] == "activity"
            and target["phase"] == phase]


def _start(seen: dict, phase: str) -> dict:
    """按阶段选 start_activity 的一项（对象、动词与方式都取自所见工具层）。"""
    def pick(accept) -> tuple[str, str, str]:
        """@brief 第一个满足条件的可用动作。"""
        return next((name, item["action"], item["way"]) for name, target in seen["targets"].items()
                    for item in target["actions"] if item["available"] and accept(name, item))

    accepts = {
        "browse": lambda name, item: item["way"] == "浏览",
        "watch": lambda name, item: name.startswith("内容：") and item["action"] == "interact",
        "read": lambda name, item: name.startswith("邮件：") and item["way"] == "阅读",
        "leave": lambda name, item: name.startswith("地点：") and "办公室" in name and item["way"] == "步行",
        "office": lambda name, item: name == "自己" and item["way"] == "等待",
    }
    name, verb, way = pick(accepts[phase])
    return start_arguments([name], [verb], [way])


def _engine(engine_factory):
    """双人场景副本加一封 10:20 给陈曦的邮件；两人登记当日空计划，世界置于 1 月 5 日 10:00，陈曦的当日任务可见。"""
    def mutate(document: dict) -> None:
        """@brief 追加邮件（沿用首封邮件的其余字段）。"""
        document["messages"].append({**document["messages"][0], **_MAIL})

    engine = engine_factory("multi-person.json", mutate=mutate)
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id, submitted=True)
    now = int(datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo(engine.context.scenario.timezone)).timestamp())
    task = next(item.task_id for item in engine.context.scenario.tasks if item.task_id.startswith("task-"))

    def change(state) -> None:
        """@brief 推到 10:00，两人都在这一秒决策，陈曦看得到当日任务。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = agent.next_decision_at = now
        state.agents[1].visible_task_ids = {*state.agents[1].visible_task_ids, task}
    stage(engine, change)
    return engine


async def _people(environment: LifeEnv, root, models: dict) -> dict:
    """从全新工作区装配启用记忆的两位真实 LifeAgent；陈曦的主观记忆里预先有一条旧条目。"""
    people = {}
    for agent_id, model in models.items():
        workspace = root / f"agent_{agent_id:04d}"
        name = environment.engine.context.state.agents[agent_id].name
        LifeAgent.create(workspace, {"id": agent_id, "name": name, "occupation": "engineer"},
                         {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 8})
        service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=model), trace=None)
        people[agent_id] = await LifeAgent.from_workspace(workspace, service)
    earlier = datetime(2026, 1, 4, 19, 30, tzinfo=ZoneInfo(environment.engine.context.scenario.timezone))
    people[1]._memory_runtime.store.append_episodes(
        _stored_memories(_conclude_args("散步（1月4日 19:00 起）", memories=[[_memory(_EPISODE)]])),
        tick=0, t=earlier, step_count=0)
    return people


async def _run(environment: LifeEnv, people: dict, models: dict) -> None:
    """真实决策循环：没人就绪就推进到下一边界；就绪的人物逐个经 AS2 step 决策，再结算本轮。陈曦到办公室开始等待后，
    再过两次决策机会即停。"""
    engine = environment.engine
    rounds = done = 0
    while done < 3:
        scheduling = engine.scheduling_state()
        if not scheduling.ready_agent_ids:
            await environment.step(int(scheduling.next_event_at.timestamp()) - engine.context.state.current_time,
                                   scheduling.next_event_at)
            continue
        rounds += 1
        assert rounds < 60, (engine.context.state.current_time, models[1].phase)
        contexts = [context(engine, f"unique-{rounds}", agent_id) for agent_id in scheduling.ready_agent_ids]
        await environment.begin_decision_round(tuple(contexts))
        for decision in contexts:
            people[decision.agent_id].bind_decision_context(decision)
            await people[decision.agent_id].step(0, decision.observed_at)
        results = await environment.commit_decision_round(contexts[0].round_id)
        assert all(result.error_code is None for result in results), results
        done += models[1].phase == "done" and 1 in scheduling.ready_agent_ids


#: 续接 changes 中可按名称取代链首条目的部分：（类别，changes 键，写入键，键字段）。
_KEYED_CHANGES = (("target", "targets", "updated", "name"), ("paused", "paused_activities", "added", "name"),
                  ("errand", "errands", "added", "name"))
#: 链首六层中按名称呈现的条目：层名 → [(类别，列表键，键字段)]。
_KEYED_LAYERS = {"工具层": (("target", "targets", "name"),),
                 "待办层": (("paused", "paused_activities", "name"), ("errand", "errands", "name")),
                 "人设层": (("relationship", "relationships", "person"), ("preference", "preferences", "subject"))}


def _compact(value) -> str:
    """与七层同一写法的紧凑 JSON（中文不转义）。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json(text: str):
    """消息正文按 JSON 解析；不是 JSON 的纯文本原样返回。"""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _presented(request: dict) -> list[str]:
    """整次请求中呈现给模型的各段文本：system、原生 tools、链首六层（按名称的条目单独成段）、assistant 的正文与思考、
    每条 tool 消息；同秒续接里被后续 changes 按名称取代或移除的条目只保留最新的一段。"""
    messages = request["messages"]
    texts, latest = [messages[0]["content"], _compact(request["tools"])], {}
    layers = json.loads(messages[1]["content"])
    layers[0] = expand_tool_layer(layers[0])
    for layer in layers:
        rest = dict(layer)
        for kind, field, key in _KEYED_LAYERS.get(layer["layer"], ()):
            latest.update({(kind, item[key]): item for item in layer[field]})
            rest[field] = []
        texts.append(_compact(rest))
    for message in messages[2:]:
        if message["role"] == "assistant":
            texts.extend(message.get(key) or "" for key in ("content", "reasoning_content"))
            continue
        payload = _json(message["content"])
        if not (isinstance(payload, dict) and "result" in payload):
            texts.append(message["content"])
            continue
        changes = dict(payload.pop("changes", {}))
        for kind, field, written, key in _KEYED_CHANGES:
            part = changes.pop(field, {})
            for name in part.get("removed", []):
                latest.pop((kind, name), None)
            latest.update({(kind, item[key]): item for item in part.get(written, [])})
        for kind, field, key in (("relationship", "relationships", "person"), ("preference", "preferences", "subject")):
            latest.update({(kind, item[key]): item for item in changes.pop(field, [])})
        texts.extend([_compact(payload), _compact(changes)])
    return texts + [_compact(item) for item in latest.values()]


def _occurrences(request: dict, item: str) -> int:
    """一段文字在整次请求中呈现的次数（可以嵌在更长的句子里，例如记忆层 settled 句中的消息全文）。"""
    return sum(text.count(item) for text in _presented(request))


def _values(request: dict, item: str) -> int:
    """一个字段值在整次请求中作为完整 JSON 字符串出现的次数（不计作为别的字符串一部分的出现）。"""
    return _occurrences(request, json.dumps(item, ensure_ascii=False))


def _holders(value, text: str) -> list[str | None]:
    """value（解析后的 JSON 或纯文本）中 text 的每一处出现挂在哪个键下：恰为某个键的值时记该键，嵌在更长的字符串里或
    作为列表项时记 None。"""
    if isinstance(value, dict):
        found: list[str | None] = []
        for key, item in value.items():
            found.extend([key] if item == text else _holders(item, text))
        return found
    if isinstance(value, list):
        return [holder for item in value for holder in _holders(item, text)]
    return [None] * value.count(text) if isinstance(value, str) else []


def _holders_in(request: dict, value: str) -> list[str | None]:
    """一个字符串在整次请求中的每一处出现各挂在哪个键下（逐段解析；不是 JSON 的段按纯文本）。"""
    return [holder for text in _presented(request) if value in text for holder in _holders(_json(text), value)]


def _assert_once_each(request: dict) -> None:
    """模型此刻所见的每条看法、偏好、暂停活动进度与任务合同的剩余，在整次请求中都只在它自己那一处出现（出现次数等于所见
    条目中带这项内容的条目数：两项任务的合同可以字面相同）；合同对象同样按条目计，截止时刻只作为合同的 deadline 出现
    （同一时刻作为别的记录的 *_at 发生时刻不算复述，例如「expired 在截止时刻」）；每个门店商品目标的完整对象恰好一处，
    它的名称作为 name 只在这一处，别处只能作为名称引用（例如调用参数 targets 里的一项）；消息全文、邮件正文与记忆条目
    至多一次。"""
    seen = _current(request)
    items = [item["view"] for item in seen["relationships"].values() if item["view"]]
    items += [item["preference"] for item in seen["preferences"].values()]
    items += [item["progress"] for item in seen["paused_activities"].values()]
    contracts = [target["contract"] for target in seen["targets"].values() if target["kind"] == "task"]
    items += [contract["remaining"] for contract in contracts]
    for item, count in Counter(items).items():
        assert _values(request, item) == count, item
    for contract, count in Counter(_compact(contract) for contract in contracts).items():
        assert _occurrences(request, contract) == count, contract
    for deadline, count in Counter(contract["deadline"] for contract in contracts).items():
        holders = _holders_in(request, deadline)
        assert holders.count("deadline") == count, (deadline, holders)
        assert all(key and key.endswith("_at") for key in holders if key != "deadline"), (deadline, holders)
    for target in seen["targets"].values():
        if target["kind"] == "product" and not target["name"].startswith("持有物："):
            assert _occurrences(request, _compact(target)) == 1, target["name"]
            assert _holders_in(request, target["name"]).count("name") == 1, target["name"]
    for item in (_MESSAGE, _MAIL["body"], _EPISODE):
        assert _occurrences(request, item) <= 1, item


def _identities(engine) -> set[str]:
    """运行里的内部身份：活动、供给、订单、消息、任务、联系人与带连字符的地点身份，以及同世界人物身份。"""
    state, scenario = engine.context.state, engine.context.scenario
    names = {*state.activities, *(item.supply_id for item in scenario.supplies), *state.deliveries,
             *(item.message_id for item in scenario.messages), *(item.task_id for item in scenario.tasks),
             *(contact.contact_id for agent in scenario.agents for contact in agent.contacts),
             *(item.location_id for item in scenario.locations if "-" in item.location_id)}
    for agent in state.agents.values():
        names.update(agent.inbox)
    return names | {f"agent:{agent_id}" for agent_id in state.agents}


#: 固定位置数值字段：人设年龄与记忆查询条数；分页、品类条数、拒绝次数及错误路径另按公开结构扫描。
_NUMBERS = {("identity", "age"), ("details", "episodes")}
#: 参数路径中实际声明的数组字段，仅这些字段之后可以出现数组下标。
_ARRAY_PATH_FIELDS = {"items", "memories", "views", "keywords", "goals", "decisions", "action_items", "errands"}


def _scan_error_path(value: list) -> None:
    """完整错误参数路径只允许工具调用下标与实际数组字段下标，不许可任意位置数字。"""
    assert isinstance(value, list) and value and value[0] == "tool_calls", value
    if value == ["tool_calls"]:
        return
    assert len(value) >= 4 and type(value[1]) is int and value[1] >= 0, value
    assert value[2] == "function" and value[3] in {"arguments", "name"}, value
    if value[3] == "name":
        assert len(value) == 4, value
    for index, segment in enumerate(value[4:], start=4):
        if type(segment) is int:
            assert segment >= 0 and value[index - 1] in _ARRAY_PATH_FIELDS, value
        else:
            assert isinstance(segment, str) and segment.strip(), value
            _scan(segment)


def _scan(value, path: tuple = ()) -> None:
    """层与 tool 消息 JSON 的形状扫描：没有 _seconds、_fen、_id、_ids 结尾或名为 id 的键；数值须符合公开字段结构；
    字符串不是纯数字，不写「N 秒」，数字至多两位小数（readable 的口径）。进度层调用记录的 arguments 是模型自己按原生
    工具 Schema 写下的参数，原样保留，不扫；完整 ErrorItem 中的 path 按真实工具调用与数组结构扫描。"""
    if isinstance(value, dict):
        for key, item in value.items():
            assert key != "id" and not re.search(r"_(seconds|fen|ids?)$", key), path + (key,)
            if key == "path" and set(value) == set(ITEM_KEYS):
                _scan_error_path(item)
            elif key == "times" and path[-2:-1] == ("rejected_today",):
                assert type(path[-1]) is int and path[-1] >= 0, path
                assert set(value) == {"written", "why", "times", "last", "accepted_as"}, value
                assert type(item) is int and item >= 1, (path, item)
            elif key != "arguments":
                _scan(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan(item, path + (index,))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        query_count = len(path) >= 3 and path[-3] == "query_categories" and path[-1] == "count"
        query_page = path[-1:] in {("offset",), ("next_offset",), ("total_count",)}
        assert path[-2:] in _NUMBERS or query_count or query_page, (path, value)
    elif isinstance(value, str):
        assert not re.fullmatch(r"-?\d+(\.\d+)?", value), (path, value)
        assert not re.search(r"\d\s*秒|\d\.\d{3,}", value), (path, value)


def _property_names(schema) -> list[str]:
    """JSON Schema 中各层 properties 声明的全部参数名。"""
    if isinstance(schema, list):
        return [name for item in schema for name in _property_names(item)]
    if not isinstance(schema, dict):
        return []
    return [*schema.get("properties", {}), *(name for item in schema.values() for name in _property_names(item))]


def _assert_readable(request: dict, identities: set[str]) -> None:
    """六层与每条 tool 消息通过形状扫描；六层逐层通过 Schema；原生工具的参数名没有秒数、分与身份后缀；整次请求不含任何
    内部身份。"""
    layers = json.loads(request["messages"][1]["content"])
    for layer in layers:
        validate_layer(layer)
    payloads = [_json(item["content"]) for item in request["messages"][2:] if item["role"] == "tool"]
    for payload in [*layers, *(item for item in payloads if not isinstance(item, str))]:
        _scan(payload)
    parameters = _property_names([tool["function"]["parameters"] for tool in request["tools"]])
    assert not [name for name in parameters if name == "id" or re.search(r"_(seconds|fen|ids?)$", name)]
    whole = "".join(_presented(request))
    assert not [identity for identity in identities if identity in whole]


def _where(request: dict, item: str) -> set[str]:
    """一项信息出现在请求的哪里：链首某层的层名，或续接的 tool 消息。"""
    places = {layer["layer"] for layer in json.loads(request["messages"][1]["content"])
              if item in _compact(layer)}
    return places | {"tool 消息" for message in request["messages"][2:]
                     if message["role"] == "tool" and item in message["content"]}


def test_readability_allows_only_complete_error_path_indices() -> None:
    entry = ErrorItem(("items", 1, "memories", 0, "keywords", 2), False, "ARGUMENTS_INVALID",
                      expected="A JSON string is required.", why="A JSON string is required.")
    item = entry.payload()
    _scan(ErrorReport("ARGUMENTS_INVALID", "Correct the declared field.", items=(entry,)).payload())
    for path in (["tool_calls"], ["tool_calls", 0, "function", "arguments"],
                 ["tool_calls", 2, "function", "name"]):
        _scan({**item, "path": path})
    for path in (["items", 0, "target"], ["tool_calls", -1, "function", "arguments"],
                 ["tool_calls", True, "function", "arguments"], ["tool_calls", 0.5, "function", "arguments"],
                 ["tool_calls", 0, "function", "arguments", "balance", 1],
                 ["tool_calls", 0, "function", "arguments", "items", -1, "target"],
                 ["tool_calls", 0, "function", "name", 1]):
        with pytest.raises(AssertionError):
            _scan({**item, "path": path})
    for broken in ({"path": item["path"]}, {"path": 42}, {"items": [item], "price": 3200},
                   {**item, "quantity": 1}, {**item, "order_id": "order-1"}):
        with pytest.raises(AssertionError):
            _scan(broken)


def test_readability_allows_only_declared_rejection_counts() -> None:
    """操作备忘仅允许完整公开结构的正整数重复次数，其他字段与错误类型仍拒绝。"""
    memo = {"written": "自己", "why": "Repeated target.", "times": 1,
            "last": "2026-01-05 10:00", "accepted_as": None}
    for count in (1, 3):
        _scan({"rejected_today": [{**memo, "times": count}]})
    for count in (0, -1, True, 1.0, "1"):
        with pytest.raises(AssertionError):
            _scan({"rejected_today": [{**memo, "times": count}]})
    for broken in ({"times": 1}, {"events": [memo]}, {"rejected_today": memo},
                   {"rejected_today": [{"times": 1}]}, {"rejected_today": [memo], "price": 3200}):
        with pytest.raises(AssertionError):
            _scan(broken)


@pytest.mark.asyncio
async def test_every_request_of_a_multi_state_run_presents_each_item_once(_engine_factory, tmp_path) -> None:
    """整段真实运行中两人的每个行动请求都满足唯一出现与人读写法；关键信息在各阶段确实被看到过（性质不空转）。"""
    engine = _engine(_engine_factory)
    environment = LifeEnv()
    environment.engine = engine
    models = {agent_id: _Script(agent_id) for agent_id in (1, 2)}
    people = await _people(environment, tmp_path / "people", models)
    try:
        await _run(environment, people, models)
        identities = _identities(engine)
        requests = [*models[1].requests, *models[2].requests]
        for request in requests:
            _assert_once_each(request)
            _assert_readable(request, identities)
        _assert_seen_in_every_state(models[1].requests)
    finally:
        engine.close()


def _observed_at(request: dict) -> str:
    """请求链首环境层的观察时刻。"""
    return json.loads(request["messages"][1]["content"])[4]["observed_at"]


def _assert_seen_in_every_state(requests: list[dict]) -> None:
    """陈曦的请求里：消息全文先在同秒续接的 tool 消息、后在进度层、停留结束后在记忆层，送达之后的每个请求都恰好一次；
    邮件正文出现的次数恰好等于链首进度层里带邮件的阅读结果数（至多一个），停留结束后不再出现；记忆条目只在查询的那一秒
    （查询回合与同秒续接）出现，时间推进后不再出现；看法、偏好、暂停活动、门店商品目标都被看到过，每个请求都有任务目标；
    到达办公室后的请求消息全文仍恰好一次。"""
    message = set().union(*(_where(request, _MESSAGE) for request in requests))
    assert {"tool 消息", "进度层", "记忆层"} <= message
    arrived = next(index for index, request in enumerate(requests) if _occurrences(request, _MESSAGE))
    assert all(_occurrences(request, _MESSAGE) == 1 for request in requests[arrived:])
    for request in requests:
        progress = json.loads(request["messages"][1]["content"])[5]["events"]
        read = [row for row in progress if row["type"] == "tool_call" and "mail" in row["result"]["details"]]
        assert _occurrences(request, _MAIL["body"]) == len(read) <= 1
    assert "进度层" in set().union(*(_where(request, _MAIL["body"]) for request in requests))
    queried = {_observed_at(request) for request in requests if _occurrences(request, _EPISODE)}
    assert len(queried) == 1
    later = [request for request in requests if _observed_at(request) > next(iter(queried))]
    assert later and not any(_occurrences(request, _EPISODE) for request in later)
    for item in (_VIEW, _PREFERENCE):
        assert any(_values(request, item) == 1 for request in requests)
    assert any(_current(request)["paused_activities"] for request in requests)
    office = [request for request in requests if "办公室" in json.loads(request["messages"][1]["content"])[4][
        "location"]["name"]]
    assert office and all(_occurrences(request, _MESSAGE) == 1 for request in office)
    assert all(any(target["kind"] == "task" for target in _current(request)["targets"].values())
               for request in requests)
    assert any(target["kind"] == "product" for request in requests for target in _current(request)["targets"].values())


def _observed_request(engine) -> dict:
    """一次真实观察按请求写法组成的整次请求（system、六层 user、没有原生工具）：陈曦在家，当日任务可见。"""
    observed = engine.observe(context(engine, "teeth"))
    layers = life_user_layers(observed, persona_layer({}, "陈曦", observed["persona_views"]))
    return {"messages": [system_message(False), user_message(layers)], "tools": []}


def _with(request: dict, change) -> dict:
    """把 change(layers) 作用到请求 user 六层的深拷贝上，返回新请求。"""
    layers = json.loads(request["messages"][1]["content"])
    layers[0] = expand_tool_layer(layers[0])
    change(layers)
    return {**request, "messages": [request["messages"][0], user_message(layers)]}


def test_the_presence_and_readability_checks_have_teeth(_engine_factory) -> None:
    """检查不空转：真实观察组成的请求通过两项检查；把任务合同的剩余写进环境层、把消息全文写两处时唯一出现检查失败；
    同秒续接的 tool 消息以 changes 取代任务目标、或某条结果恰好发生在截止时刻时不算第二处，但在结果里复述合同的剩余或
    在句子里写出截止时刻时失败；门店商品目标名作为调用参数引用不算第二处，结果里再列一个同名商品对象时失败；
    形状扫描对 duration_seconds 键、
    以分为单位的整数价格、纯数字字符串、「N 秒」、「0.0166667 小时」这样超过两位小数的数字与 order_id 键都失败，tool
    消息带内部身份时人读写法检查失败。"""
    engine = _engine(_engine_factory)
    request = _observed_request(engine)
    identities = _identities(engine)
    _assert_once_each(request)
    _assert_readable(request, identities)
    task = next(target for target in expand_tool_layer(json.loads(request["messages"][1]["content"])[0])["targets"]
                if target["kind"] == "task")
    remaining = task["contract"]["remaining"]
    for change in (lambda layers: layers[4]["location"]["fixed_rules"].append(remaining),
                   lambda layers: layers[4]["location"]["fixed_rules"].extend([_MESSAGE, _MESSAGE])):
        with pytest.raises(AssertionError):
            _assert_once_each(_with(request, change))
    assistant = {"role": "assistant", "content": "", "reasoning_content": "想一想。", "tool_calls": []}

    def continued(payload: dict) -> dict:
        """@brief 请求后接一个 assistant 与一条续接 tool 消息。"""
        tool = {"role": "tool", "tool_call_id": "call", "content": _compact(payload)}
        return {**request, "messages": [*request["messages"], assistant, tool]}

    error = ErrorReport("ARGUMENTS_INVALID", "A JSON string is required.", items=(ErrorItem(
        ("items", 1, "memories", 0, "keywords", 2), False, "ARGUMENTS_INVALID"),)).payload()
    _assert_readable(continued(error), identities)
    leaked = copy.deepcopy(error)
    leaked["items"][0]["path"].append(engine.context.scenario.tasks[0].task_id)
    with pytest.raises(AssertionError):
        _assert_readable(continued(leaked), identities)

    _assert_once_each(continued({"result": {"details": {}}, "changes": {"targets": {"updated": [task]}}}))
    _assert_once_each(continued({"result": {"occurred_at": task["contract"]["deadline"], "details": {}}}))
    for payload in ({"result": {"details": {"contract": remaining}}},
                    {"result": {"details": {"note": f"截止 {task['contract']['deadline']}"}}}):
        with pytest.raises(AssertionError):
            _assert_once_each(continued(payload))
    product = {"name": "集市出售的鸡汤面", "kind": "product", "phase": None, "state": None, "actions": [], "tools": []}
    shop = _with(request, lambda layers: layers[0]["targets"].append(product))
    referenced = {"type": "tool_call", "tool": "start_activity", "arguments": start_arguments(
        [product["name"]], ["obtain"], ["远程下单"])}
    _assert_once_each(_with(shop, lambda layers: layers[5]["events"].append(referenced)))
    with pytest.raises(AssertionError):
        _assert_once_each(_with(shop, lambda layers: layers[5]["events"].append(
            {"type": "tool_call", "result": {"details": {"items": [{"name": product["name"], "price": "8 元"}]}}})))
    target = next(item for item in expand_tool_layer(json.loads(request["messages"][1]["content"])[0])["targets"] if item["actions"])
    priced = {**target, "actions": [{**target["actions"][0], "price": 3200}]}
    for broken in ({**target, "duration_seconds": 720}, priced, {**target, "state": "3600"}, {**target, "state": "约 30 秒"},
                   {"contract": {**task["contract"], "remaining": "有效工时还差约 1 分钟（约 0/0.0166667 小时）"}},
                   {"result": {"details": {"order_id": "order-1"}}}):
        with pytest.raises(AssertionError):
            _scan(broken)
    with pytest.raises(AssertionError):
        _assert_readable(continued({"result": {"details": {"note": f"见 {engine.context.scenario.tasks[0].task_id}"}}}),
                         identities)


#: 个人事务那段运行里的三条事务：手边那盒奶吃掉即办成（被宿主纠正过一次备注），食材包先记下后改备注，冰红茶记下后取消。
_DRINK_ERRAND, _KIT_ERRAND, _TEA_ERRAND = "把冰箱里那盒奶喝掉", "周末补一份食材包", "囤一箱冰红茶"
_DRINK_MEMO, _KIT_MEMO, _KIT_MEMO_LATER = "午饭后趁凉喝", "下班顺路去超市", "改成周六上午去菜场"
_TEA_REASON = "家里还有半箱"
#: 超过 120 字的备注：第一个由宿主就地纠正（它以合法备注开头），第二个经绕过宿主的请求由环境拒绝（以更新后的备注开头）。
_TOO_LONG_MEMO = _DRINK_MEMO + "，" + "顺便把冰箱里的剩菜也清一清" * 10
_BYPASSED_MEMO = _KIT_MEMO_LATER + "，" + "顺便看看有没有新到的蔬菜" * 10
#: 剧本各阶段（陈曦一人），依次推进；bypass 一步不经人物模型，由驱动直接登记给环境。
_ERRAND_PHASES = ("wait", "note", "note-fixed", "change", "drink", "bypass", "after-bypass", "finish", "end", "done")


def _errand_items(tool: str, names: list[str], summary: str) -> list[dict]:
    """控制工具的逐项活动：一项一个对象、写同一句自述；结束工具每项另带空的记忆与看法。"""
    extra = {"memories": [], "views": []} if tool == "conclude_activity" else {}
    return [{"activity_name": name, "summary": summary, **extra} for name in names]


class _ErrandScript:
    """只替换远程模型传输：陈曦按阶段记下、纠正、更新、取消个人事务并喝掉那盒奶，按本次请求中模型此刻所见决定唯一一个
    工具调用，保存实际发送的每个行动请求。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.phase = "wait"

    async def call(self, **request):
        """保存行动请求并按剧本作答；记忆运行时不带工具的请求返回一句话。"""
        names = [item["function"]["name"] for item in request.get("tools") or []]
        if not names:
            return _TextResponse("我按眼前的情况决定接下来做什么。")
        assert "start_activity" in names, f"unexpected non-action request with tools {names}"
        self.requests.append(copy.deepcopy(request))
        tool, arguments = self._decide(_current(request))
        return _ModelResponse(f"call-errand-{len(self.requests)}", tool, json.dumps(arguments, ensure_ascii=False),
                              reasoning_content="按此刻所见决定下一步。")

    def _advance(self) -> None:
        """进入下一阶段。"""
        self.phase = _ERRAND_PHASES[_ERRAND_PHASES.index(self.phase) + 1]

    def _decide(self, seen: dict) -> tuple[str, dict]:
        """当前阶段要做的事；阶段条件不满足时刚结束的先收尾、进行中的播报（都不带事务变更），否则开始等待。"""
        pending, running = _activities(seen, "刚结束"), _activities(seen, "进行中")
        planned = self._planned(pending, running)
        if planned is not None:
            self._advance()
            return planned
        if pending:
            return "conclude_activity", {"items": _errand_items("conclude_activity", pending, "收尾。"), "errands": []}
        if running:
            return "report_status", {"items": _errand_items("report_status", running, "接着做。"), "errands": []}
        return "start_activity", start_arguments(["自己"], ["interact"], ["等待"])

    def _planned(self, pending: list[str], running: list[str]) -> tuple[str, dict] | None:
        """本阶段的计划动作；条件不满足时为 None。"""
        report = _errand_items("report_status", running, "还在等。")
        steps = {
            "wait": (True, "start_activity", start_arguments(["自己"], ["interact"], ["等待"])),
            "note": (running, "report_status", {"items": report, "errands": [
                errand_add(_DRINK_ERRAND, MILK, outcome="吃掉", memo=_TOO_LONG_MEMO)]}),
            "note-fixed": (running, "report_status", {"items": report, "errands": [
                errand_add(_DRINK_ERRAND, MILK, outcome="吃掉", memo=_DRINK_MEMO),
                errand_add(_KIT_ERRAND, KIT_NAME, memo=_KIT_MEMO), errand_add(_TEA_ERRAND, TEA, quantity=3)]}),
            "change": (pending, "conclude_activity", {
                "items": _errand_items("conclude_activity", pending, "不等了。"),
                "errands": [errand_update(_KIT_ERRAND, memo=_KIT_MEMO_LATER), errand_cancel(_TEA_ERRAND, _TEA_REASON)]}),
            "drink": (not pending and not running, "start_activity",
                      start_arguments([f"持有物：{MILK}"], ["consume"], ["食用"])),
            "after-bypass": (running, "report_status", {
                "items": _errand_items("report_status", running, "接着喝。"), "errands": []}),
            "finish": (pending, "conclude_activity", {
                "items": _errand_items("conclude_activity", pending, "喝完了。"), "errands": []}),
            "end": (not pending and not running, "start_activity", start_arguments(["自己"], ["interact"], ["等待"])),
        }
        ready, tool, arguments = steps.get(self.phase, (False, None, None))
        return (tool, arguments) if ready else None


def _bypass(engine, decision) -> None:
    """绕过宿主的一次登记（不经人物模型）：播报进行中的喝奶，同时把食材包的备注更新成超过 120 字的写法；请求不经校验
    构造，交给环境复核。"""
    observed = engine.observe(decision)
    [name] = [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_ACTIVE]
    request = REQUEST_MODELS["report_status"].model_construct(
        items=[ActivityProgressItem(activity_name=name, summary="先喝着。")],
        errands=[errand_update(_KIT_ERRAND, memo=_BYPASSED_MEMO)])
    engine.submit_action(DirectToolCall(decision, f"action-{decision.round_id}", "report_status", request))


async def _errand_run(environment: LifeEnv, person: LifeAgent, script: _ErrandScript) -> list:
    """真实决策循环：没人就绪就推进到下一边界；陈曦就绪时经 AS2 step 决策，到 bypass 阶段改由驱动直接登记，再结算本轮。
    返回各轮的动作结果。"""
    engine, results, rounds = environment.engine, [], 0
    while script.phase != "done":
        scheduling = engine.scheduling_state()
        if not scheduling.ready_agent_ids:
            await environment.step(int(scheduling.next_event_at.timestamp()) - engine.context.state.current_time,
                                   scheduling.next_event_at)
            continue
        rounds += 1
        assert rounds < 40, (engine.context.state.current_time, script.phase)
        decision = context(engine, f"errand-{rounds}")
        await environment.begin_decision_round((decision,))
        if script.phase == "bypass":
            _bypass(engine, decision)
            script.phase = "after-bypass"
        else:
            person.bind_decision_context(decision)
            await person.step(0, decision.observed_at)
        results.extend(await environment.commit_decision_round(decision.round_id))
    return results


def _errand_rows(request: dict) -> list[dict]:
    """模型在这次请求里看到的全部事务行：链首待办层的行与各续接 tool 消息 changes.errands.added 的行。"""
    rows = list(json.loads(request["messages"][1]["content"])[3]["errands"])
    for message in request["messages"][2:]:
        payload = _json(message["content"]) if message["role"] == "tool" else None
        if isinstance(payload, dict) and "result" in payload:
            rows += payload.get("changes", {}).get("errands", {}).get("added", [])
    return rows


def _blank_rejected(result: dict) -> None:
    """被拒结果明细里人物所写的内容（items[].written）置空：规格 2.6 的第三处回显位置。"""
    if result.get("status") == "rejected":
        for item in result["details"].get("items", []):
            item["written"] = None


def _blank_echoes(layer: dict) -> None:
    """进度层里照原始证据回显所写内容的两处置空：当日操作备忘的 written 与 accepted_as，被拒结果明细的 written。"""
    for row in layer["rejected_today"]:
        row["written"] = row["accepted_as"] = None
    for record in layer["events"]:
        if record["type"] == "tool_call":
            _blank_rejected(record["result"])


def _outside(request: dict, strip: bool = True) -> list:
    """待办层事务行之外呈现给模型的各段（能解析的按 JSON）：system、原生 tools、链首六层（待办层的 errands 不算）、
    其后各回合 assistant 的正文与思考、每条 tool 消息。strip 为真时去掉规格 2.6 的三处回显位置且只去掉它们：同秒续接中
    上一决策带回的调用参数（assistant 的 tool_calls；与上面的唯一出现检查同一口径，本决策纠错回合里模型自己调用的原样
    回放同样不是环境呈现的信息）与 tool 消息里的改动（changes.errands），进度层当日操作备忘的 written 与 accepted_as，
    被拒结果明细的 written。strip 为假时这三处都算，用来证明例外确实被用到。"""
    messages = copy.deepcopy(request["messages"])
    layers = json.loads(messages[1]["content"])
    layers[3]["errands"] = []
    if strip:
        _blank_echoes(layers[5])
    parts = [_json(messages[0]["content"]), request["tools"], *layers]
    for message in messages[2:]:
        if message["role"] == "assistant":
            parts.extend(message.get(key) or "" for key in ("content", "reasoning_content"))
            if not strip:
                parts.extend(call["function"]["arguments"] for call in message.get("tool_calls") or [])
            continue
        payload = _json(message["content"])
        if strip and isinstance(payload, dict) and "result" in payload:
            payload.get("changes", {}).pop("errands", None)
            _blank_rejected(payload["result"])
        parts.append(payload)
    return parts


def _row_leaks(request: dict, names: list[str], strip: bool = True) -> list[str]:
    """事务完整行的内容（要办成什么、已办到哪、备注）出现在待办层之外的每一处，以及事务名不作为 errand 键（进度层两种
    记录与回执）出现的每一处。"""
    values = {row[key] for row in _errand_rows(request) for key in ("done_when", "progress", "memo") if row[key]}
    found = []
    for part in _outside(request, strip):
        text = part if isinstance(part, str) else _compact(part)
        found += [value for value in sorted(values) if value in text]
        found += [name for name in names for holder in _holders(part, name) if holder != "errand"]
    return found


@pytest.mark.asyncio
async def test_an_errand_full_row_appears_only_in_the_todo_layer(_engine_factory, tmp_path) -> None:
    """个人事务的完整行只在待办层（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 「唯一出现的口径」）：真实 LifeEngine、SQLite、LifeEnv
    与从工作区装配、启用记忆的 LifeAgent，单人白领 12:30 在家、手边一盒纯牛奶。陈曦开始等待；12:31 邮件来时播报并记下
    「吃掉纯牛奶」的事务，备注超过 120 字被宿主就地纠正后连同另两条一起记下（新增带备注）；12:45 等待结束时收尾，更新食材包
    的备注、取消冰红茶；同一秒续接开始喝奶；12:50 邮件来时一次绕过宿主的请求把食材包备注写成超长，环境整请求拒绝；随后
    播报喝奶，13:00 喝完那一刻事务办成（进度层出现只写名称的办成记录），收尾后再开始等待。对陈曦的每一次行动请求（续接
    与非续接）检查：模型所见每条事务行的要办成什么、已办到哪与备注只在待办层（或续接 changes 里替换它的新行）出现；
    事务名在别处只作为进度层办成记录与回执的 errand 键出现；规格 2.6 的三处回显位置——同秒续接中上一决策的调用参数与
    tool 消息里的改动、当日操作备忘的 written 与 accepted_as、被拒结果明细的 written——之外再没有它们；不去掉这三处时
    确实会查到（例外被用到，检查不空转）。每个请求照常通过唯一出现与人读写法的既有检查（错误路径里的 errands 下标被
    接受），整次请求里没有事务身份、事务的商品身份与两类私有绑定键。"""
    engine = home(_engine_factory, {MILK_ID: 1.0})
    wake_by_mail(engine, offset=60)
    wake_by_mail(engine, offset=1200)
    environment, script = LifeEnv(), _ErrandScript()
    environment.engine = engine
    workspace = tmp_path / "agent_0001"
    LifeAgent.create(workspace, {"id": 1, "name": engine.context.state.agents[1].name, "occupation": "engineer"},
                     {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 8})
    service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=script), trace=None)
    person = await LifeAgent.from_workspace(workspace, service)
    try:
        results = await _errand_run(environment, person, script)
        [rejected] = [result for result in results if result.error_code is not None]
        assert rejected.error_code == "ACTION_REQUEST_INVALID" and rejected.data["items"][0]["written"] == _BYPASSED_MEMO
        events = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") in ERRAND_EVENT_KINDS]
        kinds = [event.data["kind"] for event in events]
        assert kinds.count("errand_registered") == 3 and kinds.count("errand_completed") == 1
        assert (kinds.count("errand_updated"), kinds.count("errand_cancelled")) == (1, 1)
        corrected = [event.data["protocol_feedback"] for event in engine.context.store.read_agent_events("model_response", 1)
                     if event.data.get("protocol_feedback")]
        assert [item["path"][-3:] for report in corrected for item in report["items"]] == [["errands", 0, "memo"]]
        names = [_DRINK_ERRAND, _KIT_ERRAND, _TEA_ERRAND]
        identities = _identities(engine) | {value for event in events
                                            for value in (event.data["errand"]["errand_id"],
                                                          event.data["errand"]["product_id"])}
        assert KIT in identities and MILK_ID in identities
        for request in script.requests:
            _assert_once_each(request)
            _assert_readable(request, identities)
            assert _row_leaks(request, names) == [], _observed_at(request)
            whole = "".join(_presented(request))
            assert not [key for key in ("errand_bindings", "errand_item_bindings", "errand_id") if key in whole]
        echoed = [request for request in script.requests if _row_leaks(request, names, strip=False)]
        assert {len(request["messages"]) > 2 for request in echoed} == {True, False}
        heads = [json.loads(request["messages"][1]["content"]) for request in script.requests]
        assert any(row["accepted_as"] == _DRINK_MEMO and _DRINK_MEMO in row["written"]
                   for layers in heads for row in layers[5]["rejected_today"])
        assert any(item["written"] == _BYPASSED_MEMO and _KIT_MEMO_LATER in [row["memo"] for row in layers[3]["errands"]]
                   for layers in heads for record in layers[5]["events"] if record["type"] == "tool_call"
                   for item in record["result"]["details"].get("items", []))
        assert any("errands" in payload.get("changes", {}) for request in script.requests
                   for payload in (_json(message["content"]) for message in request["messages"][2:]
                                   if message["role"] == "tool") if isinstance(payload, dict) and "result" in payload)
        assert any(record == {"occurred_at": record["occurred_at"], "type": "errand_completed", "errand": _DRINK_ERRAND}
                   for layers in heads for record in layers[5]["events"])
    finally:
        engine.close()
