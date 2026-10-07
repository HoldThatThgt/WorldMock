"""@brief 生活运行的唯一状态、冻结输入与日志上下文。"""

from __future__ import annotations
from worldmock.run_setup import validate_manifest_stage_identity

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from worldmock.catalog.affordances import AffordanceView
from worldmock.errors import IntegrityError
from worldmock.footprints.store import FootprintStore
from worldmock.life.app_push import validate_app_pushes
from worldmock.life.models import WorldState
from worldmock.life.run_identity import require_environment_identity
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.opslog import OpsLogger
from dataclasses import dataclass, field


if TYPE_CHECKING:
    from worldmock.life.activity_titles import TitleIndex
    from worldmock.life.rules import LifeRuleEngine


#: 观察合同条目的四键闭集；宿主与环境校验同一形状。
AFFORDANCE_CONTRACT_KEYS = frozenset({
    "action", "target_discovery", "target_field", "feedback_eligible"})


def affordance_contract(view: AffordanceView) -> dict[str, dict]:
    """@brief 从可供性派生视图构造观察合同冻结块（docs/design/02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎「可供性与活动声明」）。

    合同随场景组装后不变，键集见 AFFORDANCE_CONTRACT_KEYS。
    """
    feedback = view.feedback_kinds
    contract: dict[str, dict] = {}
    for name, spec in view.kinds.items():
        contract[name] = {
            "action": spec.action,
            "target_discovery": spec.target_discovery,
            "target_field": (
                [spec.target_field, spec.target_id_key] if spec.target_discovery == "catalog_field" else None
            ),
            "feedback_eligible": name in feedback,
        }
    return contract


@dataclass
class EngineContext:
    """@brief 所有职责对象共用的唯一世界状态与冻结输入。"""

    #: 唯一冻结场景语义，场景由其派生。
    semantics: ScenarioSemantics
    #: 真实场景文件内容身份。
    scenario_sha256: str
    #: 唯一持久提交和恢复存储。
    store: FootprintStore
    #: 当前已提交世界状态，不保存第二份权威。
    state: WorldState = field(init=False)
    #: 冻结目录与固定规则。
    rules: LifeRuleEngine = field(init=False)
    #: 活动与目标名称的纯投影索引。
    titles: TitleIndex = field(init=False)
    #: 观察所需的冻结可供性合同。
    affordance_contract: dict = field(init=False)
    #: 本运行操作日志。
    ops: OpsLogger = field(init=False)

    def __post_init__(self) -> None:
        """@brief 读取冻结目录、恢复状态并核验身份。"""
        from worldmock.life.activity_titles import title_index
        from worldmock.life.rules import LifeRuleEngine

        self.state = WorldState.model_validate(self.store.state)
        self.rules = LifeRuleEngine(self.scenario, self.semantics.catalog)
        self.affordance_contract = affordance_contract(self.semantics.affordances)
        self.titles = title_index(self.scenario, {}, self.semantics.catalog)
        self.ops = OpsLogger(self.store.run_dir, self.scenario.timezone)
        self.validate()

    @property
    def scenario(self) -> RuntimeScenario:
        """@brief 场景只由当前冻结语义派生，不建立第二份权威。"""
        return self.semantics.scenario

    def validate(self) -> None:
        """@brief 在新建和恢复时核对场景、内容、决策阶段、路线、内容与状态身份。"""
        if self.state.scenario_sha256 != self.scenario_sha256:
            logging.error("Runtime scenario identity mismatch")
            raise IntegrityError("Runtime scenario identity mismatch")
        if self.state.run_id != self.store.manifest["run_id"]:
            logging.error("Runtime run identity mismatch")
            raise IntegrityError("Runtime run identity mismatch")
        validate_manifest_stage_identity(self.store.manifest)
        require_environment_identity(self.store.manifest, self.semantics)
        from worldmock.life.activity_validation import validate_activity_targets

        validate_activity_targets(self.state, self.rules, self.scenario)
        validate_app_pushes(self.state, self.semantics.catalog)
