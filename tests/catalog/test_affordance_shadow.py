"""@brief 可供性注册表影子等价测试（M0）与派生事实锚点（M1）。

M0 影子阶段：注册表派生集逐一 == 引擎现行硬编码常量（motivation.FAMILIES、
semantics.ENGINE_*、activities.FEEDBACK、body.ENERGY_RECOVERY、agent 侧四常量、
content.TITLE_SOURCES、习惯归并路由、采样常量）——失败即影子不等价。

M1 之后引擎常量本身从注册表派生，"常量侧"断言退化为接线检查；M2 之后
agent 侧四个字面常量与 content.TITLE_SOURCES 字面字典删除，闭集经观察合同
（affordance_contract）派生，断言改为"注册表 → 合同 → agent 派生"链条；
每个断言同时携带行为期望字面值（EXPECTED_*），把现行引擎行为钉死——注册
表或派生接线任一侧漂移都会在这里红。
"""

from __future__ import annotations

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
    default_affordances,
    load_affordances,
)
from worldmock.life.activities import (
    FEEDBACK_ACTIVITY_KINDS,
    _activity_met,
    _entertainment_relief,
    resource_claims,
)
from worldmock.life.body import ENERGY_RECOVERY_ACTIVITY_KINDS
from worldmock.life.models import ActivityKind, TravelMode
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
EXPECTED_RELIEF_KEYS = frozenset({"watch", "play_game", "wait"})
EXPECTED_RECOVERY_KINDS = frozenset({"sleep", "wait", "consume"})
EXPECTED_FEEDBACK_KINDS = frozenset({
    "wait", "work", "watch", "travel", "read_mail", "browse", "consume", "lifestyle"})
EXPECTED_CONTINUABLE_KINDS = frozenset({
    "wait", "work", "watch", "travel", "read_mail", "browse", "consume", "lifestyle"})
EXPECTED_TARGETED_ORDER = ("work", "watch", "play_game", "read_mail", "browse")
EXPECTED_CATALOG_FIELDS = {
    "work": ("tasks", "task_id"),
    "play_game": ("game_catalog", "game_id"),
    "read_mail": ("messages", "message_id"),
    "browse": ("browse_targets", None),
}
EXPECTED_ENERGY_LIMIT_KINDS = frozenset({"work", "watch", "play_game", "read_mail", "browse"})
EXPECTED_TITLE_SOURCES = {
    "feature_film": ("movies/items.json", "item_id"),
    "drama_series": ("drama/items.json", "item_id"),
    "webnovel_excerpt": ("novels/items.json", "item_id"),
    "subtitle_experience": ("sintel/media-identity.json", ""),
    # M4：lifestyle 9 品类（条目名是 name_zh，标题注入按合同缺省不写）。
    **{kind: ("lifestyle/items.json", "activity_id") for kind in (
        "sport", "hobby", "outdoor", "wellness", "household", "learning",
        "pet", "social", "volunteer")},
}
#: 现行习惯归并路由（classify_action_event if 链的逐行快照；M4 起
#: lifestyle:* 前缀自 continue 迁入 lifestyle 家族）。
EXPECTED_HABIT_ROUTES = [
    ("continue_activity", None, "continue"),
    ("start_basic_activity", "sleep", "sleep"),
    ("start_basic_activity", "wait", "continue"),
    ("start_basic_activity", "lifestyle:yingyong", "lifestyle"),
    ("start_basic_activity", "anything-else", "continue"),
    ("purchase", None, "eat"),
    ("consume", None, "eat"),
    ("start_travel", None, "travel"),
    ("start_targeted_activity", "watch", "entertainment"),
    ("start_targeted_activity", "play_game", "entertainment"),
    ("start_targeted_activity", "work", "work"),
    ("start_targeted_activity", "browse", "browse"),
    ("start_targeted_activity", "read_mail", None),
    ("start_targeted_activity", "unknown-kind", None),
    ("submit_work", None, "work"),
    ("nonexistent_tool", None, None),
]
#: 现行资源锁矩阵（activities.resource_claims if 链的逐分支快照；position 恒占）。
EXPECTED_RESOURCE_CLAIMS = {
    ("wait", None): {"position"},
    ("sleep", None): {"position"},
    ("work", None): {"position", "attention", "device", "hands"},
    ("watch", None): {"position", "attention", "device"},
    ("play_game", None): {"position", "attention", "device", "hands"},
    ("travel", "driving"): {"position", "attention", "hands", "vehicle"},
    ("travel", "walking"): {"position"},
    ("read_mail", None): {"position", "attention", "device"},
    ("browse", None): {"position", "attention", "device"},
    ("consume", None): {"position", "hands"},
    ("lifestyle", None): {"position"},
}


@pytest.fixture(scope="module")
def view():
    """@brief 仓库默认注册表的派生视图。"""
    return default_affordances()


def test_registry_entry_counts(view) -> None:
    """@brief 注册表覆盖度：10 kinds + 8 families + 16 内容品类（7 娱乐 + 9 生活）。"""
    assert len(view.kinds) == 10
    assert len(view.families) == 8
    entertainment = view.families["entertainment"]
    assert len(entertainment.content_kinds) == 7
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
    assert view.continuable_kinds == EXPECTED_FEEDBACK_KINDS


def test_continuable_matches_agent_derivation(view) -> None:
    """@brief L4：continue 资格闭集 == agent 侧合同派生（M2：字面常量已删，合同即事实）。"""
    from custom.agents import life_agent
    from worldmock.life.engine import affordance_contract

    contract = affordance_contract(view)
    assert life_agent._continuable_activity_kinds(contract) == EXPECTED_CONTINUABLE_KINDS
    assert view.continuable_kinds == EXPECTED_CONTINUABLE_KINDS
    assert not hasattr(life_agent, "_CONTINUABLE_ACTIVITY_KINDS")


def test_targeted_order_matches_agent_derivation(view) -> None:
    """@brief L4：start_targeted_activity 枚举序 == agent 侧合同派生（声明序）。"""
    from custom.agents import life_agent
    from worldmock.life.engine import affordance_contract

    contract = affordance_contract(view)
    assert life_agent._targeted_activity_kinds(contract) == EXPECTED_TARGETED_ORDER
    assert view.targeted_activity_kinds == EXPECTED_TARGETED_ORDER
    assert not hasattr(life_agent, "_TARGETED_ACTIVITY_KINDS")


def test_target_catalog_fields_match_agent_derivation(view) -> None:
    """@brief L4：观察目录直读字段映射 == agent 侧合同派生。"""
    from custom.agents import life_agent
    from worldmock.life.engine import affordance_contract

    contract = affordance_contract(view)
    assert life_agent._target_catalog_fields(contract) == EXPECTED_CATALOG_FIELDS
    assert view.target_catalog_fields == EXPECTED_CATALOG_FIELDS
    assert not hasattr(life_agent, "_TARGET_CATALOG_FIELDS")


def test_energy_limit_matches_agent_derivation(view) -> None:
    """@brief L4：能量投影闭集 == agent 侧合同派生 == 行为期望。"""
    from custom.agents import life_agent
    from worldmock.life.engine import affordance_contract

    contract = affordance_contract(view)
    assert life_agent._energy_limit_kinds(contract) == EXPECTED_ENERGY_LIMIT_KINDS
    assert view.energy_limit_kinds == EXPECTED_ENERGY_LIMIT_KINDS
    assert not hasattr(life_agent, "_ENERGY_LIMIT_KINDS")


def test_title_sources_match_content(view) -> None:
    """@brief L2：标题来源映射 == 注册表派生（M2：字面字典已删，注册表即事实）。"""
    import worldmock.life.content as content

    assert view.title_sources == EXPECTED_TITLE_SOURCES
    assert not hasattr(content, "TITLE_SOURCES")
    assert content.default_affordances().title_sources == view.title_sources


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
    """@brief M4：attention_required=true 键集 = 娱乐 5 + 生活 9 = 14 键
    （画像键集由注册表驱动：MotivationProfile 动态键按此键集核对）。"""
    expected = {"feature_film", "drama_series", "webnovel_excerpt", "news_feed",
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


@pytest.mark.parametrize("tool,activity_type,expected", EXPECTED_HABIT_ROUTES)
def test_habit_routing_matches_classify_action_event(view, tool, activity_type,
                                                     expected) -> None:
    """@brief L3：习惯归并路由 == classify_action_event == 行为期望（逐事件）。"""
    request = {"activity_type": activity_type} if activity_type is not None else {}
    assert view.classify_habit_event(tool, activity_type) == expected
    assert classify_action_event(tool, request) == expected


@pytest.mark.parametrize("kind_value,mode", sorted(EXPECTED_RESOURCE_CLAIMS))
def test_resource_claims_match_activities(view, kind_value, mode) -> None:
    """@brief L5：资源锁查表 == activities.resource_claims == if 链快照（逐类型）。"""
    expected = EXPECTED_RESOURCE_CLAIMS[(kind_value, mode)]
    assert view.resource_claims(kind_value, travel_mode=mode) == frozenset(expected)
    travel_mode = TravelMode(mode) if mode is not None else None
    request = SimpleNamespace(activity_type=ActivityKind(kind_value), travel_mode=travel_mode)
    assert resource_claims(request) == expected


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
    """@brief L5：缓解通道四值 == activities._entertainment_relief 的分支语义。"""
    assert view.relief_channel("wait") == "intrinsic"
    assert view.relief_channel("watch") == "content_rate"
    assert view.relief_channel("play_game") == "preference_rate"
    assert view.relief_channel("sleep") == "none"
    config = SimpleNamespace(entertainment_relief_per_hour={"play_game": 0.45, "wait": 0.05})
    wait = SimpleNamespace(kind=ActivityKind.WAIT, data={})
    watch = SimpleNamespace(kind=ActivityKind.WATCH,
                            data={"entertainment_relief_per_hour": 0.3, "exposure_count": 2})
    play = SimpleNamespace(kind=ActivityKind.PLAY_GAME, data={})
    assert _entertainment_relief(wait, 1.0, config) == 0
    assert _entertainment_relief(watch, 0.5, config) == pytest.approx(0.3 * 0.5 / 2)
    assert _entertainment_relief(play, 0.5, config) == pytest.approx(0.5 * 0.45)


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
# fail-closed 校验（docs/design/03-resource-engine.md「准入边界」的加载期五条拒绝面）
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
energy_projection = false
tool = { surface = "basic", activity_type = "wait" }
target_discovery = "none"

[families.continue]
kinds = ["wait"]
attention = { kind = "none" }
habit_members = [{ tool = "start_basic_activity", activity_type = "*" }]
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
habit_members = [{ tool = "start_basic_activity", activity_type = "*" }]
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
    """@brief 规则 5：targeted kind 无 habit_members 覆盖且无 unclassified 即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'tool = { surface = "basic", activity_type = "wait" }',
        'tool = { surface = "targeted", activity_type = "wait" }'),
        "covered by no habit_members")


def test_unregistered_backlog_resource_rejected(tmp_path) -> None:
    """@brief 规则 2：clear_backlog 引用未注册资源 id 即拒绝。"""
    _load_rejects(tmp_path, MINIMAL_TOML.replace(
        'candidate = { generator = "continue_current" }',
        'candidate = { generator = "clear_backlog", resource = "sleepiness" }'),
        "resource registry")


def test_corpus_reference_must_exist(tmp_path) -> None:
    """@brief 规则 2（语料半边）：提供 corpora_root 时 target_corpus 缺文件即拒绝；
    文件在场则放行。"""
    text = MINIMAL_TOML.replace(
        'target_discovery = "none"',
        'target_discovery = "browse_result"\ntarget_corpus = "lifestyle/items.json"')
    path = tmp_path / "affordances.toml"
    path.write_text(text, encoding="utf-8")
    corpora = tmp_path / "corpora"
    (corpora / "lifestyle").mkdir(parents=True)
    from worldmock.errors import IntegrityError

    with pytest.raises(IntegrityError, match="target_corpus"):
        load_affordances(path, corpora_root=corpora)
    (corpora / "lifestyle/items.json").write_text("{}", encoding="utf-8")
    assert load_affordances(path, corpora_root=corpora).kinds["wait"].target_corpus == \
        "lifestyle/items.json"
