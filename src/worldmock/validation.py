"""@brief 在启动人物前验证完整场景、语料和实际数据消费者。"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from worldmock.catalog.admission import (
    CONTENT_CORPORA,
    validate_task_assignees,
    validate_world_categories,
)
from worldmock.catalog.loader import load_catalog
from worldmock.corpora.manifest import load_manifest
from worldmock.delivery.generation import EXPRESSIONS
from worldmock.delivery.rules import execute_fixture
from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.life.scenario import load_scenario
from worldmock.profile.coverage import (
    CoverageTargets,
    validate_coverage,
    require_coverage,
)

#: 媒介品类闭集映射：世界内容目录 kind -> 中文媒介词（与 render_day 标签同口径）。
_MEDIA_KIND_LABELS = {
    "feature_film": "电影",
    "drama_series": "剧集",
    "podcast_episode": "播客",
    "webnovel_excerpt": "网文",
    "news_feed": "新闻",
    "joke_feed": "段子",
    "subtitle_experience": "字幕体验",
    "game_session": "游戏",
}


def validate_project(project_root: Path, scenario_path: Path, *,
                     persona_compiler=None, coverage_out: Path | None = None) -> dict:
    """@brief 全量来源校验、实际规则执行与 Schema 检查失败时阻止新运行。

    新增（修订合同）：世界素材品类准入、任务受理人校验、两阶段
    model_identity 一致性与 persona_coverage.json 覆盖率门禁（<80% 拒绝）。
    """
    scenario, scenario_sha256 = load_scenario(scenario_path.resolve())
    corpus_root = Path(scenario.corpus_root)
    if not corpus_root.is_absolute():
        corpus_root = scenario_path.resolve().parent / corpus_root
    corpus = load_manifest(corpus_root, scenario.corpus_manifest_sha256)
    schemas = sorted((project_root / "labelkit-projects").rglob("*.json"))
    for path in schemas:
        Draft202012Validator.check_schema(json.loads(path.read_text()))
    versions = {name: importlib.metadata.version(name) for name in
                ("agentsociety2", "ray", "litellm", "mcp", "datasets", "textworld", "rule-engine")}
    if not (project_root / "vendor/labelkit/.venv/bin/labelkit").is_file():
        raise IntegrityError("The isolated LabelKit CLI environment is not installed")
    fixture = {"initial_state": {"deadline_seconds": 60},
               "events": [{"role": role, "timestamp_seconds": value} for role, value in
                          (("request", 0), ("accept", 1), ("complete", 2))],
               "expected": {"allowed": True}, "provenance": {"kind": "validation_fixture"}}
    execute_fixture(fixture, EXPRESSIONS)
    catalog_dir = scenario_path.resolve().parent.parent / "catalog"
    catalog = load_catalog(catalog_dir)
    _require_world_categories(catalog, corpus_root)
    _require_task_assignees(scenario)
    identity = validate_two_stage_model_identity()
    coverage = validate_persona_coverage(
        scenario_path, persona_compiler=persona_compiler, coverage_out=coverage_out,
        _scenario=scenario, _corpus_root=corpus_root)
    return {"valid": True, "scenario_id": scenario.scenario_id, "scenario_sha256": scenario_sha256,
            "corpus_entries": len(corpus.entries), "checked_schemas": len(schemas),
            "dependencies": versions, "model_identity": {"stages": sorted(identity["stages"]),
                                                          "consistent": True},
            "persona_coverage": coverage["agents"]}


def validate_two_stage_model_identity(identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """@brief 两阶段 model_identity 一致性校验（docs/dev/SPEC-life-realism.md「两阶段决策」，B2 常量单一事实源）。"""
    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        DECISION_STATEMENT_MAX_TOKENS,
        DECISION_STATEMENT_TEMPERATURE,
    )
    from agentsociety2.config.llm_dispatcher import TRANSPORT_MAX_RETRIES
    from worldmock.runtime import MAX_REACT_TURNS, two_stage_model_identity

    actual = identity if identity is not None else two_stage_model_identity()
    if not isinstance(actual, dict):
        raise IntegrityError("Two-stage model identity must be an object")
    problems: list[str] = []
    if actual.get("base_url") != "https://openrouter.ai/api/v1":
        problems.append(f"base_url={actual.get('base_url')!r} must be the frozen openai_compatible OpenRouter endpoint")
    if actual.get("max_retries") != 0:
        problems.append(f"max_retries={actual.get('max_retries')!r} must be 0")
    if type(actual.get("transport_max_retries")) is not int or actual["transport_max_retries"] != TRANSPORT_MAX_RETRIES:
        problems.append("transport_max_retries must match the dispatcher budget")
    if type(actual.get("max_react_turns")) is not int or actual["max_react_turns"] != MAX_REACT_TURNS:
        problems.append("max_react_turns must match the agent budget")
    if actual.get("strict") is not True:
        problems.append("every function declaration must be strict=true")
    if actual.get("reasoning_effort") is not None:
        problems.append("reasoning_effort must not be sent")
    stages = actual.get("stages")
    if not isinstance(stages, dict) or set(stages) != {"decision_statement", "action", "work_review"}:
        raise IntegrityError("Model identity stages must be exactly decision_statement, action, work_review")
    expected_decision_statement = {
        "thinking": "disabled", "temperature": DECISION_STATEMENT_TEMPERATURE,
        "max_tokens": DECISION_STATEMENT_MAX_TOKENS, "tools": None, "tool_choice": None,
    }
    expected_action = {"thinking": ACTION_THINKING["thinking"]["type"],
                       "tool_choice": ACTION_TOOL_CHOICE}
    expected_review = {"thinking": "disabled", "tool_choice": "required"}
    for name, expected in (("decision_statement", expected_decision_statement), ("action", expected_action),
                           ("work_review", expected_review)):
        if stages.get(name) != expected:
            problems.append(f"stage {name} is {stages.get(name)!r}, expected {expected!r}")
    if problems:
        raise IntegrityError("Two-stage model identity is inconsistent: " + "; ".join(problems))
    return actual


def validate_persona_coverage(scenario_path: Path, *,
                              persona_compiler=None,
                              coverage_out: Path | None = None,
                              _scenario: Any = None, _corpus_root: Path | None = None) -> dict:
    """@brief 校验场景人物卡片口味/习惯的世界可执行覆盖并写 persona_coverage.json。

    每个声明 background_story 的人物经画像编译（默认真实三次一致性抽取，
    persona_compiler 供确定性测试注入）得到 PersonaCard；可执行比例低于
    80% 时抛 PersonaCoverageError（校验失败），缺失项列在报告与文件中。
    """
    scenario = _scenario if _scenario is not None else load_scenario(
        Path(scenario_path).resolve())[0]
    corpus_root = _corpus_root
    if corpus_root is None:
        corpus_root = Path(scenario.corpus_root)
        if not corpus_root.is_absolute():
            corpus_root = Path(scenario_path).resolve().parent / corpus_root
    catalog_dir = Path(scenario_path).resolve().parent.parent / "catalog"
    targets = build_coverage_targets(scenario, load_catalog(catalog_dir), corpus_root)
    compile_story = persona_compiler or _compile_persona
    reports = []
    for agent in scenario.agents:
        if agent.background_story is None:
            continue
        card = compile_story(agent.background_story).card
        report = validate_coverage(card, targets)
        require_coverage(report)
        reports.append({"agent_id": agent.agent_id, **report.model_dump()})
    document = {"schema_version": 1, "scenario_id": scenario.scenario_id,
                "threshold": 0.8, "agents": reports}
    destination = Path(coverage_out) if coverage_out is not None else (
        Path(scenario_path).resolve().parent / "persona_coverage.json")
    atomic_write(destination, (json.dumps(document, ensure_ascii=False, indent=2)
                               + "\n").encode("utf-8"))
    return document


def _compile_persona(story: str) -> Any:
    """@brief 默认编译：A9 compile_persona（真实三次一致性抽取）。"""
    from worldmock.profile.compile import compile_persona

    return compile_persona(story)


def build_coverage_targets(scenario: Any, catalog: Any, corpora_root: Path) -> CoverageTargets:
    """@brief 从 catalog 品类/目录条目派生覆盖率目标集（确定性）。

    habit_response_keywords=生活活动目录名 + 场景地名；content_genres=内容
    语料条目 genre 闭集；food_kinds=正餐语料名 + 目录可进食商品名；
    media_kinds=世界实际可观看/游玩媒介的中文品类词。
    """
    root = Path(corpora_root)
    habit_keywords = sorted({item["name_zh"] for item in _corpus_items(root, "lifestyle/items.json")
                             if isinstance(item, dict) and item.get("name_zh")})
    habit_keywords += sorted({location.name for location in scenario.locations})
    genres = sorted({genre for relative, _ in CONTENT_CORPORA
                     for item in _corpus_items(root, relative) if isinstance(item, dict)
                     for genre in (item.get("genre") or [])})
    foods = sorted({item["name_zh"] for item in _corpus_items(root, "meals/items.json")
                    if isinstance(item, dict) and item.get("name_zh")})
    foods += sorted({obj.name for obj in catalog.objects.values()
                     if obj.object_kind == "product" and "eat" in obj.use_modes})
    media = sorted({_MEDIA_KIND_LABELS[kind] for kind in _scenario_content_kinds(scenario)})
    return CoverageTargets(habit_response_keywords=tuple(habit_keywords),
                           content_genres=tuple(genres),
                           food_kinds=tuple(foods),
                           media_kinds=tuple(media))


def _scenario_content_kinds(scenario: Any) -> set[str]:
    """@brief 场景实际可见的内容与游戏媒介品类闭集。"""
    kinds = {item.kind for item in scenario.contents}
    if getattr(scenario, "games", None):
        kinds.add("game_session")
    return kinds


def _corpus_items(root: Path, relative: str) -> list:
    """@brief 读取语料 JSON 并按装载方式展开条目（缺失返回空列表）。"""
    path = root / relative
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    mode = next((mode for candidate, mode in CONTENT_CORPORA if candidate == relative), "list")
    if mode == "single":
        return [document]
    if mode == "items":
        return list(document.get("items", [])) if isinstance(document, dict) else []
    return list(document) if isinstance(document, list) else []


def _require_world_categories(catalog: Any, corpora_root: Path) -> None:
    """@brief 世界素材品类准入：任一品类违例即拒绝（fail-closed，英文错误）。"""
    result = validate_world_categories(catalog, corpora_root)
    if not result.ok:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in result.violations)
        raise IntegrityError(f"World category admission rejected: {detail}")


def _require_task_assignees(scenario: Any) -> None:
    """@brief 场景任务受理人校验：tasks[].assignee_agent_id 必填。

    ScenarioTaskRef 是最小闭集 Schema（extra=forbid），只投影受理人
    校验三字段，不把完整合同字段灌进准入模型。
    """
    projection = ({"task_id": item.task_id, "assignee_agent_id": item.assignee_agent_id,
                   "input_path": item.input_path} for item in scenario.tasks)
    result = validate_task_assignees(projection)
    if not result.ok:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in result.violations)
        raise IntegrityError(f"Task assignee admission rejected: {detail}")
