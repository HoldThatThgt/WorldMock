"""@brief 场景完整装配、世界准入与新建运行的唯一编排。"""

from __future__ import annotations
from worldmock.life.load_checks import (
    check_location_kinds,
    check_visible_labels,
    check_work_schedules,
    load_purchase_channels,
)
from worldmock.life.work import load_work_input, load_work_oracle
import logging
from worldmock.life.models import AgentState
from worldmock.profile.injection import frozen_input_warnings
from worldmock.run_setup import validate_manifest_stage_identity
import hashlib
from worldmock.life import relevance
from worldmock.life.readable import _datetime
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from worldmock.catalog import load_catalog
from worldmock.categories.common import CategoryError
from worldmock.catalog.affordances import engine_affordances
from worldmock.corpora import load_manifest
from worldmock.errors import IntegrityError
from worldmock.life.map_data import load_map_dataset
from worldmock.life.motivation import load_motivation_transforms
from worldmock.life.scenario import RuntimeScenario, parse_scenario_document
from worldmock.scheduling.compile import compile_schedule
from worldmock.life.semantics import ScenarioSemantics, assemble_semantics, resolve_catalog_dir
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import RunIdentity, WorldState, WORLD_SCHEMA_VERSION
from worldmock.life.body import initial_body
from worldmock.life.settlement import inject_initial_funds
from worldmock.life.storefront_state import initial_supply_state, map_provenance
from worldmock.life.run_identity import category_identity, open_category_validated_store
from worldmock.life.readable import _datetime
from worldmock.life.state_commit import validated_payload, commit_state
from worldmock.life import sessions


def load_runtime_scenario(
    scenario_path: Path,
    catalog_dir: Path | None = None,
) -> tuple[ScenarioSemantics, str]:
    """@brief 完整加载：文档校验、目录组装、路线费率核验、目录准入与世界准入。

    世界准入（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-场景加载时就该拒绝的问题 ）：所选地点的 kind 属于环境层场所类型表，所选地点显示名唯一，
    每个人物可能同时看到的对象名称唯一；越界或重名在加载时拒绝，不留到运行中途。购买渠道（3.3）从场景钉定的手机配置
    派生并经准入后挂到 purchase_channels（load_checks.load_purchase_channels）。人物工作安排的跨引用（工作地点是所选地点，
    场景时间窗内与场景日历一致；docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）同样在加载时拒绝（load_checks.check_work_schedu
    les）
    。

    :param scenario_path: 瘦身场景 JSON 路径。
    :param catalog_dir: 目录根；缺省取场景兄弟目录 catalog/。
    :returns: 组装结果与场景文件 SHA-256。
    :raises IntegrityError: 文档、目录、组装、准入、世界准入、工作安排或渠道准入任一环节失败。
    """

    document, digest = parse_scenario_document(scenario_path)
    directory = Path(catalog_dir) if catalog_dir is not None else resolve_catalog_dir(scenario_path)
    try:
        catalog = load_catalog(directory)
        _require_engine_rules(directory)
        motivation_transforms = load_motivation_transforms(directory / "rules")
    except CategoryError as error:
        raise IntegrityError(f"Catalog load failed: {error}") from error
    affordances = engine_affordances()
    root = (Path(scenario_path).resolve().parent / document.corpus_root).resolve()
    dataset = load_map_dataset(root, document.map_dataset)
    semantics = assemble_semantics(document, catalog, dataset, motivation_transforms, affordances)
    check_location_kinds(semantics.scenario)
    check_visible_labels(semantics.scenario, root)
    check_work_schedules(semantics.scenario)
    _check_work_material(semantics.scenario, root)
    return replace(semantics, purchase_channels=load_purchase_channels(semantics.scenario, root)), digest


def _require_engine_rules(directory: Path) -> None:
    """@brief 冻结目录中的通用规则副本必须逐字节绑定已安装引擎的唯一规则资源。"""
    packaged = files("worldmock.data")
    for relative in ("affordances.toml", "rules/motivation.toml", "rules/persona-mapping.toml"):
        try:
            actual = (directory / relative).read_bytes()
            expected = packaged.joinpath(Path(relative).name).read_bytes()
        except OSError as error:
            logging.error("Engine rule snapshot is missing: %s", relative)
            raise IntegrityError(f"Engine rule snapshot is missing: {relative}") from error
        if actual != expected:
            logging.error("Engine rule snapshot does not match the installed engine: %s", relative)
            raise IntegrityError(f"Engine rule snapshot does not match the installed engine: {relative}")


def _check_work_material(scenario: RuntimeScenario, root: Path) -> None:
    """@brief 首次世界保存前核验每个任务的输入、验收材料和参考身份。"""

    for task in scenario.tasks:
        try:
            load_work_input(root, task)
            load_work_oracle(root, task)
        except IntegrityError:
            logging.error("Work material validation failed for task %s", task.task_id)
            raise


def load_scenario(path: Path) -> tuple[RuntimeScenario, str]:
    """@brief 完整加载场景：文档校验、目录组装与准入通过后返回运行时场景。"""

    semantics, digest = load_runtime_scenario(path)
    return semantics.scenario, digest


def _initial_state(semantics: ScenarioSemantics, scenario_sha256: str, run_id: str, taste_weights: dict) -> WorldState:
    """@brief 从组装语义形成无隐藏动作的完整初始世界；口味权重按人物注入。"""
    scenario = semantics.scenario
    start = int(scenario.start_at.timestamp())
    end = int(scenario.end_at.timestamp())
    agents = {}
    accounts = inject_initial_funds(dict(semantics.accounts), start)
    for config in scenario.agents:
        account_id = f"agent:{config.agent_id}"
        agents[config.agent_id] = _initial_agent(config, scenario, account_id, start, taste_weights)
    pending = compile_schedule(scenario, start, end)
    random_state = hashlib.sha256(f"random:{scenario.random_seed}".encode()).hexdigest()
    state = WorldState(
        schema_version=WORLD_SCHEMA_VERSION,
        version=0,
        current_time=start,
        end_time=end,
        run_id=run_id,
        timezone=scenario.timezone,
        scenario_sha256=scenario_sha256,
        content_manifest_sha256=scenario.corpus_manifest_sha256,
        agents=agents,
        accounts=accounts,
        **initial_supply_state(scenario, start),
        pending_events=pending,
        random_seed=scenario.random_seed,
        random_state=random_state,
    )
    place_names = {item.location_id: item.name for item in scenario.locations}
    for agent_id in sorted(agents):
        state = sessions.open_stay(state, agent_id, place_names)
    return state


def _initial_agent(config, scenario: RuntimeScenario, account_id: str, start: int, taste_weights: dict):
    """@brief 构造人物身体（两过程睡眠反推初值）、可见日历、空进度和注入口味。"""

    delivered = [
        item
        for item in scenario.messages
        if int(item.sent_at.timestamp()) <= start and item.recipient_agent_id == config.agent_id
    ]
    visible_mail = {item.message_id for item in delivered}
    visible_tasks = {item.related_task_id for item in delivered if item.related_task_id is not None}
    start_hour = _datetime(start, scenario.timezone).hour + _datetime(start, scenario.timezone).minute / 60
    body = initial_body(scenario.body, start_hour, config.sleep_need_hours, config.chronotype_midsleep)
    return AgentState(
        agent_id=config.agent_id,
        name=config.name,
        location_id=config.home_location_id,
        balance_account_id=account_id,
        skill_levels=config.skill_levels,
        qualifications=config.qualifications,
        body=body,
        body_settled_at=start,
        visible_task_ids=visible_tasks,
        known_task_ids=set(visible_tasks),
        visible_message_ids=visible_mail,
        next_decision_at=start,
        pending_wake_reasons=["run_start"],
        taste_weights=taste_weights.get(config.agent_id) or {},
        inventory=config.initial_inventory,
    )


def _run_manifest(
    semantics: ScenarioSemantics,
    scenario_sha256: str,
    identity: RunIdentity,
    stage_identity: dict | None = None,
    options: dict | None = None,
) -> dict:
    """@brief 绑定新运行的场景、内容、人物身份、代码身份（另记 Git 提交与未提交标志）、各阶段请求形状、运行输入警告与
    习惯签名的反应文本。

    清单版本 2 起带 input_warnings（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 ：创建时计算的输入警告，按闭集形状冻结；
    引擎直驱为 null）与 persona_habit_responses（5.1 相关度的人设层习惯签名反应文本，人物编号 → 文本列表；引擎直驱为
    null），两者都取自 create_options。
    """
    validate_manifest_stage_identity({"stage_model_identity": stage_identity})
    scenario, options = semantics.scenario, options or {}
    category_fields = category_identity(semantics)
    return {
        "schema_version": 2,
        "run_id": identity.run_id,
        "source_group_id": identity.source_group_id,
        "scenario_id": scenario.scenario_id,
        "map_dataset": map_provenance(scenario),
        "scenario_sha256": scenario_sha256,
        "corpus_manifest_sha256": scenario.corpus_manifest_sha256,
        "agent_ids": list(identity.agent_ids),
        "code_sha256": identity.code_sha256,
        "git_commit": identity.git_commit,
        "git_dirty": identity.git_dirty,
        "model_identity": identity.model_identity.model_dump(mode="json"),
        "stage_model_identity": stage_identity,
        "input_warnings": frozen_input_warnings(options.get("input_warnings")),
        relevance.MANIFEST_KEY: relevance.frozen_habit_responses(options.get(relevance.MANIFEST_KEY)),
        "window": {
            "start": int(scenario.start_at.timestamp()),
            "end": int(scenario.end_at.timestamp()),
            "timezone": scenario.timezone,
        },
        **category_fields,
    }


def _validate_run_identity(scenario: RuntimeScenario, identity: RunIdentity) -> None:
    """@brief 拒绝 runtime 身份与冻结场景窗口或人物不一致。"""
    expected_agents = tuple(sorted(item.agent_id for item in scenario.agents))
    if tuple(sorted(identity.agent_ids)) != expected_agents:
        raise IntegrityError("Run identity agent ids disagree with scenario")
    if identity.start_at != scenario.start_at.isoformat() or identity.end_at != scenario.end_at.isoformat():
        raise IntegrityError("Run identity window disagrees with scenario")
    if identity.timezone != scenario.timezone:
        raise IntegrityError("Run identity timezone disagrees with scenario")


def create_world(
    scenario_path: Path, run_dir: Path, run_identity: RunIdentity, stage_identity: dict | None, options: dict
):
    """@brief 完成实际新建装配、输入身份检查和初始资金事务。"""
    semantics, digest = load_runtime_scenario(scenario_path)
    scenario = semantics.scenario
    corpus_root = (Path(scenario_path).resolve().parent / scenario.corpus_root).resolve()
    load_manifest(corpus_root, scenario.corpus_manifest_sha256)
    _validate_run_identity(scenario, run_identity)
    state = _initial_state(semantics, digest, run_identity.run_id, options.get("agent_taste_weights") or {})
    manifest = _run_manifest(semantics, digest, run_identity, stage_identity, options)
    store = FootprintStore.create(Path(run_dir), manifest, validated_payload(state))
    changes = {f"account:{key}": value for key, value in state.accounts.items()}
    changes["account:external:scenario"] = -sum(state.accounts.values())
    injection = EventDraft(
        event_type="environment_event",
        occurred_at=state.current_time,
        visibility="evaluation",
        source_kind="scenario",
        data={"kind": "initial_funds_injected", "resource_changes": changes},
    )
    commit_state(store, state, [injection])
    return semantics, digest, corpus_root, store


def open_world(scenario_path: Path, run_dir: Path):
    """@brief 真实加载场景、核对语料并只打开现有合法运行。"""
    semantics, digest = load_runtime_scenario(scenario_path)
    corpus_root = (Path(scenario_path).resolve().parent / semantics.scenario.corpus_root).resolve()
    load_manifest(corpus_root, semantics.scenario.corpus_manifest_sha256)
    return semantics, digest, corpus_root, open_category_validated_store(Path(run_dir), semantics)
