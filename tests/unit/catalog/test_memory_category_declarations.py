"""内容行与 app 行的记忆类别覆盖 memory_category：合法写法、行模型拒绝的形状与系列各集同值，经真实加载与纯校验两个入口。"""

import pytest

from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.catalog.loader import load_catalog, validate_catalog_files

#: 行模型对闭集外取值给出的原文。
OUTSIDE = "Input should be '饮食', '工作' or '生活'"


@pytest.fixture
def root(tmp_path):
    return copy_environment(tmp_path / "input")


def change_row(root, name, key, row_id, update):
    """改样例副本里一行（内容行或 app 行），按生产入口刷新清单，返回这一行的行号。"""
    rows = catalog_rows(root, name)
    number = next(index for index, row in enumerate(rows, start=1) if row[key] == row_id)
    rows[number - 1].update(update)
    write_catalog_rows(root, name, rows)
    return number


def add_dinner_series(root, *categories):
    """在家添一个三集的地点系列「做晚饭」，第 k 集写 categories[k-1]（None 表示不写这个键）；行按内容身份升序写回。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    template = next(row for row in rows if row["content_id"] == "c-rest")
    for episode, category in enumerate(categories, start=1):
        row = {**template, "content_id": f"dinner-{episode}", "title": f"晚饭第{episode}步",
               "series": {"title": "做晚饭", "episode": episode}}
        if category is not None:
            row["memory_category"] = category
        rows.append(row)
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))


def refused(root):
    """加载与纯校验给出同一个登记失败。"""
    with pytest.raises(RegistrationError) as loaded:
        load_catalog(root / "catalog", 7)
    with pytest.raises(RegistrationError) as checked:
        validate_catalog_files(root / "catalog")
    assert (loaded.value.code, loaded.value.detail) == (checked.value.code, checked.value.detail)
    return loaded.value


def test_contents_and_apps_may_declare_a_memory_category(root):
    """一条内容写「饮食」、一个 app 写「工作」都能加载，字段值原样；没写的行是 None。"""
    change_row(root, "content-catalog.jsonl", "content_id", "c-rest", {"memory_category": "饮食"})
    change_row(root, "app-catalog.jsonl", "app_id", "bilibili", {"memory_category": "工作"})
    validate_catalog_files(root / "catalog")
    catalog = load_catalog(root / "catalog", 7)
    assert (catalog.contents["c-rest"].memory_category, catalog.apps["bilibili"].memory_category) == ("饮食", "工作")
    assert {row.memory_category for key, row in catalog.contents.items() if key != "c-rest"} == {None}
    assert {row.memory_category for key, row in catalog.apps.items() if key != "bilibili"} == {None}


@pytest.mark.parametrize("name,key,row_id", [
    ("content-catalog.jsonl", "content_id", "c-rest"),
    ("app-catalog.jsonl", "app_id", "bilibili"),
])
@pytest.mark.parametrize("value,text", [
    (None, "A declared memory category must be a category name."),
    ("", OUTSIDE),
    ("休息", OUTSIDE),
    (1, OUTSIDE),
    (["饮食"], OUTSIDE),
])
def test_memory_category_shape_is_refused_by_the_row_model(root, name, key, row_id, value, text):
    """显式 null 与闭集外的值（空串、别的词、数字、数组）都由行模型报 ENVIRONMENT_KIND_INVALID，详情指到那一行。"""
    number = change_row(root, name, key, row_id, {"memory_category": value})
    failure = refused(root)
    assert failure.code == "ENVIRONMENT_KIND_INVALID"
    assert failure.detail.startswith(f"{name}:{number} ")
    assert "memory_category" in failure.detail and text in failure.detail


def test_series_episodes_must_declare_the_same_memory_category(root):
    """三集系列只有第 2 集写「饮食」时报 CONTENT_SERIES_INVALID 并点名系列；三集都写「饮食」时能加载。"""
    add_dinner_series(root, None, "饮食", None)
    failure = refused(root)
    assert (failure.code, failure.detail) == (
        "CONTENT_SERIES_INVALID", "做晚饭 episodes declare different memory categories.")
    rows = [row for row in catalog_rows(root, "content-catalog.jsonl") if not row["content_id"].startswith("dinner-")]
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    add_dinner_series(root, "饮食", "饮食", "饮食")
    catalog = load_catalog(root / "catalog", 7)
    assert {catalog.contents[f"dinner-{episode}"].memory_category for episode in (1, 2, 3)} == {"饮食"}
