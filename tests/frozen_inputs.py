"""@brief 测试会话复用已验证的人工输入；每个世界仍取得独立的可变场景与存储。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType

import pytest
import networkx as nx
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]


def _children(value):
    """@brief 枚举输入数据的成员，不遍历类、函数或解释器内部状态。"""
    if isinstance(value, nx.Graph):
        return [("graph", value.graph), ("nodes", value._node), ("adjacency", value._adj)]
    if isinstance(value, BaseModel):
        return [(name, getattr(value, name)) for name in type(value).model_fields]
    if is_dataclass(value):
        return [(field.name, getattr(value, field.name)) for field in fields(value)]
    if isinstance(value, Mapping):
        return list(value.items())
    if isinstance(value, (tuple, list, set, frozenset)):
        return list(enumerate(value))
    return []


def assert_deeply_immutable(value, seen=None):
    """@brief 共享分支的所有可达成员都必须不可变，禁止以顶层 frozen 声明代替验证。"""
    visited = set() if seen is None else seen
    if id(value) in visited:
        return
    visited.add(id(value))
    if isinstance(value, (dict, list, set)):
        raise AssertionError(f"Shared frozen input contains a mutable {type(value).__name__}")
    if isinstance(value, BaseModel) and not value.model_config.get("frozen"):
        raise AssertionError("Shared frozen input contains a mutable model")
    if is_dataclass(value) and not value.__dataclass_params__.frozen:
        raise AssertionError("Shared frozen input contains a mutable dataclass")
    if isinstance(value, nx.Graph) and not nx.is_frozen(value):
        raise AssertionError("Shared frozen input contains a mutable graph")
    supported = (type(None), str, int, float, bool, bytes, Path, date, datetime, Decimal, Enum,
                 tuple, frozenset, MappingProxyType, BaseModel, nx.Graph)
    if not isinstance(value, supported) and not is_dataclass(value):
        raise AssertionError(f"Shared input has no immutability contract: {type(value).__name__}")
    for _, child in _children(value):
        assert_deeply_immutable(child, visited)


def _canonical(value):
    """@brief 把场景数据写成可重复摘要的结构，保留全部成员与容器内容。"""
    children = _children(value)
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    if isinstance(value, Mapping):
        members = [(_canonical(key), _canonical(child)) for key, child in children]
        return {"type": kind, "members": sorted(members, key=repr)}
    if isinstance(value, (set, frozenset)):
        return {"type": kind, "members": sorted((_canonical(child) for _, child in children), key=repr)}
    if isinstance(value, (list, tuple)):
        return {"type": kind, "members": [_canonical(child) for _, child in children]}
    if isinstance(value, (BaseModel, nx.Graph)) or is_dataclass(value):
        return {"type": kind, "members": {str(key): _canonical(child) for key, child in children}}
    if isinstance(value, (Path, date, datetime, Decimal, Enum)):
        return {"type": kind, "value": str(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    raise TypeError(f"Unsupported cached input type: {type(value).__name__}")


def object_digest(value):
    """@brief 对缓存对象全部数据成员求摘要，检查会话间串扰。"""
    encoded = json.dumps(_canonical(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def tree_digest(root):
    """@brief 摘要覆盖目录的完整文件集合、相对路径和真实字节；拒绝符号链接。"""
    digest = hashlib.sha256()
    if Path(root).is_symlink() or not Path(root).is_dir():
        raise AssertionError("Cached input root is missing or is a symbolic link")
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise AssertionError("Cached input cannot contain symbolic links")
        if path.is_file():
            relative = path.relative_to(root).as_posix().encode("utf-8")
            content = path.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.hexdigest()


def input_digest(root):
    """测试缓存摘要包含完整环境的真实文件集合及原文字节。"""
    return tree_digest(root)


def fixture_metadata(config):
    """明确离线服务返回的权威测试容量，不访问真实模型服务。"""
    from worldmock.configuration.inputs.models import ModelMetadata

    return ModelMetadata(requested_model=config.requested_model, metadata_url=config.metadata_url,
                         context_window=524288, max_output_tokens=32768)


class FrozenInputs:
    """测试会话共享严格不可变完整输入，世界与存储仍分别新建。"""

    def __init__(self):
        self.records = {}

    def record(self, root):
        from worldmock.configuration.inputs.environment import load_environment

        key = (Path(root).absolute(), input_digest(root))
        if key not in self.records:
            inputs = load_environment(root, metadata_loader=fixture_metadata)
            assert_deeply_immutable(inputs)
            self.records[key] = {"inputs": inputs, "object_sha256": object_digest(inputs)}
        return self.records[key]

    @contextmanager
    def use(self, root):
        yield self.record(root)["inputs"]

    def verify(self):
        for (root, digest), value in self.records.items():
            assert input_digest(root) == digest, "Cached source input changed during the test session"
            assert object_digest(value["inputs"]) == value["object_sha256"], "Cached object changed"
