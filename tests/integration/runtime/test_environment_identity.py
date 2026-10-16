"""当前完整环境身份在真实打开、检查点和导出路径中保持冻结。"""
import copy
import hashlib
import json
import shutil
import pytest
from tests.footprints.recording import artifact_rows
from tests.engine_fixture import engine_factory
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service
from tests.life.conftest import context
from worldmock.environment import environment_identity, load_environment
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.run_identity import require_environment_identity
from tests.frozen_inputs import fixture_metadata


def test_loaded_identity_matches_actual_manifest_and_map_bytes():
    frozen = inputs()
    identity = environment_identity(frozen)
    assert identity["map_sha256"] == frozen.catalog.map_sha256
    assert identity["catalog_content_sha256"] == frozen.catalog.manifest.catalog_content_sha256
    assert len(identity) == 5


@pytest.mark.parametrize("field", ["schema_version", "environment_identity", "persona_sha256", "config_sha256", "model_metadata"])
def test_each_frozen_input_identity_is_required(engine_factory, field):
    engine = engine_factory()
    manifest = copy.deepcopy(engine.context.store.manifest)
    del manifest[field]
    with pytest.raises(IntegrityError, match="environment identity mismatch"):
        require_environment_identity(manifest, engine.context.inputs)


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 7, "6", 6.5, None])
def test_retired_or_unknown_run_manifest_versions_are_rejected(engine_factory, version):
    """运行清单的结构版本是 6：上一版的 5、更早的值、没见过的 7 和不是这个整数的写法都按环境身份不符拒绝。"""
    engine = engine_factory()
    assert engine.context.store.manifest["schema_version"] == 6
    require_environment_identity(engine.context.store.manifest, engine.context.inputs)
    manifest = {**engine.context.store.manifest, "schema_version": version}
    with pytest.raises(IntegrityError, match="^Runtime environment identity mismatch$"):
        require_environment_identity(manifest, engine.context.inputs)


def test_run_whose_stored_manifest_carries_the_previous_structure_number_cannot_be_opened(engine_factory, caplog):
    """磁盘上的运行清单写着上一版结构号 5 时，打开这个运行在读世界状态之前就被拒绝，并留下英文错误日志；
    把清单写回 6 后同一个目录照常打开。"""
    from worldmock.footprints.models import canonical_json

    engine = engine_factory()
    store, frozen = engine.context.store, engine.context.inputs
    path = store.run_dir / "manifest.json"
    current = path.read_bytes()
    path.write_bytes(canonical_json({**store.manifest, "schema_version": 5}))
    with caplog.at_level("ERROR"), pytest.raises(IntegrityError, match="^Runtime environment identity mismatch$"):
        LifeEngine.open(frozen, store.run_dir)
    assert "Runtime environment identity mismatch" in caplog.text
    path.write_bytes(current)
    reopened = LifeEngine.open(frozen, store.run_dir)
    try:
        assert reopened.context.store.state == store.state
    finally:
        reopened.close()


def test_open_rejects_changed_persona_without_mutating_original_run(engine_factory, tmp_path):
    engine = engine_factory()
    original = engine.context.store.state
    root = tmp_path / "changed"
    shutil.copytree(engine.context.inputs.root, root)
    path = root / "persona.toml"
    path.write_bytes(path.read_bytes() + b"\n")
    changed = load_environment(root, metadata_loader=fixture_metadata)
    with pytest.raises(IntegrityError):
        LifeEngine.open(changed, engine.context.store.run_dir)
    assert engine.context.store.state == original


def test_export_carries_exact_frozen_environment_and_readable_inputs(engine_factory, tmp_path):
    from worldmock.footprints.export import export_run
    engine = engine_factory()
    from tests.life.conftest import stage_day_plan, do_at, advance_next
    from tests.life.environment_runtime import act
    stage_day_plan(engine)
    act(engine, "export", do_at("家", "测试休息"))
    advance_next(engine)
    report = export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    manifest = report["frozen_run_manifest"]
    assert manifest["environment_identity"] == environment_identity(engine.context.inputs)
    assert manifest["persona_sha256"] == engine.context.inputs.persona_sha256
    assert manifest["config_sha256"] == engine.context.inputs.config_sha256
    assert manifest["model_metadata"] == engine.context.inputs.model_metadata.model_dump(mode="json")


CALLBACK_ROWS = (
    b'{"cap":3,"initial":2,"reset_on_goods":["tissue"],"state":"stock"}\n'
    b'{"effect":{"amount_fen":100,"direction":"income","type":"transfer"},"rule_id":"hourly-income",'
    b'"trigger":{"class":"time","key":"hourly"}}\n')
MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'
METHODS = "travel-method.jsonl"


def _environment(root, files):
    """夹具副本里的回调与距离声明恰为给定的这些，清单按生产发布入口刷新。

    出行方式文件缺省保留夹具的三行；files 里把它写成 None 表示不放、写成字节表示换成这些字节。
    不放或放零行时夹具的三种方式都没有登记，导航 app 改为只绑定自带的步行。
    """
    from tests.life.environment_inputs import catalog_rows, write_catalog_rows
    from worldmock.catalog.identity import refresh_manifest_identity

    if not root.exists():
        shutil.copytree(inputs().root, root)
    for name in (METHODS, "app-catalog.jsonl"):
        shutil.copyfile(inputs().root / "catalog" / name, root / "catalog" / name)
    for name in ("callbacks.jsonl", "map.jsonl"):
        (root / "catalog" / name).unlink(missing_ok=True)
    for name, content in files.items():
        if content is None:
            (root / "catalog" / name).unlink()
        else:
            (root / "catalog" / name).write_bytes(content)
    if not files.get(METHODS, True):
        rows = catalog_rows(root, "app-catalog.jsonl")
        next(row for row in rows if row["app_id"] == "amap")["travel_method_ids"] = ["walking"]
        write_catalog_rows(root, "app-catalog.jsonl", rows)
    refresh_manifest_identity(root / "catalog")
    return root


@pytest.mark.parametrize("identity,value", [("rules_version", 5), ("rules_version", 7), ("environment_version", "3"),
                                            ("catalog_content_sha256", "0" * 64), ("map_sha256", "0" * 64)])
def test_parent_run_published_under_another_environment_identity_cannot_be_opened(engine_factory, identity, value):
    """父运行清单的五项环境身份任一不同就拒绝，规则版本是旧号的运行不能恢复。"""
    engine = engine_factory()
    manifest = copy.deepcopy(engine.context.store.manifest)
    assert manifest["environment_identity"]["rules_version"] == 6
    manifest["environment_identity"][identity] = value
    with pytest.raises(IntegrityError, match="^Runtime environment identity mismatch$"):
        require_environment_identity(manifest, engine.context.inputs)


@pytest.mark.parametrize("files", [{METHODS: None}, {}, {"callbacks.jsonl": CALLBACK_ROWS}, {"map.jsonl": MAP_ROW},
                                   {"callbacks.jsonl": CALLBACK_ROWS, "map.jsonl": MAP_ROW},
                                   {"callbacks.jsonl": b"", "map.jsonl": b"", METHODS: b""}],
                         ids=["required-only", "fixture-methods", "callbacks", "map", "all-three", "all-three-empty"])
def test_complete_run_with_any_optional_files_passes_export_certification(engine_factory, tmp_path, files):
    """带与不带各可选文件的运行都走完新建、检查点和导出，冻结素材跟随清单并通过导出自查。"""
    from tests.footprints.recording import recorded_engine
    from worldmock.footprints.export import export_run
    from worldmock.footprints.validation import validate_export

    root = _environment(tmp_path / "input", files)
    engine, point = recorded_engine(lambda mutate: engine_factory(root, mutate=mutate))
    export_run(engine.context.store, point.last_event_seq, tmp_path / "export")
    manifest, _ = validate_export(tmp_path / "export")
    events = artifact_rows(tmp_path / "export", "events")
    frozen = manifest["frozen_run_manifest"]
    present = {name for name, content in {METHODS: b"fixture", **files}.items() if content is not None}
    assert set(frozen["environment_assets"]) == {
        "persona.toml", "config.toml", "map.json", "catalog/manifest.json", "catalog/app-catalog.jsonl",
        "catalog/content-catalog.jsonl", "catalog/location.jsonl", "catalog/merchant-catalog.jsonl",
    } | {f"catalog/{name}" for name in present}
    assert set(engine.context.catalog.travel_methods) == (
        {"bus", "driving", "taxi"} if files.get(METHODS, True) else set())
    assert frozen["environment_identity"] == environment_identity(engine.context.inputs)
    effects = [row for row in events if row["data"].get("kind") == "callback_effect"]
    assert bool(effects) == (files.get("callbacks.jsonl") == CALLBACK_ROWS)


@pytest.mark.parametrize("before,after", [
    ({}, {"callbacks.jsonl": b""}),
    ({}, {"map.jsonl": b""}),
    ({"callbacks.jsonl": b""}, {}),
    ({"map.jsonl": MAP_ROW}, {}),
    ({"callbacks.jsonl": b""}, {"callbacks.jsonl": CALLBACK_ROWS}),
    ({"map.jsonl": MAP_ROW}, {"map.jsonl": MAP_ROW.replace(b"12600", b"12601")}),
    ({"map.jsonl": MAP_ROW}, {"map.jsonl": MAP_ROW.replace(b":12600", b": 12600")}),
    ({METHODS: None}, {METHODS: b""}),
    ({METHODS: b""}, {METHODS: None}),
    ({}, {METHODS: None}),
    ({}, {METHODS: "price"}),
    ({}, {METHODS: "whitespace"}),
], ids=["add-empty-callbacks", "add-empty-map", "remove-callbacks", "remove-map", "change-callbacks",
        "change-distance", "change-map-whitespace", "add-empty-methods", "remove-empty-methods", "remove-methods",
        "change-method-price", "change-method-whitespace"])
def test_adding_removing_or_changing_an_optional_file_refuses_the_old_run(engine_factory, tmp_path, before, after):
    fixture = (inputs().root / "catalog" / METHODS).read_bytes()
    changed_rows = {"price": fixture.replace(b'"start_price_fen":500', b'"start_price_fen":501'),
                    "whitespace": fixture.replace(b'"travel_method_id":"taxi"', b'"travel_method_id": "taxi"')}
    if after.get(METHODS) in changed_rows:
        after = {METHODS: changed_rows[after[METHODS]]}
        assert after[METHODS] != fixture and len(after[METHODS].splitlines()) == 3
    root = _environment(tmp_path / "input", before)
    engine = engine_factory(root)
    run_dir, original = engine.context.store.run_dir, engine.context.store.state
    changed = load_environment(_environment(tmp_path / "changed", after), metadata_loader=fixture_metadata)
    assert environment_identity(changed) != environment_identity(engine.context.inputs)
    with pytest.raises(IntegrityError, match="^Runtime environment identity mismatch$"):
        LifeEngine.open(changed, run_dir)
    assert engine.context.store.state == original


def test_declared_distance_value_moves_both_digests_and_row_whitespace_only_the_catalog_digest(tmp_path):
    """地图摘要取决于声明边解析后的值；行内空白和键序只改变目录字节身份。"""
    root = tmp_path / "input"
    base = load_environment(_environment(root, {"map.jsonl": MAP_ROW}), metadata_loader=fixture_metadata)
    spaced = load_environment(_environment(root, {"map.jsonl": b'{ "length_m": 12600, "b": "office", "a": "home" }\n'}),
                              metadata_loader=fixture_metadata)
    longer = load_environment(_environment(root, {"map.jsonl": MAP_ROW.replace(b"12600", b"12601")}),
                              metadata_loader=fixture_metadata)
    absent = load_environment(_environment(root, {}), metadata_loader=fixture_metadata)
    base_identity, spaced_identity = environment_identity(base), environment_identity(spaced)
    assert spaced_identity["map_sha256"] == base_identity["map_sha256"]
    assert spaced_identity["catalog_content_sha256"] != base_identity["catalog_content_sha256"]
    assert spaced.catalog.map_data == base.catalog.map_data
    for other in (longer, absent):
        identity = environment_identity(other)
        assert identity["map_sha256"] != base_identity["map_sha256"]
        assert identity["catalog_content_sha256"] != base_identity["catalog_content_sha256"]
    assert environment_identity(absent)["map_sha256"] == environment_identity(inputs())["map_sha256"]
    assert environment_identity(absent) == environment_identity(inputs())


def test_frozen_generated_map_matches_the_memory_map_and_is_not_the_declaration_file(engine_factory, tmp_path):
    """运行目录里的 map.json 是整张生成结果的规范字节；catalog/map.jsonl 按原字节另行冻结。"""
    from worldmock.catalog.mapgen import map_bytes

    engine = engine_factory(_environment(tmp_path / "input", {"map.jsonl": MAP_ROW}))
    store, catalog = engine.context.store, engine.context.catalog
    assets = store.manifest["environment_assets"]
    generated = (store.run_dir / "assets" / assets["map.json"]["sha256"]).read_bytes()
    assert generated == map_bytes(catalog.map_data)
    assert hashlib.sha256(generated).hexdigest() == store.manifest["environment_identity"]["map_sha256"]
    assert (store.run_dir / "assets" / assets["catalog/map.jsonl"]["sha256"]).read_bytes() == MAP_ROW
    assert {"a": "home", "b": "office", "length_m": 12600} in json.loads(generated)["edges"]
