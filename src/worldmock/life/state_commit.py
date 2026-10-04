"""@brief 世界状态写入唯一入口：落盘前复校五条域不变量（refute P0-2 修复）。

引擎侧 model_copy 深拷贝与逐属性赋值都不重跑 pydantic 校验，历史实现
16 处 store.commit 直写使非法世界状态可被接受并落盘，只有下次加载
（LifeEngine.open 的 WorldState.model_validate）才抛错——运行永久不可
恢复。本模块把全部写入收口到 commit_state/validated_payload：先按加载
口径复校（WorldState.validate_world 五条不变量：时间超界/负账户/负库存/
人物映射身份/负物品量），违规即 IntegrityError fail-closed，再持久化。
docs/CONTRACTS.md#contracts-worldmock-接口合同「数字足迹存储接口」判定校验责任在生活模型侧，存储只接收已验证状态。
"""

from __future__ import annotations

from worldmock.errors import IntegrityError
from worldmock.life.models import SameMomentPresentation, WorldState


def validated_payload(state: WorldState) -> dict:
    """@brief 序列化并按加载口径复校世界状态，域不变量违规即拒绝。

    @param state 待写入的世界状态实例
    @return 已通过 WorldState.model_validate 复校的 JSON 载荷
    @exception IntegrityError 五条域不变量任一不成立
    """
    payload = state.model_dump(mode="json")
    try:
        WorldState.model_validate(payload)
    except ValueError as error:
        raise IntegrityError(
            f"World state invariant violated before persisting: {error}") from error
    return payload


def commit_state(store, state: WorldState, drafts: list) -> list:
    """@brief 引擎全部状态提交的唯一 choke-point：先复校再写足迹库。

    @param store 足迹存储（FootprintStore）
    @param state 本次提交的世界状态（复校不通过则不写入任何事件）
    @param drafts 与状态同事务提交的事件草稿
    @return store.commit 返回的已持久化事件列表
    @exception IntegrityError 域不变量复校失败（写入前拦截）
    """
    return store.commit(validated_payload(state), drafts)


def stage_presentation(state: WorldState, pending: dict[int, SameMomentPresentation]) -> WorldState:
    """@brief 把本轮各人物观察的呈现留痕写入世界状态中的本秒暂存（决策轮提交路径）。

    观察边界不允许同版本改状态（`store._validate_state` 只允许同版本追加事件），因此观察先把留痕
    记在引擎内存，随本轮提交按人物写入 `same_moment_presentation`；同一秒已有暂存的人物合并对象时刻，
    保留该秒首轮置顶的浏览结果（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「同一秒内的呈现与「刚才」」）。

    @param state 本轮提交的世界状态。
    @param pending 本轮观察产生的人物→呈现暂存；空字典表示没有留痕。
    @return 写入暂存后的世界状态（没有留痕时即原状态）。
    """
    if not pending:
        return state
    staging = dict(state.same_moment_presentation)
    for agent_id, entry in pending.items():
        earlier = staging.get(agent_id)
        staging[agent_id] = entry if earlier is None else SameMomentPresentation(
            stamps={**earlier.stamps, **entry.stamps}, pinned_supply_ids=earlier.pinned_supply_ids)
    return state.model_copy(update={"same_moment_presentation": staging}, deep=True)


def flush_presentation(state: WorldState) -> WorldState:
    """@brief 时钟推进时把本秒暂存的呈现留痕逐人并入 `presentation_state` 并清空暂存。

    这里只覆盖派生量字段，不改目录、不改对象状态、不产生下架事件；多人同轮观察各自保留留痕，
    后观察者不覆盖前者。

    @param state 推进前的世界状态。
    @return 并入留痕后的世界状态（没有暂存时即原状态）。
    """
    if not state.same_moment_presentation:
        return state
    presented = dict(state.presentation_state)
    for agent_id, entry in state.same_moment_presentation.items():
        presented[agent_id] = {**presented.get(agent_id, {}), **entry.stamps}
    return state.model_copy(update={"presentation_state": presented, "same_moment_presentation": {}}, deep=True)


def flush_supply_visibility(state: WorldState, presented: dict[int, set[str]]) -> WorldState:
    """@brief 只把实际观察呈现的门店供给凭据随下一次世界提交落盘。"""
    agents = dict(state.agents)
    for agent_id, identifiers in presented.items():
        agent = agents[agent_id]
        moments = {**agent.supply_browsed_at, **dict.fromkeys(identifiers, state.current_time)}
        locations = {**agent.supply_browsed_location, **dict.fromkeys(identifiers, agent.location_id)}
        agents[agent_id] = agent.model_copy(update={
            "visible_supply_ids": agent.visible_supply_ids | set(identifiers), "supply_browsed_at": moments,
            "supply_browsed_location": locations}, deep=True)
    return state.model_copy(update={"agents": agents}, deep=True)
