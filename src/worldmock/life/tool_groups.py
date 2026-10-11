"""@brief 工具层的无损分组渲染，完整冻结观察始终保留原 targets。"""

from __future__ import annotations

import logging
from copy import deepcopy

from worldmock.errors import IntegrityError

#: 动作的字段闭集，用于把分组行还原为完整公开动作。
_ACTION_FIELDS = ("detail", "available", "reason", "price", "duration", "quantity", "note")


def compact(value: object) -> object:
    """@brief 只略去 null 和空集合，false 与零值保持原类型原值；返回新的字典与列表（叶值深拷贝），调用者改动结果不影响
    输入。工具层分组与世界查询的返回项（docs/design/04-simulation-engine.md「出行、内容与目录浏览」）共用这一份紧凑规则。"""
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items() if item is not None and item != [] and item != {}}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return deepcopy(value)


def compact_tool_layer(layer: dict) -> dict:
    """@brief 相邻同类别同动作方式分组，shared 仅承载逐值相同的字段。"""
    groups = []
    for target in layer["targets"]:
        metadata = {key: value for key, value in target.items() if key not in {"kind", "actions"}}
        actions = target["actions"] or [None]
        for action in actions:
            key = (target["kind"], None if action is None else action["action"])
            fields = {} if action is None else {name: action[name] for name in _ACTION_FIELDS if name in action}
            row = compact({**metadata, **fields})
            if not groups or tuple(groups[-1][name] for name in ("kind", "action")) != key:
                groups.append({"kind": key[0], "action": key[1], "shared": {}, "targets": []})
            groups[-1]["targets"].append(row)
    for group in groups:
        targets = group["targets"]
        shared = {key: value for key, value in targets[0].items() if key != "name"
                  and all(key in row and row[key] == value for row in targets[1:])}
        group["shared"] = shared
        group["targets"] = [{key: value for key, value in row.items() if key not in shared} for row in targets]
    return {"layer": layer["layer"], "groups": groups, "callable_index": deepcopy(layer["callable_index"])}


def expand_tool_layer(layer: dict) -> dict:
    """@brief 按名称合并一致元信息与动作，冲突直接报渲染错误。"""
    targets = {}
    for group in layer["groups"]:
        for entry in group["targets"]:
            row = {**deepcopy(group["shared"]), **deepcopy(entry)}
            metadata = {key: value for key, value in row.items() if key not in _ACTION_FIELDS}
            metadata.update(kind=group["kind"])
            metadata.setdefault("phase", None)
            metadata.setdefault("state", None)
            metadata.setdefault("tools", [])
            for tool in metadata["tools"]:
                tool.setdefault("reason", None)
            name = metadata["name"]
            if name in targets and {key: value for key, value in targets[name].items() if key != "actions"} != metadata:
                logging.error("Compact tool groups contain conflicting target metadata")
                raise IntegrityError("Compact tool groups contain conflicting target metadata")
            target = targets.setdefault(name, {**metadata, "actions": []})
            if group["action"] is not None:
                action = {"action": group["action"], **{key: row.get(key) for key in _ACTION_FIELDS}}
                if action not in target["actions"]:
                    target["actions"].append(action)
    return {"layer": layer["layer"], "targets": list(targets.values()),
            "callable_index": deepcopy(layer["callable_index"])}


def compact_render(value: object) -> object:
    """@brief 仅在提示和公开 changes 渲染时递归替换工具层，不改动输入对象。"""
    if isinstance(value, dict):
        if value.get("layer") == "工具层" and "targets" in value:
            return compact_tool_layer(value)
        return {key: compact_render(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [compact_render(item) for item in value]
    return deepcopy(value)
