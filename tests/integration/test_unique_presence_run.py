"""唯一出现原则的性质测试（docs/dev/SPEC-persona-prompt.md 第一章「唯一出现原则」「通用格式」、验收台账「唯一出现」）。

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
"""

from __future__ import annotations

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

from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.agents.life_continuation import _earlier_changes, _overlay, _view  # noqa: E402
from custom.agents.life_persona import persona_layer  # noqa: E402
from custom.agents.life_prompt import life_user_layers, system_message, user_message  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.integration.test_life_agent_protocol import (_ModelResponse, _TextResponse, _conclude_args,  # noqa: E402
                                                        _memory, _stored_memories, _view as _opinion)
from tests.life.conftest import context, engine_factory as _engine_factory, stage_day_plan  # noqa: E402,F401
from tests.life.routing_fixtures import stage  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
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
        """保存请求；行动请求按剧本返回一个工具调用，记忆运行时的纯文本请求返回一句话。"""
        if "tools" not in request:
            return _TextResponse("我按眼前的情况决定接下来做什么。")
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
    """控制工具参数：一项一个对象、写同一句自述；结束工具每项另带空的普通记忆，看法只写在第一项。"""
    rows = []
    for index, name in enumerate(names):
        row: dict = {"activity_name": name, "summary": summary}
        if tool == "conclude_activity":
            row.update(memories=[], views=list(views or []) if index == 0 else [])
        rows.append(row)
    return {"items": rows}


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
    return {"targets": [name], "actions": [verb], "ways": [way], "from": [None], "to": [None], "answering": None}


def _engine(engine_factory):
    """双人场景副本加一封 10:20 给陈曦的邮件；两人登记当日空计划，世界置于 1 月 5 日 10:00，陈曦的当日任务可见。"""
    def mutate(document: dict) -> None:
        """@brief 追加邮件（沿用首封邮件的其余字段）。"""
        document["messages"].append({**document["messages"][0], **_MAIL})

    engine = engine_factory("multi-person.json", mutate=mutate)
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id, submitted=True)
    now = int(datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo(engine.scenario.timezone)).timestamp())
    task = next(item.task_id for item in engine.scenario.tasks if item.task_id.startswith("task-"))

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
        name = environment.engine.state.agents[agent_id].name
        LifeAgent.create(workspace, {"id": agent_id, "name": name, "occupation": "engineer"},
                         {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 8})
        service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=model), trace=None)
        people[agent_id] = await LifeAgent.from_workspace(workspace, service)
    earlier = datetime(2026, 1, 4, 19, 30, tzinfo=ZoneInfo(environment.engine.scenario.timezone))
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
            await environment.step(int(scheduling.next_event_at.timestamp()) - engine.state.current_time,
                                   scheduling.next_event_at)
            continue
        rounds += 1
        assert rounds < 60, (engine.state.current_time, models[1].phase)
        contexts = [context(engine, f"unique-{rounds}", agent_id) for agent_id in scheduling.ready_agent_ids]
        await environment.begin_decision_round(tuple(contexts))
        for decision in contexts:
            people[decision.agent_id].bind_decision_context(decision)
            await people[decision.agent_id].step(0, decision.observed_at)
        results = await environment.commit_decision_round(contexts[0].round_id)
        assert all(result.error_code is None for result in results), results
        done += models[1].phase == "done" and 1 in scheduling.ready_agent_ids


#: 续接 changes 中可按名称取代链首条目的部分：（类别，changes 键，写入键，键字段）。
_KEYED_CHANGES = (("target", "targets", "updated", "name"), ("paused", "paused_activities", "added", "name"))
#: 链首六层中按名称呈现的条目：层名 → [(类别，列表键，键字段)]。
_KEYED_LAYERS = {"工具层": (("target", "targets", "name"),), "待办层": (("paused", "paused_activities", "name"),),
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
    for layer in json.loads(messages[1]["content"]):
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
    state, scenario = engine.state, engine.scenario
    names = {*state.activities, *(item.supply_id for item in scenario.supplies), *state.deliveries,
             *(item.message_id for item in scenario.messages), *(item.task_id for item in scenario.tasks),
             *(contact.contact_id for agent in scenario.agents for contact in agent.contacts),
             *(item.location_id for item in scenario.locations if "-" in item.location_id)}
    for agent in state.agents.values():
        names.update(agent.inbox)
    return names | {f"agent:{agent_id}" for agent_id in state.agents}


#: 允许为数值的字段：人设层的年龄（Schema 为整数）与记忆查询结果的条数（第一章「进度层」：记忆查询的 result 只写条数）。
_NUMBERS = {("identity", "age"), ("details", "episodes")}


def _scan(value, path: tuple = ()) -> None:
    """层与 tool 消息 JSON 的形状扫描：没有 _seconds、_fen、_id、_ids 结尾或名为 id 的键；数值只有年龄与记忆查询条数；
    字符串不是纯数字，不写「N 秒」，数字至多两位小数（readable 的口径）。进度层调用记录的 arguments 是模型自己按原生
    工具 Schema 写下的参数，原样保留，不扫。"""
    if isinstance(value, dict):
        for key, item in value.items():
            assert key != "id" and not re.search(r"_(seconds|fen|ids?)$", key), path + (key,)
            if key != "arguments":
                _scan(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan(item, path + (index,))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        assert path[-2:] in _NUMBERS, (path, value)
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
    task = next(target for target in json.loads(request["messages"][1]["content"])[0]["targets"]
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

    _assert_once_each(continued({"result": {"details": {}}, "changes": {"targets": {"updated": [task]}}}))
    _assert_once_each(continued({"result": {"occurred_at": task["contract"]["deadline"], "details": {}}}))
    for payload in ({"result": {"details": {"contract": remaining}}},
                    {"result": {"details": {"note": f"截止 {task['contract']['deadline']}"}}}):
        with pytest.raises(AssertionError):
            _assert_once_each(continued(payload))
    product = {"name": "集市出售的鸡汤面", "kind": "product", "phase": None, "state": None, "actions": [], "tools": []}
    shop = _with(request, lambda layers: layers[0]["targets"].append(product))
    referenced = {"type": "tool_call", "tool": "start_activity", "arguments": {"targets": [product["name"]]}}
    _assert_once_each(_with(shop, lambda layers: layers[5]["events"].append(referenced)))
    with pytest.raises(AssertionError):
        _assert_once_each(_with(shop, lambda layers: layers[5]["events"].append(
            {"type": "tool_call", "result": {"details": {"items": [{"name": product["name"], "price": "8 元"}]}}})))
    target = next(item for item in json.loads(request["messages"][1]["content"])[0]["targets"] if item["actions"])
    priced = {**target, "actions": [{**target["actions"][0], "price": 3200}]}
    for broken in ({**target, "duration_seconds": 720}, priced, {**target, "state": "3600"}, {**target, "state": "约 30 秒"},
                   {"contract": {**task["contract"], "remaining": "有效工时还差约 1 分钟（约 0/0.0166667 小时）"}},
                   {"result": {"details": {"order_id": "order-1"}}}):
        with pytest.raises(AssertionError):
            _scan(broken)
    with pytest.raises(AssertionError):
        _assert_readable(continued({"result": {"details": {"note": f"见 {engine.scenario.tasks[0].task_id}"}}}),
                         identities)
