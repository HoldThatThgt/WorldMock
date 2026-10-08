"""@brief 所有目录登记错误码的真实文件最小反例。"""

import json

import pytest

from tests.catalog.conftest import OBJECT_COUNTEREXAMPLES, edit_row, read_payload, write_payload
from worldmock.catalog.errors import REGISTRATION_REASONS, RegistrationError
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.catalog.loader import load_catalog


@pytest.mark.parametrize("filename,identity,updates,code", OBJECT_COUNTEREXAMPLES)

def test_registration_rejects_minimal_object_counterexample(catalog_dir, filename, identity, updates, code):
    """@brief 每条对象规则通过生产发布与加载边界给出稳定码及英文原因。"""
    edit_row(catalog_dir, filename, identity, updates)
    with pytest.raises(RegistrationError) as caught:
        load_catalog(catalog_dir)
    assert caught.value.code == code
    assert caught.value.message == REGISTRATION_REASONS[code]


def test_registration_rejects_missing_goods_binding(catalog_dir):
    """@brief 没有绑定字段时不能补齐默认店铺。"""
    payload = read_payload(catalog_dir, "goods.json")
    payload["goods"][0].pop("store_ids")
    write_payload(catalog_dir, "goods.json", payload)
    with pytest.raises(RegistrationError, match="GOODS_BINDING_MISSING"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("field,value", [("rules_version", 1), ("size_bytes", -1)])
def test_registration_rejects_manifest_values(catalog_dir, field, value):
    """@brief 清单规则版本和数字域不能越界。"""
    payload = read_payload(catalog_dir, "manifest.json")
    if field == "size_bytes":
        payload["files"][0][field] = value
    else:
        payload[field] = value
    write_payload(catalog_dir, "manifest.json", payload, refresh=field == "rules_version")
    code = "RULES_VERSION_MISMATCH" if field == "rules_version" else "ENVIRONMENT_NUMBER_INVALID"
    with pytest.raises(RegistrationError, match=code):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("name", [
    "foods.json", "work_topics.json", "affordances.toml", "rules/travel-time.toml",
])
def test_registration_rejects_every_retired_file(catalog_dir, name):
    """@brief 清单未列出的历史文件不能被加载器静默忽略。"""
    (catalog_dir / name).parent.mkdir(parents=True, exist_ok=True)
    (catalog_dir / name).write_text("{}\n")
    with pytest.raises(RegistrationError, match="ENVIRONMENT_STALE_FILE"):
        load_catalog(catalog_dir)


def test_registration_rejects_missing_manifest_file_entry(catalog_dir):
    """@brief 清单必须恰好登记所有必需文件。"""
    payload = read_payload(catalog_dir, "manifest.json")
    payload["files"].pop()
    write_payload(catalog_dir, "manifest.json", payload, refresh=False)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_FILE_UNLISTED"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("change", ["bytes", "digest", "size", "missing", "manifest_hash"])
def test_registration_rejects_file_identity_drift(catalog_dir, change):
    """@brief 真实字节、大小、摘要或文件集合漂移均不能继续登记。"""
    payload = read_payload(catalog_dir, "manifest.json")
    if change == "bytes":
        with (catalog_dir / "goods.json").open("ab") as handle:
            handle.write(b" ")
    elif change == "missing":
        (catalog_dir / "goods.json").unlink()
    else:
        if change == "manifest_hash":
            payload["catalog_content_sha256"] = "0" * 64
        elif change == "digest":
            payload["files"][0]["sha256"] = "0" * 64
        else:
            payload["files"][0]["size_bytes"] += 1
        write_payload(catalog_dir, "manifest.json", payload, refresh=False)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        load_catalog(catalog_dir)


def test_registration_rejects_unbound_app(catalog_dir):
    """@brief 应用必须有店铺或内容绑定。"""
    payload = read_payload(catalog_dir, "apps.json")
    payload["apps"].append({"app_id": "z-unbound", "name": "空应用", "purposes": [], "payment_app_ids": []})
    write_payload(catalog_dir, "apps.json", payload)
    with pytest.raises(RegistrationError, match="APP_UNBOUND"):
        load_catalog(catalog_dir)


def test_registration_rejects_duplicate_identity(catalog_dir):
    """@brief 建索引前捕获重复身份，不能被字典构造覆盖。"""
    payload = read_payload(catalog_dir, "apps.json")
    payload["apps"].append(dict(payload["apps"][0]))
    write_payload(catalog_dir, "apps.json", payload)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_ID_DUPLICATE"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("case", ["self", "duplicate", "unknown", "zero", "unsorted", "nodes", "disconnected"])
def test_registration_rejects_invalid_map(catalog_dir, case):
    """@brief 自环、重复、未知端点、零长、乱序与不连通各由真实加载拒绝。"""
    graph = read_payload(catalog_dir, "map.json")
    edge = graph["edges"][0]
    if case == "self":
        edge["b"] = edge["a"]
    elif case == "duplicate":
        graph["edges"].append(dict(edge))
    elif case == "unknown":
        edge["b"] = "unknown"
    elif case == "zero":
        edge["length_m"] = 0
    elif case == "unsorted":
        graph["edges"].reverse()
    elif case == "nodes":
        graph["nodes"].pop()
    else:
        graph["edges"] = [edge]
    write_payload(catalog_dir, "map.json", graph)
    code = "MAP_DISCONNECTED" if case == "disconnected" else "MAP_EDGE_INVALID"
    with pytest.raises(RegistrationError, match=code):
        load_catalog(catalog_dir)


def test_registration_rejects_too_small_map(catalog_dir):
    """@brief 单节点图不能登记为可出行地图。"""
    graph = read_payload(catalog_dir, "map.json")
    graph.update(nodes=["home"], edges=[])
    write_payload(catalog_dir, "map.json", graph)
    with pytest.raises(RegistrationError, match="MAP_TOO_SMALL"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("field", ["duration_seconds", "action_id"])
def test_registration_rejects_missing_content_fields(catalog_dir, field):
    """@brief 内容动作身份和时长不能由加载器猜测或补齐。"""
    payload = read_payload(catalog_dir, "contents.json")
    payload["contents"][0].pop(field)
    write_payload(catalog_dir, "contents.json", payload)
    code = "CONTENT_ACTION_MISSING" if field == "action_id" else "ENVIRONMENT_NUMBER_INVALID"
    with pytest.raises(RegistrationError, match=code):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("identity,app", [("c-run", None), (None, "douyin")])
def test_registration_rejects_action_without_bound_content(catalog_dir, identity, app):
    """@brief 删掉动作内容或一个动作应用组合的全部内容都必须拒绝。"""
    payload = read_payload(catalog_dir, "contents.json")
    payload["contents"] = [row for row in payload["contents"]
                           if row["content_id"] != identity and (app is None or app not in row["app_ids"])]
    write_payload(catalog_dir, "contents.json", payload)
    with pytest.raises(RegistrationError, match="ACTION_CONTENT_EMPTY"):
        load_catalog(catalog_dir)


def test_registration_accepts_self_payment_binding(catalog_dir):
    """@brief 同一个真实应用可以同时提供购物和支付用途，并自绑定支付。"""
    edit_row(catalog_dir, "apps.json", "meituan", {"purposes": ["payment", "shopping"],
                                                "payment_app_ids": ["alipay", "meituan", "wechat"]})
    catalog = load_catalog(catalog_dir)
    assert catalog.apps_by_purpose["payment"] == ("alipay", "meituan", "wechat")


@pytest.mark.parametrize("value", [60, 10 ** 9])
def test_registration_accepts_content_minimum_without_upper_limit(catalog_dir, value):
    """@brief 内容登记允许下限恰值和很长内容，没有隐含默认窗口。"""
    edit_row(catalog_dir, "contents.json", "c-short", {"duration_seconds": value})
    assert load_catalog(catalog_dir).contents["c-short"].duration_seconds == value


@pytest.mark.parametrize("value", [True, 60.0, "60", float("nan"), float("inf")])
def test_registration_rejects_noninteger_content_duration(catalog_dir, value):
    """@brief 数字必须是真正整数，布尔、浮点、字符串和非有限值不能冒充。"""
    payload = read_payload(catalog_dir, "contents.json")
    payload["contents"][0]["duration_seconds"] = value
    (catalog_dir / "contents.json").write_text(json.dumps(payload) + "\n", encoding="utf-8", newline="\n")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_NUMBER_INVALID"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("filename,identity,field,value,code", [
    ("apps.json", "meituan", "payment_app_ids", ["alipay", "alipay"], "ENVIRONMENT_ID_DUPLICATE"),
    ("apps.json", "meituan", "purposes", ["shopping", "shopping"], "ENVIRONMENT_ID_DUPLICATE"),
    ("apps.json", "meituan", "payment_app_ids", ["wechat", "alipay"], "ENVIRONMENT_KIND_INVALID"),
    ("apps.json", "meituan", "payment_app_ids", ["unknown"], "ENVIRONMENT_REF_UNKNOWN"),
    ("actions.json", "run", "app_ids", ["unknown"], "ENVIRONMENT_REF_UNKNOWN"),
    ("actions.json", "run", "app_ids", ["keep", "keep"], "ENVIRONMENT_ID_DUPLICATE"),
    ("contents.json", "c-short", "app_ids", ["unknown"], "ENVIRONMENT_REF_UNKNOWN"),
    ("goods.json", "chips", "store_ids", ["cafe", "cafe"], "ENVIRONMENT_ID_DUPLICATE"),
])
def test_registration_rejects_binding_identity_errors(catalog_dir, filename, identity, field, value, code):
    """@brief 各层绑定在构造映射前拒绝未知、重复或未排序的身份。"""
    edit_row(catalog_dir, filename, identity, {field: value})
    with pytest.raises(RegistrationError, match=code):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("filename", ["apps.json", "actions.json", "goods.json", "contents.json"])
def test_registration_rejects_unsorted_objects(catalog_dir, filename):
    """@brief 每个对象文件必须按身份排序，不在加载期重排。"""
    payload = read_payload(catalog_dir, filename)
    next(iter(payload.values())).reverse()
    write_payload(catalog_dir, filename, payload)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_KIND_INVALID"):
        load_catalog(catalog_dir)


def test_registration_rejects_legacy_catalog_version(catalog_dir):
    """@brief 格式一直接拒绝，不读取旧文件或运行转换。"""
    payload = read_payload(catalog_dir, "manifest.json")
    payload["format_version"] = 1
    write_payload(catalog_dir, "manifest.json", payload)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_KIND_INVALID"):
        load_catalog(catalog_dir)


def test_registration_rejects_stale_manifest_path(catalog_dir):
    """@brief 清单不能列出目录固定六文件之外的旧文件或规则。"""
    payload = read_payload(catalog_dir, "manifest.json")
    payload["files"].append({"path": "foods.json", "sha256": "0" * 64, "size_bytes": 0})
    write_payload(catalog_dir, "manifest.json", payload, refresh=False)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_STALE_FILE"):
        load_catalog(catalog_dir)


def test_registration_rejects_empty_rules_directory(catalog_dir):
    """@brief 已退出环境的规则目录即使为空也不能继续发布。"""
    (catalog_dir / "rules").mkdir()
    with pytest.raises(RegistrationError, match="ENVIRONMENT_STALE_FILE"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("filename", ["manifest.json", "contents.json"])
def test_registration_rejects_crlf_json(catalog_dir, filename):
    """@brief 文件身份重新发布不能使 CRLF JSON 通过目录格式要求。"""
    path = catalog_dir / filename
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    if filename != "manifest.json":
        refresh_manifest_identity(catalog_dir)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_KIND_INVALID"):
        load_catalog(catalog_dir)


def test_registration_uses_episode_number_for_series_index(catalog_dir):
    """@brief 系列索引按集数而非内容标识排序，标题本身不要求唯一。"""
    edit_row(catalog_dir, "contents.json", "c-drama-1", {"series": {"title": "测试剧集", "episode": 3}})
    edit_row(catalog_dir, "contents.json", "c-drama-3", {"series": {"title": "测试剧集", "episode": 1}})
    catalog = load_catalog(catalog_dir)
    assert catalog.contents_by_series[("browse-feed", "测试剧集")] == ("c-drama-3", "c-drama-2", "c-drama-1")


def test_registration_reasons_have_only_the_frozen_error_codes():
    """@brief 新的三十四个登记码是唯一集合，退役食品与工作主题码不存在。"""
    assert len(REGISTRATION_REASONS) == 34
    assert not any(code.startswith("FOOD_") or code == "WORK_TOPIC_UNKNOWN" or code == "CONTENT_APP_MISSING"
                   for code in REGISTRATION_REASONS)


@pytest.mark.parametrize("app_name,action_name,title", [
    ("王者荣耀", "玩王者荣耀", "测试对局"),
    ("网易云音乐", "听音乐", "测试歌曲"),
    ("小宇宙", "听轻松播客", "测试播客"),
])
def test_content_accepts_custom_actions_without_a_system_content_kind(catalog_dir, app_name, action_name, title):
    """@brief 游戏、音乐与播客仅由环境动作及内容表达，不要求系统维护内容分类。"""
    edit_row(catalog_dir, "apps.json", "douyin", {"name": app_name})
    edit_row(catalog_dir, "actions.json", "browse-feed", {"name": action_name})
    edit_row(catalog_dir, "contents.json", "c-short", {"title": title})
    catalog = load_catalog(catalog_dir)
    content = catalog.contents["c-short"]
    assert content.action_id == "browse-feed" and content.app_ids == ("douyin",)
    assert catalog.actions[content.action_id].name == action_name
    assert catalog.apps[content.app_ids[0]].name == app_name
    assert content.title == title
    assert "kind" not in content.model_dump()
