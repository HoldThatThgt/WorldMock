"""@brief 世界状态写入唯一入口：落盘前复校五条域不变量（refute P0-2 修复）。

引擎侧 model_copy 深拷贝与逐属性赋值都不重跑 pydantic 校验，历史实现
16 处 store.commit 直写使非法世界状态可被接受并落盘，只有下次加载
（LifeEngine.open 的 WorldState.model_validate）才抛错——运行永久不可
恢复。本模块把全部写入收口到 commit_state/validated_payload：先按加载
口径复校（WorldState.validate_world 五条不变量：时间超界/负账户/负库存/
人物映射身份/负物品量），违规即 IntegrityError fail-closed，再持久化。
docs/CONTRACTS.md「数字足迹存储接口」判定校验责任在生活模型侧，存储只接收已验证状态。
"""

from __future__ import annotations

from worldmock.errors import IntegrityError
from worldmock.life.models import WorldState


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


def flush_presentation(state: WorldState, stamps: dict | None) -> WorldState:
    """@brief 把观察边界暂存的呈现留痕并入世界状态（提交路径上的唯一写状态处）。

    观察边界不允许同版本改状态（`store._validate_state` 只允许同版本追加事件），因此
    呈现留痕在观察时暂存为 `presentation_state` 的完整取值，随下一个状态提交一起落盘。
    这里只覆盖派生量字段，不改目录、不改对象状态、不产生下架事件。

    @param state 当前世界状态。
    @param stamps 暂存的 `presentation_state` 取值；None 或空字典表示没有留痕。
    @return 并入留痕后的世界状态（无留痕时即原状态）。
    """
    if not stamps:
        return state
    return state.model_copy(update={"presentation_state": stamps}, deep=True)
