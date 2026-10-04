"""@brief 冻结地图的文件身份、完整关系与场景引用准入验证。"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.errors import IntegrityError
from worldmock.life.map_data import load_map_dataset, select_map
from worldmock.life.map_models import MAP_SCHEMA_VERSION, LocationConfig, MapDatasetReference, RouteConfig
from tests.life.test_opening_hours import location, source


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def route(origin, target, **changes):
    value = {"route_id": f"{origin}-{target}-walking", "origin_id": origin, "destination_id": target,
             "mode": "walking", "status": "unknown", "reason": "not prepared", "fare_source_kind": "scenario",
             "source_url": "https://router.example/route/v1/foot/0,0;1,1", "routing_instance": "https://router.example",
             "routing_profile": "foot", "source": source()}
    value.update(changes)
    return value


def create_dataset(tmp_path, edit=None, metadata_changes=None):
    first = location().model_dump(mode="json")
    second = {**first, "location_id": "home", "kind": "home", "business_kind": "home", "location_kinds": ["home"]}
    declarations = [{**first["opening_calendars"][0], "price_version": "v1", "price_fen": 500}]
    for item in declarations:
        del item["weekly"], item["exceptions"]
    supply = {"supply_id": "s", "location_id": "store", "product_id": "p", "seller_account_id": "seller",
              "quotes": declarations, "initial_stock": 10, "fulfillment_modes": ["pickup"],
              "delivery_destination_ids": [], "delivery_delay_seconds": 0, "required_qualifications": [],
              "source": source()}
    data = {"locations": [first, second], "supplies": [supply],
            "routes": [route("home", "store"), route("store", "home")]}
    if edit:
        edit(data)
    metadata = {"schema_version": MAP_SCHEMA_VERSION, "dataset_id": "test", "version": 1,
                "coordinate_system": "WGS84", "timezone": "Asia/Shanghai", "bounds": [121, 31, 122, 32],
                "prepared_at": "2026-09-20T12:00:00+08:00", "source": source(), "route_modes": ["walking"]}
    from worldmock.life.readable import PLACE_TYPES

    kinds = {kind: [] for kind in PLACE_TYPES}
    kinds["retail"] = ["food"]
    assignment = {"kinds": kinds, "locations": [{"location_id": "store", "category_ids": [],
                   "object_ids": ["p"], "price_position_percent": 50, "source": source()}], "source": source()}
    identity = write(tmp_path / "zhangjiang-map/category-assignment.json", assignment)
    metadata.update(random_seed=1, category_assignment_sha256=identity["sha256"])
    if metadata_changes is not None:
        metadata.update(metadata_changes)
    for name, rows in data.items():
        payload = {"schema_version": MAP_SCHEMA_VERSION, name: rows}
        metadata[f"{name}_file"] = write(tmp_path / f"{name}.json", payload)
    ref = write(tmp_path / "dataset.json", metadata)
    return MapDatasetReference.model_validate({**ref, "dataset_id": "test", "version": 1})


def scenario(**changes):
    result = SimpleNamespace(timezone="Asia/Shanghai", random_seed=1, locations=[SimpleNamespace(location_id="store"),
        SimpleNamespace(location_id="home")], supplies=[SimpleNamespace(supply_id="s", initial_stock=3)],
        products=[SimpleNamespace(product_id="p")], accounts=[SimpleNamespace(account_id="seller")],
        map_dataset=SimpleNamespace(dataset_id="test", version=1))
    for name, value in changes.items():
        setattr(result, name, value)
    return result


def test_selected_map_preserves_unknown_route_and_instance_stock(tmp_path):
    ref = create_dataset(tmp_path)
    dataset = load_map_dataset(tmp_path, ref)
    places, routes, supplies = select_map(dataset, scenario())
    assert {item.location_id for item in places} == {"home", "store"}
    assert {item.status for item in routes} == {"unknown"}
    assert supplies[0].initial_stock == 3
    assert dataset.supplies[0].initial_stock == 10


def test_file_tampering_is_rejected_before_parsing(tmp_path):
    ref = create_dataset(tmp_path)
    (tmp_path / "supplies.json").write_text('{}')
    with pytest.raises(IntegrityError, match="hash mismatch"):
        load_map_dataset(tmp_path, ref)


def test_map_identity_pin_is_checked(tmp_path):
    ref = create_dataset(tmp_path).model_copy(update={"version": 2})
    with pytest.raises(IntegrityError, match="identity"):
        load_map_dataset(tmp_path, ref)


@pytest.mark.parametrize("name", ["locations", "supplies", "routes"])
@pytest.mark.parametrize("change", [{"schema_version": 1}, {"schema_version": MAP_SCHEMA_VERSION + 1},
                                    {"unrecognized_section": []}])
def test_frozen_directory_envelope_is_closed_even_with_correct_hash(tmp_path, name, change):
    """@brief 文件哈希正确也不能发布未知结构或未支持的目录版本：旧版本 1 与未来版本都拒绝，不做兼容解析。"""
    ref = create_dataset(tmp_path)
    metadata = json.loads((tmp_path / ref.path).read_text())
    payload = json.loads((tmp_path / f"{name}.json").read_text())
    metadata[f"{name}_file"] = write(tmp_path / f"{name}.json", {**payload, **change})
    ref = MapDatasetReference.model_validate({**write(tmp_path / ref.path, metadata),
                                            "dataset_id": ref.dataset_id, "version": ref.version})
    with pytest.raises(IntegrityError, match=f"Map {name} file schema is invalid"):
        load_map_dataset(tmp_path, ref)


def test_old_map_structure_version_is_rejected(tmp_path):
    """@brief 地图文件结构随 branch_label 升到 2：结构 1 的数据集声明直接拒绝，不做兼容解析或迁移。"""
    assert MAP_SCHEMA_VERSION == 3
    ref = create_dataset(tmp_path, metadata_changes={"schema_version": 1})
    with pytest.raises(IntegrityError, match="Map dataset validation failed") as failure:
        load_map_dataset(tmp_path, ref)
    assert "schema_version" in str(failure.value)


@pytest.mark.parametrize("label", ["", " "], ids=["empty", "space"])
def test_branch_label_must_be_nonblank_when_declared(label):
    """@brief 分店标签可选；声明了就不能是空白（空白标签无法区分同名地点）。"""
    assert location().branch_label is None
    with pytest.raises(ValidationError, match="branch_label"):
        LocationConfig.model_validate({**location().model_dump(), "branch_label": label})


def test_selected_locations_use_display_names_and_the_map_keeps_branch_labels(tmp_path):
    """@brief 同名的两处地点各带分店标签：场景选图后的运行时地点名是「名称（分店）」且不再带标签（不会重复拼接），
    地图本身仍保留原名与标签；没有标签的地点原样选出。"""
    def branches(data):
        """@brief 两处地点同名，各带不同的分店标签。"""
        data["locations"][0].update(name="全家", branch_label="东昌路店")
        data["locations"][1].update(name="全家", branch_label="张江路店")
    dataset = load_map_dataset(tmp_path, create_dataset(tmp_path, branches))
    assert [(item.name, item.branch_label, item.display_name) for item in dataset.locations] == [
        ("全家", "东昌路店", "全家（东昌路店）"), ("全家", "张江路店", "全家（张江路店）")]
    places, _, _ = select_map(dataset, scenario())
    assert {item.location_id: (item.name, item.branch_label) for item in places} == {
        "store": ("全家（东昌路店）", None), "home": ("全家（张江路店）", None)}
    assert all(item.display_name == item.name for item in places)
    plain = load_map_dataset(tmp_path / "plain", create_dataset(tmp_path / "plain"))
    places, _, _ = select_map(plain, scenario())
    assert {item.location_id: item for item in places} == {item.location_id: item for item in plain.locations}


@pytest.mark.parametrize("change,reason", [
    ({"timezone": "Invalid/Map-Timezone"}, "Map timezone is invalid"),
    ({"bounds": [122, 31, 121, 32]}, "coordinate bounds are invalid"),
    ({"bounds": [-181, 31, 122, 32]}, "coordinate bounds are invalid"),
    ({"bounds": [121, 32, 122, 31]}, "coordinate bounds are invalid"),
    ({"bounds": [121, -91, 122, 32]}, "coordinate bounds are invalid"),
    ({"route_modes": ["walking", "walking"]}, "route modes must contain unique"),
])
def test_publication_requires_valid_timezone_bounds_and_unique_modes(tmp_path, change, reason):
    """@brief 发布坐标边界、时区和交通方式必须形成无歧义的地图元数据。"""
    ref = create_dataset(tmp_path, metadata_changes=change)
    with pytest.raises(IntegrityError, match=reason):
        load_map_dataset(tmp_path, ref)


def test_route_mode_must_be_declared_by_the_dataset(tmp_path):
    """@brief 单条路线的方式与路由配置合法，仍不得越出整版地图声明的交通方式。"""
    def driving(data):
        data["routes"][0].update(route_id="home-store-driving", mode="driving", routing_profile="car",
                                  source_url="https://router.example/route/v1/car/0,0;1,1")
    ref = create_dataset(tmp_path, driving)
    with pytest.raises(IntegrityError, match="Map route uses an undeclared travel mode"):
        load_map_dataset(tmp_path, ref)


@pytest.mark.parametrize("edit,reason", [
    (lambda data: data["locations"][0].update(longitude=120), "outside"),
    (lambda data: data["locations"].append(dict(data["locations"][0])), "unique"),
    (lambda data: data["supplies"].append({**data["supplies"][0], "supply_id": "another"}), "unique"),
    (lambda data: data["supplies"][0].update(location_id="missing"), "unknown"),
    (lambda data: data["routes"][0].update(destination_id="missing"), "unknown"),
    (lambda data: data["supplies"][0]["quotes"][0].update(still_valid_on="2026-09-19"), "validation failed"),
    (lambda data: data["supplies"][0]["quotes"][0].update(reviewed_on="2026-09-19", still_valid_on="2026-09-19"),
     "stale"),
])
def test_incomplete_or_stale_map_declarations_are_rejected(tmp_path, edit, reason):
    ref = create_dataset(tmp_path, edit)
    with pytest.raises(IntegrityError, match=reason):
        load_map_dataset(tmp_path, ref)


def test_missing_route_record_cannot_mean_unreachable(tmp_path):
    ref = create_dataset(tmp_path, lambda data: data["routes"].pop())
    dataset = load_map_dataset(tmp_path, ref)
    with pytest.raises(IntegrityError, match="coverage is incomplete"):
        select_map(dataset, scenario())


@pytest.mark.parametrize("changes,reason", [
    ({"products": []}, "unselected product"), ({"accounts": []}, "undeclared seller"),
    ({"timezone": "UTC"}, "timezone"),
    ({"random_seed": 2}, "Scenario random seed disagrees with the selected map"),
    ({"map_dataset": SimpleNamespace(dataset_id="test", version=2)}, "identity"),
    ({"locations": [SimpleNamespace(location_id="missing")]}, "unknown map location"),
])
def test_selected_scope_is_enforced(tmp_path, changes, reason):
    dataset = load_map_dataset(tmp_path, create_dataset(tmp_path))
    with pytest.raises(IntegrityError, match=reason):
        select_map(dataset, scenario(**changes))


@pytest.mark.parametrize("change", [{"location_kinds": []}, {"location_kinds": ["anywhere"]},
                                     {"open_hour": 0}, {"location_kinds": ["supermarket"]}])
def test_locations_reject_old_or_inferred_activity_environments(change):
    with pytest.raises(ValidationError):
        LocationConfig.model_validate({**location().model_dump(), **change})


def test_map_publication_rejects_missing_activity_environment_with_valid_file_identity(tmp_path, caplog):
    """@brief 文件与哈希完全一致仍不能给缺失的地点活动环境补默认值。"""
    ref = create_dataset(tmp_path, lambda data: data["locations"][0].pop("location_kinds"))
    with pytest.raises(IntegrityError, match="Map dataset validation failed") as failure:
        load_map_dataset(tmp_path, ref)
    assert "location_kinds" in str(failure.value)
    assert "Field required" in str(failure.value)
    assert "Map dataset loading failed" in caplog.text


def test_unreachable_status_requires_real_no_route_evidence(tmp_path):
    response = write(tmp_path / "no-route.json", {"code": "NoRoute"})
    def edit(data):
        data["routes"][0].update(status="unreachable", response_path=response["path"],
                                 response_sha256=response["sha256"])
    ref = create_dataset(tmp_path, edit)
    dataset = load_map_dataset(tmp_path, ref)
    assert dataset.routes[0].status == "unreachable"
    write(tmp_path / "no-route.json", {"code": "Ok", "routes": []})
    with pytest.raises(IntegrityError, match="hash mismatch"):
        load_map_dataset(tmp_path, ref)


def test_unknown_status_cannot_claim_reachable_metrics_or_missing_reason():
    for changes in ({"distance_meters": 100}, {"reason": None}, {"routing_profile": "car"}):
        with pytest.raises(ValidationError):
            RouteConfig.model_validate(route("home", "store", **changes))


@pytest.mark.parametrize("metric", ["distance_meters", "duration_seconds", "fare_fen"])
def test_reachable_route_cannot_omit_any_travel_cost(metric):
    """@brief 可达路线不得把距离、时间或金额之一留空后继续用于行程判断。"""
    payload = route("home", "store", status="reachable", reason=None, distance_meters=100,
                    duration_seconds=80, fare_fen=0, response_path="road.json", response_sha256="0" * 64)
    payload[metric] = None
    with pytest.raises(ValidationError, match="Reachable route requires distance, duration, and fare"):
        RouteConfig.model_validate(payload)


@pytest.mark.parametrize("evidence", [{}, {"response_path": "no-route.json"}, {"response_sha256": "0" * 64}])
def test_unreachable_declaration_requires_both_response_path_and_hash(evidence):
    """@brief 不可达是已确认的路由结果，必须同时钉扎响应位置和内容身份。"""
    with pytest.raises(ValidationError, match="Known route status requires a frozen response"):
        RouteConfig.model_validate(route("home", "store", status="unreachable", **evidence))


def test_route_cannot_connect_a_location_to_itself():
    """@brief 同地行动不应伪造成一条旅行路线。"""
    with pytest.raises(ValidationError, match="Route endpoints must differ"):
        RouteConfig.model_validate(route("home", "home"))


@pytest.mark.parametrize("change,reason", [
    ({"fulfillment_modes": ["pickup", "delivery"]}, "Delivery support must match its explicit destination scope"),
    ({"delivery_destination_ids": ["home"]}, "Delivery support must match its explicit destination scope"),
    ({"required_qualifications": ["member", "member"]}, "Supply qualifications must contain unique nonblank values"),
    ({"required_qualifications": [" "]}, "Supply qualifications must contain unique nonblank values"),
])
def test_supply_fulfillment_scope_and_qualification_identities_are_explicit(tmp_path, change, reason):
    """@brief 配送能力与服务范围必须双向一致，资格名称不得重复或为空白。"""
    ref = create_dataset(tmp_path, lambda data: data["supplies"][0].update(change))
    with pytest.raises(IntegrityError, match=reason):
        load_map_dataset(tmp_path, ref)


def reachable_response():
    return {"code": "Ok", "routes": [{"distance": 100.1, "duration": 80.2,
        "geometry": {"type": "LineString", "coordinates": [[121.6, 31.2], [121.61, 31.2]]},
        "legs": [{"steps": [{"mode": "walking"}]}]}]}


@pytest.mark.parametrize("edit,reason", [
    (lambda row: row.update(geometry={}), "geometry"),
    (lambda row: row.update(legs=[]), "mode"),
    (lambda row: row["legs"][0]["steps"][0].update(mode="driving"), "mode"),
    (lambda row: row.update(distance=101), "distance"),
    (lambda row: row.update(duration=83), "duration"),
])
def test_reachable_routes_require_actual_geometry_mode_and_metrics(tmp_path, edit, reason):
    payload = reachable_response()
    edit(payload["routes"][0])
    response = write(tmp_path / "road.json", payload)
    def declare(data):
        data["routes"][0].update(status="reachable", reason=None, response_path=response["path"],
            response_sha256=response["sha256"], distance_meters=100.1, duration_seconds=80, fare_fen=0)
    ref = create_dataset(tmp_path, declare)
    with pytest.raises(IntegrityError, match=reason):
        load_map_dataset(tmp_path, ref)


def test_future_source_retrieval_rejects_map_publication(tmp_path):
    def future(data):
        data["supplies"][0]["source"]["retrieved_at"] = "2026-09-21T00:00:00+08:00"
    ref = create_dataset(tmp_path, future)
    with pytest.raises(IntegrityError, match="retrieval cannot follow publication"):
        load_map_dataset(tmp_path, ref)


def test_source_cannot_claim_facts_after_its_retrieval_date(tmp_path):
    """@brief 来源不能声称覆盖尚未取得资料的未来事实。"""
    def future_cutoff(data):
        data["supplies"][0]["source"]["fact_cutoff"] = "2026-09-21"
    ref = create_dataset(tmp_path, future_cutoff)
    with pytest.raises(IntegrityError, match="Source fact cutoff cannot follow retrieval date"):
        load_map_dataset(tmp_path, ref)


@pytest.mark.parametrize("kind", ["quote", "opening"])
def test_review_cannot_claim_facts_after_the_source_cutoff(tmp_path, kind):
    def outdated(data):
        declaration = (data["supplies"][0]["quotes"][0] if kind == "quote"
                       else data["locations"][0]["opening_calendars"][0])
        declaration["source"]["fact_cutoff"] = "2026-09-19"
    ref = create_dataset(tmp_path, outdated)
    with pytest.raises(IntegrityError, match="review date cannot follow its source fact cutoff"):
        load_map_dataset(tmp_path, ref)


def test_preparation_fetches_full_selected_set_and_publishes_verifiable_version(tmp_path, monkeypatch):
    from worldmock.corpora import map_prepare
    from worldmock.catalog.loader import load_catalog

    source_root = tmp_path / "reviewed"
    create_dataset(source_root)
    folder = source_root / "zhangjiang-map"
    folder.mkdir(exist_ok=True)
    seed_folder = folder / "1"
    seed_folder.mkdir()
    (source_root / "dataset.json").rename(seed_folder / "dataset.json")
    write(folder / "supply-assumptions.json", {"source": "fixture"})
    (folder / "zhangjiang-probe.osm").write_text("<osm/>")
    catalog = load_catalog(Path("catalog"))
    monkeypatch.setattr(map_prepare, "load_catalog", lambda path: catalog)
    monkeypatch.setattr(map_prepare, "build_storefront_supplies",
                        lambda fare, assignment, locations, assumptions, seed:
                        load_map_dataset(source_root, MapDatasetReference(
                            dataset_id="test", version=1, path="zhangjiang-map/1/dataset.json",
                            sha256=hashlib.sha256((seed_folder / "dataset.json").read_bytes()).hexdigest())).supplies)
    monkeypatch.setattr(map_prepare, "validate_assortments", lambda dataset, fare: None)
    calls, waits = [], []
    def fetch(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=reachable_response)
    monkeypatch.setattr(map_prepare.requests, "get", fetch)
    monkeypatch.setattr(map_prepare.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(map_prepare.time, "sleep", waits.append)
    target = tmp_path / "release"
    reference = map_prepare.prepare_map(target, source_root, 1)
    dataset = load_map_dataset(target, reference)
    assert reference.version == 2 and len(dataset.routes) == 2
    assert len(calls) == 2 and all("/route/v1/foot/" in url for url in calls)
    assert waits == [1.0, 1.0]
    assert all(item.status == "reachable" and item.duration_seconds == 80 for item in dataset.routes)
    assert all(item.source.kind == "fact" and item.source.url in calls for item in dataset.routes)
    assert all(item.fare_fen == 0 for item in dataset.routes)


def test_preparation_rejects_driving_response_for_walking_profile(tmp_path, monkeypatch):
    from worldmock.corpora import map_prepare

    payload = reachable_response()
    payload["routes"][0]["legs"][0]["steps"][0]["mode"] = "driving"
    with pytest.raises(IntegrityError, match="mode is invalid"):
        map_prepare._validate_response(payload, RouteConfig.model_validate(route("home", "store")))


_FORMAL_SCENARIO = Path("scenarios/white-collar-12-weeks.json")
_FORMAL_MAP = "zhangjiang-map/20260105/dataset.json"
_OTHER_SEED_MAP = "zhangjiang-map/20260106/dataset.json"


def _reseed(document):
    """@brief 只把场景种子改成另一份冻结地图的种子，地图钉定保持原样。"""
    document["random_seed"] = 20260106


def _repin(document):
    """@brief 只把地图钉定换成另一个种子的冻结地图（地图身份与版本相同），场景种子保持原样。"""
    content = (Path("corpora") / _OTHER_SEED_MAP).read_bytes()
    document["map_dataset"] = {**document["map_dataset"], "path": _OTHER_SEED_MAP,
                               "sha256": hashlib.sha256(content).hexdigest()}


@pytest.mark.parametrize("change", [_reseed, _repin], ids=["seed-changed", "other-seed-map-pinned"])
def test_formal_scenario_seed_must_agree_with_its_pinned_map(tmp_path, change):
    """@brief 同一地图身份与版本按种子各冻结一份报价：正式场景的种子与所钉地图的种子不一致时，真实加载入口在选图时
    拒绝并写明原因，不会拿另一个种子的报价继续运行。"""
    from tests.engine_fixture import (_mutated_scenario)  # noqa: F401
    from worldmock.life.world_setup import load_runtime_scenario

    path = _mutated_scenario(_FORMAL_SCENARIO, change, tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    dataset = load_map_dataset(Path("corpora"), MapDatasetReference.model_validate(document["map_dataset"]))
    assert dataset.timezone == document["timezone"] and dataset.random_seed != document["random_seed"]
    with pytest.raises(IntegrityError) as failure:
        load_runtime_scenario(path)
    assert str(failure.value) == "Scenario assembly failed: Scenario random seed disagrees with the selected map"


def _republished_assignment(root, edit):
    """@brief 把仓库地图的数据集声明与逐店品类声明复制到隔离根，改写品类声明后按新的文件哈希重新钉定。"""
    source = Path("corpora/zhangjiang-map")
    assignment = json.loads((source / "category-assignment.json").read_text(encoding="utf-8"))
    edit(assignment)
    identity = write(root / "zhangjiang-map/category-assignment.json", assignment)
    declaration = json.loads((Path("corpora") / _FORMAL_MAP).read_text(encoding="utf-8"))
    declaration["category_assignment_sha256"] = identity["sha256"]
    pinned = write(root / _FORMAL_MAP, declaration)
    return MapDatasetReference(dataset_id=declaration["dataset_id"], version=declaration["version"],
                               path=_FORMAL_MAP, sha256=pinned["sha256"])


def _empty_store_assortment(assignment):
    """@brief 第一家门店的声明既不选品类也不点名对象。"""
    assignment["locations"][0].update(category_ids=[], object_ids=[])


def _blank_location_kind(assignment):
    """@brief 准入表多出一个只有空白字符的地点类别。"""
    assignment["kinds"][" "] = []


@pytest.mark.parametrize("edit,reason", [
    (_empty_store_assortment, "Selling locations require a nonempty assortment"),
    (_blank_location_kind, "Assortment kind must be nonblank"),
], ids=["empty-store-assortment", "blank-location-kind"])
def test_category_assignment_rejects_an_empty_assortment_and_a_blank_kind(tmp_path, caplog, edit, reason):
    """@brief 文件哈希重新钉定后一致，逐店品类声明仍不能发布空的门店商品集合或空白的地点类别：地图加载入口拒绝并
    写明原因。"""
    ref = _republished_assignment(tmp_path, edit)
    with pytest.raises(IntegrityError, match="^Map dataset validation failed: ") as failure:
        load_map_dataset(tmp_path, ref)
    assert f"Value error, {reason} [" in str(failure.value)
    assert reason in caplog.text and "Map dataset loading failed" in caplog.text
