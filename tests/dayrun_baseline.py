"""完整自然日行为基线：只固定随机身份、壁钟与机器位置，保留全部业务和因果内容。"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID


class _RecordedTime(datetime):
    """@brief 测试记录壁钟固定，模拟时钟仍由真实引擎推进。"""

    @classmethod
    def now(cls, tz=None):
        """@brief 返回固定的记录时刻，不修改模拟时间。"""
        value = cls(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


def deterministic_day_runtime(monkeypatch) -> None:
    """@brief 仅在完整日测试内固定记录身份与计时，不修改生产代码。"""
    import agentsociety2.agent.person as person
    import worldmock.footprints.checkpoint as checkpoint
    import worldmock.footprints.models as models
    import worldmock.footprints.store as store

    sequence = itertools.count(1)
    fixed_uuid = lambda: UUID(int=next(sequence))
    for module in (models, checkpoint, store):
        monkeypatch.setattr(module, "uuid4", fixed_uuid)
    monkeypatch.setattr(store, "datetime", _RecordedTime)
    monkeypatch.setattr(store, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(person, "datetime", _RecordedTime)


#: 行为签名只屏蔽的代码身份字段（运行清单与导出清单里冻结的运行清单）。
CODE_IDENTITY_FIELDS = ("code_sha256", "git_commit", "git_dirty")
#: 行为签名里代码身份的占位。
CODE_IDENTITY_MASK = "<code-identity>"


def _json(value) -> bytes:
    """@brief 规范化字典顺序，不删除任何业务字段。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(content: bytes) -> str:
    """@brief 计算字节摘要。"""
    return hashlib.sha256(content).hexdigest()


class DayBaseline:
    """@brief 将路径和由路径派生的摘要一致替换，所有内容及因果引用保持可比较。"""

    def __init__(self, case: Path, project: Path, mask_code_identity: bool = False):
        """@brief 绑定实际脚本工件与源项目，映射只来自真实输入或真实资产。

        @param mask_code_identity 为真时得到行为签名：只把代码身份换成占位，其余与绑定代码身份的签名相同。
        """
        self.case = case.resolve()
        self.project = project.resolve()
        self.mask_code_identity = mask_code_identity
        self.code_sha256 = None
        self.hashes = {}
        self.assets = {}
        self.input_assets = set()

    def _code_masked(self, manifest: dict) -> dict:
        """@brief 行为签名时屏蔽运行清单的代码身份，其余原样。"""
        masked = dict(manifest)
        if self.mask_code_identity:
            for field in CODE_IDENTITY_FIELDS:
                if field in masked:
                    masked[field] = CODE_IDENTITY_MASK
        return masked

    def _quality_content(self, raw: bytes) -> bytes:
        """@brief 规范化质量报告；行为签名时以本侧代码摘要为键的计划指标改用占位键。"""
        report = json.loads(raw)
        metrics = report.get("day_plan_metrics")
        if self.mask_code_identity and isinstance(metrics, dict) and self.code_sha256 in metrics:
            metrics[CODE_IDENTITY_MASK] = metrics.pop(self.code_sha256)
        return _json(self.normalize(report))

    def normalize(self, value):
        """@brief 递归替换确定的机器路径及其派生摘要，不屏蔽未知字段。"""
        if isinstance(value, str):
            if value in self.hashes:
                return self.hashes[value]
            return value.replace(str(self.case), "<case>").replace(str(self.project), "<project>")
        if isinstance(value, list):
            return [self.normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: self.normalize(item) for key, item in value.items()}
        raw_sha = value.get("sha256")
        if raw_sha in self.assets and "size_bytes" in value:
            original, normalized = self.assets[raw_sha]
            assert value["size_bytes"] == len(original), "asset byte size does not match its reference"
            result["size_bytes"] = len(normalized)
        return result

    def content(self, content: bytes, name: str = "") -> bytes:
        """@brief 按真实格式规范化完整文本和归档成员，保留归档元信息。"""
        if _sha(content) in self.input_assets:
            return content
        if tarfile.is_tarfile(io.BytesIO(content)):
            destination = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(content)) as source, tarfile.open(fileobj=destination, mode="w") as out:
                for member in source.getmembers():
                    assert member.isfile(), "baseline workspace contains a non-file member"
                    raw = source.extractfile(member).read()
                    normalized = self.content(raw, member.name)
                    member.size = len(normalized)
                    out.addfile(member, io.BytesIO(normalized))
            return destination.getvalue()
        text = content.decode("utf-8")
        if name.endswith(".jsonl"):
            return b"".join(_json(self.normalize(json.loads(line))) + b"\n" for line in text.splitlines())
        if name.endswith(".json") or text.lstrip().startswith(("{", "[")):
            return _json(self.normalize(json.loads(text)))
        return self.normalize(text).encode("utf-8")

    def prepare(self) -> list[dict]:
        """@brief 重新计算输入、运行清单、素材与检查点的规范化摘要，先验证原始哈希完整性。"""
        from worldmock.footprints.frozen_environment import validate_frozen_environment

        manifest_raw = (self.case / "run/manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
        assert manifest["schema_version"] == 6, "baseline run manifest is not the current format"
        assert "scenario_sha256" not in manifest, "baseline run manifest contains a retired input identity"
        self.code_sha256 = manifest.get("code_sha256")
        validate_frozen_environment(self.case / "run", manifest, [], [])
        self.input_assets = {reference["sha256"] for reference in manifest["environment_assets"].values()}
        for path in sorted((self.case / "run/assets").iterdir()):
            raw = path.read_bytes()
            assert _sha(raw) == path.name, "baseline asset digest does not match its bytes"
            normalized = self.content(raw)
            self.hashes[path.name] = _sha(normalized)
            self.assets[path.name] = raw, normalized
        normalized_manifest = _sha(_json(self.normalize(self._code_masked(manifest))))
        self.hashes[_sha(manifest_raw)] = normalized_manifest
        self.hashes[_sha(_json(manifest))] = normalized_manifest
        checkpoints = []
        with sqlite3.connect(self.case / "run/footprints.sqlite") as connection:
            for body, raw_sha in connection.execute("SELECT body, record_sha256 FROM checkpoints ORDER BY rowid"):
                assert _sha(body.encode()) == raw_sha, "baseline checkpoint digest does not match its bytes"
                normalized = self.normalize(json.loads(body))
                self.hashes[raw_sha] = _sha(_json(normalized))
                checkpoints.append(normalized)
        return checkpoints

    def signature(self) -> dict:
        """@brief 比较完整导出正文、所有检查点与真实资产，不丢弃业务字段。"""
        checkpoints = self.prepare()
        root = self.case / "export"
        artifacts = {}
        normalized_bytes = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            relative = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            normalized = self._quality_content(raw) if relative == "quality.json" else self.content(raw, relative)
            self.hashes[_sha(raw)] = _sha(normalized)
            normalized_bytes[relative] = normalized
            if relative.startswith("assets/"):
                relative = "assets/" + self.hashes[path.name]
            artifacts[relative] = _sha(normalized)
        manifest = self.normalize(json.loads((root / "manifest.json").read_text(encoding="utf-8")))
        if "frozen_run_manifest" in manifest:
            manifest["frozen_run_manifest"] = self._code_masked(manifest["frozen_run_manifest"])
        if "quality_report" in manifest:
            manifest["quality_report"]["size_bytes"] = len(normalized_bytes["quality.json"])
        for entry in manifest.get("timeline", []):
            entry["size_bytes"] = len(normalized_bytes[entry["path"]])
        artifacts["manifest.json"] = _sha(_json(manifest))
        return {"artifacts": artifacts, "checkpoints_sha256": _sha(_json(checkpoints)),
                "run_assets": sorted([self.hashes[key], len(value[1])] for key, value in self.assets.items())}


def day_baseline_signature(case: Path, project: Path) -> dict:
    """@brief 从真实运行工件构造完整日可重复的行为摘要。"""
    return DayBaseline(case, project).signature()


def behaviour_signature(case: Path, project: Path) -> dict:
    """@brief 只屏蔽代码身份的完整日行为签名。"""
    return DayBaseline(case, project, mask_code_identity=True).signature()
