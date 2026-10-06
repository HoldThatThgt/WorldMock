"""@brief 生活运行的唯一状态、冻结输入与日志上下文。"""

from __future__ import annotations
from worldmock.run_setup import validate_manifest_stage_identity

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from worldmock.catalog.affordances import AffordanceView
from worldmock.errors import IntegrityError
from worldmock.footprints.store import FootprintStore
from worldmock.life.content import load_lifestyle_items
from worldmock.life.models import WorldState
from worldmock.life.run_identity import require_environment_identity
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.opslog import OpsLogger
from dataclasses import dataclass, field


if TYPE_CHECKING:
    from worldmock.life.activity_titles import TitleIndex
    from worldmock.life.rules import LifeRuleEngine


#: 观察合同条目的五键闭集；宿主与环境校验同一形状。
AFFORDANCE_CONTRACT_KEYS = frozenset({
    "action", "target_discovery", "target_field", "feedback_eligible", "game_boundary"})


def affordance_contract(view: AffordanceView) -> dict[str, dict]:
    """@brief 从可供性派生视图构造观察合同冻结块（docs/design/02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎「可供性与活动声明」）。

    合同随场景组装后不变，键集见 AFFORDANCE_CONTRACT_KEYS；游戏继续交互走 game_boundary。
    """
    feedback = view.feedback_kinds
    game = view.game_boundary_kinds
    contract: dict[str, dict] = {}
    for name, spec in view.kinds.items():
        contract[name] = {
            "action": spec.action,
            "target_discovery": spec.target_discovery,
            "target_field": (
                [spec.target_field, spec.target_id_key] if spec.target_discovery == "catalog_field" else None
            ),
            "feedback_eligible": name in feedback,
            "game_boundary": name in game,
        }
    return contract


@dataclass
class EngineContext:
    """@brief 所有职责对象共用的唯一世界状态与冻结输入。"""

    #: 唯一冻结场景语义，场景由其派生。
    semantics: ScenarioSemantics
    #: 真实场景文件内容身份。
    scenario_sha256: str
    #: 本运行实际读取的语料根。
    corpus_root: Path
    #: 唯一持久提交和恢复存储。
    store: FootprintStore
    #: 当前已提交世界状态，不保存第二份权威。
    state: WorldState = field(init=False)
    #: 冻结目录与内容驱动的生活规则。
    rules: LifeRuleEngine = field(init=False)
    #: 已加载的生活活动内容。
    lifestyle_items: tuple = field(init=False)
    #: 活动与目标名称的纯投影索引。
    titles: TitleIndex = field(init=False)
    #: 观察所需的冻结可供性合同。
    affordance_contract: dict = field(init=False)
    #: 本运行操作日志。
    ops: OpsLogger = field(init=False)

    def __post_init__(self) -> None:
        """@brief 按原生产顺序读取内容、恢复状态并核验身份。"""
        from worldmock.life.activity_titles import title_index
        from worldmock.life.rules import LifeRuleEngine

        self.corpus_root = Path(self.corpus_root).resolve()
        self.rules = LifeRuleEngine(self.scenario, self.corpus_root, self.semantics.catalog.graph)
        self.affordance_contract = affordance_contract(self.semantics.affordances)
        self.lifestyle_items = load_lifestyle_items(self.corpus_root, self.scenario.body.activity_met)
        names = {item.activity_id: item.name for item in self.lifestyle_items}
        self.titles = title_index(self.scenario, names, self.rules.content_titles)
        self.state = WorldState.model_validate(self.store.state)
        self.ops = OpsLogger(self.store.run_dir, self.scenario.timezone)
        self.validate()

    @property
    def scenario(self) -> RuntimeScenario:
        """@brief 场景只由当前冻结语义派生，不建立第二份权威。"""
        return self.semantics.scenario

    def validate(self) -> None:
        """@brief 在新建和恢复时核对场景、内容、决策阶段、路线、游戏和状态身份。"""
        if self.state.scenario_sha256 != self.scenario_sha256:
            logging.error("Runtime scenario identity mismatch")
            raise IntegrityError("Runtime scenario identity mismatch")
        if self.state.content_manifest_sha256 != self.scenario.corpus_manifest_sha256:
            logging.error("Runtime corpus identity mismatch")
            raise IntegrityError("Runtime corpus identity mismatch")
        if self.state.run_id != self.store.manifest["run_id"]:
            logging.error("Runtime run identity mismatch")
            raise IntegrityError("Runtime run identity mismatch")
        validate_manifest_stage_identity(self.store.manifest)
        require_environment_identity(self.store.manifest, self.semantics)
        from worldmock.life.activity_validation import validate_activity_targets

        validate_activity_targets(self.state, self.rules, self.scenario)
        self._validate_inventory_foods()

    def _validate_inventory_foods(self) -> None:
        """@brief 恢复的持有食品必须存在于当前冻结环境。"""
        known = set(self.semantics.catalog.foods)
        held = {food_id for agent in self.state.agents.values()
                for inventory in agent.inventory.values() for food_id in inventory}
        if not held <= known:
            logging.error("Runtime inventory refers to an unregistered food")
            raise IntegrityError("Runtime inventory refers to an unregistered food")
