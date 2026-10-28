"""完整日摘要必须保留业务、因果和内容，不能通过宽泛屏蔽制造不变结论。"""

import hashlib
import json
import sqlite3
import zlib
from pathlib import Path

import pytest

from tests.dayrun_baseline import CODE_IDENTITY_MASK, DayBaseline, behaviour_signature, day_baseline_signature
from worldmock.foundation.errors import IntegrityError


def _bytes(value):
    """@brief 写出确定性 JSON 工件。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(content):
    """@brief 测试用真实字节摘要。"""
    return hashlib.sha256(content).hexdigest()


#: 最小运行工件里的代码身份与提示、工具指纹。
CODE, PROMPT, TOOLS = "c" * 64, "d" * 64, "e" * 64


def _case(root: Path, code: str = CODE, commit: str = "f" * 40, model: dict | None = None, amount: int = 2):
    """@brief 创建带完整哈希、检查点、代码身份与以代码摘要为键的质量报告的最小独立运行工件。"""
    from tests.integration.runtime.current_contract_fixtures import inputs
    from worldmock.configuration.inputs.environment import environment_identity
    from worldmock.recording_engine.storage.assets import AssetStore
    from worldmock.simulation_engine.world.world_setup import _freeze_environment

    for relative in ["run/assets", "export"]:
        (root / relative).mkdir(parents=True)
    frozen = inputs()
    assets = _freeze_environment(frozen, AssetStore(root / "run/assets"))
    identity = model or {"prompt_sha256": PROMPT, "tool_schema_sha256": TOOLS}
    manifest = _bytes({"schema_version": 7, "persona_sha256": frozen.persona_sha256,
                       "config_sha256": frozen.config_sha256, "environment_identity": environment_identity(frozen),
                       "environment_assets": assets, "run_id": "day-test", "code_sha256": code,
                       "git_commit": commit, "git_dirty": False, "model_identity": identity})
    (root / "run/manifest.json").write_bytes(manifest)
    body = _bytes({"manifest_sha256": _digest(manifest), "state": {"balance": 10}, "snapshots": {}})
    with sqlite3.connect(root / "run/footprints.sqlite") as connection:
        connection.execute("CREATE TABLE checkpoints (body BLOB, record_sha256 TEXT)")
        connection.execute("INSERT INTO checkpoints VALUES (?,?)", (zlib.compress(body), _digest(body)))
    events = _bytes({"event_id": "fact", "caused_by": [], "amount": amount}) + b"\n"
    (root / "export/events.jsonl").write_bytes(events)
    quality = _bytes({"schema_version": 8, "day_plan_metrics": {code: {"block_count": 0}}})
    (root / "export/quality.json").write_bytes(quality)
    (root / "export/manifest.json").write_bytes(_bytes({"frozen_run_manifest": json.loads(manifest),
        "artifacts": {"events": {"path": "events.jsonl", "sha256": _digest(events)}},
        "quality_report": {"path": "quality.json", "size_bytes": len(quality), "sha256": _digest(quality)}}))
    return root


def test_signature_is_stable_across_machine_locations(tmp_path):
    """@brief 路径不同的同内容工件得到同一规范化摘要。"""
    first, second = _case(tmp_path / "first"), _case(tmp_path / "second")
    assert day_baseline_signature(first, first) == day_baseline_signature(second, second)


@pytest.mark.parametrize("field,value", [("event_id", "changed"), ("caused_by", ["fact"]), ("amount", 3)])
def test_signature_detects_identity_causality_and_business_changes(tmp_path, field, value):
    """@brief 随机源已经固定后，身份、因果或资源值变化仍必须改变摘要。"""
    case = _case(tmp_path / "case")
    before = day_baseline_signature(case, case)
    path = case / "export/events.jsonl"
    event = json.loads(path.read_text())
    event[field] = value
    path.write_bytes(_bytes(event) + b"\n")
    assert day_baseline_signature(case, case) != before


def test_corrupt_asset_or_checkpoint_is_rejected(tmp_path):
    """@brief 错误原始哈希不能被规范化过程掩盖。"""
    case = _case(tmp_path / "case")
    path = case / "run/assets" / ("a" * 64)
    path.write_text("changed asset", encoding="utf-8")
    with pytest.raises(AssertionError, match="asset digest"):
        day_baseline_signature(case, case)
    path.unlink()
    with sqlite3.connect(case / "run/footprints.sqlite") as connection:
        connection.execute("UPDATE checkpoints SET record_sha256=?", ("b" * 64,))
    with pytest.raises(AssertionError, match="checkpoint digest"):
        day_baseline_signature(case, case)


def test_normalization_preserves_unknown_hashes_and_every_other_field(tmp_path):
    """@brief 除明确来自真实资产或位置的映射外，摘要与字段不被删除。"""
    baseline = DayBaseline(tmp_path, tmp_path)
    payload = {"code_sha256": "a" * 64, "some_id": "123", "nested": {"result": False}, "wall_elapsed_ms": 9}
    assert baseline.normalize(payload) == payload


@pytest.mark.parametrize("field", ["persona_sha256", "config_sha256", "map_sha256", "catalog_content_sha256"])
def test_frozen_environment_input_identity_is_authenticated_before_normalizing(tmp_path, field):
    case = _case(tmp_path / "case")
    path = case / "run/manifest.json"
    manifest = json.loads(path.read_bytes())
    identity = manifest if field in {"persona_sha256", "config_sha256"} else manifest["environment_identity"]
    identity[field] = "0" * 64
    path.write_bytes(_bytes(manifest))
    with pytest.raises(IntegrityError):
        day_baseline_signature(case, case)


def test_behaviour_signature_ignores_only_code_identity(tmp_path):
    """@brief 只差代码摘要（清单、冻结清单、检查点里的清单摘要与质量报告的键一致变化）和提交：
    行为签名相等，绑定代码身份的签名不等。"""
    first = _case(tmp_path / "first")
    second = _case(tmp_path / "second", code="0" * 64, commit="1" * 40)
    assert behaviour_signature(first, first) == behaviour_signature(second, second)
    assert day_baseline_signature(first, first) != day_baseline_signature(second, second)


@pytest.mark.parametrize("change", ["prompt_sha256", "tool_schema_sha256", "amount"])
def test_behaviour_signature_detects_fingerprint_and_business_changes(tmp_path, change):
    """@brief 系统文本或工具定义的指纹、业务数额，任何一项变化都让行为签名不等。"""
    before = _case(tmp_path / "before")
    if change == "amount":
        after = _case(tmp_path / "after", amount=3)
    else:
        after = _case(tmp_path / "after", model={"prompt_sha256": PROMPT, "tool_schema_sha256": TOOLS,
                                                 change: "0" * 64})
    assert behaviour_signature(before, before) != behaviour_signature(after, after)


def test_behaviour_signature_keeps_every_other_manifest_field(tmp_path):
    """@brief 屏蔽只改三项代码身份，其余字段原样；绑定代码身份时不改。"""
    manifest = {"code_sha256": CODE, "git_commit": "f" * 40, "git_dirty": True, "run_id": "day-test",
                "model_identity": {"prompt_sha256": PROMPT}, "persona_sha256": "a" * 64}
    masked = DayBaseline(tmp_path, tmp_path, mask_code_identity=True)._code_masked(manifest)
    assert {key for key in manifest if masked[key] != manifest[key]} == {"code_sha256", "git_commit", "git_dirty"}
    assert {masked[key] for key in ("code_sha256", "git_commit", "git_dirty")} == {CODE_IDENTITY_MASK}
    assert DayBaseline(tmp_path, tmp_path)._code_masked(manifest) == manifest
