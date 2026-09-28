"""@brief session 的事件序号区间（docs/dev/SPEC-persona-prompt.md 第三章「session 与记忆条目」、第一章「进度层」session 下界）。

一个 session 的记录区间是（前一边界提交的末事件序号, 本边界提交的末事件序号]，同一提交按 state_version_after 聚合：
出发请求所在的提交整体归旧 session，到达那一秒其后提交的收尾调用归新 session，路程中收到的消息、送达与发出的消息归路程
session。边界提交由参照点识别：运行开始的 session 下界为 0；到达（发生在时钟推进中，含到达被拒）取该 session 开始时刻的
首个事件所在的提交；由决策引起的切换（出发、途中结束出行）取引起切换的那条结果所在的提交。进度层的下界与记忆层的区间都取
这里，同一事件只在两层之一出现。
"""

from __future__ import annotations

import logging

from worldmock.errors import IntegrityError
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import SessionState, WorldState


def session_lower(store: FootprintStore, state: WorldState, session: SessionState) -> int:
    """@brief session 的事件序号下界（不含）：开启它的边界提交的末事件序号；人物的首个 session（运行开始）为 0。"""
    if session.opened_by_action_id is not None:
        reference = _opening_result_seq(store, session)
    elif _first_session(state, session):
        return 0
    else:
        reference = store.last_event_seq_before(session.started_at) + 1
    return store.commit_last_seq(reference)


def _opening_result_seq(store: FootprintStore, session: SessionState) -> int:
    """@brief 引起切换的那条结果（该动作的首个 action_result）的事件序号。"""
    seqs = [event.event_seq for event in store.read_agent_events("action_result", session.agent_id)
            if event.action_id == session.opened_by_action_id]
    if not seqs:
        logging.error("Session opening action has no committed result")
        raise IntegrityError("Session opening action has no committed result")
    return seqs[0]


def _first_session(state: WorldState, session: SessionState) -> bool:
    """@brief 该 session 是人物按开始顺序的第一个（运行开始时在初始地点开始的停留）。"""
    key = (session.started_at, session.session_id)
    return not any(item.agent_id == session.agent_id and (item.started_at, item.session_id) < key
                   for item in state.sessions.values())
