"""@brief 多数字人环境、唯一世界状态和足迹提交的共享上下文。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.configuration.inputs.models import PersonaConfig
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import Event, EventDraft
from worldmock.recording_engine.storage.opslog import OpsLogger
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.activities.lifecycle.activity_validation import validate_activity_targets
from worldmock.simulation_engine.body.body_config import BodyConfig, assemble_bodies
from worldmock.simulation_engine.scheduling.callbacks.app_push import validate_app_pushes
from worldmock.simulation_engine.scheduling.callbacks.callbacks import validate_callback_states
from worldmock.simulation_engine.world.run_identity import require_environment_identity
from worldmock.state.world.world import WorldState, validated_payload

_LOGGER = logging.getLogger(__name__)


@dataclass
class EngineContext:
    """@brief 所有运行职责读取同一输入并提交到同一世界，不保留场景容器。"""

    #: 纯加载器已经验证的完整环境。
    inputs: EnvironmentInputs
    #: 唯一持久化存储。
    store: FootprintStore
    #: 当前已提交状态；只由 commit_step 在一步世界推进成功后替换。
    state: WorldState = field(init=False)
    #: 按人物编号索引的固定身体规则视图。
    bodies: dict[int, BodyConfig] = field(init=False)
    #: 运行日志。
    ops: OpsLogger = field(init=False)
    #: 同次提交的回调原因，提交后清空，不成为第二份世界状态。
    callback_causes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """@brief 状态恢复先核对完整输入身份，再创建运行日志。"""
        self.state = WorldState.model_validate(self.store.state)
        self.bodies = assemble_bodies(self.inputs)
        self.validate()
        self.ops = OpsLogger(self.store.run_dir, self.inputs.config.timezone)

    @property
    def catalog(self):
        """@brief 直接读取冻结目录，避免复制另一份登记状态。"""
        return self.inputs.catalog

    @property
    def persona_by_id(self) -> Mapping[int, PersonaConfig]:
        """@brief 按编号索引的全部人物声明直接来自完整环境。"""
        return self.inputs.persona_by_id

    @property
    def config(self):
        """@brief 唯一运行配置直接来自完整环境。"""
        return self.inputs.config

    def validate(self) -> None:
        """@brief 恢复拒绝世界身份、人物集合或输入原文字节变化。"""
        valid = (dict(self.state.persona_sha256s) == dict(self.inputs.persona_sha256s)
                 and self.state.config_sha256 == self.inputs.config_sha256
                 and self.state.run_id == self.store.manifest["run_id"]
                 and self.state.timezone == self.config.timezone
                 and tuple(sorted(self.state.agents)) == tuple(sorted(self.inputs.persona_by_id)))
        if not valid:
            _LOGGER.error("Runtime environment inputs or agent identity mismatch")
            raise IntegrityError("Runtime environment inputs or agent identity mismatch")
        require_environment_identity(self.store.manifest, self.inputs)
        validate_activity_targets(self.state, self.catalog, self.inputs.persona_by_id)
        validate_callback_states(self.state, self.catalog)
        validate_app_pushes(self.state, self.catalog)

    def commit_step(self, staged: WorldState, drafts: list[EventDraft]) -> list[Event]:
        """@brief 提交一步世界推进并换上新世界。

        存储先核对本上下文的当前世界与已提交世界逐字节相同，再与 drafts 同一事务写入复校过的 staged；
        成功后 staged 成为当前世界，失败时当前世界不变。
        """
        events = self.store.commit(self.state.model_dump(mode="json"), validated_payload(staged), drafts)
        self.state = staged
        return events
