"""实际环境运行与严格导出所用的测试输入。"""

from datetime import timedelta
import hashlib
import json

from tests.life.conftest import advance_next, conclude_request, errand_add, purchase, use_app
from tests.life.environment_runtime import act, conclude_app_use, finish_activity, run_to_end

#: 导出清单 artifacts 的键与各自的文件名。
ARTIFACT_FILES = {"events": "events.jsonl", "activities": "activities.jsonl",
                  "verified_experiences": "verified-experiences.jsonl", "purchases": "purchases.jsonl",
                  "app_usage": "app-usage.jsonl", "location_stays": "location-stays.jsonl"}


def short_window(persona, config):
    """一小时完整运行，无需外部模型服务。"""
    persona["initial_location"] = "四季便利"
    config["stop_time"] = config["start_time"] + timedelta(hours=1)


def recorded_engine(engine_factory, *, purchases=True, content=True):
    """通过真实请求得到付款、本人收货和持续内容接触；哔哩哔哩放完第一条之后的第一次决策就收尾这次使用，人物随后
    步行回家，运行截止时已在家。"""
    engine = engine_factory(mutate=short_window)
    if purchases:
        _, result = act(engine, "recorded-basket", purchase("柠檬茶", "纸巾"))
        assert result.status == "in_progress", result.error_code
        finish_activity(engine, result, "recorded-basket")
    if content:
        _, result = act(engine, "recorded-content", use_app("哔哩哔哩"))
        assert result.status == "in_progress", result.error_code
        conclude_app_use(engine, result.activity_id, "recorded-content")
    run_to_end(engine)
    return engine, final_checkpoint(engine)


def errand_engine(engine_factory):
    """个人事务场景（test_errand_export 的同一场景）：用 Keep 时记下「买纸巾」，随后真实买到纸巾、事务完成，运行到截止
    并写最终检查点；途中的观察带着待办里的事务与它的商品绑定。"""
    def setup(persona, config):
        short_window(persona, config)
        config["progress_report_interval_seconds"] = 60
    engine = engine_factory(mutate=setup)
    _, using = act(engine, "note-keep", use_app("Keep"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    activity = engine.context.state.activities[using.activity_id]
    _, noted = act(engine, "note", conclude_request(activity.name, errands=[errand_add("买纸巾", "纸巾")]),
                   "conclude_activity")
    assert noted.status == "completed", noted.error_code
    _, bought = act(engine, "buy", purchase("纸巾"))
    assert engine.context.state.agents[1].errands
    finish_activity(engine, bought, "buy")
    assert engine.context.state.agents[1].errands == {}
    run_to_end(engine)
    return engine, final_checkpoint(engine)


def final_checkpoint(engine):
    """运行截止后写最终共同检查点；人物快照用一份明确的测试素材。"""
    store = engine.context.store
    snapshot = store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    return engine.checkpoint({"agent:1": snapshot})


def export_completed(engine, directory):
    """运行到截止、写最终检查点、导出并认证；返回（清单，事件行，活动行），事件行取导出文件里的完整事件。"""
    from worldmock.footprints.export import export_run
    from worldmock.footprints.validation import validate_export

    run_to_end(engine)
    point = final_checkpoint(engine)
    export_run(engine.context.store, point.last_event_seq, directory)
    manifest, activities = validate_export(directory)
    return manifest, artifact_rows(directory, "events"), activities


def artifact_rows(directory, name):
    """导出目录里一份工件的全部行。"""
    return [json.loads(line) for line in (directory / ARTIFACT_FILES[name]).read_bytes().splitlines()]


def write_rows(directory, manifest, name, rows):
    """@brief 改写被测导出文件并刷新真实字节摘要和行数，不用损坏哈希代替行为认证。"""
    from worldmock.footprints.models import canonical_json
    descriptor = manifest["artifacts"][name]
    raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    (directory / descriptor["path"]).write_bytes(raw)
    descriptor.update(sha256=hashlib.sha256(raw).hexdigest(), rows=len(rows))


def write_manifest(directory, manifest):
    """@brief 按规范 JSON 写回导出清单。"""
    from worldmock.footprints.models import canonical_json
    (directory / "manifest.json").write_bytes(canonical_json(manifest) + b"\n")


def reproject(directory, manifest, events):
    """@brief 六份工件和质量全部按被污染后的事件重算并重新签名，失败只能来自实际语义约束。"""
    from worldmock.footprints.app_usage import project_app_usage
    from worldmock.footprints.export import _verified_experience_rows, reconstruct_activities
    from worldmock.footprints.frozen_environment import frozen_display_names
    from worldmock.footprints.location_stays import project_location_stays
    from worldmock.footprints.models import Event, canonical_json, sha256_json
    from worldmock.footprints.purchases import project_purchase_facts
    from worldmock.footprints.quality import build_quality_report
    records = [Event.model_validate(event) for event in events]
    activities = reconstruct_activities(records)
    write_rows(directory, manifest, "events", events)
    write_rows(directory, manifest, "activities", activities)
    write_rows(directory, manifest, "verified_experiences",
               _verified_experience_rows(manifest["frozen_run_manifest"]["agent_ids"], records))
    write_rows(directory, manifest, "purchases", project_purchase_facts(events))
    write_rows(directory, manifest, "app_usage", project_app_usage(events))
    write_rows(directory, manifest, "location_stays", project_location_stays(events))
    names = frozen_display_names(directory, manifest["frozen_run_manifest"])
    quality = canonical_json(build_quality_report(records, activities, manifest["frozen_run_manifest"], names)) + b"\n"
    (directory / "quality.json").write_bytes(quality)
    manifest["quality_report"].update(size_bytes=len(quality), sha256=hashlib.sha256(quality).hexdigest())
    checkpoint = manifest["checkpoint"]
    checkpoint["last_event_seq"] = manifest["cutoff_event_seq"] = len(events)
    checkpoint["record_sha256"] = sha256_json({key: value for key, value in checkpoint.items()
                                               if key != "record_sha256"})
    write_manifest(directory, manifest)
    return activities


def read_export(directory):
    """@brief 原始证据是已认证过的当前真实运行导出。"""
    manifest = json.loads((directory / "manifest.json").read_bytes())
    return manifest, artifact_rows(directory, "events")
