"""@brief 已登记内容标题与到达即能阅读的邮件。"""

from __future__ import annotations

from worldmock.catalog.models import Content
from worldmock.life.models import AgentState
from worldmock.life.scenario import MessageConfig


def load_content_titles(items: tuple[Content, ...]) -> dict[str, str]:
    """@brief 直接投影登记内容的可读标题。"""
    return {item.content_id: item.title for item in items}


def visible_mail(agent: AgentState, messages: dict[str, MessageConfig]) -> list[dict]:
    """@brief 已到达邮件立即给出正文，不以阅读活动或已读状态阻挡。"""
    return [{"message_id": identity, "thread_id": messages[identity].thread_id,
             "subject": messages[identity].subject, "body": messages[identity].body}
            for identity in sorted(agent.visible_message_ids)]
