"""@brief 多数字人环境输入冻结、世界初始化与已有运行准入。"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from worldmock.configuration.inputs.environment import EnvironmentInputs, environment_identity, load_environment
from worldmock.configuration.inputs.models import PersonaConfig, referenced_profile_ids
from worldmock.configuration.inputs.warnings import frozen_input_warnings
from worldmock.configuration.model.model_metadata import frozen_metadata_loader
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.assets import AssetStore
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.resource_engine.map.mapgen import map_bytes
from worldmock.simulation_engine.activities.lifecycle import sessions
from worldmock.simulation_engine.body.body import initial_body
from worldmock.simulation_engine.body.body_config import assemble_body_config
from worldmock.simulation_engine.evidence.stays import run_start_events
from worldmock.simulation_engine.scheduling.callbacks.callbacks import (
    CallbackCause,
    compare_callback_states,
    evaluate_callbacks,
)
from worldmock.simulation_engine.scheduling.clock.compile import compile_schedule
from worldmock.simulation_engine.scheduling.clock.effects import time_trigger_draft
from worldmock.simulation_engine.scheduling.clock.weather import register_daily_weather
from worldmock.simulation_engine.world.run_identity import require_environment_identity
from worldmock.state.identity import RUN_MANIFEST_SCHEMA_VERSION, ModelIdentity, RunIdentity, StageProfileIdentity
from worldmock.state.world.agent import AgentState
from worldmock.state.world.world import WORLD_SCHEMA_VERSION, WorldState, validated_payload

_LOGGER = logging.getLogger(__name__)
#: 明确拒绝的已退役清单或创建选项键。
_RETIRED_MODEL_KEYS = frozenset({"stage_model_identity", "two_stage_model_identity", "same_second_continuation"})


def _initial_state(inputs: EnvironmentInputs, run_id: str) -> WorldState:
    """@brief 无隐式活动地初始化全部数字人物、签名账户与目录回调状态。"""
    start = int(inputs.config.start_time.timestamp())
    agents = {persona.agent_id: _initial_agent(inputs, persona, start) for persona in inputs.personas}
    total_balance = sum(persona.initial_balance_fen for persona in inputs.personas)
    accounts = {f"agent:{persona.agent_id}": persona.initial_balance_fen for persona in inputs.personas}
    accounts["external:scenario"] = -total_balance
    state = WorldState(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=start,
        end_time=int(inputs.config.stop_time.timestamp()), run_id=run_id, timezone=inputs.config.timezone,
        persona_sha256s=dict(inputs.persona_sha256s), config_sha256=inputs.config_sha256,
        agents=agents, accounts=accounts, random_seed=inputs.config.seed,
        random_state=hashlib.sha256(f"random:{inputs.config.seed}".encode()).hexdigest(),
    )
    register_daily_weather(state, inputs.catalog)
    names = {identity: place.display_name for identity, place in inputs.catalog.locations.items()}
    for persona in sorted(inputs.personas, key=lambda item: item.agent_id):
        state = sessions.open_stay(state, persona.agent_id, names)
    return state


def _initial_agent(inputs: EnvironmentInputs, persona: PersonaConfig, start: int) -> AgentState:
    """@brief 身体初值、睡眠节律与社交增长率都来自对应人设表，不提供缺省替代。"""
    local = datetime.fromtimestamp(start, ZoneInfo(inputs.config.timezone))
    hour = local.hour + local.minute / 60
    body = initial_body(assemble_body_config(persona), hour, persona)
    return AgentState(
        agent_id=persona.agent_id, name=persona.name, location_id=inputs.initial_location_ids[persona.agent_id],
        balance_account_id=f"agent:{persona.agent_id}", body=body, body_settled_at=start,
        next_decision_at=start, pending_wake_reasons=["session_open"],
        callback_states={name: row.initial for name, row in inputs.catalog.callback_states.items()},
    )


def _freeze_environment(inputs: EnvironmentInputs, assets: AssetStore) -> dict[str, dict]:
    """@brief 原文字节与内存地图分别冻结，身份仍由各自原字节计算。"""
    names = [*inputs.persona_paths.values(), "config.toml", "catalog/manifest.json"]
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


def _run_manifest(inputs: EnvironmentInputs, identity: RunIdentity,
                  options: dict[str, Any], assets: dict[str, dict]) -> dict[str, Any]:
    """@brief 构造 v9 运行清单：多数字人摘要、双维模型身份与冻结环境资产。"""
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION, "run_id": identity.run_id,
        "source_group_id": identity.source_group_id,
        "environment_identity": environment_identity(inputs), "environment_assets": assets,
        "persona_sha256s": {str(key): value for key, value in inputs.persona_sha256s.items()},
        "config_sha256": inputs.config_sha256, "agent_ids": list(identity.agent_ids),
        "code_sha256": identity.code_sha256, "git_commit": identity.git_commit, "git_dirty": identity.git_dirty,
        "model_identity": identity.model_identity.model_dump(mode="json"),
        "input_warnings": frozen_input_warnings(options.get("input_warnings")),
        "persona_habit_responses": options.get("persona_habit_responses"),
        "window": {"start": int(inputs.config.start_time.timestamp()),
                   "end": int(inputs.config.stop_time.timestamp()), "timezone": inputs.config.timezone},
        "lineage": [],
    }


def _validate_run_identity(inputs: EnvironmentInputs, identity: RunIdentity) -> None:
    """@brief 新运行身份必须逐字匹配多数字人集合、时间窗口与时区。"""
    expected_ids = tuple(sorted(persona.agent_id for persona in inputs.personas))
    valid = (tuple(sorted(identity.agent_ids)) == expected_ids
             and dict(identity.persona_sha256s) == dict(inputs.persona_sha256s)
             and identity.start_at == inputs.config.start_time.isoformat()
             and identity.end_at == inputs.config.stop_time.isoformat()
             and identity.timezone == inputs.config.timezone)
    if not valid:
        _LOGGER.error("Run identity disagrees with environment inputs")
        raise IntegrityError("Run identity disagrees with environment inputs")


def _expected_profile_identity(inputs: EnvironmentInputs, profile_id: str) -> StageProfileIdentity:
    """@brief 从配置与权威服务元数据派生单个模型档的冻结身份。"""
    profile = inputs.config.models[profile_id]
    metadata = inputs.model_metadata[profile_id]
    return StageProfileIdentity(**profile.model_dump(exclude={"api_key_env"}), model_metadata=metadata,
                                effective_max_tokens=min(profile.max_output_tokens, metadata.max_output_tokens))


def _validate_routing_and_profiles(inputs: EnvironmentInputs, checked: ModelIdentity) -> None:
    """@brief 核对模型身份的路由映射、触发阈值与缓存开关等激活档集合与冻结环境配置及元数据一致。"""
    expected_routing = inputs.config.model_routing.model_dump(mode="json")
    expected_profiles = {pid: _expected_profile_identity(inputs, pid)
                         for pid in referenced_profile_ids(inputs.config.model_routing)}
    if checked.routing != expected_routing or dict(checked.profiles) != expected_profiles:
        _LOGGER.error("Run model identity disagrees with environment inputs")
        raise IntegrityError("Run model identity disagrees with environment inputs")
    for pid, expected in expected_profiles.items():
        actual = checked.profiles[pid]
        if (actual.compact_trigger_tokens != expected.compact_trigger_tokens
                or actual.prompt_cache_key is not expected.prompt_cache_key):
            _LOGGER.error("Stage profile %s compaction or cache settings disagree with config", pid)
            raise IntegrityError("Run model identity disagrees with environment inputs")


def _validate_model_identity(inputs: EnvironmentInputs, identity: RunIdentity,
                             supplied: ModelIdentity | dict[str, Any] | None) -> None:
    """@brief 在写任何运行文件之前核对显式模型身份与环境配置及元数据一致。"""
    if supplied is None:
        _LOGGER.error("Explicit model identity is required when creating a run")
        raise IntegrityError("Explicit model identity is required when creating a run")
    try:
        checked = supplied if isinstance(supplied, ModelIdentity) else ModelIdentity.model_validate(supplied)
    except ValidationError as error:
        _LOGGER.error("Supplied model identity is invalid")
        raise IntegrityError("Supplied model identity is invalid") from error
    if checked != identity.model_identity:
        _LOGGER.error("Run model identity disagrees with supplied model identity")
        raise IntegrityError("Run model identity disagrees with supplied model identity")
    _validate_routing_and_profiles(inputs, checked)


def create_world(environment: EnvironmentInputs | Path, run_dir: Path, identity: RunIdentity,
                 model_identity: ModelIdentity | dict[str, Any] | None, options: dict[str, Any] | None = None):
    """@brief 已验证输入或完整目录新建唯一世界，保存原文并登记初始资金与全部人物的初始停留。"""
    resolved_options = dict(options or {})
    if _RETIRED_MODEL_KEYS & set(resolved_options):
        _LOGGER.error("Retired model identity option keys are rejected")
        raise IntegrityError("Retired model identity option keys are rejected")
    inputs = environment if isinstance(environment, EnvironmentInputs) else load_environment(environment)
    _validate_run_identity(inputs, identity)
    _validate_model_identity(inputs, identity, model_identity)
    state = _initial_state(inputs, identity.run_id)
    state.pending_events = compile_schedule(inputs)
    assets = _freeze_environment(inputs, AssetStore(Path(run_dir) / "assets"))
    manifest = _run_manifest(inputs, identity, resolved_options, assets)
    initial = validated_payload(state)
    store = FootprintStore.create(Path(run_dir), manifest, initial)
    injection = EventDraft(
        event_type="environment_event", occurred_at=state.current_time, visibility="evaluation",
        source_kind="scenario", data={"kind": "initial_funds_injected"},
        resource_changes={f"account:{key}": value for key, value in state.accounts.items()},
    )
    entered = run_start_events(state)
    state, drafts = _run_start_callbacks(state, inputs.catalog)
    store.commit(initial, validated_payload(state), [injection, *entered, *drafts])
    return inputs, store


def open_world(environment: EnvironmentInputs | Path, run_dir: Path):
    """@brief 打开已有运行存储，按运行清单中的冻结模型元数据校验目录及全部人物配置摘要。"""
    store = FootprintStore(Path(run_dir))
    try:
        if _RETIRED_MODEL_KEYS & set(store.manifest):
            _LOGGER.error("Run manifest contains retired model identity keys")
            raise IntegrityError("Run manifest contains retired model identity keys")
        inputs = (environment if isinstance(environment, EnvironmentInputs)
                  else load_environment(environment, metadata_loader=frozen_metadata_loader(store.manifest)))
        require_environment_identity(store.manifest, inputs)
        _validate_routing_and_profiles(inputs, ModelIdentity.model_validate(store.manifest.get("model_identity")))
    except Exception:
        _LOGGER.error("Existing run could not be opened")
        store.close()
        raise
    return inputs, store


def _run_start_callbacks(state: WorldState, catalog) -> tuple[WorldState, list[EventDraft]]:
    """@brief 初始化之后为每个人物执行一次运行开始回调，跨人物按编号排序并只评估一次阈值。"""
    before = state
    current = state
    drafts: list[EventDraft] = []
    causes: dict = {}
    multi = len(state.agents) > 1
    for agent_id in sorted(state.agents):
        scheduled_id = f"time-run_start-{state.current_time}-{agent_id}" if multi else None
        source = time_trigger_draft(current, "run_start", scheduled_id, agent_id=agent_id)
        cause = CallbackCause("time", "run_start", agent_id, source.event_id, source.data["scheduled_id"])
        outcome = evaluate_callbacks(current, catalog, cause)
        current = outcome.state
        drafts.extend((source, *outcome.drafts))
        causes.update(outcome.causes)
    threshold = compare_callback_states(before, current, catalog, drafts, causes)
    changed = threshold.state.model_copy(update={"run_start_callbacks_applied": True, "version": state.version + 1})
    return changed, [*drafts, *threshold.drafts]
