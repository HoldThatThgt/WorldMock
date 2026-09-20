"""@brief 双端祖先链上的稀疏有向配对偏序解析。"""

from __future__ import annotations

from worldmock.categories.common import Action, CategoryError, PairingState
from worldmock.categories.models import CategoryContract, PairingDeclaration, PairingResolution


def resolve_pairing(
    contract: CategoryContract,
    actor_category_id: str,
    target_category_id: str,
    action: Action | str,
) -> PairingResolution:
    """@brief 返回唯一最具体配对；缺项返回未定义，歧义明确失败。

    :param contract: 冻结品类契约。
    :param actor_category_id: 动作参与者品类。
    :param target_category_id: 动作目标品类。
    :param action: 封闭动作枚举或其字符串值。
    :returns: 三态配对解析结果。
    :raises CategoryError: 品类未知、动作未知或存在不可比较命中。
    """
    actor = contract.require_category(actor_category_id)
    target = contract.require_category(target_category_id)
    try:
        parsed_action = action if isinstance(action, Action) else Action(action)
    except ValueError as error:
        raise CategoryError("PAIRING_ACTION_UNKNOWN", f"unknown pairing action {action}") from error
    actor_depth = _ancestor_depths(actor.lineage)
    target_depth = _ancestor_depths(target.lineage)
    hits = _matching_declarations(contract, actor_depth, target_depth, parsed_action)
    if not hits:
        return PairingResolution(state=PairingState.UNDEFINED, declaration=None)
    maxima = _maximal_hits(hits, actor_depth, target_depth)
    if len(maxima) != 1:
        keys = sorted(_declaration_key(item) for item in maxima)
        raise CategoryError("PAIRING_AMBIGUOUS", f"pairing has incomparable declarations: {keys}")
    return PairingResolution(state=maxima[0].state, declaration=maxima[0])


def _ancestor_depths(lineage: tuple[str, ...]) -> dict[str, int]:
    """@brief 把根到叶谱系映射为深度，越大越具体。"""
    return {category_id: depth for depth, category_id in enumerate(lineage)}


def _matching_declarations(
    contract: CategoryContract,
    actor_depth: dict[str, int],
    target_depth: dict[str, int],
    action: Action,
) -> list[PairingDeclaration]:
    """@brief 选出双端都位于查询祖先链且动作相同的声明。"""
    return [
        declaration
        for declaration in contract.pairings
        if declaration.action == action
        and declaration.actor_category_id in actor_depth
        and declaration.target_category_id in target_depth
    ]


def _maximal_hits(
    hits: list[PairingDeclaration],
    actor_depth: dict[str, int],
    target_depth: dict[str, int],
) -> list[PairingDeclaration]:
    """@brief 保留偏序中未被双端更具体声明覆盖的命中。"""
    return [
        candidate
        for candidate in hits
        if not any(
            other is not candidate and _dominates(other, candidate, actor_depth, target_depth)
            for other in hits
        )
    ]


def _dominates(
    left: PairingDeclaration,
    right: PairingDeclaration,
    actor_depth: dict[str, int],
    target_depth: dict[str, int],
) -> bool:
    """@brief 判断左声明在两端均不浅且至少一端更深。"""
    left_depth = (actor_depth[left.actor_category_id], target_depth[left.target_category_id])
    right_depth = (actor_depth[right.actor_category_id], target_depth[right.target_category_id])
    return all(left_value >= right_value for left_value, right_value in zip(left_depth, right_depth)) \
        and left_depth != right_depth


def _declaration_key(declaration: PairingDeclaration) -> tuple[str, str, str]:
    """@brief 返回用于稳定错误输出的配对键。"""
    return (
        declaration.actor_category_id,
        declaration.target_category_id,
        declaration.action.value,
    )
