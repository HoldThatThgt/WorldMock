"""@brief 包内可供性注册表的严格解析与不可变活动视图。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import as_file, files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from worldmock.errors import IntegrityError
from worldmock.exchange.types import ActionCategory, AppUseManner, START_ACTIVITY_ACTIONS


AFFORDANCES_FILE = "affordances.toml"
RELIEF_CHANNELS = frozenset({"none", "content_rate"})
SLEEP_CHANNELS = frozenset({"recovery", "awake"})
TARGET_DISCOVERY_SOURCES = frozenset({"none", "browse_result", "catalog_field"})
#: 四种活动共同使用的固定占用名额。
RESOURCE_CLAIM_SLOTS = frozenset({
    "wait", "travel", "talk", "use_app", "entertainment", "work_study", "exercise", "daily_care",
})
TRAVEL_MODES = frozenset({"driving", "walking"})
_ACTIVITY_KINDS = frozenset({"wait", "sleep", "travel", "action"})
_CLAIM_CONDITIONS = MappingProxyType({
    "mode": TRAVEL_MODES, "category": frozenset(ActionCategory), "manner": frozenset(AppUseManner),
})
_KIND_KEYS = frozenset({
    "met_key", "met_key_modes", "energy_floor_exempt", "sleep_channel", "relief",
    "resource_claims", "feedback_boundary", "action", "target_discovery",
})


@dataclass(frozen=True, slots=True)
class KindSpec:
    """@brief 一个活动种类的固定可供性声明。"""

    #: 活动种类名。
    name: str
    #: 身体常数中的代谢查表键；action 由类别表结算，此字段为空。
    met_key: str | None
    #: 出行模板允许展开的方式。
    met_key_modes: tuple[str, ...] = ()
    #: 能量恢复种类标记。
    energy_floor_exempt: bool = False
    #: 睡眠结算通道。
    sleep_channel: str = "awake"
    #: 娱乐缓解结算通道。
    relief_channel: str = "none"
    #: 始终占用的名额。
    resource_claims: tuple[str, ...] = ()
    #: 按出行方式、类别与使用方式追加的占用。
    conditional_claims: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: 是否参与到期进度反馈。
    feedback_boundary: bool = False
    #: 启动活动的内部标准动作。
    action: str = "none"
    #: 目标发现来源。
    target_discovery: str = "none"
    #: 目标目录字段。
    target_field: str | None = None
    #: 目标目录条目的身份字段。
    target_id_key: str | None = None


@dataclass(frozen=True)
class AffordanceView:
    """@brief 校验后的固定活动声明；运行期查询不读取环境文件。"""

    #: 活动种类名对应的不可变声明。
    kinds: Mapping[str, KindSpec]

    @property
    def met_closed_keys(self) -> frozenset[str]:
        """@brief 身体常数的封闭查表键；action 使用独立的固定类别表。"""
        keys: set[str] = set()
        for spec in self.kinds.values():
            if spec.met_key is None:
                continue
            if "{mode}" in spec.met_key:
                keys.update(spec.met_key.replace("{mode}", mode) for mode in spec.met_key_modes)
            else:
                keys.add(spec.met_key)
        return frozenset(keys)

    def met_lookup_key(self, kind_value: str, travel_mode: Any = None) -> str:
        """@brief 返回等待、睡眠或具体出行方式的身体查表键。"""
        spec = self.kinds[kind_value]
        if spec.met_key is None:
            raise IntegrityError("Action MET must be read from the fixed category table")
        if "{mode}" not in spec.met_key:
            return spec.met_key
        mode = getattr(travel_mode, "value", travel_mode)
        if mode not in spec.met_key_modes:
            raise IntegrityError("Travel MET requires a registered travel mode")
        return spec.met_key.replace("{mode}", mode)

    @property
    def relief_keys(self) -> frozenset[str]:
        """@brief 有娱乐缓解声明的活动种类。"""
        return frozenset(name for name, spec in self.kinds.items() if spec.relief_channel != "none")

    def relief_channel(self, kind_value: str) -> str:
        """@brief 读取一个活动种类的娱乐缓解通道。"""
        return self.kinds[kind_value].relief_channel

    @property
    def energy_recovery_kinds(self) -> frozenset[str]:
        """@brief 读取包内声明的能量恢复活动种类。"""
        return frozenset(name for name, spec in self.kinds.items() if spec.energy_floor_exempt)

    def resource_claims(self, kind_value: str, travel_mode: Any = None,
                        category: Any = None, manner: Any = None) -> frozenset[str]:
        """@brief 按 mode、category 与 manner 声明求活动的全部占用。"""
        spec = self.kinds[kind_value]
        values = {"mode": travel_mode, "category": category, "manner": manner}
        if kind_value == "action" and category is None:
            raise IntegrityError("Action resource claims require a fixed category")
        if kind_value == "travel" and travel_mode is None:
            raise IntegrityError("Travel resource claims require a fixed travel mode")
        for dimension, value in values.items():
            if value is not None and (not isinstance(value, str) or value not in _CLAIM_CONDITIONS[dimension]):
                raise IntegrityError(f"Activity claim {dimension} is outside the fixed set")
        claims = set(spec.resource_claims)
        for condition, extra in spec.conditional_claims:
            dimension, _, expected = condition.partition(":")
            value = getattr(values[dimension], "value", values[dimension])
            if value == expected:
                claims.update(extra)
        return frozenset(claims)

    @property
    def feedback_kinds(self) -> frozenset[str]:
        """@brief 参与到期进度反馈的活动种类。"""
        return frozenset(name for name, spec in self.kinds.items() if spec.feedback_boundary)

    @property
    def target_catalog_fields(self) -> dict[str, tuple[str, str | None]]:
        """@brief 以目录字段发现目标的活动种类。"""
        return {name: (spec.target_field, spec.target_id_key)
                for name, spec in self.kinds.items() if spec.target_discovery == "catalog_field"}


def load_affordances(path: Path) -> AffordanceView:
    """@brief 严格加载版本 2 的固定活动声明，拒绝旧号、家族和未知字段。"""
    path = Path(path)
    if not path.is_file():
        raise IntegrityError(f"Affordance registry {path} does not exist")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise IntegrityError(f"Affordance registry TOML is malformed: {error}") from error
    except (OSError, UnicodeError) as error:
        raise IntegrityError(f"Affordance registry cannot be read: {error}") from error
    if type(payload.get("version")) is not int or payload["version"] != 2:
        raise IntegrityError("Affordance registry version must be 2")
    _reject_unknown("registry", payload, frozenset({"version", "kinds"}))
    kinds_raw = payload.get("kinds")
    if not isinstance(kinds_raw, dict) or set(kinds_raw) != _ACTIVITY_KINDS:
        raise IntegrityError("Affordance kinds must be exactly wait, sleep, travel, and action")
    kinds = MappingProxyType({name: _parse_kind(name, body) for name, body in kinds_raw.items()})
    return AffordanceView(kinds=kinds)


@lru_cache(maxsize=1)
def engine_affordances() -> AffordanceView:
    """@brief 只从引擎包资源读取并缓存固定可供性。"""
    resource = files("worldmock.data").joinpath(AFFORDANCES_FILE)
    with as_file(resource) as path:
        return load_affordances(path)


def _reject_unknown(entry_id: str, body: Mapping, allowed: frozenset[str]) -> None:
    """@brief 拒绝声明中未知的字段或表。"""
    unknown = set(body) - allowed
    if unknown:
        raise IntegrityError(f"Affordance {entry_id!r} declares unknown keys: {sorted(unknown)}")


def _require_bool(entry_id: str, body: Mapping, key: str) -> bool:
    """@brief 读取布尔声明并拒绝其他类型。"""
    value = body.get(key, False)
    if not isinstance(value, bool):
        raise IntegrityError(f"Affordance kind {entry_id!r} field {key} must be a boolean")
    return value


def _parse_kind(name: str, body: Mapping) -> KindSpec:
    """@brief 校验活动声明的查表键、通道、占用与目标发现。"""
    if not isinstance(body, dict):
        raise IntegrityError(f"Affordance kind {name!r} must be a table")
    _reject_unknown(name, body, _KIND_KEYS)
    met_key, modes = _parse_met_key(name, body)
    sleep_channel = body.get("sleep_channel", "awake")
    if not isinstance(sleep_channel, str) or sleep_channel not in SLEEP_CHANNELS:
        raise IntegrityError(f"Affordance kind {name!r} sleep_channel is invalid")
    relief = body.get("relief", {"channel": "none"})
    if (not isinstance(relief, dict) or set(relief) != {"channel"} or not isinstance(relief["channel"], str)
            or relief["channel"] not in RELIEF_CHANNELS):
        raise IntegrityError(f"Affordance kind {name!r} relief channel is invalid")
    claims, conditional = _parse_resource_claims(name, body.get("resource_claims", []))
    action = body.get("action", "none")
    if not isinstance(action, str) or action != "none" and action not in START_ACTIVITY_ACTIONS:
        raise IntegrityError(f"Affordance kind {name!r} action is invalid")
    source, target_field, target_id_key = _parse_target_discovery(name, body)
    if relief["channel"] == "content_rate" and source not in ("browse_result", "catalog_field"):
        raise IntegrityError(f"Affordance kind {name!r} content_rate requires target discovery")
    return KindSpec(
        name=name, met_key=met_key, met_key_modes=modes,
        energy_floor_exempt=_require_bool(name, body, "energy_floor_exempt"),
        sleep_channel=sleep_channel, relief_channel=relief["channel"],
        resource_claims=claims, conditional_claims=conditional,
        feedback_boundary=_require_bool(name, body, "feedback_boundary"), action=action,
        target_discovery=source, target_field=target_field, target_id_key=target_id_key,
    )


def _parse_met_key(name: str, body: Mapping) -> tuple[str | None, tuple[str, ...]]:
    """@brief action 没有身体查表键，出行模板只展开固定的两种方式。"""
    if name == "action":
        if "met_key" in body or "met_key_modes" in body:
            raise IntegrityError("Affordance action MET belongs to the fixed category table")
        return None, ()
    expected = "travel:{mode}" if name == "travel" else name
    if body.get("met_key") != expected:
        raise IntegrityError(f"Affordance kind {name!r} met_key must be {expected!r}")
    modes = body.get("met_key_modes", [])
    if not isinstance(modes, list) or any(not isinstance(mode, str) for mode in modes):
        raise IntegrityError(f"Affordance kind {name!r} met_key_modes must be a list of strings")
    if name == "travel":
        if len(modes) != len(TRAVEL_MODES) or set(modes) != TRAVEL_MODES:
            raise IntegrityError("Affordance travel met_key_modes must declare walking and driving")
    elif modes:
        raise IntegrityError(f"Affordance kind {name!r} declares met_key_modes without a mode template")
    return expected, tuple(modes)


def _parse_resource_claims(
        name: str, raw: Any) -> tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    """@brief 解析直接占用列表或包含条件占用的表。"""
    if isinstance(raw, list):
        return _parse_slots(name, raw), ()
    if not isinstance(raw, dict):
        raise IntegrityError(f"Affordance kind {name!r} resource_claims is malformed")
    _reject_unknown(name, raw, frozenset({"base", "conditional"}))
    base = _parse_slots(name, raw.get("base", []))
    conditional = raw.get("conditional", [])
    if not isinstance(conditional, list):
        raise IntegrityError(f"Affordance kind {name!r} conditional claims must be a list")
    clauses = tuple(_parse_claim_condition(name, clause) for clause in conditional)
    if len({condition for condition, _ in clauses}) != len(clauses):
        raise IntegrityError(f"Affordance kind {name!r} conditional claims contain duplicate conditions")
    return base, clauses


def _parse_slots(name: str, raw: Any) -> tuple[str, ...]:
    """@brief 拒绝占用列表中的未知名额与重复声明。"""
    if (not isinstance(raw, list) or any(not isinstance(slot, str) or slot not in RESOURCE_CLAIM_SLOTS
                                         for slot in raw)):
        raise IntegrityError(f"Affordance kind {name!r} resource_claims must be within {sorted(RESOURCE_CLAIM_SLOTS)}")
    if len(set(raw)) != len(raw):
        raise IntegrityError(f"Affordance kind {name!r} resource_claims contain duplicate slots")
    return tuple(raw)


def _parse_claim_condition(name: str, clause: Any) -> tuple[str, tuple[str, ...]]:
    """@brief 校验 mode、category 与 manner 三种条件的闭集取值。"""
    if not isinstance(clause, dict) or set(clause) != {"when", "add"}:
        raise IntegrityError(f"Affordance kind {name!r} conditional clause needs exactly when/add")
    when = clause["when"]
    dimension, _, value = when.partition(":") if isinstance(when, str) else ("", "", "")
    if dimension not in _CLAIM_CONDITIONS or value not in _CLAIM_CONDITIONS[dimension]:
        raise IntegrityError(f"Affordance kind {name!r} conditional when {when!r} is invalid")
    return when, _parse_slots(name, clause["add"])


def _parse_target_discovery(name: str, body: Mapping) -> tuple[str, str | None, str | None]:
    """@brief 解析无目标、候选结果或目录字段的目标发现声明。"""
    raw = body.get("target_discovery", "none")
    if isinstance(raw, str):
        if raw not in {"none", "browse_result"}:
            raise IntegrityError(f"Affordance kind {name!r} target_discovery {raw!r} is invalid")
        return raw, None, None
    if not isinstance(raw, dict) or set(raw) != {"source", "field", "id_key"}:
        raise IntegrityError(f"Affordance kind {name!r} target_discovery is malformed")
    if raw["source"] != "catalog_field" or not isinstance(raw["field"], str) or not raw["field"]:
        raise IntegrityError(f"Affordance kind {name!r} catalog_field discovery requires a field")
    if not isinstance(raw["id_key"], str) or not raw["id_key"]:
        raise IntegrityError(f"Affordance kind {name!r} target_discovery requires an id_key")
    return "catalog_field", raw["field"], raw["id_key"]
