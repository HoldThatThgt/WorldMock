"""@brief 最终占用闭集、活动声明及严格解析的直接行为证明。"""

import itertools
from pathlib import Path

import pytest

from worldmock.catalog.affordances import RESOURCE_CLAIM_SLOTS, engine_affordances, load_affordances
from worldmock.life.activities import resource_claims
from worldmock.life.models import ActivityKind, ActivityStartParams, TravelMode
from worldmock.life.social import SOCIAL_RESOURCE_CLAIMS

FINAL_CLAIMS = frozenset({"wait", "work", "use_app", "read_mail", "consume", "lifestyle", "talk", "travel"})
RETIRED_CLAIMS = ("position", "attention", "device", "vehicle", "idle", "eyes", "ears", "hands", "voice",
                  "watch", "screen", "play_game", "browse")


def test_final_claim_slots_and_activity_kinds_are_closed():
    assert RESOURCE_CLAIM_SLOTS == FINAL_CLAIMS
    assert tuple(ActivityKind) == ("wait", "sleep", "work", "use_app", "travel", "read_mail", "consume",
                                  "lifestyle", "chat", "call")
    assert "use_app" in engine_affordances().kinds
    assert not {"watch", "play_game", "browse"} & engine_affordances().kinds.keys()


@pytest.mark.parametrize("retired", RETIRED_CLAIMS)
@pytest.mark.parametrize("placement", ("list", "base", "conditional"))
def test_retired_claim_names_are_rejected(tmp_path, retired, placement):
    declarations = {
        "list": f'resource_claims = ["{retired}"]',
        "base": f'resource_claims = {{ base = ["{retired}"] }}',
        "conditional": ('resource_claims = { base = ["wait"], conditional = '
                        f'[{{ when = "mode:driving", add = ["{retired}"] }}] }}'),
    }
    _load_rejects(tmp_path, MINIMAL_TOML.replace("resource_claims = []", declarations[placement]), "must be within")


def test_driving_blocks_app_and_meals_but_allows_talk():
    view = engine_affordances()
    driving = view.resource_claims("travel", travel_mode="driving")
    assert driving == FINAL_CLAIMS - {"talk"}
    assert driving & view.resource_claims("use_app")
    assert driving & view.resource_claims("consume")
    assert not driving & SOCIAL_RESOURCE_CLAIMS
    assert not view.resource_claims("consume") & view.resource_claims("use_app")
    assert view.resource_claims("sleep") == FINAL_CLAIMS


def test_only_same_kind_sleep_and_driving_share_claims():
    view = engine_affordances()
    rows = [(name, view.resource_claims(name)) for name in view.kinds if name != "travel"]
    rows += [("walking", view.resource_claims("travel", "walking")),
             ("driving", view.resource_claims("travel", "driving")), ("talk", SOCIAL_RESOURCE_CLAIMS)]
    for (left, left_claims), (right, right_claims) in itertools.combinations(rows, 2):
        if "sleep" in {left, right}:
            assert left_claims & right_claims
        elif "driving" in {left, right}:
            other = right if left == "driving" else left
            assert bool(left_claims & right_claims) == (other != "talk")
        else:
            assert not left_claims & right_claims


def test_app_claims_match_activity_admission():
    request = ActivityStartParams(activity_type=ActivityKind.USE_APP, target_id="douyin",
                                  planned_duration_seconds=1800)
    assert resource_claims(request) == {"use_app"}


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
        "unknown keys")


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
