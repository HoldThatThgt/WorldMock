"""@brief 单人环境输入冻结、世界初始化与已有运行准入。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.catalog.mapgen import map_bytes
from worldmock.environment import EnvironmentInputs, environment_identity, load_environment
from worldmock.errors import IntegrityError
from worldmock.footprints.assets import AssetStore
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import AgentState, RunIdentity, WorldState, WORLD_SCHEMA_VERSION
from worldmock.life.models import validated_payload
from worldmock.life.run_identity import open_environment_validated_store
from worldmock.life.stay_records import run_start_event
from worldmock.profile.models import WorldDynamics


def _initial_state(inputs: EnvironmentInputs, run_id: str, dynamics: WorldDynamics) -> WorldState:
    """@brief 无隐式活动地初始化唯一人物、签名账户与目录回调状态。"""
    from worldmock.life import sessions

    start = int(inputs.config.start_time.timestamp())
    persona = inputs.persona
    account_id = f"agent:{persona.agent_id}"
    agent = _initial_agent(inputs, dynamics, start)
    state = WorldState(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=start,
        end_time=int(inputs.config.stop_time.timestamp()), run_id=run_id, timezone=inputs.config.timezone,
        persona_sha256=inputs.persona_sha256, config_sha256=inputs.config_sha256,
        agents={persona.agent_id: agent},
        accounts={account_id: persona.initial_balance_fen, "external:scenario": -persona.initial_balance_fen},
        random_seed=inputs.config.seed,
        random_state=hashlib.sha256(f"random:{inputs.config.seed}".encode()).hexdigest(),
    )
    from worldmock.life.weather import generated_weather, local_weather_date

    date = local_weather_date(state)
    state.weather_by_date[date] = generated_weather(state, date)
    names = {identity: place.display_name for identity, place in inputs.catalog.locations.items()}
    return sessions.open_stay(state, persona.agent_id, names)


def _initial_agent(inputs: EnvironmentInputs, dynamics: WorldDynamics, start: int) -> AgentState:
    """@brief 身体初值来自人设，社交增长率来自实际编译画像，不提供缺省替代。"""
    from datetime import datetime

    persona = inputs.persona
    local = datetime.fromtimestamp(start, ZoneInfo(inputs.config.timezone))
    hour = local.hour + local.minute / 60
    from worldmock.life.body import initial_body
    from worldmock.life.body_config import assemble_body_config

    body = initial_body(assemble_body_config(persona), hour, persona, dynamics.social_need_growth_per_hour)
    return AgentState(
        agent_id=persona.agent_id, name=persona.name, location_id=inputs.initial_location_id,
        balance_account_id=f"agent:{persona.agent_id}", body=body, body_settled_at=start,
        next_decision_at=start, pending_wake_reasons=["session_open"],
        callback_states={name: row.initial for name, row in inputs.catalog.callback_states.items()},
    )


def _freeze_environment(inputs: EnvironmentInputs, assets: AssetStore) -> dict[str, dict]:
    """@brief 原文字节与内存地图分别冻结，身份仍由各自原字节计算。"""
    names = ["persona.toml", "config.toml", "catalog/manifest.json"]
    names.extend(f"catalog/{entry.path}" for entry in inputs.catalog.manifest.files)
    frozen = {}
    for name in sorted(names):
        content = (inputs.root / name).read_bytes()
        media = "application/json" if name.endswith(".json") else "text/plain"
        reference = assets.put(content, {"media_type": media, "source": {"kind": "environment_input", "path": name}})
        frozen[name] = reference.model_dump(mode="json")
    reference = assets.put(map_bytes(inputs.catalog.map_data),
                           {"media_type": "application/json", "source": {"kind": "environment_map"}})
    frozen["map.json"] = reference.model_dump(mode="json")
    return frozen


def _run_manifest(inputs: EnvironmentInputs, identity: RunIdentity, stages: dict,
                  options: dict, assets: dict[str, dict]) -> dict:
    """@brief 目录、人物与配置各自保持身份，清单不再含场景文档身份。"""
    from worldmock.profile.injection import frozen_input_warnings
    return {
        "schema_version": 5, "run_id": identity.run_id, "source_group_id": identity.source_group_id,
        "environment_identity": environment_identity(inputs), "environment_assets": assets,
        "persona_sha256": inputs.persona_sha256, "config_sha256": inputs.config_sha256,
        "agent_ids": list(identity.agent_ids), "code_sha256": identity.code_sha256,
        "git_commit": identity.git_commit, "git_dirty": identity.git_dirty,
        "model_identity": identity.model_identity.model_dump(mode="json"), "stage_model_identity": stages,
        "model_metadata": inputs.model_metadata.model_dump(mode="json"),
        "world_dynamics": options["world_dynamics"],
        "input_warnings": frozen_input_warnings(options.get("input_warnings")),
        "persona_habit_responses": options.get("persona_habit_responses"),
        "window": {"start": int(inputs.config.start_time.timestamp()),
                   "end": int(inputs.config.stop_time.timestamp()), "timezone": inputs.config.timezone},
    }


def _validate_run_identity(inputs: EnvironmentInputs, identity: RunIdentity) -> None:
    """@brief 新运行身份必须逐字匹配单人人设、原生时间窗口与时区。"""
    valid = (identity.agent_ids == (inputs.persona.agent_id,)
             and identity.start_at == inputs.config.start_time.isoformat()
             and identity.end_at == inputs.config.stop_time.isoformat()
             and identity.timezone == inputs.config.timezone)
    if not valid:
        logging.error("Run identity disagrees with environment inputs")
        raise IntegrityError("Run identity disagrees with environment inputs")


def create_world(environment: EnvironmentInputs | Path, run_dir: Path, identity: RunIdentity,
                 stage_identity: dict, options: dict):
    """@brief 已验证输入或完整目录新建唯一世界，保存原文并登记真实初始资金与运行开始时人物所在的那段停留。"""
    inputs = environment if isinstance(environment, EnvironmentInputs) else load_environment(environment)
    _validate_run_identity(inputs, identity)
    _validate_creation_model(inputs, identity, stage_identity)
    if "world_dynamics" not in options:
        logging.error("Compiled persona dynamics are required to create a world")
        raise IntegrityError("Compiled persona dynamics are required to create a world")
    dynamics = WorldDynamics.model_validate(options["world_dynamics"])
    state = _initial_state(inputs, identity.run_id, dynamics)
    from worldmock.scheduling.compile import compile_schedule

    state.pending_events = compile_schedule(inputs)
    assets = _freeze_environment(inputs, AssetStore(Path(run_dir) / "assets"))
    manifest = _run_manifest(inputs, identity, stage_identity, options, assets)
    store = FootprintStore.create(Path(run_dir), manifest, validated_payload(state))
    injection = EventDraft(
        event_type="environment_event", occurred_at=state.current_time, visibility="evaluation",
        source_kind="scenario", data={"kind": "initial_funds_injected"},
        resource_changes={f"account:{key}": value for key, value in state.accounts.items()},
    )
    entered = run_start_event(state)
    state, drafts = _run_start_callbacks(state, inputs.catalog)
    store.commit(validated_payload(state), [injection, entered, *drafts])
    return inputs, store


def _validate_creation_model(inputs: EnvironmentInputs, identity: RunIdentity, stages: dict) -> None:
    """@brief 从实际配置与元数据派生既有身份，在创建资产前拒绝自洽但不同源的调用方声明。"""
    from worldmock import run_setup

    checked = run_setup.validate_manifest_stage_identity({"stage_model_identity": stages})
    expected_model = run_setup._model_identity(inputs.config.model)
    if identity.model_identity != expected_model:
        logging.error("Run model identity disagrees with environment inputs")
        raise IntegrityError("Run model identity disagrees with environment inputs")
    continuation = checked["stages"]["action"]["same_second_continuation"]
    expected_stages = run_setup.stage_model_identity(inputs.config.model, inputs.model_metadata, continuation)
    if checked != expected_stages:
        logging.error("Stage model identity disagrees with environment inputs")
        raise IntegrityError("Stage model identity disagrees with environment inputs")


def open_world(environment: EnvironmentInputs | Path, run_dir: Path):
    """@brief 已有运行核对目录及两个原文字节身份，不重建初值或重复运行开始回调。"""
    inputs = environment if isinstance(environment, EnvironmentInputs) else load_environment(environment)
    return inputs, open_environment_validated_store(Path(run_dir), inputs)


def _run_start_callbacks(state: WorldState, catalog) -> tuple[WorldState, list[EventDraft]]:
    """@brief 初始化之后只执行一次运行开始，不订阅首次启动的唤醒类别。"""
    from worldmock.life.callbacks import CallbackCause, compare_callback_states, evaluate_callbacks
    from worldmock.scheduling.effects import time_trigger_draft

    before = state
    source = time_trigger_draft(state, "run_start")
    cause = CallbackCause("time", "run_start", next(iter(state.agents)), source.event_id, source.data["scheduled_id"])
    outcome = evaluate_callbacks(state, catalog, cause)
    drafts = [source, *outcome.drafts]
    threshold = compare_callback_states(before, outcome.state, catalog, drafts, outcome.causes)
    changed = threshold.state.model_copy(update={"run_start_callbacks_applied": True, "version": state.version + 1})
    return changed, [*drafts, *threshold.drafts]
