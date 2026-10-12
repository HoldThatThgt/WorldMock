"""显示名称只取这次运行冻结的环境目录：运行目录与导出目录读出同一张名称表，功能 app 也有名称，观察里的名称不被读取，
冻结目录缺失或被改动时 Python 一侧的每个读者都按运行故障失败。"""

import argparse
import shutil

import pytest

from scripts import inspect_run, render_day
from tests.footprints.recording import export_completed, short_window
from tests.life.conftest import purchase, use_app
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity, reach
from tests.life.phone_records import paying_label
from worldmock.errors import IntegrityError
from worldmock.footprints.display_wording import display_names
from worldmock.footprints.export import export_run
from worldmock.footprints.frozen_environment import frozen_display_names
from worldmock.footprints.timeline_labels import timeline_label_report
from worldmock.footprints.validation import validate_export

#: 副本里哔哩哔哩的新名称。
RENAMED = "B站"


def _renamed_bilibili(tmp_path):
    """夹具副本：哔哩哔哩改名，身份不变。"""
    root = copy_environment(tmp_path / "renamed-environment")
    apps = catalog_rows(root, "app-catalog.jsonl")
    next(row for row in apps if row["app_id"] == "bilibili")["name"] = RENAMED
    write_catalog_rows(root, "app-catalog.jsonl", apps)
    return root


def _memory_names(catalog):
    """用引擎内存里的目录建出的名称表。"""
    return display_names(catalog.apps.values(), catalog.contents.values(), catalog.locations.values(),
                         catalog.goods.values(), catalog.travel_methods.values())


def _day(sqlite_path, out):
    """经命令行入口写出 2026-01-05 的时间线，返回它的全文。"""
    render_day.main([str(sqlite_path), "--agent", "1", "--date", "2026-01-05", "--out", str(out)])
    return (out / "timeline" / "1-2026-01-05.md").read_text(encoding="utf-8")


def test_names_come_from_the_frozen_catalog_of_the_run_directory_and_of_the_export_directory(engine_factory,
                                                                                           tmp_path):
    """哔哩哔哩改名的副本建运行：时间线写新名称；运行目录和导出目录读出的名称相等，并等于用内存目录建出的名称。"""
    engine = engine_factory(environment_root=_renamed_bilibili(tmp_path), mutate=short_window)
    _, watched = act(engine, "watch", use_app(RENAMED))
    assert watched.status == "in_progress", watched.error_code
    export = tmp_path / "export"
    manifest, _, _ = export_completed(engine, export)
    store = engine.context.store
    from_run = frozen_display_names(store.run_dir, store.manifest)
    from_export = frozen_display_names(export, manifest["frozen_run_manifest"])
    assert from_run == from_export == _memory_names(engine.context.catalog)
    assert from_run.apps["bilibili"] == RENAMED
    for text in (_day(store.run_dir / "footprints.sqlite", tmp_path / "out"),
                 (export / "timeline" / "1-2026-01-05.md").read_text(encoding="utf-8")):
        assert f"使用{RENAMED}《" in text and "哔哩哔哩" not in text


def test_function_app_names_are_read_and_observation_names_are_never_used(engine_factory, capsys):
    """外卖送达后消费通知行写支付 app（功能 app）的名称；时间线标签检查的事件里混入带 apps 名称的观察，结果不变。"""
    engine = engine_factory()
    label = paying_label(engine, "pay", {"meituan": "alipay"})
    _, bought = act(engine, label, purchase("牛肉面"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, label)
    (order,) = engine.context.state.deliveries.values()
    assert order.payment_app_id == "alipay"
    reach(engine, order.expected_at)
    store = engine.context.store
    with inspect_run._connect(store.run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(run_dir=store.run_dir, type="environment_event", agent=1,
                                                        tail=None, full=False))
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.endswith(f"消费通知 · 支付宝 · 支付成功 {order.order_id}")]
    recorded = events(engine)
    observation = next(row for row in recorded if row.event_type == "observation")
    misleading = observation.model_copy(update={"data": {
        "apps": [{"app_id": "meituan", "name": "interact"}], "goods": [{"goods_id": "beef-noodles", "name": "x-1"}],
        "locations": [{"location_id": "home", "name": "agent:5"}]}})
    names = frozen_display_names(store.run_dir, store.manifest)
    plain = timeline_label_report(recorded, store.manifest, names)
    mixed = timeline_label_report(sorted([*recorded, misleading], key=lambda row: row.event_seq), store.manifest, names)
    assert mixed == plain and plain["rows_checked"] > 0 and plain["findings"] == []


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_missing_or_altered_frozen_catalog_fails_every_python_reader(engine_factory, tmp_path, capsys, damage):
    """删掉或改动运行目录里一份冻结的目录素材：命令行时间线抛 IntegrityError 且不写文件，导出不发布，events 抛错；
    改动导出目录里的同一份素材：导出认证失败。"""
    engine = engine_factory(mutate=short_window)
    _, bought = act(engine, "basket", purchase("柠檬茶"))
    finish_activity(engine, bought, "basket")
    export = tmp_path / "export"
    export_completed(engine, export)
    store = engine.context.store
    reference = store.manifest["environment_assets"]["catalog/app-catalog.jsonl"]["sha256"]
    tampered = tmp_path / "tampered"
    shutil.copytree(export, tampered)
    (tampered / "assets" / reference).write_bytes(b"{}\n")
    with pytest.raises(IntegrityError):
        validate_export(tampered)
    asset = store.run_dir / "assets" / reference
    asset.unlink() if damage == "missing" else asset.write_bytes(b"{}\n")
    out = tmp_path / "out"
    with pytest.raises(IntegrityError, match="Frozen environment asset"):
        render_day.main([str(store.run_dir / "footprints.sqlite"), "--agent", "1", "--date", "2026-01-05",
                         "--out", str(out)])
    assert not out.exists()
    with pytest.raises(IntegrityError):
        export_run(store, store.last_event_seq, tmp_path / "second-export")
    assert not (tmp_path / "second-export").exists()
    with inspect_run._connect(store.run_dir) as conn, pytest.raises(IntegrityError, match="Frozen environment asset"):
        inspect_run.cmd_events(conn, argparse.Namespace(run_dir=store.run_dir, type=None, agent=None, tail=None,
                                                        full=False))
    assert capsys.readouterr().out == ""

