"""@brief 用注册环境、人物实例与系统固定规则组装唯一运行场景。"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from worldmock.catalog import Catalog
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.admission import validate_scenario_against_catalog
from worldmock.catalog.affordances import AffordanceView, engine_affordances
from worldmock.exchange.body import BODY_CONSTANTS
from worldmock.exchange.constants import ENTERTAINMENT_RELIEF_PER_HOUR, RULES_VERSION
from worldmock.life.scenario import BodyConfig, LifeScenario, RuntimeScenario


@dataclass(frozen=True)
class CandidateConstants:
    """@brief 人物场景声明的候选呈现节律，不引入环境规则。"""

    #: 一次呈现的对象数上限。
    presentation_limit: int
    #: 候选池大小上限。
    pool_limit: int
    #: 为长期未呈现对象保留的名额比例。
    rotation_quota_ratio: float
    #: 判定长期未呈现的秒数。
    rotation_stale_seconds: int


@dataclass(frozen=True)
class ScenarioSemantics:
    """@brief 注册目录、固定身体规则和场景实例组成的不可变语义。"""

    #: 原始场景文档。
    document: LifeScenario
    #: 已校验且冻结的环境登记。
    catalog: Catalog
    #: 后续运行消费者读取的唯一场景。
    scenario: RuntimeScenario
    #: 实例初值和系统身体规则。
    body: BodyConfig
    #: 场景账户和人物账户的初始金额。
    accounts: Mapping[str, int]
    #: 包资源的可供性派生视图。
    affordances: AffordanceView = field(default_factory=engine_affordances)

    @property
    def candidate_constants(self) -> CandidateConstants:
        """@brief 原样透出场景节律，不在消费者复制数值。"""
        scenario = self.scenario
        return CandidateConstants(scenario.candidate_presentation_limit, scenario.candidate_pool_limit,
                                  scenario.rotation_quota_ratio, scenario.rotation_stale_seconds)


def resolve_catalog_dir(scenario_path: Path) -> Path:
    """@brief 场景目录旁的 catalog 是环境登记根。"""
    return Path(scenario_path).resolve().parent.parent / "catalog"


def assemble_semantics(document: LifeScenario, catalog: Catalog) -> ScenarioSemantics:
    """@brief 在唯一登记准入通过后组装固定身体和注册对象，不创建引擎。"""
    validate_scenario_against_catalog(document, catalog)
    body = _assemble_body(document)
    employer, transport = _service_accounts(document)
    payload = document.model_dump(mode="python", exclude={"body", "locations", "agents"})
    payload.update({
        "body": body,
        "agents": document.agents,
        "locations": tuple(catalog.locations[row.location_id] for row in document.locations),
        "apps": tuple(catalog.apps.values()),
        "goods": tuple(catalog.goods.values()),
        "contents": tuple(catalog.contents.values()),
        "actions": tuple(catalog.actions.values()),
        "employer_account_id": employer,
        "transport_account_id": transport,
    })
    scenario = RuntimeScenario.model_validate(payload)
    accounts = {row.account_id: row.money_fen for row in document.accounts if row.role != "external:scenario"}
    accounts.update({f"agent:{row.agent_id}": row.initial_balance_fen for row in document.agents})
    return ScenarioSemantics(document, catalog, scenario, body, MappingProxyType(accounts))


def _assemble_body(document: LifeScenario) -> BodyConfig:
    """@brief 身体规则只读系统常数，不并入环境提供的效果或代谢当量。"""
    constants = BODY_CONSTANTS
    payload = document.body.model_dump(mode="python")
    payload.update({
        "maximum_energy_kcal": constants.maximum_energy_kcal,
        "basal_kcal_per_hour": constants.basal_kcal_per_hour,
        "hunger_threshold_kcal": constants.hunger_threshold_kcal,
        "emergency_threshold_kcal": constants.emergency_threshold_kcal,
        "entertainment_need_per_awake_hour": constants.entertainment_need_per_awake_hour,
        "activity_met": dict(constants.activity_met),
        "entertainment_relief_per_hour": ENTERTAINMENT_RELIEF_PER_HOUR,
        "source_kind": f"worldmock:{RULES_VERSION}",
    })
    return BodyConfig.model_validate(payload)


def _service_accounts(document: LifeScenario) -> tuple[str | None, str]:
    """@brief 雇主和交通账户只取场景显式角色，不生成隐式服务账户。"""
    employers = [row.account_id for row in document.accounts if row.role.startswith("employer:")]
    transport = [row.account_id for row in document.accounts if row.role == "transport:service"]
    if len(transport) != 1 or len(employers) > 1 or not employers and any(
        agent.work_content_ids for agent in document.agents
    ):
        logging.error("Scenario service account roles are missing or ambiguous")
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", "A binding refers to an object that is not registered.")
    return employers[0] if employers else None, transport[0]
