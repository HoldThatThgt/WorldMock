"""地点内容单元：同类地点下单元名唯一的登记检查、单元的排序键、下一条内容与动作行的时长和说明、被结束时记为做完一轮。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog, validate_catalog_files
from worldmock.catalog.models import Location
from worldmock.life.location_activities import (
    held_refusal, location_units, next_content, unit_binding, unit_done, unit_facts,
)
from worldmock.life.models import ContentProgress

_SOURCE = {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}


def _content(content_id: str, title: str, seconds: int = 600, **binding) -> dict:
    """一行地点或 app 内容（缺省绑定到家）。"""
    row = {"content_id": content_id, "duration_seconds": seconds, "exertion": "light", "pushable": False,
           "relaxation_relief": "none", "social_relief": "none", "summary": f"{title}的测试用合成摘要。",
           "summary_source": dict(_SOURCE), "title": title}
    return {**row, **(binding or {"location_kinds": ["home"]})}


def _episode(content_id: str, title: str, number: int, seconds: int, series: str = "工作日晚饭", **binding) -> dict:
    """系列的一集。"""
    return {**_content(content_id, title, seconds, **binding), "series": {"episode": number, "title": series}}


#: 提案例子：系列「工作日晚饭」三集，以及「洗漱」「睡前准备」两条独立内容，都绑定到家。
_DINNER = (_episode("d-1-prep", "备菜", 1, 1200), _episode("d-2-eat", "吃饭", 2, 1500),
           _episode("d-3-wash", "洗碗", 3, 600), _content("e-wash", "洗漱", 300), _content("f-bed", "睡前准备", 900))


def _catalog_dir(tmp_path, extra=(), drop=("c-chat", "c-rest")):
    """夹具目录的副本：去掉给定内容、加入额外行，按身份升序重写并刷新清单。"""
    root = copy_environment(tmp_path / "environment")
    rows = [row for row in catalog_rows(root, "content-catalog.jsonl") if row["content_id"] not in drop]
    write_catalog_rows(root, "content-catalog.jsonl", sorted([*rows, *extra], key=lambda row: row["content_id"]))
    return root / "catalog"


def _line(directory, content_id: str) -> int:
    """内容在 content-catalog.jsonl 里的行号（从 1 起）。"""
    rows = catalog_rows(directory.parent, "content-catalog.jsonl")
    return [row["content_id"] for row in rows].index(content_id) + 1


@pytest.fixture
def person(engine_factory):
    """按给定共享进度（内容身份到已做位置与完成轮数）得到夹具人物的状态。"""
    base = engine_factory().context.state.agents[1]

    def with_progress(progress: dict[str, tuple[float, int]]):
        records = {key: ContentProgress(content_id=key, position_seconds=position, completed_rounds=rounds,
                                        last_consumed_at=1) for key, (position, rounds) in progress.items()}
        return base.model_copy(update={"content_progress": records}, deep=True)

    return with_progress


@pytest.mark.parametrize("extra,first,second,name", [
    ((_content("c-rest-2", "测试 休息"),), "c-rest", "c-rest-2", "测试休息"),
    ((_content("c-rest-2", "测试休息"),), "c-rest", "c-rest-2", "测试休息"),
    ((_content("x-upper", "Tea Time"), _content("y-lower", "tea  time")), "x-upper", "y-lower", "Tea Time"),
    ((_episode("x-1", "第一集", 1, 600, "测试休息"),), "c-rest", "x-1", "测试休息"),
    ((_episode("a-1", "第一集", 1, 600, "晚 间"), _content("b-single", "晚间")), "a-1", "b-single", "晚 间"),
    ((_episode("a-1", "第一集", 1, 600, "晚间"), _episode("b-1", "第一集", 1, 600, "晚 间")), "a-1", "b-1", "晚间"),
])
def test_unit_names_under_one_location_kind_must_be_unique(tmp_path, extra, first, second, name):
    """两条独立内容重名、独立内容与系列重名、只差空白或大小写的重名都被拒绝，详情给出两行行号、类型和名称。"""
    directory = _catalog_dir(tmp_path, extra, drop=())
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert failure.value.code == "ENVIRONMENT_ID_DUPLICATE"
    assert failure.value.message == "An identifier or display name is declared twice in one catalog file."
    assert failure.value.detail == (
        f"content-catalog.jsonl:{_line(directory, first)} and content-catalog.jsonl:{_line(directory, second)} "
        f"give location kind home the same activity name {name}.")
    with pytest.raises(RegistrationError, match="ENVIRONMENT_ID_DUPLICATE"):
        validate_catalog_files(directory)


def test_specification_example_reports_lines_ten_and_eleven(tmp_path):
    """规格的例子：c-rest 在第 10 行，新增的 c-rest-2「测试 休息」落在第 11 行。"""
    directory = _catalog_dir(tmp_path, (_content("c-rest-2", "测试 休息"),), drop=())
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert str(failure.value) == (
        "ENVIRONMENT_ID_DUPLICATE: An identifier or display name is declared twice in one catalog file. "
        "content-catalog.jsonl:10 and content-catalog.jsonl:11 give location kind home the same activity name 测试休息.")


@pytest.mark.parametrize("extra", [
    (_content("x-office", "测试休息", location_kinds=["office"]),),
    (_content("x-app", "测试文章", app_ids=["douyin"]),),
    (_episode("x-1", "测试休息", 1, 600, "夜间收尾"), _episode("x-2", "测试闲聊", 2, 600, "夜间收尾")),
    _DINNER,
    (_content("x-both", "测试休息", location_kinds=["office", "park"]),),
])
def test_names_may_repeat_across_kinds_apps_and_inside_one_series(tmp_path, extra):
    """不同地点类型重名、app 内容标题重名、同一系列各集、系列内集标题与别的单元同名都能加载。"""
    catalog = load_catalog(_catalog_dir(tmp_path, extra, drop=()), 7)
    assert {row["content_id"] for row in extra} <= set(catalog.contents)


def test_units_are_listed_completely_in_unit_key_order(tmp_path):
    """独立内容的单元键是它的身份，系列是集号最小一集的身份；全部单元按单元键升序，系列各集按集号升序。"""
    shuffled = (_episode("m-b", "第二集", 2, 900, "夜读"), _episode("z-a", "第一集", 1, 600, "夜读"),
                _content("n-solo", "散步", 300))
    catalog = load_catalog(_catalog_dir(tmp_path, (*_DINNER, *shuffled)), 7)
    units = location_units(catalog, "home")
    assert [(unit.name, unit.key) for unit in units] == [
        ("工作日晚饭", "d-1-prep"), ("洗漱", "e-wash"), ("睡前准备", "f-bed"), ("散步", "n-solo"), ("夜读", "z-a")]
    assert [item.content_id for item in units[0].contents] == ["d-1-prep", "d-2-eat", "d-3-wash"]
    assert [item.content_id for item in units[4].contents] == ["z-a", "m-b"]
    assert location_units(catalog, "park") == () and location_units(catalog, "不存在") == ()
    assert [unit.name for unit in location_units(catalog, "office")] == ["季度销售数据整理"]
    assert unit_binding(units[0]) == {
        "action": "interact", "detail": "工作日晚饭", "note": None,
        "content_ids": ["d-1-prep", "d-2-eat", "d-3-wash"], "episode_titles": ["备菜", "吃饭", "洗碗"]}
    assert unit_binding(units[1]) == {"action": "interact", "detail": "洗漱", "note": None,
                                      "content_ids": ["e-wash"], "episode_titles": []}


@pytest.mark.parametrize("progress,content_id,position,facts", [
    ({}, "d-1-prep", 0.0, {"duration": "约 20 分钟", "note": "第 1 集，共 3 集"}),
    ({"d-1-prep": (0.0, 1)}, "d-2-eat", 0.0, {"duration": "约 25 分钟", "note": "第 2 集，共 3 集"}),
    ({"d-1-prep": (0.0, 1), "d-2-eat": (600.0, 0)}, "d-2-eat", 600.0,
     {"duration": "约 15 分钟", "note": "第 2 集，共 3 集；上次没做完，接着做"}),
    ({"d-1-prep": (0.0, 1), "d-2-eat": (0.0, 1), "d-3-wash": (0.0, 1)}, "d-1-prep", 0.0,
     {"duration": "约 20 分钟", "note": "第 1 集，共 3 集"}),
    ({"d-1-prep": (0.0, 2), "d-2-eat": (0.0, 1), "d-3-wash": (0.0, 1)}, "d-2-eat", 0.0,
     {"duration": "约 25 分钟", "note": "第 2 集，共 3 集"}),
])
def test_series_starts_at_the_lowest_episode_with_the_fewest_rounds(tmp_path, person, progress, content_id,
                                                                    position, facts):
    """没做过从第一集开始，位置大于零（正在做，或被暂停的活动占着）从原位置接着做，整个系列做完后从第一集开始新的
    一轮。"""
    catalog = load_catalog(_catalog_dir(tmp_path, _DINNER), 7)
    contents = location_units(catalog, "home")[0].contents
    content, at = next_content(contents, person(progress))
    assert (content.content_id, at) == (content_id, position)
    assert unit_facts(contents, person(progress)) == facts


@pytest.mark.parametrize("progress,facts", [
    ({}, {"duration": "约 5 分钟", "note": None}),
    ({"e-wash": (0.0, 3)}, {"duration": "约 5 分钟", "note": None}),
    ({"e-wash": (120.5, 0)}, {"duration": "约 3 分钟", "note": "上次没做完，接着做"}),
])
def test_standalone_unit_shows_remaining_time_and_the_resume_note(tmp_path, person, progress, facts):
    """独立内容：没做过或做完过没有说明；位置大于零（正在做，或被暂停的活动占着）时长是剩余时长（向上取整到秒）并写
    接着做。"""
    catalog = load_catalog(_catalog_dir(tmp_path, _DINNER), 7)
    contents = location_units(catalog, "home")[1].contents
    assert unit_facts(contents, person(progress)) == facts
    assert next_content(contents, person(progress))[0].content_id == "e-wash"


#: 结束的时刻：新建的进度记录以它为 last_consumed_at（person 夹具里已有记录的 last_consumed_at 都是 1）。
_ENDED_AT = 1767576600


@pytest.mark.parametrize("content_id,progress,expected", [
    ("e-wash", {}, {"e-wash": (0.0, 1, _ENDED_AT)}),
    ("e-wash", {"e-wash": (120.5, 0)}, {"e-wash": (0.0, 1, 1)}),
    ("e-wash", {"e-wash": (60.0, 2), "f-bed": (300.0, 0)}, {"e-wash": (0.0, 3, 1), "f-bed": (300.0, 0, 1)}),
    ("d-1-prep", {"d-1-prep": (900.0, 0)},
     {"d-1-prep": (0.0, 1, 1), "d-2-eat": (0.0, 1, _ENDED_AT), "d-3-wash": (0.0, 1, _ENDED_AT)}),
    ("d-2-eat", {"d-1-prep": (0.0, 1), "d-2-eat": (600.0, 0)},
     {"d-1-prep": (0.0, 1, 1), "d-2-eat": (0.0, 1, 1), "d-3-wash": (0.0, 1, _ENDED_AT)}),
    ("d-2-eat", {"d-1-prep": (0.0, 1)},
     {"d-1-prep": (0.0, 1, 1), "d-2-eat": (0.0, 1, _ENDED_AT), "d-3-wash": (0.0, 1, _ENDED_AT)}),
    ("d-2-eat", {"d-1-prep": (450.0, 3), "d-2-eat": (300.0, 1)},
     {"d-1-prep": (450.0, 3, 1), "d-2-eat": (0.0, 2, 1), "d-3-wash": (0.0, 2, _ENDED_AT)}),
], ids=["standalone-without-a-record", "standalone-half-done", "standalone-later-round-leaves-other-units",
        "series-at-episode-one", "series-at-episode-two", "series-just-moved-to-episode-two",
        "series-episode-ahead-is-untouched"])
def test_unit_done_raises_every_content_behind_to_the_next_round(tmp_path, person, content_id, progress, expected):
    """被结束的这条内容的轮数加一是目标轮数；单元里轮数低于它的各条位置归零、轮数置为目标轮数（系列各集一并拉齐），
    已不低于目标的不动；新建的记录 last_consumed_at 取结束的时刻，已有记录保留原值；别的单元不受影响。"""
    catalog = load_catalog(_catalog_dir(tmp_path, _DINNER), 7)
    before = person(progress)
    after = unit_done(before, catalog, content_id, _ENDED_AT)
    assert {key: (row.position_seconds, row.completed_rounds, row.last_consumed_at)
            for key, row in after.content_progress.items()} == expected
    assert before.content_progress == person(progress).content_progress
    contents = next(unit for unit in location_units(catalog, "home") if content_id in
                    [item.content_id for item in unit.contents]).contents
    assert next_content(contents, after)[1] == 0.0


def test_location_sleepable_is_a_strict_boolean_that_defaults_to_false():
    """sleepable 缺省为假，与 is_home 互不推导；null、数字或字符串按类型错误拒绝。"""
    base = {"location_id": "home", "name": "家", "kind": "home", "app_ids": []}
    assert Location.model_validate(base).sleepable is False
    assert Location.model_validate({**base, "is_home": True}).sleepable is False
    assert Location.model_validate({**base, "sleepable": True}).is_home is False
    for value in (None, 1, 0, "true", "yes", [True]):
        with pytest.raises(ValidationError):
            Location.model_validate({**base, "sleepable": value})


def test_held_refusal_ignores_units_without_a_paused_activity(engine_factory):
    """没有暂停中的活动占着时，任何单元都没有被占的拒绝。"""
    engine = engine_factory()
    unit = unit_binding(location_units(engine.context.catalog, "home")[0])
    assert held_refusal(engine.context.state, 1, unit, {}) is None
