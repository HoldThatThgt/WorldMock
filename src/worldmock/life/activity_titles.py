"""@brief 由真实应用和内容目录生成权威活动快照。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from worldmock.catalog.models import Catalog
from worldmock.life.models import ActivityState


@dataclass(frozen=True, slots=True)
class TitleIndex:
    """@brief 直接冻结实际内容显示名。"""

    #: 内容身份对应标题。
    contents: Mapping[str, str]


def title_index(catalog: Catalog) -> TitleIndex:
    """@brief 从唯一登记目录建立名称索引，不保留动作目录。"""
    return TitleIndex(contents={key: content.title for key, content in catalog.contents.items()})


def activity_snapshot(activity: ActivityState, index: TitleIndex) -> dict:
    """@brief 权威快照只附实际已选择内容的标题。"""
    payload = activity.model_dump(mode="json")
    content_id = activity.data.get("content_id")
    if content_id is not None:
        payload["content_title"] = index.contents[content_id]
    return payload
