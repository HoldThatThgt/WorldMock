"""地点内容凭的状态与完成效果：行模型的形状、登记检查的次序与详情，经真实加载与纯校验两个入口。"""

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog, validate_catalog_files
from worldmock.catalog.models import CompletionEffects

GROCERIES = {"state": "groceries", "initial": 0, "cap": 1, "reset_on_goods": ["tissue"]}
APP_CONTENT = "c-article is an app content; consumes_state and completion_effects belong to location contents."


@pytest.fixture
def root(tmp_path):
    return copy_environment(tmp_path / "input")


def change_content(root, content_id, update):
    """改样例副本里一行内容，按生产入口刷新清单，返回这一行的行号。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    number = next(index for index, row in enumerate(rows, start=1) if row["content_id"] == content_id)
    rows[number - 1].update(update)
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return number


def add_dinner_series(root, *updates):
    """在家添一个三集的地点系列「做晚饭」，第 k 集合并 updates[k-1]；行按内容身份升序写回。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    template = next(row for row in rows if row["content_id"] == "c-rest")
    for episode, update in enumerate(updates, start=1):
        episode_row = {key: value for key, value in template.items()}
        episode_row.update({"content_id": f"dinner-{episode}", "title": f"晚饭第{episode}步",
                            "series": {"title": "做晚饭", "episode": episode}, **update})
        rows.append(episode_row)
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))


def register_states(root, *states):
    write_catalog_rows(root, "callbacks.jsonl", list(states))


def refused(root):
    """加载与纯校验给出同一个登记失败。"""
    with pytest.raises(RegistrationError) as loaded:
        load_catalog(root / "catalog", 7)
    with pytest.raises(RegistrationError) as checked:
        validate_catalog_files(root / "catalog")
    assert (loaded.value.code, loaded.value.detail) == (checked.value.code, checked.value.detail)
    return loaded.value


def test_location_contents_may_declare_a_consumed_state_and_completion_effects(root):
    """两个字段合法的地点内容能加载，互不要求；没写的内容两个字段都是 None，样例原行没有这两个键。"""
    register_states(root, GROCERIES)
    change_content(root, "c-rest", {"consumes_state": "groceries", "completion_effects": {"food": "meal"}})
    change_content(root, "c-chat", {"completion_effects": {"relaxation_relief": "low", "social_relief": "high"}})
    change_content(root, "parcel-service", {"consumes_state": "groceries"})
    validate_catalog_files(root / "catalog")
    catalog = load_catalog(root / "catalog", 7)
    rest, chat, parcel = (catalog.contents[key] for key in ("c-rest", "c-chat", "parcel-service"))
    assert (rest.consumes_state, rest.completion_effects) == ("groceries", CompletionEffects(food="meal"))
    assert (chat.consumes_state, chat.completion_effects) == (
        None, CompletionEffects(relaxation_relief="low", social_relief="high"))
    assert (parcel.consumes_state, parcel.completion_effects) == ("groceries", None)
    for key in ("c-article", "topic-sales"):
        assert (catalog.contents[key].consumes_state, catalog.contents[key].completion_effects) == (None, None)


def test_sample_environment_rows_write_neither_field():
    """样例环境的内容行不写两个新字段，也不写已撤销的 provides_meal。"""
    for row in catalog_rows(ENVIRONMENT_ROOT, "content-catalog.jsonl"):
        assert not {"consumes_state", "completion_effects", "provides_meal"} & set(row)


@pytest.mark.parametrize("update", [
    {"completion_effects": {"food": "meal"}},
    {"completion_effects": {"social_relief": "low"}},
    {"consumes_state": "groceries"},
    {"consumes_state": "groceries", "completion_effects": {"food": "snack"}},
])
def test_app_content_may_not_declare_consumed_state_or_completion_effects(root, update):
    """app 内容写了任一字段都以 ENVIRONMENT_KIND_INVALID 被拒，详情逐字；状态已登记也一样。"""
    register_states(root, GROCERIES)
    change_content(root, "c-article", update)
    failure = refused(root)
    assert str(failure) == f"ENVIRONMENT_KIND_INVALID: A declared kind is outside the fixed set. {APP_CONTENT}"


@pytest.mark.parametrize("states", [(), (dict(GROCERIES, state="pantry"),)])
def test_consumed_state_must_name_a_registered_state(root, states):
    """没有 callbacks.jsonl，或登记的状态里没有这个名字，都报 ENVIRONMENT_REF_UNKNOWN 并点名。"""
    if states:
        register_states(root, *states)
    change_content(root, "c-rest", {"consumes_state": "groceries"})
    failure = refused(root)
    assert (failure.code, failure.message, failure.detail) == (
        "ENVIRONMENT_REF_UNKNOWN", "A binding refers to an object that is not registered.",
        "c-rest consumes_state names groceries.")


def test_empty_completion_effects_is_refused_by_registration(root):
    """三个成员都省略的完成效果由登记拒绝，详情逐字。"""
    change_content(root, "c-rest", {"completion_effects": {}})
    failure = refused(root)
    assert (failure.code, failure.detail) == (
        "ENVIRONMENT_KIND_INVALID", "c-rest declares an empty completion_effects.")


@pytest.mark.parametrize("value,text", [
    (None, "A declared completion effect must be an object."),
    ([], "Input should be a valid dictionary or instance of CompletionEffects"),
    (["food"], "Input should be a valid dictionary or instance of CompletionEffects"),
    ({"food": "meal", "drink": "low"}, "Extra inputs are not permitted"),
    ({"food": None}, "A declared completion effect tier must be a tier name."),
    ({"relaxation_relief": None}, "A declared completion effect tier must be a tier name."),
    ({"social_relief": None}, "A declared completion effect tier must be a tier name."),
    ({"food": "none"}, "Input should be 'snack' or 'meal'"),
    ({"food": 600}, "Input should be 'snack' or 'meal'"),
    ({"food": "dinner"}, "Input should be 'snack' or 'meal'"),
    ({"relaxation_relief": "none"}, "Input should be 'low', 'medium' or 'high'"),
    ({"relaxation_relief": 0.3}, "Input should be 'low', 'medium' or 'high'"),
    ({"social_relief": "none"}, "Input should be 'low', 'medium' or 'high'"),
])
def test_completion_effects_shape_is_refused_by_the_row_model(root, value, text):
    """null、数组、未知成员、成员为 null、档位不在表内（含 none 与数字）都由行模型报 ENVIRONMENT_KIND_INVALID。"""
    number = change_content(root, "c-rest", {"completion_effects": value})
    failure = refused(root)
    assert failure.code == "ENVIRONMENT_KIND_INVALID"
    assert failure.detail.startswith(f"content-catalog.jsonl:{number} ")
    assert "completion_effects" in failure.detail and text in failure.detail


@pytest.mark.parametrize("value,text", [
    (None, "A declared consumed state must be a state name."),
    ("", "String should match pattern"),
    ("  ", "String should match pattern"),
    (3, "Input should be a valid string"),
])
def test_consumed_state_shape_is_refused_by_the_row_model(root, value, text):
    """显式 null、空串、空白与数字都由行模型报 ENVIRONMENT_KIND_INVALID，详情指到那一行。"""
    register_states(root, GROCERIES)
    number = change_content(root, "c-rest", {"consumes_state": value})
    failure = refused(root)
    assert failure.code == "ENVIRONMENT_KIND_INVALID"
    assert failure.detail.startswith(f"content-catalog.jsonl:{number} ")
    assert "consumes_state" in failure.detail and text in failure.detail


def test_row_writing_the_withdrawn_meal_flag_is_refused_as_an_unknown_key(root):
    """已撤销的 provides_meal 只是一个未知键，由行模型以 ENVIRONMENT_KIND_INVALID 拒绝。"""
    number = change_content(root, "c-rest", {"provides_meal": True})
    failure = refused(root)
    assert failure.code == "ENVIRONMENT_KIND_INVALID"
    lines = failure.detail.splitlines()
    assert lines[0] == f"content-catalog.jsonl:{number} 1 validation error for Content"
    assert lines[1] == "provides_meal" and "Extra inputs are not permitted" in lines[2]


@pytest.mark.parametrize("second,third", [
    ({"consumes_state": "pantry"}, {"consumes_state": "groceries"}),
    ({}, {"consumes_state": "groceries"}),
])
def test_series_episodes_must_share_the_consumed_state(root, second, third):
    """同一系列各集的凭的状态必须相同（都不写或写同一个名字），否则报 CONTENT_SERIES_INVALID 并点名系列。"""
    register_states(root, GROCERIES, dict(GROCERIES, state="pantry"))
    add_dinner_series(root, {"consumes_state": "groceries"}, second, third)
    failure = refused(root)
    assert (failure.code, failure.detail) == (
        "CONTENT_SERIES_INVALID", "做晚饭 episodes declare different consumed states.")


def test_series_episodes_declare_their_own_completion_effects(root):
    """各集凭同一个状态、完成效果各写各的，能加载。"""
    register_states(root, GROCERIES)
    add_dinner_series(root, {"consumes_state": "groceries"},
                      {"consumes_state": "groceries", "completion_effects": {"food": "meal"}},
                      {"consumes_state": "groceries", "completion_effects": {"relaxation_relief": "low"}})
    catalog = load_catalog(root / "catalog", 7)
    assert [catalog.contents[f"dinner-{k}"].completion_effects for k in (1, 2, 3)] == [
        None, CompletionEffects(food="meal"), CompletionEffects(relaxation_relief="low")]
    assert {catalog.contents[f"dinner-{k}"].consumes_state for k in (1, 2, 3)} == {"groceries"}


@pytest.mark.parametrize("content_id,update,code,detail", [
    ("c-rest", {"prerequisite": {"location_kinds": ["home"]}, "completion_effects": {}},
     "CONTENT_PREREQUISITE_INVALID", "c-rest is a location content."),
    ("c-article", {"prerequisite": {"location_kinds": ["moon"]}, "consumes_state": "groceries"},
     "CONTENT_BINDING_DANGLING", "c-article prerequisite names moon."),
])
def test_new_checks_come_after_the_prerequisite_checks(root, content_id, update, code, detail):
    """同一行既有前提错误又有新检查的错误时，先报前提那一条。"""
    change_content(root, content_id, update)
    failure = refused(root)
    assert (failure.code, failure.detail) == (code, detail)


def test_new_checks_come_before_the_series_check(root):
    """同一集既与系列绑定不同又写了空的完成效果时，先报新检查那一条。"""
    add_dinner_series(root, {}, {"location_kinds": ["office"], "completion_effects": {}}, {})
    failure = refused(root)
    assert (failure.code, failure.detail) == (
        "ENVIRONMENT_KIND_INVALID", "dinner-2 declares an empty completion_effects.")


def test_new_checks_come_before_the_unit_name_check(root):
    """后登记的一行既与先登记的单元同名又凭了未登记的状态时，先报新检查那一条。"""
    chat = next(row for row in catalog_rows(root, "content-catalog.jsonl") if row["content_id"] == "c-chat")
    change_content(root, "c-rest", {"title": chat["title"]})
    assert refused(root).code == "ENVIRONMENT_ID_DUPLICATE"
    change_content(root, "c-rest", {"consumes_state": "groceries"})
    failure = refused(root)
    assert (failure.code, failure.detail) == ("ENVIRONMENT_REF_UNKNOWN", "c-rest consumes_state names groceries.")


def test_a_state_with_cap_zero_may_be_consumed(root):
    """上限为 0 的状态被凭是合法写法，登记不拦。"""
    register_states(root, dict(GROCERIES, cap=0))
    change_content(root, "c-rest", {"consumes_state": "groceries"})
    validate_catalog_files(root / "catalog")
    assert load_catalog(root / "catalog", 7).contents["c-rest"].consumes_state == "groceries"
