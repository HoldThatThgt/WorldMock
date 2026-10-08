"""@brief 内容选择优先级、系列单元、预留例外与确定性的纯所有者证据。"""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from tests.catalog.conftest import read_payload, write_payload
from worldmock.catalog.affordances import engine_affordances
from worldmock.catalog.loader import load_catalog
from worldmock.errors import BusinessRuleError
from worldmock.life.content_select import (
    ContentRequest, content_duration_range, eligible_contents, required_work_location, select_content,
)
from worldmock.life.models import (
    ActivityState, AgentState, AppPush, BodyState, ContentProgress, SessionState, WorldState,
)
from worldmock.life.randomness import random_stream
from worldmock.life.scenario import parse_scenario_document
from worldmock.life.semantics import assemble_semantics

ENVIRONMENT = Path(__file__).resolve().parents[3] / 'tests' / "fixtures/environment"


class CountingRandom(Random):
    """@brief 记录一次选择实际使用的抽取次数与候选数量。"""

    def __init__(self, selected=0):
        super().__init__(7)
        self.selected = selected
        self.bounds = []

    def randrange(self, stop):
        self.bounds.append(stop)
        return self.selected % stop


def registered_content_world(catalog_dir=None):
    """@brief 生产严格目录与场景模型形成纯世界，无引擎或 SQLite。"""
    document, digest = parse_scenario_document(ENVIRONMENT / "scenarios/multi-person.json")
    catalog = load_catalog(catalog_dir or ENVIRONMENT / "catalog")
    semantics = assemble_semantics(document, catalog)
    now = int(semantics.scenario.start_at.timestamp())
    agents = {person.agent_id: AgentState(
        agent_id=person.agent_id, name=person.name, location_id=person.home_location_id,
        balance_account_id=f"agent:{person.agent_id}", driving_days=person.initial_driving_days,
        last_driving_date=None, body=BodyState(energy_kcal=2000, entertainment_need=0.5),
        body_settled_at=now, next_decision_at=now) for person in semantics.scenario.agents}
    sessions = {f"stay-{person.agent_id}": SessionState(
        session_id=f"stay-{person.agent_id}", agent_id=person.agent_id, kind="stay", status="active",
        location_id=person.home_location_id, started_at=now, name=f"Stay {person.agent_id}")
        for person in semantics.scenario.agents}
    state = WorldState(schema_version=11, version=0, current_time=now,
        end_time=int(semantics.scenario.end_at.timestamp()), run_id="selector", timezone=document.timezone,
        scenario_sha256=digest, agents=agents, accounts=dict(semantics.accounts), sessions=sessions,
        random_seed=7, random_state="selector-fixed")
    return semantics, state


@pytest.fixture
def content_world():
    """@brief 每个测试独立加载实际发布的人工目录。"""
    return registered_content_world()


def progress(state, identity, position=0, rounds=0, last=100, agent_id=1):
    """@brief 以严格状态模型声明已经实际消费过的内容历史。"""
    state.agents[agent_id].content_progress[identity] = ContentProgress(
        content_id=identity, position_seconds=position, completed_rounds=rounds, last_consumed_at=last)


def push(state, identity, app="bilibili", status="pending", agent_id=1):
    """@brief 当前会话的一条真实推送状态，送达本身不写消费历史。"""
    state.sessions[f"stay-{agent_id}"].app_pushes[app] = AppPush(
        push_id=f"p:{app}:{identity}", content_id=identity, issued_at=state.current_time, status=status)


def reserve(semantics, state, identity, status="paused", activity_id="other", agent_id=1):
    """@brief 已登记动作的进行中或暂停活动保存所选内容，预留从该状态派生。"""
    item = semantics.catalog.contents[identity]
    action = semantics.catalog.actions[item.action_id]
    app = item.app_ids[0] if item.app_ids else None
    now = state.current_time
    activity = ActivityState(activity_id=activity_id, agent_id=agent_id, kind="action", status=status,
        location_id=state.agents[agent_id].location_id, started_at=now, ended_at=now if status == "completed" else None,
        planned_end_at=now + item.duration_seconds, last_settled_at=now, elapsed_seconds=0,
        planned_duration_seconds=item.duration_seconds, origin_action_id="a", origin_decision_id="d",
        origin_request_event_id="r", session_ids=[f"stay-{agent_id}"],
        resource_claims=set(engine_affordances().resource_claims("action", category=action.category, manner=action.manner)),
        data={"action_id": action.action_id, "category": action.category, "app_id": app, "manner": action.manner, "communication_mode": action.communication_mode,
              "counterpart": None, "content_id": identity, "start_position_seconds": 0,
              "pinned_by_push_id": None, "draw_index": 0, "played": []})
    state.activities[activity_id] = activity
    state.sessions[f"stay-{agent_id}"].activity_ids.append(activity_id)


def choose(semantics, state, request, stream=None):
    """@brief 经公开选择入口读取真实目录和世界，返回内容身份与推送身份。"""
    item, pinned = select_content(semantics.catalog, semantics.scenario, state, request,
                                  stream if stream is not None else random_stream(state.random_state, "activity:0"))
    return item.content_id, pinned


@pytest.mark.parametrize("status", ["pending", "opened"])
def test_same_app_action_push_precedes_history_without_mutating_status(content_world, status):
    """@brief 同动作应用的推送优先于最近未完成内容，选择只读且不执行打开。"""
    semantics, state = content_world
    progress(state, "c-long", position=30, last=200)
    push(state, "c-article", status=status)
    request = ContentRequest(1, "browse-feed", "bilibili", "activity")
    before = state.model_dump(mode="json")
    stream = CountingRandom()
    assert choose(semantics, state, request, stream) == ("c-article", "p:bilibili:c-article")
    assert stream.bounds == []
    assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("selection_request", [ContentRequest(1, "browse-feed", "douyin", "activity"),
                                     ContentRequest(1, "study-course", "bilibili", "activity"),
                                     ContentRequest(1, "browse-feed", "bilibili", "activity", "c-long"),
                                     ContentRequest(1, "browse-feed", "bilibili")])
def test_push_is_not_adopted_by_other_app_action_continuation_or_generation(content_world, selection_request):
    """@brief 推送不跨应用或动作，不在娱乐接续或生成推送时被打开。"""
    semantics, state = content_world
    push(state, "c-article")
    identity, pinned = choose(semantics, state, selection_request)
    assert pinned is None and identity != "c-article"
    assert state.sessions["stay-1"].app_pushes["bilibili"].status == "pending"


def test_released_and_ended_session_pushes_do_not_pin_content(content_world):
    """@brief 完成解除与会话结束都失去推送资格，消费历史继续可用。"""
    semantics, state = content_world
    progress(state, "c-long", position=20, last=200)
    push(state, "c-article", status="released")
    request = ContentRequest(1, "browse-feed", "bilibili", "activity")
    assert choose(semantics, state, request) == ("c-long", None)
    state.sessions["stay-1"].status = "ended"
    state.sessions["stay-1"].ended_at = state.current_time
    state.sessions["stay-1"].app_pushes["bilibili"].status = "pending"
    assert choose(semantics, state, request) == ("c-long", None)


@pytest.mark.parametrize("equal,expected", [(False, "c-long"), (True, "c-live")])
def test_most_recent_unfinished_content_precedes_unseen_and_ties_by_identity(content_world, equal, expected):
    """@brief 未完成内容先续最近消费的，并列时取最小身份，而不是随机未看内容。"""
    semantics, state = content_world
    progress(state, "c-live", position=10, last=200 if equal else 100)
    progress(state, "c-long", position=20, last=200)
    stream = CountingRandom()
    assert choose(semantics, state, ContentRequest(1, "browse-feed", "douyin", "activity"), stream) == (expected, None)
    assert stream.bounds == []


def test_unfinished_content_precedes_started_series(content_world):
    """@brief 继续观看先于已经追到下一集的系列。"""
    semantics, state = content_world
    progress(state, "c-drama-1", rounds=1, last=300)
    progress(state, "c-long", position=20, last=100)
    assert choose(semantics, state, ContentRequest(1, "browse-feed", "bilibili", "activity")) == ("c-long", None)


@pytest.mark.parametrize("rounds,next_episode", [(0, "c-drama-2"), (1, "c-drama-2"), (3, "c-drama-2")])
def test_started_series_next_episode_precedes_unseen_units(content_world, rounds, next_episode):
    """@brief 每轮最低未完成集先于未看独立内容，重播系列也保持集数顺序。"""
    semantics, state = content_world
    for episode in range(1, 4):
        progress(state, f"c-drama-{episode}", rounds=rounds + (episode == 1), last=100 + episode)
    stream = CountingRandom()
    assert choose(semantics, state, ContentRequest(1, "browse-feed", "bilibili", "activity"), stream) == (
        next_episode, None)
    assert stream.bounds == []


def test_multiple_started_series_choose_recent_then_representative_identity(catalog_dir):
    """@brief 多部追看系列按最近消费排序，同秒按代表内容身份排序。"""
    payload = read_payload(catalog_dir, "contents.json")
    original = next(row for row in payload["contents"] if row["content_id"] == "c-drama-1")
    for episode in range(1, 3):
        payload["contents"].append({**original, "content_id": f"z-drama-{episode}",
                                    "series": {"title": "第二测试剧集", "episode": episode}})
    payload["contents"].sort(key=lambda row: row["content_id"])
    write_payload(catalog_dir, "contents.json", payload)
    semantics, state = registered_content_world(catalog_dir)
    progress(state, "c-drama-1", rounds=1, last=100)
    progress(state, "z-drama-1", rounds=1, last=200)
    request = ContentRequest(1, "browse-feed", "bilibili", "activity")
    assert choose(semantics, state, request) == ("z-drama-2", None)
    progress(state, "z-drama-1", rounds=1, last=100)
    assert choose(semantics, state, request) == ("c-drama-2", None)


def test_unseen_selection_samples_units_once_not_each_episode(content_world):
    """@brief 三集系列只占一个抽取单元，不因集数多而加大首次被选概率。"""
    semantics, state = content_world
    stream = CountingRandom(selected=1)
    request = ContentRequest(1, "browse-feed", "bilibili", "activity")
    assert choose(semantics, state, request, stream) == ("c-drama-1", None)
    assert stream.bounds == [4]


def test_delivered_push_without_consumption_remains_unseen(content_world):
    """@brief 推送送达但未消费时仍为未看，不创建内容历史。"""
    semantics, state = content_world
    push(state, "c-long", status="released")
    stream = CountingRandom()
    assert choose(semantics, state, ContentRequest(1, "browse-feed", "bilibili"), stream) == ("c-article", None)
    assert stream.bounds == [4]
    assert state.agents[1].content_progress == {}


def test_unseen_units_precede_seen_incomplete_rounds(content_world):
    """@brief 消费过但还没有完成轮数的内容不能抢在从未看过的单元之前。"""
    semantics, state = content_world
    progress(state, "c-live", last=100)
    stream = CountingRandom()
    result, _ = choose(semantics, state, ContentRequest(1, "browse-feed", "douyin"), stream)
    assert result in {"c-long", "c-short", "c-short-2"}
    assert stream.bounds == [3]


def test_exhausted_content_uses_minimum_rounds_then_uniform_tie(content_world):
    """@brief 全部看过之后只从轮数最少的内容抽取，真实轮数不由选择器增加。"""
    semantics, state = content_world
    for identity, rounds in (("c-live", 3), ("c-long", 2), ("c-short", 1), ("c-short-2", 1)):
        progress(state, identity, rounds=rounds)
    stream = CountingRandom(selected=1)
    assert choose(semantics, state, ContentRequest(1, "browse-feed", "douyin"), stream) == ("c-short-2", None)
    assert stream.bounds == [2]
    assert state.agents[1].content_progress["c-short-2"].completed_rounds == 1


@pytest.mark.parametrize("status", ["active", "paused"])
def test_other_running_or_paused_activity_reserves_content_when_alternative_exists(content_world, status):
    """@brief 其他活动选中的未完成内容只在无替代项时才允许再次抽到。"""
    semantics, state = content_world
    progress(state, "c-long", position=20, last=100)
    reserve(semantics, state, "c-long", status=status)
    assert choose(semantics, state, ContentRequest(1, "browse-feed", "douyin", "activity"))[0] != "c-long"


def test_current_activity_is_not_its_own_reservation(content_world):
    """@brief 接续派生预留时排除正在调用选择器的活动自身。"""
    semantics, state = content_world
    progress(state, "c-long", position=20, last=100)
    reserve(semantics, state, "c-long", activity_id="activity")
    assert choose(semantics, state, ContentRequest(1, "browse-feed", "douyin", "activity", "c-short")) == (
        "c-long", None)


def test_push_reservation_is_skipped_without_opening_during_continuation(content_world):
    """@brief 接续跳过仍待消费的推送，已释放后恢复可选，不执行推送状态变化。"""
    semantics, state = content_world
    progress(state, "c-long", position=20, last=100)
    push(state, "c-long", app="douyin")
    request = ContentRequest(1, "browse-feed", "douyin", "activity", "c-short")
    assert choose(semantics, state, request)[0] != "c-long"
    state.sessions["stay-1"].app_pushes["douyin"].status = "released"
    assert choose(semantics, state, request) == ("c-long", None)


def test_reserved_series_representative_excludes_the_whole_series(content_world):
    """@brief 第一集被预留时整部剧退出该次候选，不跳到第二集。"""
    semantics, state = content_world
    reserve(semantics, state, "c-drama-1")
    stream = CountingRandom(selected=1)
    identity, _ = choose(semantics, state, ContentRequest(1, "browse-feed", "bilibili", "activity"), stream)
    assert identity in {"c-article", "c-image", "c-long"}
    assert stream.bounds == [3]


def test_reservations_then_recent_completion_exclusions_are_applied_in_order(content_world):
    """@brief 先排除预留，再排除刚完成内容；各步空集时分别保留前一步集合。"""
    semantics, state = content_world
    for identity in ("c-live", "c-long", "c-short-2"):
        reserve(semantics, state, identity, activity_id=f"paused-{identity}")
    request = ContentRequest(1, "browse-feed", "douyin", "activity", "c-short")
    assert choose(semantics, state, request) == ("c-short", None)


def test_just_completed_content_is_skipped_when_another_content_exists(content_world):
    """@brief 接续有其他合格内容时不立即重播刚完成内容，即使它轮数更低。"""
    semantics, state = content_world
    for identity in ("c-live", "c-long", "c-short", "c-short-2"):
        progress(state, identity, rounds=0 if identity == "c-long" else 2)
    request = ContentRequest(1, "browse-feed", "douyin", "activity", "c-long")
    assert choose(semantics, state, request)[0] != "c-long"


def test_only_eligible_unit_can_be_reserved_and_immediately_replayed(content_world):
    """@brief 只有一条合格内容时，预留和刚完成排除都不能制造无内容状态。"""
    semantics, state = content_world
    reserve(semantics, state, "c-rest")
    progress(state, "c-rest", rounds=1)
    request = ContentRequest(1, "quiet-rest", None, "activity", "c-rest")
    assert choose(semantics, state, request) == ("c-rest", None)


def test_progress_is_shared_between_apps_and_isolated_between_people(content_world):
    """@brief 同一人物跨应用续同一个内容，另一人物仍从自己的未看单元抽取。"""
    semantics, state = content_world
    progress(state, "c-long", position=100, last=200)
    for app in ("douyin", "bilibili"):
        assert choose(semantics, state, ContentRequest(1, "browse-feed", app, "activity")) == ("c-long", None)
    stream = CountingRandom()
    assert choose(semantics, state, ContentRequest(2, "browse-feed", "douyin", "activity"), stream) == ("c-live", None)
    assert stream.bounds == [4]
    reserve(semantics, state, "c-live", agent_id=1)
    assert choose(semantics, state, ContentRequest(2, "browse-feed", "douyin", "activity"), CountingRandom()) == (
        "c-live", None)


def test_assigned_work_is_owner_only_location_bound_and_finite(content_world):
    """@brief 指派工作只对本人在工作地点合格，完成后彻底耗尽而不是进入重播轮。"""
    semantics, state = content_world
    request = ContentRequest(1, "office-work", None, "activity")
    assert eligible_contents(semantics.catalog, semantics.scenario, state, request) == ()
    assert required_work_location(semantics.catalog, semantics.scenario, state, request) == "office"
    state.agents[1].location_id = "office"
    state.sessions["stay-1"].location_id = "office"
    assert choose(semantics, state, request) == ("topic-sales", None)
    assert required_work_location(semantics.catalog, semantics.scenario, state, request) is None
    other = replace(request, agent_id=2)
    assert eligible_contents(semantics.catalog, semantics.scenario, state, other) == ()
    assert required_work_location(semantics.catalog, semantics.scenario, state, other) is None
    progress(state, "topic-sales", rounds=1)
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        choose(semantics, state, request)
    assert required_work_location(semantics.catalog, semantics.scenario, state, request) is None


def test_transit_does_not_count_as_presence_at_work_location(content_world):
    """@brief 出发地是公司也不能把途中状态当作仍在工作地点。"""
    semantics, state = content_world
    state.agents[1].location_id = "office"
    state.sessions["stay-1"] = SessionState(session_id="stay-1", agent_id=1, kind="transit", status="active",
        origin_id="office", destination_id="home", started_at=state.current_time, name="Transit")
    request = ContentRequest(1, "office-work", None, "activity")
    assert required_work_location(semantics.catalog, semantics.scenario, state, request) == "office"
    assert eligible_contents(semantics.catalog, semantics.scenario, state, request) == ()


def test_unassigned_work_content_can_repeat_anywhere_for_any_person(content_world):
    """@brief 未指派课程完成后对双方仍可重复，无工作地点要求。"""
    semantics, state = content_world
    for agent_id in (1, 2):
        progress(state, "c-course", rounds=4, agent_id=agent_id)
        request = ContentRequest(agent_id, "study-course", "bilibili", "activity")
        assert choose(semantics, state, request) == ("c-course", None)
        assert required_work_location(semantics.catalog, semantics.scenario, state, request) is None


@pytest.mark.parametrize("selection_request,expected", [
    (ContentRequest(1, "browse-feed", "douyin"), (90, 2400)),
    (ContentRequest(1, "browse-feed", "bilibili"), (300, 2400)),
    (ContentRequest(1, "quiet-rest", None), (900, 900)),
    (ContentRequest(1, "office-work", None), None),
])
def test_content_duration_range_uses_full_eligible_lengths_without_drawing(content_world, selection_request, expected):
    """@brief 没有可续推送或未完成内容时只展示合格全长范围，不产生选择或消费记录。"""
    semantics, state = content_world
    before = state.model_dump(mode="json")
    assert content_duration_range(semantics.catalog, semantics.scenario, state, selection_request) == expected
    assert state.model_dump(mode="json") == before


def test_content_duration_range_prioritizes_pinned_then_recent_remaining(content_world):
    """@brief 工具行无需活动身份就能显示推送余量；释放后改显示最近未完成余量。"""
    semantics, state = content_world
    progress(state, "c-article", position=599.5, last=100)
    progress(state, "c-long", position=100, last=200)
    push(state, "c-article")
    request = ContentRequest(1, "browse-feed", "bilibili")
    assert content_duration_range(semantics.catalog, semantics.scenario, state, request) == (1, 1)
    state.sessions["stay-1"].app_pushes["bilibili"].status = "released"
    assert content_duration_range(semantics.catalog, semantics.scenario, state, request) == (2300, 2300)


def test_selection_is_stable_under_mapping_order_and_does_not_advance_world_randomness(content_world):
    """@brief 同键选择可重复，字典插入顺序与另一人物历史不改变结果或任何输入状态。"""
    semantics, state = content_world
    progress(state, "c-long", rounds=1)
    request = ContentRequest(1, "browse-feed", "douyin", "activity")
    before = state.model_dump(mode="json")
    first = choose(semantics, state, request, random_stream(state.random_state, "activity:0"))
    second = choose(semantics, state, request, random_stream(state.random_state, "activity:0"))
    assert first == second and state.model_dump(mode="json") == before
    reversed_state = state.model_copy(deep=True)
    reversed_state.agents = dict(reversed(list(reversed_state.agents.items())))
    reversed_state.sessions = dict(reversed(list(reversed_state.sessions.items())))
    reversed_catalog = replace(semantics.catalog, contents=dict(reversed(list(semantics.catalog.contents.items()))))
    selected, _ = select_content(reversed_catalog, semantics.scenario, reversed_state, request,
                                 random_stream(reversed_state.random_state, "activity:0"))
    assert selected.content_id == first[0]
    assert reversed_state.random_state == state.random_state


def test_selector_import_and_cross_process_results_are_hash_seed_independent():
    """@brief 真正新进程的选择结果不依赖 Python 散列种子，也不导入 LifeEngine。"""
    script = """import json, sys
from tests.integration.life.test_content_select import registered_content_world
from worldmock.life.content_select import ContentRequest, select_content
from worldmock.life.randomness import random_stream
semantics, state = registered_content_world()
request = ContentRequest(1, 'browse-feed', 'bilibili', 'activity')
results = [select_content(semantics.catalog, semantics.scenario, state, request,
           random_stream(state.random_state, f'activity:{draw}'))[0].content_id for draw in range(12)]
assert 'worldmock.life.engine' not in sys.modules
print(json.dumps(results))
"""
    results = []
    for seed in ("1", "888"):
        environment = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=os.pathsep.join(("src", ".")))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=environment, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        results.append(json.loads(result.stdout))
    assert results[0] == results[1]
