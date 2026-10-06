"""@brief 订单与给予的收货人：可读名称与收货地点（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「来源与去向」）。

收货人身份：同世界人物为 agent:<id>（付款人自己也是），场景联系人为联系人身份。收货地点不作参数：自己是当前地点
（途中为出发地，与远程下单的送达地同一口径），同世界人物是其所在地点（途中没有），联系人取
Contact.receive_location_id（为空时不能收实物）。同世界的其他人物不论在哪里都有私有绑定 person_bindings
（名称「人物：<名>」→ agent:<id>，不渲染进提示），去向与 relationship 记忆的对象按它识别；工具层仍只列同处的人物。
"""

from __future__ import annotations

from worldmock.life.admission import present_location
from worldmock.life.models import WorldState
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.social import Contact


def agent_identity(agent_id: int) -> str:
    """@brief 同世界人物的收货人身份 agent:<id>。"""
    return f"agent:{agent_id}"


def person_bindings(state: WorldState, agent_id: int) -> list[dict[str, str]]:
    """@brief 同世界全部其他人物的私有绑定（不论是否同处）：名称「人物：<名>」与工具层人物目标同一写法 → agent:<id>。"""
    return [{"name": f"人物：{other.name}", "person_id": agent_identity(other.agent_id)}
            for other in state.agents.values() if other.agent_id != agent_id]


def person_name(state: WorldState, scenario: RuntimeScenario, agent_id: int, identity: str) -> str:
    """@brief 人物身份的可读名称：同世界人物取姓名，场景联系人取本人通讯录中的称谓。"""
    if identity.startswith("agent:"):
        return state.agents[int(identity.split(":", 1)[1])].name
    return _contact(scenario, agent_id, identity).salutation


def recipient_location(state: WorldState, scenario: RuntimeScenario, agent_id: int, recipient_id: str) -> str | None:
    """@brief 收货人此刻的收货地点：自己为当前地点，同世界人物为其所在地点（途中为 None），联系人为其收货地点。

    :param agent_id: 付款或给予的人物（收货人是联系人时按其通讯录查找）。
    """
    if recipient_id == agent_identity(agent_id):
        return present_location(state, agent_id)
    if recipient_id.startswith("agent:"):
        return present_location(state, int(recipient_id.split(":", 1)[1]))
    return _contact(scenario, agent_id, recipient_id).receive_location_id


def _contact(scenario: RuntimeScenario, agent_id: int, contact_id: str) -> Contact:
    """@brief 人物通讯录中的一位联系人（身份来自本轮观察的私有绑定，必然存在）。"""
    config = next(item for item in scenario.agents if item.agent_id == agent_id)
    return next(item for item in config.contacts if item.contact_id == contact_id)
