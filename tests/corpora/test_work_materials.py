"""工作材料语料结构测试：oracle 配套、输入/参考分离、manifest 留痕与场景接线。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from worldmock.corpora import load_manifest
from worldmock.life.world_setup import load_scenario

_CORPUS = Path("corpora")
_WORK = _CORPUS / "scenario-work"
_REGISTER = json.loads((_WORK / "materials.json").read_text(encoding="utf-8"))
_REGISTERED = {item["material_id"] for item in _REGISTER["materials"]}
# 65 份 = 注册表 20 份（产品 10 + 后端 10）+ 新增 45 份 PM 场景创作材料。
_PM_MATERIALS = [f"work-{n:04d}" for n in range(1, 11)] + [f"work-{n:04d}" for n in range(21, 66)]
_ALL_MATERIALS = sorted(_REGISTERED | set(_PM_MATERIALS))


def test_every_material_has_input_reference_and_oracle_triplet() -> None:
    """每份材料三件套齐备：人物输入、评估参考、评审 oracle。"""
    assert len(_ALL_MATERIALS) == 65
    for material in _ALL_MATERIALS:
        source = _WORK / f"inputs/{material}.txt"
        reference = _WORK / f"references/{material}.reference.json"
        oracle = _WORK / f"references/{material}.oracle.json"
        assert source.is_file() and source.stat().st_size > 100
        document = json.loads(reference.read_text(encoding="utf-8"))
        assert document["type"] == "scenario" and document["material_id"] == material
        sentences = document["summary_sentences"]
        assert sentences and all(isinstance(s, str) and s.strip() for s in sentences)
        review = json.loads(oracle.read_text(encoding="utf-8"))
        assert review["type"] == "scenario"
        assert review["source_reference"] == f"references/{material}.reference.json"
        assert review["expected"]["material_id"] == material
        assert review["expected"]["decisions"] and review["expected"]["action_items"]
        assert isinstance(review["piece_count"], int) and review["piece_count"] > 0


def test_person_inputs_never_contain_reference_or_oracle_content() -> None:
    """输入/参考分离存储：人物输入不含参考文本，参考答案位于独立 manifest 文件。"""
    for material in _ALL_MATERIALS:
        source = (_WORK / f"inputs/{material}.txt").read_bytes()
        oracle = (_WORK / f"references/{material}.oracle.json").read_bytes()
        reference = (_WORK / f"references/{material}.reference.json").read_bytes()
        assert hashlib.sha256(source).hexdigest() != hashlib.sha256(oracle).hexdigest()
        assert hashlib.sha256(source).hexdigest() != hashlib.sha256(reference).hexdigest()
        text = source.decode("utf-8")
        for sentence in json.loads(reference)["summary_sentences"]:
            assert sentence not in text
    paths = {entry.path for entry in load_manifest(_CORPUS).entries}
    assert "scenario-work/inputs/work-0001.txt" in paths
    assert "scenario-work/references/work-0001.oracle.json" in paths
    assert "scenario-work/references/work-0001.reference.json" in paths


def test_all_material_inputs_are_distinct_chinese_texts() -> None:
    """65 份输入互不相同、全部 UTF-8 中文、以注册表正文为字节子集不漂移。"""
    bodies = {}
    for material in _ALL_MATERIALS:
        text = (_WORK / f"inputs/{material}.txt").read_text(encoding="utf-8")
        assert any("\u4e00" <= ch <= "\u9fff" for ch in text)
        bodies[material] = text
    assert len(set(bodies.values())) == len(bodies)
    for item in _REGISTER["materials"]:
        assert bodies[item["material_id"]].strip() == item["body"]


def test_every_new_corpus_file_is_pinned_in_the_manifest() -> None:
    """材料三件套全部在 corpora manifest 留痕（来源/许可/哈希）。"""
    manifest = load_manifest(_CORPUS)
    by_path = {entry.path: entry for entry in manifest.entries}
    for material in _ALL_MATERIALS:
        for rel in (f"scenario-work/inputs/{material}.txt",
                    f"scenario-work/references/{material}.oracle.json",
                    f"scenario-work/references/{material}.reference.json"):
            entry = by_path[rel]
            content = (_CORPUS / rel).read_bytes()
            assert entry.sha256 == hashlib.sha256(content).hexdigest()
            assert entry.size_bytes == len(content)
            assert entry.source.license == "WorldMock-project"


def test_formal_scenario_inputs_use_distinct_pm_materials_across_84_days() -> None:
    """正式场景 56 任务：首任务钉 AMI 回放夹具，其余 55 份中文 PM 材料互不重复。"""
    document = json.loads(Path("scenarios/white-collar-12-weeks.json").read_text(encoding="utf-8"))
    tasks = document["tasks"]
    assert len(tasks) == 56
    assert tasks[0]["input_path"] == "ami-es2002/inputs/ES2002a.txt"
    chinese = [task["input_path"] for task in tasks[1:]]
    assert len(chinese) == len(set(chinese)) == 55
    assert all(path.startswith("scenario-work/inputs/") for path in chinese)
    assert all(Path(f"corpora/{path}").is_file() for path in chinese)
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    days = sorted(task.available_at.date().isoformat() for task in scenario.tasks)
    assert len(set(days)) == 56  # 84 天窗口内不重复分布（每任务独占一天）
    assert all(task.assignee_agent_id == "agent:1" for task in scenario.tasks)


def test_multi_person_assigns_backend_material_tasks_to_li_fan() -> None:
    """multi-person 给李凡（agent:2，后端）分配后端材料合同且技能达标。"""
    scenario, _ = load_scenario(Path("scenarios/multi-person.json"))
    skills = {agent.agent_id: agent.skill_levels for agent in scenario.agents}
    li_fan = [task for task in scenario.tasks if task.assignee_agent_id == "agent:2"]
    assert {task.task_id for task in li_fan} == {
        "backend-task-2026-01-05", "backend-hourly-2026-01-05", "backend-piece-2026-01-05"}
    for task in li_fan:
        assert task.input_path.startswith("scenario-work/inputs/work-00")
        number = int(Path(task.input_path).stem.split("-")[1])
        assert 11 <= number <= 20  # 后端材料区间
        assert task.required_skill == "backend_engineering"
        assert skills[2][task.required_skill] >= task.minimum_skill
    chenxi = [task for task in scenario.tasks if task.assignee_agent_id == "agent:1"]
    assert all(task.input_path.startswith("ami-es2002/inputs/") for task in chenxi)
