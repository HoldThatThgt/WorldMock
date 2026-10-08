"""@brief 场景完整装配、世界准入与新建运行的唯一编排。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from worldmock.catalog import load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.load_checks import (
    check_location_kinds, check_parcel_stations, check_visible_labels, check_work_schedules,
)
from worldmock.life.models import AgentState, RunIdentity, WorldState, WORLD_SCHEMA_VERSION
from worldmock.life.scenario import RuntimeScenario, parse_scenario_document
from worldmock.life.semantics import ScenarioSemantics, assemble_semantics, resolve_catalog_dir
from worldmock.life.run_identity import environment_identity, open_environment_validated_store

if TYPE_CHECKING:
    from worldmock.footprints.store import FootprintStore


def load_runtime_scenario(
    scenario_path: Path,
    catalog_dir: Path | None = None,
) -> tuple[ScenarioSemantics, str]:
    """@brief 读取结构八场景、登记环境与固定规则，再执行可见名称和工作安排准入。

    @param scenario_path 场景 JSON 路径，目录使用场景兄弟目录 catalog。
    @param catalog_dir 环境登记根；缺省为场景兄弟目录 catalog。
    @return 已验证的冻结语义及场景原文字节的 SHA-256。
    @exception IntegrityError 文档、环境身份或场景准入不满足合同。
    """

    document, digest = parse_scenario_document(scenario_path)
    directory = Path(catalog_dir) if catalog_dir is not None else resolve_catalog_dir(scenario_path)
    catalog = load_catalog(directory)
    semantics = assemble_semantics(document, catalog)
    check_location_kinds(semantics.scenario)
    check_visible_labels(semantics.scenario)
    check_work_schedules(semantics.scenario)
    check_parcel_stations(semantics)
    return semantics, digest


def load_scenario(path: Path) -> tuple[RuntimeScenario, str]:
    """@brief 完整加载场景：文档校验、目录组装与准入通过后返回运行时场景。"""

    semantics, digest = load_runtime_scenario(path)
    return semantics.scenario, digest


def _initial_state(semantics: ScenarioSemantics, scenario_sha256: str, run_id: str) -> WorldState:
    """@brief 从组装语义形成无隐藏动作的完整初始世界。"""
    from worldmock.life import sessions
    from worldmock.life.settlement import inject_initial_funds
    from worldmock.scheduling.compile import compile_schedule

    scenario = semantics.scenario
    start = int(scenario.start_at.timestamp())
    end = int(scenario.end_at.timestamp())
    agents = {}
    accounts = inject_initial_funds(dict(semantics.accounts), start)
    for config in scenario.agents:
        account_id = f"agent:{config.agent_id}"
        agents[config.agent_id] = _initial_agent(config, scenario, account_id, start)
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
        agents=agents,
        accounts=accounts,
        pending_events=pending,
        random_seed=scenario.random_seed,
        random_state=random_state,
    )
    place_names = {item.location_id: item.name for item in scenario.locations}
    for agent_id in sorted(agents):
        state = sessions.open_stay(state, agent_id, place_names)
    return state


def _initial_agent(config, scenario: RuntimeScenario, account_id: str, start: int):
    """@brief 构造人物身体初值与已送达的可见邮件。"""

    from worldmock.life.body import initial_body
    from worldmock.life.readable import _datetime

    delivered = [
        item
        for item in scenario.messages
        if int(item.sent_at.timestamp()) <= start and item.recipient_agent_id == config.agent_id
    ]
    visible_mail = {item.message_id for item in delivered}
    start_hour = _datetime(start, scenario.timezone).hour + _datetime(start, scenario.timezone).minute / 60
    body = initial_body(scenario.body, start_hour, config.sleep_need_hours, config.chronotype_midsleep)
    body = body.model_copy(update={"entertainment_need": scenario.body.initial_entertainment_need})
    return AgentState(
        agent_id=config.agent_id,
        name=config.name,
        location_id=config.home_location_id,
        balance_account_id=account_id,
        driving_days=config.initial_driving_days,
        last_driving_date=None,
        body=body,
        body_settled_at=start,
        visible_message_ids=visible_mail,
        next_decision_at=start,
        pending_wake_reasons=["run_start"],
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

    清单版本 2 起带 input_warnings（docs/design/04-simulation-engine.md ：创建时计算的输入警告，按闭集形状冻结；
    引擎直驱为 null）与 persona_habit_responses（5.1 相关度的人设层习惯签名反应文本，人物编号 → 文本列表；引擎直驱为
    null），两者都取自 create_options。
    """
    from worldmock.life import relevance
    from worldmock.profile.injection import frozen_input_warnings
    from worldmock.run_setup import validate_manifest_stage_identity

    validate_manifest_stage_identity({"stage_model_identity": stage_identity})
    scenario, options = semantics.scenario, options or {}
    return {
        "schema_version": 4,
        "run_id": identity.run_id,
        "source_group_id": identity.source_group_id,
        "scenario_id": scenario.scenario_id,
        "environment_identity": environment_identity(semantics),
        "scenario_sha256": scenario_sha256,
        "agent_ids": list(identity.agent_ids),
        "work_content_ids": {str(agent.agent_id): list(agent.work_content_ids) for agent in scenario.agents},
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
    }


def _validate_run_identity(scenario: RuntimeScenario, identity: RunIdentity) -> None:
    """@brief 拒绝 runtime 身份与冻结场景窗口或人物不一致。"""
    expected_agents = tuple(sorted(item.agent_id for item in scenario.agents))
    if tuple(sorted(identity.agent_ids)) != expected_agents:
        logging.error("Run identity agent ids disagree with scenario")
        raise IntegrityError("Run identity agent ids disagree with scenario")
    if identity.start_at != scenario.start_at.isoformat() or identity.end_at != scenario.end_at.isoformat():
        logging.error("Run identity window disagrees with scenario")
        raise IntegrityError("Run identity window disagrees with scenario")
    if identity.timezone != scenario.timezone:
        logging.error("Run identity timezone disagrees with scenario")
        raise IntegrityError("Run identity timezone disagrees with scenario")


def create_world(
    scenario_path: Path, run_dir: Path, run_identity: RunIdentity, stage_identity: dict | None, options: dict
):
    """@brief 完成实际新建装配、输入身份检查和初始资金事务。"""
    from worldmock.footprints.models import EventDraft
    from worldmock.footprints.store import FootprintStore
    from worldmock.life.models import validated_payload

    semantics, digest = load_runtime_scenario(scenario_path)
    scenario = semantics.scenario
    _validate_run_identity(scenario, run_identity)
    state = _initial_state(semantics, digest, run_identity.run_id)
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
    store.commit(validated_payload(state), [injection])
    return semantics, digest, store


def open_world(scenario_path: Path, run_dir: Path):
    """@brief 真实加载场景、核对环境身份并只打开现有合法运行。"""
    semantics, digest = load_runtime_scenario(scenario_path)
    return semantics, digest, open_environment_validated_store(Path(run_dir), semantics)
