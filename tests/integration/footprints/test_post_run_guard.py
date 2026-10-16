"""事后读取视图的守护：导出与认证从视图算出的每个字节，与从完整事件算出的相同；夹具里的观察真带着表里的每个键。"""

import pytest

from tests.footprints.recording import errand_engine, recorded_engine
from worldmock.footprints import export as export_module
from worldmock.footprints import validation as validation_module
from worldmock.footprints.export import export_run
from worldmock.footprints.post_run import DROPPED_PAYLOADS
from worldmock.footprints.validation import validate_export

#: 两个夹具运行：购买加内容使用，以及个人事务由真实收货完成。
RUNS = {"recorded": recorded_engine, "errand": errand_engine}


@pytest.fixture(params=["recorded", "errand"])
def fixture_run(request, engine_factory):
    """运行到截止并写了最终检查点的夹具运行（引擎，最终检查点）。"""
    return RUNS[request.param](engine_factory)


def _files(directory):
    """导出目录里每个文件的相对路径与字节。"""
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def test_export_is_byte_identical_with_and_without_the_post_run_view(fixture_run, tmp_path, monkeypatch):
    engine, point = fixture_run
    store = engine.context.store
    export_run(store, point.last_event_seq, tmp_path / "views")
    with monkeypatch.context() as patched:
        patched.setattr(export_module, "post_run_view", lambda event: event)
        export_run(store, point.last_event_seq, tmp_path / "full")
    assert _files(tmp_path / "views") == _files(tmp_path / "full")
    validate_export(tmp_path / "views")
    validate_export(tmp_path / "full")
    monkeypatch.setattr(validation_module, "post_run_view", lambda event: event)
    monkeypatch.setattr(validation_module, "post_run_row", lambda row: row)
    validate_export(tmp_path / "full")


def test_every_fixture_observation_carries_every_dropped_payload(fixture_run, request):
    engine, point = fixture_run
    store = engine.context.store
    observations = [event for event in store.iter_events(point.last_event_seq) if event.event_type == "observation"]
    assert observations
    assert all(DROPPED_PAYLOADS["observation"] <= set(event.data) for event in observations)
    if request.node.callspec.params["fixture_run"] == "errand":
        assert any(event.data.get("errand_item_bindings") and event.data.get("todo_layer", {}).get("errands")
                   for event in observations)


def test_export_holds_only_post_run_views_in_memory(engine_factory, tmp_path, monkeypatch):
    engine, point = recorded_engine(engine_factory)
    captured, original = [], export_module.build_quality_report

    def capture(events, *args):
        captured.extend(events)
        return original(events, *args)

    monkeypatch.setattr(export_module, "build_quality_report", capture)
    export_run(engine.context.store, point.last_event_seq, tmp_path / "export")
    observations = [event for event in captured if event.event_type == "observation"]
    assert observations
    assert not any(DROPPED_PAYLOADS["observation"] & set(event.data) for event in observations)
