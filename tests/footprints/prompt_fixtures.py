"""@brief 导出认证测试使用的已记录六层行动请求。"""

from __future__ import annotations

import json

LAYER_NAMES = ('工具层', '人设层', '记忆层', '待办层', '环境层', '进度层')


def six_layers(progress_events=()):
    """六层对象：各层只带层名，进度层带给定的记录（用于区分不同决策的层 JSON）。"""
    layers = [{'layer': name} for name in LAYER_NAMES]
    layers[-1]['events'] = list(progress_events)
    return layers


def layered_user(layers=None):
    """由层数组形成 user 消息：与宿主相同的紧凑 JSON。"""
    value = six_layers() if layers is None else layers
    return {'role': 'user', 'content': json.dumps(value, ensure_ascii=False, separators=(',', ':'))}
