"""@brief 可供性注册表、引擎与冻结观察合同的闭集接线测试。"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

# life_agent 的导入链（agentsociety2）在导入期读取 LLM 环境变量（与
# tests/integration 的处理一致）。
os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.catalog.affordances import (
    PROTOTYPE_GENERATORS,
    RESOURCE_CLAIM_SLOTS,
    engine_affordances,
    load_affordances,
)
from worldmock.life.activities import (
    FEEDBACK_ACTIVITY_KINDS,
    _activity_met,
    entertainment_relief_rate,
    resource_claims,
)
from worldmock.life.body import ENERGY_RECOVERY_ACTIVITY_KINDS
from worldmock.life.social import SOCIAL_RESOURCE_CLAIMS
from worldmock.life.vocabulary import WAYS
from worldmock.life.models import ActivityKind, StartActivityRequest, TravelMode
from worldmock.life.motivation import (
    ENTERTAINMENT_PER_KIND,
    FAMILIES,
    MAX_CANDIDATES,
    classify_action_event,
)
from worldmock.life.semantics import ENGINE_ACTIVITY_MET_KEYS, ENGINE_RELIEF_KEYS

#: 现行引擎行为期望（字面值钉住；与 M0 前的硬编码常量逐字一致；M4 起
#: lifestyle 家族激活参与动机，闭集 7→8）。
EXPECTED_FAMILIES = frozenset({
    "continue", "sleep", "eat", "travel", "browse", "entertainment", "work",
    "lifestyle"})
EXPECTED_MET_KEYS = frozenset({
    "wait", "sleep", "work", "watch", "play_game", "travel:driving", "travel:walking",
    "read_mail", "browse", "consume"})
EXPECTED_RELIEF_KEYS = frozenset({"watch", "play_game"})
EXPECTED_RECOVERY_KINDS = frozenset({"sleep", "wait", "consume"})
EXPECTED_FEEDBACK_KINDS = frozenset({
    "wait", "work", "watch", "travel", "read_mail", "browse", "consume", "lifestyle"})
EXPECTED_CATALOG_FIELDS = {
    "work": ("tasks", "task_id"),
    "play_game": ("game_catalog", "game_id"),
    "read_mail": ("messages", "message_id"),
    "browse": ("browse_targets", None),
}
#: 现行习惯归并路由：（内部标准动作或工具名，人物所选方式，习惯家族）。start_activity 逐项按方式经私有绑定的
#: 方式闭集（vocabulary.WAYS）映射为内部动作再归并（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「请求形状与
#: 菜单」），其余稳定工具取工具名；M4 起 lifestyle:* 前缀自 continue 迁入 lifestyle 家族。
EXPECTED_HABIT_ROUTES = [
    ("report_status", None, "continue"),
    ("sleep", "睡觉", "sleep"),
    ("wait", "等待", "continue"),
    ("purchase", "到店", "eat"),
    ("purchase", "远程下单", "eat"),
    ("purchase", "购买", "eat"),
    ("consume", "制作", "eat"),
    ("consume", "食用", "eat"),
    ("consume", "使用", "eat"),
    ("consume", "给予", "eat"),
    ("travel_to", "步行", "travel"),
    ("travel_to", "驾车", "travel"),
    ("watch", "观看", "entertainment"),
    ("watch", "接着看", "entertainment"),
    ("watch", "从头再看", "entertainment"),
    ("watch", "接着听", "entertainment"),
    ("watch", "从头再听", "entertainment"),
    ("play", "游玩", "entertainment"),
    ("work", "工作", "work"),
    ("browse", "浏览", "browse"),
    ("read", "阅读", None),
    ("chat", "交谈", None),
    ("call", "通话", None),
    ("do", "进行", "lifestyle"),
    ("submit_work", None, "work"),
    ("nonexistent_tool", None, None),
]
#: 占用名闭集：每种动作占自己的名字（docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-占用声明）。
ALL_CLAIM_SLOTS = frozenset({
    "wait", "work", "watch", "screen", "play_game", "read_mail", "browse", "consume", "lifestyle", "talk",
    "travel"})
#: 退役的占用名：时间与能量规格之前的槽位名，与同时做两件事规格退役的五个身体部位名（hands 两次退役，只列一次）。
RETIRED_CLAIM_NAMES = ("position", "attention", "device", "vehicle", "idle", "eyes", "ears", "hands", "voice")
#: 占用表（docs/dev/SPEC-life-runtime.md#time-and-energy-五类生活动作与并行准入-并行准入 按同时做两件事规格重列）：
#: 键为（活动类型, 出行方式, 内容品类）；只有同一种、睡眠与驾车（除播客与说话）相交。
EXPECTED_RESOURCE_CLAIMS = {
    ("wait", None, None): {"wait"},
    ("sleep", None, None): set(ALL_CLAIM_SLOTS),
    ("work", None, None): {"work"},
    ("watch", None, "feature_film"): {"watch", "screen"},
    ("watch", None, "joke_feed"): {"watch", "screen"},
    ("watch", None, "game_session"): {"watch", "screen"},
    ("watch", None, "podcast_episode"): {"watch"},
    ("play_game", None, None): {"play_game"},
    ("travel", "driving", None): {
        "travel", "wait", "work", "screen", "play_game", "read_mail", "browse", "consume", "lifestyle"},
    ("travel", "walking", None): {"travel"},
    ("read_mail", None, None): {"read_mail"},
    ("browse", None, None): {"browse"},
    ("consume", None, None): {"consume"},
    ("lifestyle", None, None): {"lifestyle"},
}


@pytest.fixture(scope="module")
def view():
    """@brief 引擎包通用注册表的派生视图。"""
    return engine_affordances()


def test_registry_entry_counts(view) -> None:
    """@brief 注册表覆盖度：10 kinds + 8 families + 17 内容品类（8 娱乐 + 9 生活）。"""
    assert len(view.kinds) == 10
    assert len(view.families) == 8
    entertainment = view.families["entertainment"]
    assert len(entertainment.content_kinds) == 8
    lifestyle = view.families["lifestyle"]
    assert len(lifestyle.content_kinds) == 9


def test_family_set_matches_engine(view) -> None:
    """@brief L3：动机家族闭集 == motivation.FAMILIES == 行为期望（M4 起 8 家族；
    lifestyle 家族激活 content_menu 生成器与 lifestyle:* 习惯路由）。"""
    assert view.motivation_families == EXPECTED_FAMILIES
    assert FAMILIES == EXPECTED_FAMILIES
    assert view.family_names == EXPECTED_FAMILIES


def test_met_closed_keys_match_engine(view) -> None:
    """@brief L5：MET 封闭基键集 == semantics.ENGINE_ACTIVITY_MET_KEYS == 行为期望；
    开放命名空间前缀只有 lifestyle:。"""
    assert view.met_closed_keys == EXPECTED_MET_KEYS
    assert ENGINE_ACTIVITY_MET_KEYS == EXPECTED_MET_KEYS
    assert view.met_open_prefixes == ("lifestyle:",)


def test_relief_keys_match_engine(view) -> None:
    """@brief L5：娱乐缓解表键集 == semantics.ENGINE_RELIEF_KEYS == 行为期望。"""
    assert view.relief_keys == EXPECTED_RELIEF_KEYS
    assert ENGINE_RELIEF_KEYS == EXPECTED_RELIEF_KEYS


def test_energy_recovery_matches_body(view) -> None:
    """@brief L5：能量下界豁免集 == body.ENERGY_RECOVERY_ACTIVITY_KINDS == 行为期望。"""
    assert view.energy_recovery_kinds == EXPECTED_RECOVERY_KINDS
    assert {kind.value for kind in ENERGY_RECOVERY_ACTIVITY_KINDS} == EXPECTED_RECOVERY_KINDS


def test_feedback_kinds_match_activities(view) -> None:
    """@brief L5.5：反馈边界闭集 == activities.FEEDBACK_ACTIVITY_KINDS == 行为期望。"""
    assert view.feedback_kinds == EXPECTED_FEEDBACK_KINDS
    assert {kind.value for kind in FEEDBACK_ACTIVITY_KINDS} == EXPECTED_FEEDBACK_KINDS


def test_feedback_boundary_matches_frozen_observation_contract(view) -> None:
    """@brief 注册表反馈边界进入冻结观察合同。"""
    from worldmock.life.engine_context import affordance_contract

    contract = affordance_contract(view)
    assert {name for name, row in contract.items() if row["feedback_eligible"]} == EXPECTED_FEEDBACK_KINDS


def test_target_catalog_fields_match_observation_contract(view) -> None:
    """@brief 目标发现字段进入冻结观察合同。"""
    from worldmock.life.engine_context import affordance_contract

    contract = affordance_contract(view)
    assert view.target_catalog_fields == EXPECTED_CATALOG_FIELDS
    assert {name: tuple(row["target_field"]) for name, row in contract.items()
            if row["target_discovery"] == "catalog_field"} == EXPECTED_CATALOG_FIELDS


def test_energy_projection_is_gone_from_registry_and_observation_contract(view, tmp_path) -> None:
    """@brief 能量不参与受理（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第三章「删除的机制」）：注册表与冻结观察合同都没有
    能量投影；仍声明 energy_projection 的旧注册表以未知键拒绝，能量豁免只供内部动机评分保留。"""
    from worldmock.life.engine_context import affordance_contract

    contract = affordance_contract(view)
    assert all("energy_projection" not in row for row in contract.values())
    assert not hasattr(view, "energy_limit_kinds")
    assert all(not hasattr(spec, "energy_projection") for spec in view.kinds.values())
    assert view.energy_recovery_kinds == ENERGY_RECOVERY_ACTIVITY_KINDS
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'target_discovery = "none"', 'target_discovery = "none"\nenergy_projection = false', 1),
        "unknown keys")


def test_registry_declares_no_environment_title_sources(view) -> None:
    """@brief 标题由具体场景声明，通用注册表和内容模块不保存来源映射。"""
    import worldmock.life.content as content

    assert not hasattr(view, "title_sources")
    assert not hasattr(content, "TITLE_SOURCES")
    for family in view.families.values():
        for kind in family.content_kinds:
            assert not hasattr(kind, "title_source")
            assert not hasattr(kind, "corpus_path")
            assert not hasattr(kind, "id_key")


def test_attention_sampling_constants(view) -> None:
    """@brief L3：娱乐采样上限 == motivation.ENTERTAINMENT_PER_KIND == 2/14；
    生活采样 1/4；生存位 8 + 生活 4 + 娱乐 14 == MAX_CANDIDATES 26 的现行账目。"""
    attention = view.attention_params("entertainment")
    assert attention["per_kind"] == ENTERTAINMENT_PER_KIND == 2
    assert attention["pool_cap"] == 14
    lifestyle = view.attention_params("lifestyle")
    assert lifestyle["per_kind"] == 1
    assert lifestyle["pool_cap"] == 4
    survival_slots = 1 + 1 + 1 + 1 + 2 + 1 + 1  # eat/sleep/continue/work腿+2任务/home/browse
    assert survival_slots + lifestyle["pool_cap"] + attention["pool_cap"] \
        == MAX_CANDIDATES == 26


def test_attention_params_rejects_a_family_without_content_attention(view) -> None:
    """@brief 非内容注意力家族不能伪造采样参数。"""
    from worldmock.errors import IntegrityError

    with pytest.raises(IntegrityError, match="declares no content attention"):
        view.attention_params("continue")


def test_attention_required_keys(view) -> None:
    """@brief attention_required=true 键集 = 娱乐 6 + 生活 9 = 15 键
    （画像键集由注册表驱动：MotivationProfile 动态键按此键集核对）。"""
    expected = {"feature_film", "drama_series", "podcast_episode", "webnovel_excerpt", "news_feed",
                "joke_feed", "sport", "hobby", "outdoor", "wellness", "household",
                "learning", "pet", "social", "volunteer"}
    assert view.attention_required_kinds == frozenset(expected)
    # 画像旧 API（attention_keys/九参数模型）已删除：键集唯一事实源是注册表；
    # 场景侧 MotivationProfile 的动态键合同 = 键集精确等于注册表键集
    # （注册表规则 6；拒绝面由 test_scenario.py 的 attention 用例钉死）。
    import worldmock.profile.models as profile_models
    from worldmock.life.scenario import MotivationProfile

    assert not hasattr(profile_models, "attention_keys")
    profile = MotivationProfile.model_validate({
        "schema_version": 1,
        "big_five": {"openness": 4, "conscientiousness": 6, "extraversion": 3,
                     "agreeableness": 4, "neuroticism": 5},
        "weights": {"money_fen": 0.45, "energy_kcal": 0.15, "sleep_debt_hours": 0.15,
                    "entertainment_need": 0.20, "goal_task": 0.05},
        "tau": 1.0,
        "beta_present_bias": 0.80,
        "loss_aversion": {"money_fen": 2.25, "energy_kcal": 1.50},
        "comfort": {"balance_fen": 20000, "energy_kcal": 1200},
        "aspiration": {"daily_effective_work_seconds": 14400},
        "emotion_sensitivity": 0.3,
        "attention_prior": {kind: 0.5 for kind in sorted(view.attention_required_kinds)},
    })
    assert set(profile.attention_prior) == view.attention_required_kinds


@pytest.mark.parametrize("tool,way,expected", EXPECTED_HABIT_ROUTES)
def test_habit_routing_matches_classify_action_event(view, tool, way,
                                                     expected) -> None:
    """@brief 习惯路由逐项按方式映射出的内部标准动作归并，与注册表的 habit_members 一致。"""
    purchase = way in {"到店", "远程下单", "购买"}
    request = (StartActivityRequest(items=[{"target": "对象", "way": way, "from": None, "to": None}],
                                    answering=None, prefer="最快拿到" if purchase else None).model_dump(mode="json")
               if way is not None else {})
    tool_name = "start_activity" if way is not None else tool
    if way is not None:
        assert WAYS[way][1] == tool
    assert view.classify_habit_event(tool) == expected
    assert classify_action_event(tool_name, request) == (() if expected is None else (expected,))


def test_habit_routes_cover_every_way() -> None:
    """@brief 路由表覆盖方式闭集的每一项，新增方式必须先登记归并结果。"""
    assert {way for _, way, _ in EXPECTED_HABIT_ROUTES if way is not None} == set(WAYS)


def test_habit_routing_reads_ways_not_verbs_or_retired_phrases() -> None:
    """@brief 现行两项按方式逐项归并；旧请求、旧整句短语或项数越界不计数。"""
    parallel = StartActivityRequest(items=[
        {"target": "城西办公室", "way": "驾车", "from": None, "to": None},
        {"target": "播客", "way": "接着听", "from": None, "to": None}],
        answering=None, prefer=None).model_dump(mode="json")
    assert classify_action_event("start_activity", parallel) == ("travel", "entertainment")
    for retired in ("步行前往", "到店购买"):
        request = {**parallel, "items": [{"target": "对象", "way": retired, "from": None, "to": None}]}
        assert classify_action_event("start_activity", request) == ()
    assert classify_action_event("start_activity", {**parallel, "items": [*parallel["items"], parallel["items"][0]]}) == ()
    assert classify_action_event("start_activity", {**parallel, "items": []}) == ()


@pytest.mark.parametrize("request_body", [
    {"targets": ["城西办公室"], "actions": ["travel"]},
    {"targets": ["城西办公室"], "actions": ["travel"], "ways": ["驾车"]},
    {"targets": ["城西办公室", "播客"], "actions": ["travel", "interact"], "ways": ["驾车", "接着听"]},
    {"ways": ["步行"]},
])
def test_habit_routing_rejects_retired_parallel_array_requests(request_body: dict) -> None:
    """@brief 旧数组即使有合法方式和对应动词，也不会被当作现行逐项行为。"""
    assert classify_action_event("start_activity", request_body) == ()


@pytest.mark.parametrize("kind_value,mode,content_kind", sorted(EXPECTED_RESOURCE_CLAIMS, key=str))
def test_resource_claims_match_activities(view, kind_value, mode, content_kind) -> None:
    """@brief L5：占用查表 == activities.resource_claims == 第五章占用表（逐类型；观看按内容品类，播客只占 watch）。"""
    expected = EXPECTED_RESOURCE_CLAIMS[(kind_value, mode, content_kind)]
    assert view.resource_claims(kind_value, travel_mode=mode, content_kind=content_kind) == frozenset(expected)
    travel_mode = TravelMode(mode) if mode is not None else None
    request = SimpleNamespace(activity_type=ActivityKind(kind_value), travel_mode=travel_mode)
    assert resource_claims(request, content_kind) == expected


@pytest.mark.parametrize("retired", RETIRED_CLAIM_NAMES)
def test_claim_slots_are_the_eleven_kind_names_and_retired_names_are_rejected(tmp_path, retired) -> None:
    """@brief 占用槽位闭集是每种动作自己的十一个名字（交谈、通话的 talk 在内）；退役的槽位名写在槽位列表、
    base 或条件追加里都在加载期拒绝。"""
    assert RESOURCE_CLAIM_SLOTS == ALL_CLAIM_SLOTS
    assert set().union(*EXPECTED_RESOURCE_CLAIMS.values()) == RESOURCE_CLAIM_SLOTS
    assert SOCIAL_RESOURCE_CLAIMS == {"talk"}
    assert retired not in RESOURCE_CLAIM_SLOTS
    declarations = {
        f'resource_claims = ["{retired}"]': "resource_claims must be within",
        f'resource_claims = {{ base = ["{retired}"] }}': "resource_claims base must be within",
        ('resource_claims = { base = ["wait"], conditional = '
         f'[{{ when = "mode:driving", add = ["{retired}"] }}] }}'): "conditional add must be within",
    }
    for declaration, match in declarations.items():
        _load_rejects(tmp_path, MINIMAL_TOML.replace("resource_claims = []", declaration), match)


def _claim_rows(view) -> list[tuple[str, str, frozenset[str]]]:
    """@brief 逐对核对的行（行名, 种, 占用）：观看分视频与播客两行，出行分驾车与步行两行，交谈与通话一行。"""
    rows = [("chat_or_call", "talk", frozenset(SOCIAL_RESOURCE_CLAIMS))]
    for kind in sorted(view.kinds):
        if kind == "watch":
            rows += [(f"watch:{content}", kind, view.resource_claims(kind, content_kind=content))
                     for content in ("feature_film", "podcast_episode")]
        elif kind == "travel":
            rows += [(f"travel:{mode}", kind, view.resource_claims(kind, travel_mode=mode))
                     for mode in ("driving", "walking")]
        else:
            rows.append((kind, kind, view.resource_claims(kind)))
    return rows


def _expect_overlap(left: tuple[str, str, frozenset[str]], right: tuple[str, str, frozenset[str]]) -> bool:
    """@brief 规格乙：同一种、睡眠与任何动作、驾车与播客和说话以外的动作才互斥。"""
    hands_free = {"watch:podcast_episode", "chat_or_call"}
    driving = [row for row in (left, right) if row[0] == "travel:driving"]
    others = [row for row in (left, right) if row[0] != "travel:driving"]
    return (left[1] == right[1] or "sleep" in (left[1], right[1])
            or bool(driving) and all(row[0] not in hands_free for row in others))


def test_only_the_same_kind_sleep_and_driving_share_claims(view) -> None:
    """@brief 逐对核对十三行（含与自身成对）：占用相交当且仅当同一种、一方是睡眠，或一方是驾车而另一方不是播客、交谈或通话。"""
    rows = _claim_rows(view)
    assert len(rows) == 13
    pairs = list(itertools.combinations_with_replacement(rows, 2))
    assert len(pairs) == 91
    for left, right in pairs:
        overlap = left[2] & right[2]
        assert bool(overlap) is _expect_overlap(left, right), (left[0], right[0], sorted(overlap))


def test_conditional_claims_reject_unknown_dimension_and_undeclared_content_kind(tmp_path) -> None:
    """@brief 条件占用只接受 mode:<出行方式> 与已声明的 content_kind:<品类>，拼写错误在加载期拒绝。"""
    base = MINIMAL_TOML.replace(
        "resource_claims = []",
        'resource_claims = { base = ["wait"], conditional = [{ when = "WHEN", add = ["screen"] }] }')
    _load_rejects(tmp_path, base.replace("WHEN", "weather:rain"), "conditional when")
    _load_rejects(tmp_path, base.replace("WHEN", "mode:flying"), "conditional when")
    _load_rejects(tmp_path, base.replace("WHEN", "content_kind:"), "conditional when")
    _load_rejects(tmp_path, base.replace("WHEN", "content_kind:opera"), "content kind 'opera' is not declared")
    _load_rejects(tmp_path, base.replace('when = "WHEN"', "when = 3"), "conditional when")


def test_met_lookup_keys_match_activities(view) -> None:
    """@brief L5：met_key 模板求键 == activities._activity_met 查表（含回退分支）。"""
    config = SimpleNamespace(activity_met={
        "sleep": 1.0, "travel:driving": 2.0, "travel:walking": 3.0, "lifestyle:jianshen": 4.0})
    assert view.met_lookup_key("sleep") == "sleep"
    assert view.met_lookup_key("travel") == "travel"
    assert _activity_met(ActivityKind.SLEEP, None, config) == 1.0
    assert view.met_lookup_key("travel", travel_mode=TravelMode.DRIVING) == "travel:driving"
    assert _activity_met(ActivityKind.TRAVEL, TravelMode.DRIVING, config) == 2.0
    assert view.met_lookup_key("lifestyle", target_id="jianshen") == "lifestyle:jianshen"
    assert _activity_met(ActivityKind.LIFESTYLE, None, config, "jianshen") == 4.0
    with pytest.raises(ValueError, match="catalog item id"):
        view.met_lookup_key("lifestyle", target_id=None)


def test_relief_channels_match_activities(view) -> None:
    """@brief L5：缓解通道四值 == activities.entertainment_relief_rate 的分支语义。"""
    assert view.relief_channel("wait") == "none"
    assert view.relief_channel("watch") == "content_rate"
    assert view.relief_channel("play_game") == "preference_rate"
    assert view.relief_channel("sleep") == "none"
    config = SimpleNamespace(entertainment_relief_per_hour={"play_game": 0.45},
                             entertainment_relief_preference_floor=0.5)
    wait = SimpleNamespace(kind=ActivityKind.WAIT, data={})
    watch = SimpleNamespace(kind=ActivityKind.WATCH,
                            data={"entertainment_relief_per_hour": 0.3, "exposure_count": 2})
    play = SimpleNamespace(kind=ActivityKind.PLAY_GAME, data={})
    # 两个缓解通道都按有效偏好 f + (1 − f)·w 调幅度（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ）；none 通道没有缓解。
    effective = 0.5 + 0.5 * 0.5
    assert entertainment_relief_rate(wait, 1.0, config) == 0
    assert entertainment_relief_rate(watch, 0.5, config) == pytest.approx(0.3 * effective / 2)
    assert entertainment_relief_rate(play, 0.5, config) == pytest.approx(effective * 0.45)


def test_enumeration_order(view) -> None:
    """@brief L3：枚举序 == 现行固定优先级（eat>sleep>continue>work>travel>browse>
    lifestyle（同秩按名声后于 browse）>娱乐）。"""
    ranked = [family.name for family in view.families_by_rank()]
    assert ranked == ["eat", "sleep", "continue", "work", "travel", "browse",
                      "lifestyle", "entertainment"]


def test_prototype_functions_registered(view) -> None:
    """@brief M1 锚点：候选生成器原型全部已登记 Python 实现（枚举遍历取函数）。"""
    for family in view.families_by_rank():
        assert family.generator in PROTOTYPE_GENERATORS, family.generator


# ---------------------------------------------------------------------------
# fail-closed 校验（docs/design/03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎「准入边界」的加载期五条拒绝面）
# ---------------------------------------------------------------------------

#: 通过全部校验的最小注册表（变异基线）。
MINIMAL_TOML = """\
version = 1

[kinds.wait]
met_key = "wait"
energy_floor_exempt = true
sleep_channel = "awake"
relief = { channel = "none" }
resource_claims = []
feedback_boundary = true
action = "wait"
target_discovery = "none"

[families.continue]
kinds = ["wait"]
attention = { kind = "none" }
habit_members = ["wait"]
candidate = { generator = "continue_current" }
enumeration_rank = 30
"""


def _load_rejects(tmp_path: Path, text: str, match: str) -> None:
    """@brief 写入变异注册表并断言加载以 IntegrityError 拒绝。"""
    from worldmock.errors import IntegrityError

    path = tmp_path / "affordances.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(IntegrityError, match=match):
        load_affordances(path)


def test_minimal_registry_loads(tmp_path) -> None:
    """@brief 基线健全性：最小合法注册表可通过全部校验。"""
    path = tmp_path / "affordances.toml"
    path.write_text(MINIMAL_TOML, encoding="utf-8")
    view = load_affordances(path)
    assert view.family_names == {"continue"}


def test_missing_registry_file_rejected(tmp_path) -> None:
    """@brief 存在性：文件缺席即拒绝。"""
    from worldmock.errors import IntegrityError

    with pytest.raises(IntegrityError, match="does not exist"):
        load_affordances(tmp_path / "absent.toml")


def test_malformed_toml_rejected(tmp_path) -> None:
    """@brief 解析：坏 TOML 即拒绝。"""
    _load_rejects(tmp_path, "version = 1\nkinds = [", "malformed")


def test_non_utf8_registry_rejected(tmp_path, monkeypatch, caplog) -> None:
    """@brief 显式选择的注册表无法按 UTF-8 解码时须拒绝，不能改读包内规则。"""
    from worldmock.errors import IntegrityError

    monkeypatch.chdir(tmp_path)
    path = Path("selected-rules.toml")
    path.write_bytes(MINIMAL_TOML.encode("utf-8") + b"\xff")
    with pytest.raises(IntegrityError, match="Affordance registry cannot be read") as caught:
        load_affordances(path)
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    assert any("Affordance registry cannot be read" in message for message in caplog.messages)
    assert all(message.isascii() for message in caplog.messages)


def test_registry_read_failure_rejected(tmp_path, monkeypatch, caplog) -> None:
    """@brief 显式文件存在但读取失败时保留原始故障并记录错误，不能返回其他注册表。"""
    from worldmock.errors import IntegrityError

    path = tmp_path / "selected-rules.toml"
    path.write_text(MINIMAL_TOML, encoding="utf-8")
    original_read = Path.read_text
    failure = PermissionError("Access denied to explicitly selected rule registry")

    def read(selected, **kwargs):
        """@brief 模拟目标文件打开时的跨平台读取故障，其他文件保持真实读取。"""
        if selected == path:
            raise failure
        return original_read(selected, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(IntegrityError, match="Affordance registry cannot be read") as caught:
        load_affordances(path)
    assert caught.value.__cause__ is failure
    assert any("Access denied to explicitly selected rule registry" in message for message in caplog.messages)
    assert all(message.isascii() for message in caplog.messages)


def test_wrong_version_rejected(tmp_path) -> None:
    """@brief 版本：version != 1 即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace("version = 1", "version = 2"), "version")


def test_unknown_top_level_table_rejected(tmp_path) -> None:
    """@brief 根级多余表不能被静默忽略。"""
    _load_rejects(tmp_path, MINIMAL_TOML + "\n[extra]\nvalue = 1\n", "unknown tables")


def test_registry_requires_kinds_and_families(tmp_path) -> None:
    """@brief 注册表的 kinds 与 families 两个核心表都必须非空。"""
    without_kinds = """\
version = 1
[families.continue]
kinds = ["wait"]
attention = { kind = "none" }
habit_members = ["wait"]
candidate = { generator = "continue_current" }
enumeration_rank = 30
"""
    without_families = MINIMAL_TOML.split("[families.continue]", 1)[0]
    _load_rejects(tmp_path, without_kinds, "declares no kinds")
    _load_rejects(tmp_path, without_families, "declares no families")


def test_unknown_generator_rejected(tmp_path) -> None:
    """@brief 规则 3：未登记原型名即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace('generator = "continue_current"',
                                                 'generator = "dream_menu"'),
                  "unregistered generator")


def test_unknown_generator_param_rejected(tmp_path) -> None:
    """@brief 规则 3：原型未知参数键即拒绝（防拼写静默失效）。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'candidate = { generator = "continue_current" }',
        'candidate = { generator = "continue_current", horizon_seconds = 60 }'),
        "rejects params")


def test_unknown_kind_key_rejected(tmp_path) -> None:
    """@brief 规则 1：kind 未知字段即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'target_discovery = "none"', 'target_discovery = "none"\ncolor = "red"', 1),
        "unknown keys")


def test_family_referencing_unknown_kind_rejected(tmp_path) -> None:
    """@brief 规则 2：家族引用未声明 kind 即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace('kinds = ["wait"]', 'kinds = ["wait", "podcast"]'),
                  "reference declared kinds")


def test_boundary_mutex_violation_rejected(tmp_path) -> None:
    """@brief 规则 4：game_boundary 与 feedback_boundary 同真即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        "feedback_boundary = true", "feedback_boundary = true\ngame_boundary = true"),
        "mutually exclusive")


def test_content_rate_without_entry_discovery_rejected(tmp_path) -> None:
    """@brief 规则 4：content_rate 缓解缺条目级目标发现即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'relief = { channel = "none" }', 'relief = { channel = "content_rate" }'),
        "entry-level target discovery")


def test_content_attention_without_content_kinds_rejected(tmp_path) -> None:
    """@brief 规则 4：attention.kind=content_kind 而无 content_kinds 即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'attention = { kind = "none" }',
        'attention = { kind = "content_kind", sampling = "per_kind_top_n", per_kind = 2, pool_cap = 4 }'),
        "content_kinds")


def test_habit_coverage_hole_rejected(tmp_path) -> None:
    """@brief 规则 5：活动动作无习惯归并覆盖且无 unclassified 即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'habit_members = ["wait"]', 'habit_members = []'),
        "covered by no habit_members")


def test_unregistered_backlog_resource_rejected(tmp_path) -> None:
    """@brief 规则 2：clear_backlog 引用未注册资源 id 即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'candidate = { generator = "continue_current" }',
        'candidate = { generator = "clear_backlog", resource = "sleepiness" }'),
        "resource registry")


def test_environment_corpus_reference_is_rejected(tmp_path) -> None:
    """@brief 通用注册表不接受具体环境的语料路径声明。"""
    text = MINIMAL_TOML.replace(
        'target_discovery = "none"',
        'target_discovery = "browse_result"\ntarget_corpus = "lifestyle/items.json"')
    _load_rejects(tmp_path, text, "target_corpus")


@pytest.mark.parametrize("source", ['"self_label"', '{ corpus_path = "movies/items.json", id_key = "item_id" }'])
def test_environment_title_source_is_rejected(tmp_path, source) -> None:
    """@brief 旧标题标签和语料定位声明都属于已删除的来源映射接口。"""
    text = MINIMAL_TOML.replace(
        'attention = { kind = "none" }',
        'attention = { kind = "content_kind", sampling = "per_kind_top_n", per_kind = 1, pool_cap = 1 }')
    text += ("\n[families.continue.content_kinds.feature_film]\n"
             f"attention_required = true\ntitle_source = {source}\n")
    _load_rejects(tmp_path, text, "title_source")
