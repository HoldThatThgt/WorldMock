"""@brief 由唯一动作与内容目录生成活动名称和权威事件快照。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from worldmock.catalog.models import Catalog
from worldmock.life.models import ActivityKind, ActivityState
from worldmock.life.scenario import RuntimeScenario


@dataclass(frozen=True, slots=True)
class TitleIndex:
    """@brief 冻结的动作、应用与内容名称索引。"""

    #: 动作身份对应环境登记名称。
    actions: Mapping[str, str]
    #: 应用身份对应真实应用名称。
    apps: Mapping[str, str]
    #: 内容身份对应标题。
    contents: Mapping[str, str]


def title_index(scenario: RuntimeScenario, catalog: Catalog) -> TitleIndex:
    """@brief 从唯一登记目录建立名称索引。"""
    return TitleIndex(actions={key: action.name for key, action in catalog.actions.items()},
                      apps={key: app.name for key, app in catalog.apps.items()},
                      contents={key: content.title for key, content in catalog.contents.items()})


def activity_title(activity: ActivityState, index: TitleIndex) -> str | None:
    """@brief 环境动作显示动作名，绑定应用时附应用名。"""
    if activity.kind != ActivityKind.ACTION:
        return None
    name = index.actions[activity.data["action_id"]]
    app_id = activity.data["app_id"]
    return f"{name}（{index.apps[app_id]}）" if app_id is not None else name


def activity_snapshot(activity: ActivityState, index: TitleIndex) -> dict:
    """@brief 权威活动快照只附与真实内容及应用身份一致的显示名称。"""
    payload = activity.model_dump(mode="json")
    title = activity_title(activity, index)
    if title is not None:
        payload["title"] = title
        payload["content_title"] = index.contents[activity.data["content_id"]]
    return payload
