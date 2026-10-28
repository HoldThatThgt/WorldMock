"""@brief 完整单人环境、唯一世界状态和足迹提交的共享上下文。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import Event, EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.body.body_config import BodyConfig, assemble_body_config
from worldmock.state.world.world import WorldState, validated_payload
from worldmock.simulation_engine.world.run_identity import require_environment_identity
from worldmock.recording_engine.storage.opslog import OpsLogger


@dataclass
class EngineContext:
    """@brief 所有运行职责读取同一输入并提交到同一世界，不保留场景容器。"""

    #: 纯加载器已经验证的完整环境。
    inputs: EnvironmentInputs
    #: 唯一持久化存储。
    store: FootprintStore
    #: 当前已提交状态；只由 commit_step 在一步世界推进成功后替换。
    state: WorldState = field(init=False)
    #: 固定身体规则视图。
    body: BodyConfig = field(init=False)
    #: 运行日志。
    ops: OpsLogger = field(init=False)
    #: 同次提交的回调原因，提交后清空，不成为第二份世界状态。
    callback_causes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """@brief 状态恢复先核对完整输入身份，再创建运行日志。"""
        self.state = WorldState.model_validate(self.store.state)
        self.body = assemble_body_config(self.inputs.persona)
        self.validate()
        self.ops = OpsLogger(self.store.run_dir, self.inputs.config.timezone)

    @property
    def catalog(self):
        """@brief 直接读取冻结目录，避免复制另一份登记状态。"""
        return self.inputs.catalog

    @property
    def persona(self):
        """@brief 唯一人物声明直接来自完整环境。"""
        return self.inputs.persona

    @property
    def config(self):
        """@brief 唯一运行配置直接来自完整环境。"""
        return self.inputs.config

    def validate(self) -> None:
        """@brief 恢复拒绝世界身份、人物身份或输入原文字节变化。"""
        valid = (self.state.persona_sha256 == self.inputs.persona_sha256
                 and self.state.config_sha256 == self.inputs.config_sha256
                 and self.state.run_id == self.store.manifest["run_id"]
                 and tuple(self.state.agents) == (self.persona.agent_id,))
        if not valid:
            logging.error("Runtime environment inputs or agent identity mismatch")
            raise IntegrityError("Runtime environment inputs or agent identity mismatch")
        require_environment_identity(self.store.manifest, self.inputs)
        from worldmock.simulation_engine.activities.lifecycle.activity_validation import validate_activity_targets

        validate_activity_targets(self.state, self.catalog, self.persona)
        from worldmock.simulation_engine.scheduling.callbacks.callbacks import validate_callback_states

        validate_callback_states(self.state, self.catalog)

    def commit_step(self, staged: WorldState, drafts: list[EventDraft]) -> list[Event]:
        """@brief 提交一步世界推进并换上新世界。

        存储先核对本上下文的当前世界与已提交世界逐字节相同（暂存时原地修改共享条目而漏进当前世界的改动在此拒绝），
        再与 drafts 同一事务写入复校过的 staged；成功后 staged 成为当前世界，失败时当前世界不变。
        @param staged 版本已加一的候选世界
        @param drafts 本步的因果事件
        @return 已提交事件
        @exception IntegrityError 当前世界与已提交世界不同、候选世界违反不变量，或版本、时钟、账本、事件核对失败
        """
        events = self.store.commit(self.state.model_dump(mode="json"), validated_payload(staged), drafts)
        self.state = staged
        return events
