"""@brief 版本 2 可供性闭集、固定占用及严格注册错误的纯行为证明。"""

import itertools
from importlib.resources import files
from pathlib import Path

import pytest

from worldmock.catalog import affordances
from worldmock.catalog.affordances import RESOURCE_CLAIM_SLOTS, engine_affordances, load_affordances
from worldmock.errors import IntegrityError
from worldmock.exchange.types import ActionCategory, AppUseManner


REGISTRY_TEXT = files("worldmock.data").joinpath("affordances.toml").read_text("utf-8")
FINAL_CLAIMS = frozenset({
    "wait", "travel", "talk", "use_app", "entertainment", "work_study", "exercise", "daily_care",
})
RETIRED_CLAIMS = (
    "work", "read_mail", "consume", "lifestyle", "position", "attention", "device", "vehicle", "idle",
    "eyes", "ears", "hands", "voice", "watch", "screen", "play_game", "browse",
)


def _load_text(tmp_path: Path, text: str):
    """@brief 将原始包规则的反例写到独立临时文件后走生产加载入口。"""
    path = tmp_path / "affordances.toml"
    path.write_text(text, encoding="utf-8")
    return load_affordances(path)


def test_registry_only_declares_final_activity_kinds_and_body_keys():
    """@brief 注册表恰好四种活动，身体表只保留等待、睡眠与两种出行。"""
    view = engine_affordances()
    assert set(view.kinds) == {"wait", "sleep", "travel", "action"}
    assert RESOURCE_CLAIM_SLOTS == FINAL_CLAIMS
    assert view.met_closed_keys == {"wait", "sleep", "travel:walking", "travel:driving"}
    assert view.met_lookup_key("wait") == "wait"
    assert view.met_lookup_key("sleep") == "sleep"
    assert view.met_lookup_key("travel", "walking") == "travel:walking"
    assert view.met_lookup_key("travel", "driving") == "travel:driving"
    assert view.energy_recovery_kinds == {"wait", "sleep"}
    assert view.feedback_kinds == {"wait", "travel", "action"}
    assert view.relief_keys == {"action"}
    assert view.relief_channel("action") == "content_rate"
    assert view.relief_channel("sleep") == "none"
    assert view.target_catalog_fields == {"action": ("actions", "action_id")}
    with pytest.raises(TypeError):
        view.kinds["work"] = view.kinds["action"]


@pytest.mark.parametrize("category", tuple(ActionCategory))
@pytest.mark.parametrize("manner", (None, *tuple(AppUseManner)))
def test_each_category_and_manner_combination_has_fixed_claims(category, manner):
    """@brief 类别与使用方式没有合法组合限制，持续手机方式共同占 use_app。"""
    category_claim = "talk" if category == ActionCategory.SOCIAL else category.value
    expected = {category_claim}
    if manner == AppUseManner.PHONE_CONTINUOUS:
        expected.add("use_app")
    assert engine_affordances().resource_claims("action", category=category, manner=manner) == expected


def test_driving_allows_only_social_without_continuous_phone_use():
    """@brief 驾车占除 talk 外的所有名额，其他设备与线下辅助社交能并行。"""
    view = engine_affordances()
    driving = view.resource_claims("travel", "driving")
    assert driving == FINAL_CLAIMS - {"talk"}
    assert view.resource_claims("travel", "walking") == {"travel"}
    assert view.resource_claims("wait") == {"wait"}
    assert view.resource_claims("sleep") == FINAL_CLAIMS
    for category, manner in itertools.product(ActionCategory, (None, *tuple(AppUseManner))):
        action_claims = view.resource_claims("action", category=category, manner=manner)
        allowed = category == ActionCategory.SOCIAL and manner != AppUseManner.PHONE_CONTINUOUS
        assert bool(driving & action_claims) is not allowed


def test_categories_share_only_the_phone_claim_when_different():
    """@brief 不同类别可并行，相同类别互斥，持续手机使用无论类别都互斥。"""
    view = engine_affordances()
    for left, right in itertools.product(ActionCategory, repeat=2):
        without_apps = (view.resource_claims("action", category=left)
                        & view.resource_claims("action", category=right))
        assert bool(without_apps) == (left == right)
        with_phone = (view.resource_claims("action", category=left, manner=AppUseManner.PHONE_CONTINUOUS)
                      & view.resource_claims("action", category=right, manner=AppUseManner.PHONE_CONTINUOUS))
        assert "use_app" in with_phone


@pytest.mark.parametrize("arguments,match", [
    ({"kind_value": "action"}, "require a fixed category"),
    ({"kind_value": "travel"}, "require a fixed travel mode"),
    ({"kind_value": "action", "category": "reading"}, "outside the fixed set"),
    ({"kind_value": "action", "category": "social", "manner": "foreground"}, "outside the fixed set"),
    ({"kind_value": "travel", "travel_mode": "flying"}, "outside the fixed set"),
])
def test_missing_or_unknown_runtime_claim_dimensions_are_rejected(arguments, match, caplog):
    """@brief 内部占用计算不会把缺类别或错方式静默视作无占用。"""
    with pytest.raises(IntegrityError, match=match):
        engine_affordances().resource_claims(**arguments)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


@pytest.mark.parametrize("kind,mode", [("action", None), ("travel", None), ("travel", "flying")])
def test_action_or_missing_travel_mode_has_no_body_met_lookup(kind, mode, caplog):
    """@brief action 不读身体表，出行必须带固定方式。"""
    with pytest.raises(IntegrityError):
        engine_affordances().met_lookup_key(kind, mode)
    assert caplog.messages


@pytest.mark.parametrize("retired", RETIRED_CLAIMS)
@pytest.mark.parametrize("placement", ("list", "base", "conditional"))
def test_retired_claim_names_are_rejected(tmp_path, retired, placement, caplog):
    """@brief 直接、基础与条件三种占用位置都拒绝退役名额。"""
    replacements = {
        "list": f'resource_claims = ["{retired}"]',
        "base": f'resource_claims = {{ base = ["{retired}"] }}',
        "conditional": ('resource_claims = { base = ["wait"], conditional = '
                        f'[{{ when = "category:social", add = ["{retired}"] }}] }}'),
    }
    text = REGISTRY_TEXT.replace('resource_claims = ["wait"]', replacements[placement])
    with pytest.raises(IntegrityError, match="must be within"):
        _load_text(tmp_path, text)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


@pytest.mark.parametrize("old,new,match", [
    ("version = 2", "version = 1", "version must be 2"),
    ("version = 2", "version = true", "version must be 2"),
    ("[kinds.wait]", "[kinds.work]", "must be exactly"),
    ('met_key = "wait"', 'met_key = "lifestyle:{item_id}"', "met_key must be"),
    ('met_key = "travel:{mode}"', 'met_key = "travel:{mode}:suffix"', "met_key must be"),
    ('met_key_modes = ["driving", "walking"]', 'met_key_modes = ["flying"]', "must declare"),
    ('met_key_modes = ["driving", "walking"]', 'met_key_modes = ["walking", "walking"]', "must declare"),
    ('met_key_modes = ["driving", "walking"]', 'met_key_modes = "walking"', "list of strings"),
    ('met_key_modes = ["driving", "walking"]', 'met_key_modes = [1]', "list of strings"),
    ('met_key = "wait"', 'met_key = "wait"\nmet_key_modes = ["walking"]', "without a mode template"),
    ('energy_floor_exempt = true', 'energy_floor_exempt = "true"', "must be a boolean"),
    ('sleep_channel = "awake"', 'sleep_channel = "night"', "sleep_channel is invalid"),
    ('sleep_channel = "awake"', 'sleep_channel = []', "sleep_channel is invalid"),
    ('relief = { channel = "none" }', 'relief = { channel = "unknown" }', "relief channel is invalid"),
    ('relief = { channel = "none" }', 'relief = { channel = [] }', "relief channel is invalid"),
    ('relief = { channel = "none" }', 'relief = { channel = "none", rate = 1 }', "relief channel is invalid"),
    ('relief = { channel = "none" }', 'relief = { channel = "content_rate" }', "requires target discovery"),
    ('action = "wait"', 'action = "consume"', "action is invalid"),
    ('action = "wait"', 'action = []', "action is invalid"),
    ('resource_claims = ["wait"]', 'resource_claims = "wait"', "is malformed"),
    ('resource_claims = ["wait"]', 'resource_claims = ["wait", "wait"]', "duplicate slots"),
    ('resource_claims = ["wait"]', 'resource_claims = { base = [], invented = [] }', "unknown keys"),
    ('resource_claims = ["wait"]', 'resource_claims = { conditional = "bad" }', "must be a list"),
    ('when = "mode:driving"', 'when = "mode:flying"', "conditional when"),
    ('when = "category:social"', 'when = "category:work"', "conditional when"),
    ('when = "manner:phone_continuous"', 'when = "manner:foreground"', "conditional when"),
    ('when = "category:social"', 'when = "content_kind:article"', "conditional when"),
    ('when = "category:social"', 'when = true', "conditional when"),
    ('when = "category:social", add = ["talk"]', 'when = "category:social", remove = ["talk"]', "exactly when/add"),
    ('when = "category:work_study"', 'when = "category:social"', "duplicate conditions"),
    ('target_discovery = "none"', 'target_discovery = "corpus"', "target_discovery"),
    ('target_discovery = "none"', 'target_discovery = { source = "catalog_field", field = "actions" }', "malformed"),
    ('target_discovery = "none"', 'target_discovery = { source = "other", field = "actions", id_key = "id" }',
     "requires a field"),
    ('target_discovery = "none"', 'target_discovery = { source = "catalog_field", field = "", id_key = "id" }',
     "requires a field"),
    ('target_discovery = "none"', 'target_discovery = { source = "catalog_field", field = "actions", id_key = 3 }',
     "requires an id_key"),
    ('target_discovery = "none"', 'target_discovery = "none"\ntarget_corpus = "lifestyle/items.json"', "unknown keys"),
    ('[kinds.action]', '[kinds.action]\nmet_key = "action"', "MET belongs"),
])
def test_registry_rejects_invalid_declarations(tmp_path, old, new, match, caplog):
    """@brief 原始包规则的单点反例均由真实解析入口明确拒绝。"""
    assert old in REGISTRY_TEXT
    with pytest.raises(IntegrityError, match=match):
        _load_text(tmp_path, REGISTRY_TEXT.replace(old, new))
    assert caplog.messages and all(message.isascii() for message in caplog.messages)


def test_family_layer_and_old_interfaces_are_deleted(tmp_path):
    """@brief 旧家族配置和运行接口没有兼容读取或重导出。"""
    with pytest.raises(IntegrityError, match="unknown keys"):
        _load_text(tmp_path, REGISTRY_TEXT + '\n[families.continue]\nkinds = ["wait"]\n')
    for name in ("FamilySpec", "ContentKindSpec", "GENERATOR_PARAM_KEYS", "PROTOTYPE_GENERATORS",
                 "ATTENTION_KINDS", "HABIT_ACTIONS", "REFERENCEABLE_RESOURCES"):
        assert not hasattr(affordances, name)
    for name in ("families", "family_names", "motivation_families", "families_by_rank", "attention_params",
                 "classify_habit_event", "attention_required_kinds", "met_open_prefixes"):
        assert not hasattr(engine_affordances(), name)


def test_registry_missing_file_and_malformed_toml_are_rejected(tmp_path, caplog):
    """@brief 规则缺失和语法错误直接拒绝，不读取其他文件。"""
    with pytest.raises(IntegrityError, match="does not exist"):
        load_affordances(tmp_path / "absent.toml")
    with pytest.raises(IntegrityError, match="malformed"):
        _load_text(tmp_path, "version = 2\nkinds = [")
    assert caplog.messages


def test_registry_read_or_encoding_failure_is_logged(tmp_path, monkeypatch, caplog):
    """@brief 无法读取的规则保持原因并记录英文错误。"""
    path = tmp_path / "affordances.toml"
    path.write_bytes(REGISTRY_TEXT.encode("utf-8") + b"\xff")
    with pytest.raises(IntegrityError, match="cannot be read") as caught:
        load_affordances(path)
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)

    def denied_read(_path, *args, **kwargs):
        """@brief 只模拟当前选定文件的读取故障。"""
        raise OSError("Read denied")

    monkeypatch.setattr(Path, "read_text", denied_read)
    with pytest.raises(IntegrityError, match="cannot be read") as caught:
        load_affordances(path)
    assert isinstance(caught.value.__cause__, OSError)
    assert caplog.messages and all(message.isascii() for message in caplog.messages)
