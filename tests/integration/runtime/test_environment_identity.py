"""当前完整环境身份在真实打开、检查点和导出路径中保持冻结。"""
import copy
import hashlib
import shutil
import pytest
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


@pytest.mark.parametrize("version", [1, 2, 3, 4, 6])
def test_retired_or_unknown_run_manifest_versions_are_rejected(engine_factory, version):
    engine = engine_factory()
    manifest = {**engine.context.store.manifest, "schema_version": version}
    with pytest.raises(IntegrityError):
        require_environment_identity(manifest, engine.context.inputs)


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
    from tests.life.conftest import stage_day_plan, start_activity, advance_next
    from tests.life.environment_runtime import act
    stage_day_plan(engine)
    act(engine, "export", start_activity("interact", "家"))
    advance_next(engine)
    report = export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    manifest = report["frozen_run_manifest"]
    assert manifest["environment_identity"] == environment_identity(engine.context.inputs)
    assert manifest["persona_sha256"] == engine.context.inputs.persona_sha256
    assert manifest["config_sha256"] == engine.context.inputs.config_sha256
    assert manifest["model_metadata"] == engine.context.inputs.model_metadata.model_dump(mode="json")
