"""@brief 测试会话复用已验证的人工输入；每个世界仍取得独立的可变场景与存储。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]


def relative_corpus(corpus, scenario_directory):
    """@brief 场景保存相对语料引用；Windows 不同盘符时先复制到同一隔离目录。"""
    source, target = Path(corpus).resolve(), Path(scenario_directory).resolve()
    if source.drive.casefold() != target.drive.casefold():
        copied = target.parent / "corpora"
        shutil.copytree(source, copied)
        source = copied
    return Path(os.path.relpath(source, target)).as_posix()


def _children(value):
    """@brief 枚举输入数据的成员，不遍历类、函数或解释器内部状态。"""
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
    supported = (type(None), str, int, float, bool, bytes, Path, date, datetime, Decimal, Enum,
                 tuple, frozenset, MappingProxyType, BaseModel)
    if not isinstance(value, supported) and not is_dataclass(value):
        raise AssertionError(f"Shared input has no immutability contract: {type(value).__name__}")
    for _, child in _children(value):
        assert_deeply_immutable(child, visited)


def clone_semantics(semantics):
    """@brief 复制场景、原始声明、身体参数与账户；共享其余经过深度不可变检查的分支。"""
    copied = {"document", "scenario", "body", "accounts"}
    for field in fields(semantics):
        if field.name not in copied:
            assert_deeply_immutable(getattr(semantics, field.name))
    document, scenario, body, accounts = deepcopy(
        (semantics.document, semantics.scenario, semantics.body, dict(semantics.accounts)))
    return replace(semantics, document=document, scenario=scenario, body=body, accounts=accounts)


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
    if isinstance(value, BaseModel) or is_dataclass(value):
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


def input_digest(path):
    """@brief 缓存身份覆盖场景、目录、语料清单及真实语料，不信任声明摘要或文件时间。"""
    if Path(path).is_symlink():
        raise AssertionError("Cached scenario cannot be a symbolic link")
    content = Path(path).read_bytes()
    document = json.loads(content)
    catalog = Path(path).resolve().parent.parent / "catalog"
    corpus = (Path(path).resolve().parent / document["corpus_root"]).resolve()
    digest = hashlib.sha256(content)
    digest.update(tree_digest(catalog).encode("ascii"))
    digest.update(tree_digest(corpus).encode("ascii"))
    return digest.hexdigest()


class FrozenInputs:
    """@brief 每个 pytest 进程的会话缓存，仅供人工场景的 engine_factory 使用。"""

    def __init__(self):
        self.records = {}

    def record(self, path):
        """@brief 相同路径与完整输入字节复用组装结果；发生输入漂移时重新验证。"""
        key = (Path(path).resolve(), input_digest(path))
        if key not in self.records:
            value = self._load(path)
            assert input_digest(path) == key[1], "Source input changed while filling the test cache"
            self.records[key] = value
        return self.records[key]

    def _load(self, path):
        """@brief 真实目录与生活活动装配只执行一次，并保存完整缓存对象摘要。"""
        from worldmock.life import engine_context, world_setup

        semantics, digest = world_setup.load_runtime_scenario(path)
        clone_semantics(semantics)
        root = (Path(path).resolve().parent / semantics.scenario.corpus_root).resolve()
        manifest = world_setup.load_manifest(root, semantics.scenario.corpus_manifest_sha256)
        assert_deeply_immutable(manifest)
        value = {"semantics": semantics, "digest": digest, "manifest": manifest,
                 "lifestyle": engine_context.load_lifestyle_items(root, semantics.scenario.body.activity_met)}
        assert_deeply_immutable(value["lifestyle"])
        value["object_sha256"] = object_digest(value)
        return value

    @contextmanager
    def use(self, path):
        """@brief 只在工厂创建期间接入缓存，直接生产新建、打开与恢复仍执行真实加载器。"""
        from worldmock.life import engine_context, world_setup

        value = self.record(path)
        semantics = clone_semantics(value["semantics"])
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(world_setup, "load_runtime_scenario", lambda candidate: (semantics, value["digest"]))
            patch.setattr(world_setup, "load_manifest", lambda root, expected: value["manifest"])
            patch.setattr(engine_context, "load_lifestyle_items", lambda root, met: value["lifestyle"])
            yield

    def verify(self):
        """@brief 会话结束时同时核对输入文件与缓存对象，任一漂移都使测试失败。"""
        for (path, digest), value in self.records.items():
            assert input_digest(path) == digest, "Cached source input changed during the test session"
            original = value["object_sha256"]
            payload = {key: item for key, item in value.items() if key != "object_sha256"}
            assert object_digest(payload) == original, "Cached object changed during the test session"
