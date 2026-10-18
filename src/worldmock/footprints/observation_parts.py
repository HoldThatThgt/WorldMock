"""@brief 观察分件：观察里按内容单独存放一次的大表与按开始日期分段的活动绑定，观察正文只带它们的 sha256。

存储形式与规则见 docs/design/05-recording-engine.md「事件与来源」；parts 表的建表语句在 store.SCHEMA，
本模块是拆分、登记、核对与换回观察分件的唯一实现；分支在 FootprintStore.copy_event_prefix 里整表复制 parts。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import operator
import sqlite3
from typing import Any, NoReturn

from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json, is_sha256

#: 整表各存为一个观察分件的观察键。
WHOLE_PART_KEYS = ("catalog_bindings", "errand_item_bindings", "known_activity_actions", "locations",
                   "memory_layer", "persona_views")
#: 按所指活动开始时刻的本地日期分段、每段存为一个观察分件的观察键。
DAY_PART_KEY = "activity_bindings"


def split_observation(data: dict[str, Any], start_dates: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    """@brief 观察的存储形式与它的观察分件；不修改 data 及其中任何容器。

    activity_bindings 按行序把开始日期相同的相邻行归为一段，每段一个分件，存为分件 sha256 的列表；
    WHOLE_PART_KEYS 各整表一个分件，存为一个 sha256；其余键原样保留（同一对象）。每次都对全部分件重新编码求摘要。

    :param data: 要写入的观察（已去掉只给宿主的键）。
    :param start_dates: 与 data["activity_bindings"] 逐行对应的所指活动开始本地日期。
    :returns: (存储形式的观察, 观察分件 sha256 → 规范 JSON 正文)。
    :raises IntegrityError: 缺少要存为分件的表，或开始日期与绑定行数不同。
    """
    missing = [key for key in (DAY_PART_KEY, *WHOLE_PART_KEYS) if key not in data]
    if missing:
        logging.error("Observation lacks the tables stored as parts: %s", missing)
        raise IntegrityError("Observation lacks a table stored as a part")
    rows = data[DAY_PART_KEY]
    if len(start_dates) != len(rows):
        logging.error("Observation start dates (%d) do not match its activity bindings (%d)",
                      len(start_dates), len(rows))
        raise IntegrityError("Observation start dates do not match its activity bindings")
    parts: dict[str, str] = {}
    groups = itertools.groupby(zip(start_dates, rows), key=operator.itemgetter(0))
    day_refs = [_put([row for _, row in group], parts) for _, group in groups]
    whole = {key: _put(data[key], parts) for key in WHOLE_PART_KEYS}
    return {**data, DAY_PART_KEY: day_refs, **whole}, parts


def rehydrate_observation(data: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    """@brief 把存储形式观察的分件引用换回原表，返回键序不变的新字典；data 不变。不缓存，每次从 parts 表读并核对摘要。

    :raises IntegrityError: 引用不是 sha256、分件缺失或正文的 sha256 与引用不符。
    """
    result = dict(data)
    if DAY_PART_KEY in data:
        result[DAY_PART_KEY] = [row for ref in _day_refs(data) for row in _part_value(connection, ref)]
    for key in WHOLE_PART_KEYS:
        if key in data:
            result[key] = _part_value(connection, _whole_ref(data, key))
    return result


def insert_parts(connection: sqlite3.Connection, parts: dict[str, str]) -> None:
    """@brief 在调用方事务内登记观察分件：先核对每个正文的 sha256，已存在的同一分件不重写。

    :raises IntegrityError: 某个正文的 sha256 与它的键不符。
    """
    for digest, body in parts.items():
        if hashlib.sha256(body.encode()).hexdigest() != digest:
            logging.error("Observation part %s does not match its sha256", digest)
            raise IntegrityError("Observation part content does not match its sha256")
    connection.executemany("INSERT OR IGNORE INTO parts VALUES(?,?)", parts.items())


def require_parts(connection: sqlite3.Connection, data: dict[str, Any]) -> None:
    """@brief 写观察之前核对它引用的每个观察分件都已在 parts 表。

    :raises IntegrityError: 引用形状不对，或分件不在表中。
    """
    for ref in _part_refs(data):
        if connection.execute("SELECT 1 FROM parts WHERE sha256=?", (ref,)).fetchone() is None:
            logging.error("Observation part %s is missing", ref)
            raise IntegrityError("Observation part is missing")


def _part_refs(data: dict[str, Any]) -> list[str]:
    """@brief 存储形式观察带的全部分件引用：先 activity_bindings 的各段，再按 WHOLE_PART_KEYS；不带这些键的观察为空。"""
    refs = list(_day_refs(data)) if DAY_PART_KEY in data else []
    return refs + [_whole_ref(data, key) for key in WHOLE_PART_KEYS if key in data]


def _put(value: Any, parts: dict[str, str]) -> str:
    """@brief 把一张表或一段行登记为观察分件，返回其 sha256。"""
    body = canonical_json(value)
    digest = hashlib.sha256(body).hexdigest()
    parts[digest] = body.decode()
    return digest


def _day_refs(data: dict[str, Any]) -> list[str]:
    """@brief activity_bindings 的分段引用列表。"""
    value = data[DAY_PART_KEY]
    if not isinstance(value, list) or not all(is_sha256(item) for item in value):
        _invalid_reference(DAY_PART_KEY)
    return value


def _whole_ref(data: dict[str, Any], key: str) -> str:
    """@brief 整表存为一个观察分件的引用。"""
    if not is_sha256(data[key]):
        _invalid_reference(key)
    return data[key]


def _invalid_reference(key: str) -> NoReturn:
    """@brief 存储形式观察的某个键不是分件引用：记英文错误并拒绝。"""
    logging.error("Stored observation key %s carries an invalid part reference", key)
    raise IntegrityError("Stored observation part reference is invalid")


def _part_value(connection: sqlite3.Connection, ref: str) -> Any:
    """@brief 读一个观察分件并核对摘要，返回解析出的表或那一段行。"""
    row = connection.execute("SELECT body FROM parts WHERE sha256=?", (ref,)).fetchone()
    if row is None:
        logging.error("Observation part %s is missing", ref)
        raise IntegrityError("Observation part is missing")
    if hashlib.sha256(row[0].encode()).hexdigest() != ref:
        logging.error("Observation part %s does not match its sha256", ref)
        raise IntegrityError("Observation part content does not match its sha256")
    return json.loads(row[0])
