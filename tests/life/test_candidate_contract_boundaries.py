"""候选契约的两项边界：跨运行分支一致、层数与上限的口径（docs/dev/SPEC-life-runtime.md）。

本文件闭合矩阵里三行仅靠纯函数层证据不能成立的条款：

- 同一（冻结目录、画像、世界状态版本）下候选集在父运行与分支运行里逐字节相同；
- 动机层内部截断 `MAX_CANDIDATES` 不作用于呈现，不得当作单次呈现上限；
- 下推层数限两层（品类 → 条目），层数上限本身有明确断言，且它是约束传播不是界面分页。
"""

from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import context
from worldmock.footprints.models import EventDraft
from worldmock.life import candidates, presentation
from worldmock.life.engine import LifeEngine
from worldmock.life.models import BrowseActivitiesRequest, StartBasicActivityRequest
from worldmock.life.motivation import MAX_CANDIDATES

from .conftest import advance_until_ready, submit_round

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = Path("scenarios/white-collar-12-weeks.json")

#: 五类可选对象的分区（类别, 下钻品类），与观察投影和下推共用同一张表。
_KIND_CATEGORIES = (("product", "foods"), ("lifestyle", "sport"),
                    ("content", "feature_film"), ("game", "game_session"),
                    ("task", "work_contract"))

#: 单次呈现上限的契约默认值（docs/CONTRACTS.md「候选与呈现合同」的「场景常量」）。
_CONTRACT_PRESENTATION_LIMIT = 20
#: 下推返回体的顶层键闭集（docs/CONTRACTS.md「候选与呈现合同」的「下推返回体」）。
_DOWN_PUSH_KEYS = {"tool", "category", "returned", "total", "next_cursor",
                   "note", "items", "excluded"}
#: 活动条目行的键集（第二层条目形状）。
_LIFESTYLE_ROW_KEYS = {"activity_id", "activity_type", "name", "category",
                       "typical_duration_minutes", "description", "met_value"}
#: 条目行里一旦出现即意味着存在第三层的键名（游标、页码、嵌套子表）。
_FORBIDDEN_ROW_KEYS = {"cursor", "next_cursor", "page", "offset", "items", "children",
                       "sub_items", "excluded", "total"}
#: 返回体上出现即意味着存在第三个下推层的键名（两层只有「品类」与「条目」两页）。
_FORBIDDEN_ENVELOPE_KEYS = {"page", "page_number", "offset", "children", "sub_items"}


def _frames(engine: LifeEngine) -> dict[str, str]:
    """@brief 五个品类分区各自的呈现行与排除项：规范 JSON 文本，可逐字节比较。"""
    agent = engine.state.agents[1]
    context_value = engine._presentation_context(agent)
    frames = {}
    for kind, category in _KIND_CATEGORIES:
        entries = engine._pool_entries(context_value, agent, kind, category)
        inputs = presentation.candidate_inputs(
            context_value, presentation.entry_sources(entries), moment=False,
            rotation_offset=engine._rotation_offset(agent.agent_id),
            last_presented_at=engine._last_presented(agent.agent_id))
        window = presentation.present(entries, inputs, kind, None)
        frames[kind] = json.dumps(
            {"items": list(window.items), "total": window.total,
             "presented": list(window.presented), "deferred": list(window.deferred),
             "excluded": [item.as_row() for item in window.excluded],
             "source_counts": window.source_counts,
             "exclusion_counts": window.exclusion_counts},
            ensure_ascii=False, sort_keys=True)
    return frames


def _market_hour(engine: LifeEngine, hour: int) -> int:
    """@brief 把世界时钟挪到当日某整点（商家营业时段内）。"""
    local = datetime.fromtimestamp(engine.state.current_time,
                                   ZoneInfo(engine.scenario.timezone))
    return int(local.replace(hour=hour, minute=0, second=0).timestamp())


def _commit_state(engine: LifeEngine, state) -> None:
    """@brief 提交一个明确的测试世界状态并同步引擎（版本严格加一）。"""
    state.version = engine.state.version + 1
    engine.store.commit(state.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=state.current_time,
        visibility="system", source_kind="scenario",
        data={"kind": "candidate_boundary_setup"})])
    engine.state = state


def _move_agent_to_market(engine: LifeEngine) -> int:
    """@brief 把基准人物挪到营业中的商家并落盘，返回新的世界状态版本。"""
    merchant = next(item for item in engine.scenario.locations
                    if item.location_id == "market")
    state = engine.state.model_copy(deep=True)
    state.current_time = _market_hour(engine, 12)
    state.agents[1] = state.agents[1].model_copy(
        update={"location_id": merchant.location_id, "next_decision_at": state.current_time},
        deep=True)
    _commit_state(engine, state)
    return engine.state.version


def _advance_version_only(engine: LifeEngine) -> None:
    """@brief 只把世界状态版本推进一格（不改目录、不改人物、不改时刻）。

    分支创建本身会写入一次提交并因此把版本推进一格；要与分支落在同一版本三元组上，
    父运行必须走过同样的「版本前进、世界事实不变」这一步。
    """
    state = engine.state.model_copy(deep=True)
    _commit_state(engine, state)


def _checkpoint_and_branch(engine: LifeEngine, destination: Path) -> LifeEngine:
    """@brief 经生产接口建立共同检查点并恢复出一个普通分支运行（不含任何干预）。"""
    snapshots = {f"agent:{agent_id}": engine.store.assets.put_json(
        {"agent_id": agent_id}, {"kind": "candidate_boundary_test"})
        for agent_id in engine.store.manifest["agent_ids"]}
    checkpoint = engine.checkpoint(snapshots)
    store = engine.store.restore(checkpoint.checkpoint_id, destination)
    store.close()
    return LifeEngine.open(SCENARIO_PATH, destination)


def test_candidate_set_is_identical_in_the_branch_and_the_parent_run(
        engine_factory, tmp_path) -> None:
    """同一（冻结目录、画像、世界状态版本）下父运行与分支运行的候选集逐字节相同。"""
    parent = engine_factory()
    _move_agent_to_market(parent)
    parent_frames = _frames(parent)
    assert any(frame not in ("{}", "") for frame in parent_frames.values())
    # 商品分区确有内容，不是空集恒等
    assert [row["product_id"] for row in json.loads(parent_frames["product"])["items"]]
    catalog_digest = parent.state.content_manifest_sha256

    branch = _checkpoint_and_branch(parent, tmp_path / "branch")
    # 分支把世界状态版本重算到「父运行当时的那一版加一」（写入一次分支创建提交）
    assert branch.state.version == parent.state.version + 1
    assert branch.state.current_time == parent.state.current_time
    assert branch.state.content_manifest_sha256 == catalog_digest
    assert branch.scenario.scenario_id == parent.scenario.scenario_id

    # 对照必须落在同一（冻结目录、画像、世界状态版本）三元组上：把父运行推进到同一版本
    _advance_version_only(parent)
    assert parent.state.version == branch.state.version
    assert parent.state.agents[1].location_id == branch.state.agents[1].location_id
    assert _frames(parent) == parent_frames  # 世界除版本外未变，候选集逐字节不变
    assert _frames(branch) == parent_frames
    branch.close()


def test_motivation_truncation_never_becomes_the_presentation_limit(engine_factory) -> None:
    """呈现上限取场景常量而非 `MAX_CANDIDATES`：两者是不同的量，取值也不同。"""
    engine = engine_factory()
    constants = engine.semantics.candidate_constants
    assert constants.presentation_limit == engine.scenario.candidate_presentation_limit
    assert constants.presentation_limit == _CONTRACT_PRESENTATION_LIMIT
    assert constants.presentation_limit != MAX_CANDIDATES
    assert MAX_CANDIDATES > constants.presentation_limit

    agent = engine.state.agents[1]
    context_value = engine._presentation_context(agent)
    entries = engine._pool_entries(context_value, agent, "lifestyle", "sport")
    assert len(entries) > MAX_CANDIDATES  # 池比动机层内部截断更大
    inputs = presentation.candidate_inputs(context_value, presentation.entry_sources(entries),
                                           moment=False, rotation_offset=0)
    window = presentation.present(entries, inputs, "lifestyle", None)
    assert len(window.items) == constants.presentation_limit


@pytest.mark.parametrize("truncation", [1, 3, 64])
def test_presentation_window_ignores_the_motivation_truncation(
        engine_factory, monkeypatch, truncation: int) -> None:
    """把动机层截断改成任何取值，呈现条数都仍只由场景常量决定。"""
    engine = engine_factory()
    monkeypatch.setattr("worldmock.life.motivation.MAX_CANDIDATES", truncation)
    agent = engine.state.agents[1]
    context_value = engine._presentation_context(agent)
    entries = engine._pool_entries(context_value, agent, "content", "feature_film")
    inputs = presentation.candidate_inputs(context_value, presentation.entry_sources(entries),
                                           moment=False, rotation_offset=0)
    window = presentation.present(entries, inputs, "content", None)
    assert len(window.items) == engine.scenario.candidate_presentation_limit


def test_presentation_modules_never_read_the_motivation_truncation() -> None:
    """呈现链路四个模块都不导入 `MAX_CANDIDATES`：该常量不是呈现上限的取值来源。"""
    for name in ("candidates", "presentation", "rotation", "observe"):
        source = (PROJECT_ROOT / "src" / "worldmock" / "life" / f"{name}.py").read_text(
            encoding="utf-8")
        assert "MAX_CANDIDATES" not in source, name
        tree = ast.parse(source)
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names}
        assert "MAX_CANDIDATES" not in imported, name


def test_down_push_has_exactly_two_layers(engine_factory) -> None:
    """下推层数限两层：顶层是品类分区，第二层是条目行，条目行不携带可再下推的载荷。"""
    engine = engine_factory()

    def browse(category: str, cursor: str | None) -> dict:
        suffix = "first" if cursor is None else "next"
        _, results = submit_round(
            engine, {1: ("browse_activities",
                         BrowseActivitiesRequest(category=category, cursor=cursor))},
            f"boundary-{category}-{suffix}")
        result = results[0]
        assert result.status.value == "completed", result.error_code
        return result.data

    payload = browse("sport", None)
    assert set(payload) == _DOWN_PUSH_KEYS  # 第一层：品类分区
    assert payload["items"], "the lifestyle partition must not be empty"
    for row in payload["items"]:  # 第二层：条目行
        assert set(row) == _LIFESTYLE_ROW_KEYS
        assert all(isinstance(value, (str, int, float)) for value in row.values())
        assert not _FORBIDDEN_ROW_KEYS & set(row)

    # 层的语义是约束传播：上一层选定的品类收窄下一层的可行集合
    assert {row["category"] for row in payload["items"]} == {"sport"}
    assert payload["next_cursor"] is not None
    assert advance_until_ready(engine)  # 下一屏落在新的决策位上
    second = browse("sport", payload["next_cursor"])
    assert {row["category"] for row in second["items"]} == {"sport"}
    assert not ({row["activity_id"] for row in second["items"]}
                & {row["activity_id"] for row in payload["items"]})


def test_cursor_payload_admits_no_third_level(engine_factory) -> None:
    """游标载荷恰为（排序键, 轮转偏移, 身份），排序键整长三位；形状不合即拒载。"""
    engine = engine_factory()
    _, results = submit_round(
        engine, {1: ("browse_activities",
                     BrowseActivitiesRequest(category="sport", cursor=None))},
        "boundary-cursor")
    cursor = json.loads(results[0].data["next_cursor"])
    assert set(cursor) == {"key", "offset", "identity"}
    assert len(cursor["key"]) == 3
    assert isinstance(cursor["offset"], int) and isinstance(cursor["identity"], str)
    assert presentation.cursor_offset(results[0].data["next_cursor"]) == cursor["offset"]

    # 排序键短一位或长一位都不是两层下推能产生的形状，一律拒载而不是当作合法载荷
    for shorter in ([0, 0], [0, 0, "lifestyle-0001", "lifestyle-0002"], "lifestyle-0001", 7):
        with pytest.raises(ValueError):
            presentation.cursor_offset(json.dumps(
                {"key": shorter, "offset": 0, "identity": "x"}))


def test_presentation_envelopes_themselves_have_no_third_layer() -> None:
    """两个返回体的字段面只有两层：没有页码、偏移或嵌套子表这类第三层载荷。"""
    window_fields = set(presentation.PresentationWindow.__dataclass_fields__)
    # 窗口的页载荷只有一层：next_cursor 是标量，items 是标量行的序列
    assert "next_cursor" in window_fields and "items" in window_fields
    assert not _FORBIDDEN_ENVELOPE_KEYS & window_fields
    assert set(window_fields) >= {"items", "total", "next_cursor", "excluded"}
    page_fields = set(candidates.CandidatePage.__dataclass_fields__)
    assert not _FORBIDDEN_ENVELOPE_KEYS & page_fields
    # 候选行本身只有排序键四件，不含任何分页载荷
    assert set(candidates.OrderedCandidate.__dataclass_fields__) == {
        "object_id", "kind", "taste_weight", "attention_prior"}
    assert set(page_fields) == {
        "generated", "retained", "excluded", "source_counts", "exclusion_counts",
        "deferred", "returned", "total", "next_cursor", "order_key_version"}
