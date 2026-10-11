"""@brief 商品曝光的稳定分组与同秒呈现留痕。"""


from __future__ import annotations


import hashlib


from worldmock.life.models import SameMomentPresentation, WorldState


def tiebreak_digest(environment_id: str, agent_id: int, local_date: str, object_id: str) -> str:
    """@brief 使用环境、人物、本地日期与对象生成稳定决胜键。"""
    text = f"{environment_id}|{agent_id}|{local_date}|{object_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def subtype_of(kind: str, category: str = "", object_id: str = "") -> str:
    """@brief 反塌缩去重的子类标签：候选类别 + 品类 + 对象标识的稳定前缀。

    子类粒度必须与题材粒度相称：只用类别会让「同一子类不超过三条」退化成整类上限，
    只用对象标识又会让每条自成一类、上限永不生效。因此取三段稳定标签——候选类别、
    该条目所属品类、以及在首个数字串之前的前缀（`movie-0001` 给 `movie`）。

    @param kind 候选类别。
    @param category 该条目所属的品类或内容品类（可省略）。
    @param object_id 对象标识；前缀由它在首个数字串之前的部分给出。
    @return 供 `rotate_candidates` 去重使用的子类标签。
    """
    label = f"{kind}:{category}" if category else kind
    return f"{label}:{_object_prefix(object_id)}" if object_id else label


def _object_prefix(object_id: str) -> str:
    """@brief 对象标识在首个数字串之前的前缀；无前缀时取整个标识。"""
    head = object_id.rstrip("0123456789").rstrip("-_")
    return head or object_id


def stage_presentation(state: WorldState, pending: dict[int, SameMomentPresentation]) -> WorldState:
    """@brief 把本轮各人物观察的呈现留痕写入世界状态中的本秒暂存（决策轮提交路径）。

    观察边界不允许同版本改状态（`store._validate_state` 只允许同版本追加事件），因此观察先把留痕
    记在引擎内存，随本轮提交按人物写入 `same_moment_presentation`；同一秒已有暂存的人物合并对象时刻，
    保留该秒首轮置顶的浏览结果（docs/design/04-simulation-engine.md「原子性与恢复」）。

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
            stamps={**earlier.stamps, **entry.stamps})
    return state.model_copy(update={"same_moment_presentation": staging}, deep=True)


def flush_presentation(state: WorldState) -> WorldState:
    """@brief 时钟推进时把本秒暂存的呈现留痕逐人并入 `presentation_state` 并清空暂存。

    这里只覆盖人物的派生曝光留痕，不改目录或对象状态，不产生下架事件。

    @param state 推进前的世界状态。
    @return 并入留痕后的世界状态（没有暂存时即原状态）。
    """
    if not state.same_moment_presentation:
        return state
    presented = dict(state.presentation_state)
    for agent_id, entry in state.same_moment_presentation.items():
        presented[agent_id] = {**presented.get(agent_id, {}), **entry.stamps}
    return state.model_copy(update={"presentation_state": presented, "same_moment_presentation": {}}, deep=True)
