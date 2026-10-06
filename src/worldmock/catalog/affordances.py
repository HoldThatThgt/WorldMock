"""@brief 可供性注册表：affordances.toml 的解析、fail-closed 校验与不可变派生视图。

注册表把散落五层的手写行为闭集（动机家族、习惯归并路由、MET 键、缓解通道、
反馈边界、资源锁、采样上限……）收编为同一份数据的声明式投影
（docs/design/02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎「可供性与活动声明」「交换规则的加载与准入」）。

本模块只做加载期解析与校验（docs/design/03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎「准入边界」的加载期五条：
完整性、引用完整性、原型登记、互斥不变量、习惯覆盖完备）和派生视图查询，运行期零 IO；
候选生成器原型保持 Python 函数，由 life/motivation.py 导入时登记进
PROTOTYPE_GENERATORS；候选生成职责见 docs/design/03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎「从目录到候选」。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import as_file, files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from worldmock.errors import IntegrityError

#: 注册表文件名（位于目录顶层，与 resources.jsonl/objects.jsonl 平级）。
AFFORDANCES_FILE = "affordances.toml"

#: 内部标准动作闭集（docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面「对象与动作选择」）：交互菜单、习惯归并、
#: 证据与本注册表的
#: kinds.*.action 共用这一份声明；它不是人物词表，人物所见的五个动词与方式经私有绑定映射回这里的内部名。
START_ACTIVITY_ACTIONS = frozenset({
    "travel_to", "work", "browse", "purchase", "consume", "watch", "play",
    "chat", "call", "read", "do", "wait", "sleep",
})
RELIEF_CHANNELS = frozenset({"none", "preference_rate", "content_rate"})
SLEEP_CHANNELS = frozenset({"recovery", "awake"})
TARGET_DISCOVERY_SOURCES = frozenset({"none", "browse_result", "catalog_field"})
ATTENTION_KINDS = frozenset({"none", "content_kind"})
#: 占用槽位闭集（docs/dev/SPEC-life-runtime.md#time-and-energy-五类生活动作与并行准入-并行准入 占用表，按
#: docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-占用声明 重列）：每种动作占自己的名字，
#: 各活动显式声明，引擎不暗中添加；同一人物进行中活动的占用交集非空即 RESOURCE_CONFLICT，所以只有同一种动作互斥。
#: screen 只用来让驾车拒绝播客以外的内容；旧的身体部位名（idle、eyes、ears、hands、voice）与更早退役的名字
#: 一样不在闭集内，加载期拒绝。
RESOURCE_CLAIM_SLOTS = frozenset({
    "wait", "work", "watch", "screen", "play_game", "read_mail", "browse", "consume", "lifestyle", "talk",
    "travel",
})
#: 条件占用的维度：mode:<出行方式> 与 content_kind:<内容品类>（后者须是某家族声明的内容品类）。
_CLAIM_CONDITION_DIMENSIONS = frozenset({"mode", "content_kind"})
#: 习惯归并路由认识的动作名闭集（classify_action_event 的取值域）：start_activity
#: 的动作全集，外加会话进展自述这一个独立工具名。
HABIT_ACTIONS = START_ACTIVITY_ACTIONS | {"report_status", "submit_work"}
#: 出行方式闭集（与 life.models.TravelMode 一致；"{mode}" 模板的合法展开域）。
TRAVEL_MODES = frozenset({"driving", "walking"})
#: 结算通道可引用的资源注册表 id（catalog/resources.jsonl 的四类资源）。
REFERENCEABLE_RESOURCES = frozenset({
    "money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need",
})
#: 候选生成器原型的合法参数键；引用未知原型名或未知参数键都拒绝。
GENERATOR_PARAM_KEYS: Mapping[str, frozenset[str]] = MappingProxyType({
    "clear_backlog": frozenset({"resource", "cap_seconds", "fallback_seconds",
                                "min_session_seconds"}),
    "cheapest_feasible_meal": frozenset(),
    "continue_current": frozenset(),
    "cheapest_route": frozenset({"destination"}),
    "catalog_visibility": frozenset({"probe_duration_seconds"}),
    "content_menu": frozenset({"min_session_seconds", "cap_seconds", "slot_policy"}),
    "task_slot": frozenset({"max_tasks"}),
    "none": frozenset(),
})
#: 原型名 → Python 实现的运行期登记表（life/motivation.py 导入时填充；
#: 签名统一 (context, profile, params) -> list[candidate]）。
PROTOTYPE_GENERATORS: dict[str, Callable[..., list]] = {}

#: kinds 层允许的字段键（未知键拒绝，防拼写静默失效）。
_KIND_KEYS = frozenset({
    "met_key", "met_key_modes", "energy_floor_exempt", "sleep_channel", "relief",
    "resource_claims", "feedback_boundary", "game_boundary",
    "unclassified", "action", "target_discovery",
})
#: families 层允许的字段键。
_FAMILY_KEYS = frozenset({
    "kinds", "attention", "habit_members", "candidate", "comfort",
    "enumeration_rank", "unclassified", "content_kinds",
})
_MET_PLACEHOLDERS = frozenset({"{mode}", "{item_id}"})


# ---------------------------------------------------------------------------
# 冻结规格模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class KindSpec:
    """@brief 一个活动类型的注册表声明（引擎侧投影的唯一来源）。"""

    name: str
    met_key: str
    met_key_modes: tuple[str, ...] = ()
    energy_floor_exempt: bool = False
    sleep_channel: str = "awake"
    relief_channel: str = "none"
    resource_claims: tuple[str, ...] = ()
    conditional_claims: tuple[tuple[str, tuple[str, ...]], ...] = ()
    feedback_boundary: bool = False
    game_boundary: bool = False
    unclassified: bool = False
    action: str = "none"
    target_discovery: str = "none"
    target_field: str | None = None
    target_id_key: str | None = None


@dataclass(frozen=True, slots=True)
class ContentKindSpec:
    """@brief 一个内容品类的注意力声明；标题由场景条目提供。"""

    name: str
    attention_required: bool


@dataclass(frozen=True, slots=True)
class FamilySpec:
    """@brief 一个动机家族的注册表声明（动机侧投影的唯一来源）。"""

    name: str
    kinds: tuple[str, ...]
    attention_kind: str
    attention_params: Mapping[str, Any]
    habit_members: tuple[str, ...]
    generator: str
    generator_params: Mapping[str, Any]
    comfort: bool = False
    enumeration_rank: int | None = None
    unclassified: bool = False
    content_kinds: tuple[ContentKindSpec, ...] = ()


@dataclass(frozen=True)
class AffordanceView:
    """@brief
    校验后的不可变派生视图：各层闭集的唯一派生来源（docs/design/02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎「可供性与活动声明」）。"""

    kinds: Mapping[str, KindSpec]
    families: Mapping[str, FamilySpec]
    #: 习惯归并路由：动作名 -> 家族名（每个动作至多归一个家族）。
    _habit_routes: Mapping[str, str] = field(default_factory=dict)

    # ----- L3 动机层 -----

    @property
    def family_names(self) -> frozenset[str]:
        """@brief 家族表全键集（含 generator=none 的纯声明面家族，如 lifestyle）。"""
        return frozenset(self.families)

    @property
    def motivation_families(self) -> frozenset[str]:
        """@brief 动机家族闭集（习惯项归一键）：参与动机投影的家族 =
        generator ≠ none（lifestyle 家族是纯声明面，不生成候选、不收习惯
        份额——与现行 motivation.FAMILIES 的 7 家族一致）。"""
        return frozenset(name for name, family in self.families.items()
                         if family.generator != "none")

    def families_by_rank(self) -> tuple[FamilySpec, ...]:
        """@brief 参与候选枚举的家族（enumeration_rank 升序，generator=none 除外）。"""
        ranked = [family for family in self.families.values()
                  if family.enumeration_rank is not None and family.generator != "none"]
        return tuple(sorted(ranked, key=lambda item: (item.enumeration_rank, item.name)))

    def attention_params(self, family_name: str) -> Mapping[str, Any]:
        """@brief 取一个家族的注意力采样参数（attention.kind=content_kind 必有）。"""
        family = self.families.get(family_name)
        if family is None or family.attention_kind != "content_kind":
            raise IntegrityError(
                f"affordance family {family_name!r} declares no content attention")
        return family.attention_params

    def classify_habit_event(self, action: str) -> str | None:
        """@brief 把一次动作归入习惯家族；未声明的动作（含显式 unclassified）返回 None。"""
        return self._habit_routes.get(action)

    # ----- L5 结算层 -----

    @property
    def met_closed_keys(self) -> frozenset[str]:
        """@brief MET 表封闭基键集：无占位符键 + "{mode}" 模板展开域。"""
        keys: set[str] = set()
        for spec in self.kinds.values():
            if "{mode}" in spec.met_key:
                keys.update(spec.met_key.replace("{mode}", mode)
                            for mode in spec.met_key_modes)
            elif "{item_id}" not in spec.met_key:
                keys.add(spec.met_key)
        return frozenset(keys)

    @property
    def met_open_prefixes(self) -> tuple[str, ...]:
        """@brief MET 表开放命名空间前缀（"{item_id}" 模板的键前缀，如 "lifestyle:"）。"""
        return tuple(dict.fromkeys(
            spec.met_key.split("{")[0]
            for spec in self.kinds.values() if "{item_id}" in spec.met_key))

    def met_lookup_key(self, kind_value: str, travel_mode: Any = None,
                       target_id: str | None = None) -> str:
        """@brief 按 met_key 模板求运行期查表键（travel:{mode} / lifestyle:{item_id}）。

        travel 未携带方式时回退基键名（表内无此键，由调用方 KeyError——
        与原 _activity_met 的 fall-through 行为一致）；lifestyle 缺 target_id
        保持原 ValueError 文案。
        """
        spec = self.kinds[kind_value]
        if "{mode}" in spec.met_key:
            if travel_mode is None:
                return kind_value
            mode = getattr(travel_mode, "value", travel_mode)
            return spec.met_key.replace("{mode}", str(mode))
        if "{item_id}" in spec.met_key:
            if not target_id:
                raise ValueError("lifestyle activity requires its catalog item id")
            return spec.met_key.replace("{item_id}", target_id)
        return spec.met_key

    @property
    def relief_keys(self) -> frozenset[str]:
        """@brief 娱乐缓解表键集：relief.channel ≠ none 的活动类型。"""
        return frozenset(name for name, spec in self.kinds.items()
                         if spec.relief_channel != "none")

    def relief_channel(self, kind_value: str) -> str:
        """@brief 取一个活动类型的缓解通道（四值之一）。"""
        return self.kinds[kind_value].relief_channel

    @property
    def energy_recovery_kinds(self) -> frozenset[str]:
        """@brief 能量豁免的活动类型（energy_floor_exempt）：只供内部动机评分，不参与受理与结算。"""
        return frozenset(name for name, spec in self.kinds.items()
                         if spec.energy_floor_exempt)

    def resource_claims(self, kind_value: str, travel_mode: Any = None,
                        content_kind: str | None = None) -> frozenset[str]:
        """@brief 活动类型的显式占用集合，含按出行方式与内容品类追加的条件占用。

        :param travel_mode: 出行方式（mode:<方式> 条件的取值）；非出行活动为 None。
        :param content_kind: 观看内容的品类（content_kind:<品类> 条件的取值）；非观看活动为 None。
        """
        spec = self.kinds[kind_value]
        claims = set(spec.resource_claims)
        values = {"mode": getattr(travel_mode, "value", travel_mode), "content_kind": content_kind}
        for condition, extra in spec.conditional_claims:
            dimension, _, value = condition.partition(":")
            if values[dimension] == value:
                claims.update(extra)
        return frozenset(claims)

    # ----- L5.5 反馈层 / L4 工具面 -----

    @property
    def feedback_kinds(self) -> frozenset[str]:
        """@brief 到期反馈边界闭集（feedback_boundary = true）。"""
        return frozenset(name for name, spec in self.kinds.items()
                         if spec.feedback_boundary)

    @property
    def game_boundary_kinds(self) -> frozenset[str]:
        """@brief 游戏反馈边界闭集（game_boundary = true；与 feedback 互斥）。"""
        return frozenset(name for name, spec in self.kinds.items()
                         if spec.game_boundary)

    @property
    def target_catalog_fields(self) -> dict[str, tuple[str, str | None]]:
        """@brief 观察目录直读字段映射（target_discovery = catalog_field）。"""
        return {name: (spec.target_field, spec.target_id_key)
                for name, spec in self.kinds.items()
                if spec.target_discovery == "catalog_field"}

    # ----- L2 观察层 -----

    @property
    def attention_required_kinds(self) -> frozenset[str]:
        """@brief 画像必须声明的注意力键集（attention_required = true）。"""
        return frozenset(kind.name for family in self.families.values()
                         for kind in family.content_kinds if kind.attention_required)


# ---------------------------------------------------------------------------
# 解析与校验（docs/design/03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎「准入边界」的加载期五条，全部 IntegrityError
# fail-closed）
# ---------------------------------------------------------------------------

def load_affordances(path: Path) -> AffordanceView:
    """@brief 解析注册表并执行加载期校验（规则 1-5），返回不可变派生视图。

    :param path: affordances.toml 路径。
    :raises IntegrityError: 解析失败或任一规则被违反。
    """
    path = Path(path)
    if not path.is_file():
        raise IntegrityError(f"Affordance registry {path} does not exist")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise IntegrityError(f"Affordance registry TOML is malformed: {error}") from error
    except (OSError, UnicodeError) as error:
        raise IntegrityError(f"Affordance registry cannot be read: {error}") from error
    if payload.get("version") != 1:
        raise IntegrityError("Affordance registry version must be 1")
    unknown_top = set(payload) - {"version", "kinds", "families"}
    if unknown_top:
        raise IntegrityError(f"Affordance registry declares unknown tables: {sorted(unknown_top)}")
    kinds_raw = payload.get("kinds")
    families_raw = payload.get("families")
    if not isinstance(kinds_raw, dict) or not kinds_raw:
        raise IntegrityError("Affordance registry declares no kinds")
    if not isinstance(families_raw, dict) or not families_raw:
        raise IntegrityError("Affordance registry declares no families")
    kinds = MappingProxyType({name: _parse_kind(name, body)
                              for name, body in kinds_raw.items()})
    families = MappingProxyType({name: _parse_family(name, body, kinds)
                                 for name, body in families_raw.items()})
    view = _build_view(kinds, families)
    _check_habit_coverage(view)
    _check_claim_content_kinds(view)
    return view


@lru_cache(maxsize=1)
def engine_affordances() -> AffordanceView:
    """@brief 从引擎包资源读取通用闭集并缓存，不读取任何具体环境。"""
    resource = files("worldmock.data").joinpath(AFFORDANCES_FILE)
    with as_file(resource) as path:
        return load_affordances(path)


def _reject_unknown(entry_id: str, body: Mapping, allowed: frozenset[str],
                    section: str) -> None:
    """@brief 拒绝条目中的未知键（防拼写静默失效）。"""
    unknown = set(body) - allowed
    if unknown:
        raise IntegrityError(
            f"Affordance {section} {entry_id!r} declares unknown keys: {sorted(unknown)}")


def _require_bool(entry_id: str, body: Mapping, key: str, default: bool = False) -> bool:
    """@brief 读取布尔声明并拒绝其他类型。"""
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise IntegrityError(f"Affordance kind {entry_id!r} field {key} must be a boolean")
    return value


def _parse_kind(name: str, body: Mapping) -> KindSpec:
    """@brief 解析并校验单个活动类型条目（规则 1 + 规则 4 的 kind 侧）。"""
    if not isinstance(body, dict):
        raise IntegrityError(f"Affordance kind {name!r} must be a table")
    _reject_unknown(name, body, _KIND_KEYS, "kind")
    met_key, modes = _parse_met_key(name, body)
    sleep_channel = body.get("sleep_channel", "awake")
    if sleep_channel not in SLEEP_CHANNELS:
        raise IntegrityError(f"Affordance kind {name!r} sleep_channel is invalid")
    relief = body.get("relief", {})
    if not isinstance(relief, dict) or relief.get("channel", "none") not in RELIEF_CHANNELS:
        raise IntegrityError(f"Affordance kind {name!r} relief channel is invalid")
    claims, conditional = _parse_resource_claims(name, body.get("resource_claims", []))
    action = body.get("action", "none")
    if action != "none" and action not in START_ACTIVITY_ACTIONS:
        raise IntegrityError(f"Affordance kind {name!r} action is invalid")
    discovery_source, target_field, target_id_key = _parse_target_discovery(name, body)
    if relief.get("channel", "none") == "content_rate" and discovery_source not in (
            "browse_result", "catalog_field"):
        raise IntegrityError(
            f"Affordance kind {name!r} relief content_rate requires entry-level target discovery")
    feedback = _require_bool(name, body, "feedback_boundary")
    game = _require_bool(name, body, "game_boundary")
    if feedback and game:
        raise IntegrityError(
            f"Affordance kind {name!r} game_boundary and feedback_boundary are mutually exclusive")
    return KindSpec(
        name=name,
        met_key=met_key,
        met_key_modes=tuple(modes),
        energy_floor_exempt=_require_bool(name, body, "energy_floor_exempt"),
        sleep_channel=sleep_channel,
        relief_channel=relief.get("channel", "none"),
        resource_claims=claims,
        conditional_claims=conditional,
        feedback_boundary=feedback,
        game_boundary=game,
        unclassified=_require_bool(name, body, "unclassified"),
        action=action,
        target_discovery=discovery_source,
        target_field=target_field,
        target_id_key=target_id_key,
    )


def _parse_met_key(name: str, body: Mapping) -> tuple[str, tuple[str, ...]]:
    """@brief 校验活动代谢查表键的占位符与出行方式闭集。"""
    met_key = body.get("met_key", name)
    if not isinstance(met_key, str) or not met_key:
        raise IntegrityError(f"Affordance kind {name!r} met_key must be a non-empty string")
    # 占位符检查：模板只允许 {mode} / {item_id}，且至多一个、处于键尾。
    for placeholder in _MET_PLACEHOLDERS:
        if placeholder in met_key:
            if met_key.count(placeholder) != 1 or not met_key.endswith(placeholder):
                raise IntegrityError(
                    f"Affordance kind {name!r} met_key {met_key!r} misuses {placeholder}")
            if any(other in met_key for other in _MET_PLACEHOLDERS - {placeholder}):
                raise IntegrityError(
                    f"Affordance kind {name!r} met_key {met_key!r} mixes placeholders")
    modes = body.get("met_key_modes", [])
    if "{mode}" in met_key:
        if not isinstance(modes, list) or not modes:
            raise IntegrityError(
                f"Affordance kind {name!r} met_key {{mode}} requires met_key_modes")
        if any(mode not in TRAVEL_MODES for mode in modes):
            raise IntegrityError(
                f"Affordance kind {name!r} met_key_modes must be within {sorted(TRAVEL_MODES)}")
    elif modes:
        raise IntegrityError(
            f"Affordance kind {name!r} declares met_key_modes without a {{mode}} template")
    return met_key, tuple(modes)


def _parse_resource_claims(
        name: str, raw: Any) -> tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    """@brief 解析资源锁声明：槽位列表或 {base, conditional} 表。"""
    if isinstance(raw, list):
        if any(slot not in RESOURCE_CLAIM_SLOTS for slot in raw):
            raise IntegrityError(
                f"Affordance kind {name!r} resource_claims must be within "
                f"{sorted(RESOURCE_CLAIM_SLOTS)}")
        return tuple(raw), ()
    if not isinstance(raw, dict):
        raise IntegrityError(f"Affordance kind {name!r} resource_claims is malformed")
    unknown = set(raw) - {"base", "conditional"}
    if unknown:
        raise IntegrityError(
            f"Affordance kind {name!r} resource_claims declares unknown keys: {sorted(unknown)}")
    base = raw.get("base", [])
    if any(slot not in RESOURCE_CLAIM_SLOTS for slot in base):
        raise IntegrityError(
            f"Affordance kind {name!r} resource_claims base must be within "
            f"{sorted(RESOURCE_CLAIM_SLOTS)}")
    conditional = raw.get("conditional", [])
    parsed: list[tuple[str, tuple[str, ...]]] = []
    if not isinstance(conditional, list):
        raise IntegrityError(
            f"Affordance kind {name!r} resource_claims conditional must be a list")
    for clause in conditional:
        if not isinstance(clause, dict) or set(clause) != {"when", "add"}:
            raise IntegrityError(
                f"Affordance kind {name!r} conditional clause needs exactly when/add")
        when = clause["when"]
        dimension, _, value = when.partition(":") if isinstance(when, str) else ("", "", "")
        if (dimension not in _CLAIM_CONDITION_DIMENSIONS or not value
                or dimension == "mode" and value not in TRAVEL_MODES):
            raise IntegrityError(
                f"Affordance kind {name!r} conditional when {when!r} is invalid")
        if any(slot not in RESOURCE_CLAIM_SLOTS for slot in clause["add"]):
            raise IntegrityError(
                f"Affordance kind {name!r} conditional add must be within "
                f"{sorted(RESOURCE_CLAIM_SLOTS)}")
        parsed.append((when, tuple(clause["add"])))
    return tuple(base), tuple(parsed)


def _parse_target_discovery(name: str, body: Mapping) -> tuple[str, str | None, str | None]:
    """@brief 解析目标发现声明：字符串 none/browse_result 或 catalog_field 表。"""
    raw = body.get("target_discovery", "none")
    if isinstance(raw, str):
        if raw not in ("none", "browse_result"):
            raise IntegrityError(
                f"Affordance kind {name!r} target_discovery {raw!r} is invalid")
        return raw, None, None
    if not isinstance(raw, dict) or set(raw) - {"source", "field", "id_key"}:
        raise IntegrityError(f"Affordance kind {name!r} target_discovery is malformed")
    if raw.get("source") != "catalog_field" or not isinstance(raw.get("field"), str):
        raise IntegrityError(
            f"Affordance kind {name!r} catalog_field discovery requires a field")
    id_key = raw.get("id_key")
    if id_key is not None and not isinstance(id_key, str):
        raise IntegrityError(f"Affordance kind {name!r} target_discovery id_key is invalid")
    return "catalog_field", raw["field"], id_key


def _parse_family(name: str, body: Mapping,
                  kinds: Mapping[str, KindSpec]) -> FamilySpec:
    """@brief 解析并校验单个家族条目（规则 1-4 的家族侧）。"""
    if not isinstance(body, dict):
        raise IntegrityError(f"Affordance family {name!r} must be a table")
    _reject_unknown(name, body, _FAMILY_KEYS, "family")
    family_kinds = body.get("kinds")
    if (not isinstance(family_kinds, list) or not family_kinds
            or any(kind not in kinds for kind in family_kinds)):
        raise IntegrityError(
            f"Affordance family {name!r} kinds must reference declared kinds")
    attention_kind, attention_params, content_kinds = _parse_attention(name, body)
    members = _parse_habit_members(name, body.get("habit_members", []))
    generator, params = _parse_candidate(name, body.get("candidate"))
    rank = body.get("enumeration_rank")
    if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool) or rank < 0):
        raise IntegrityError(f"Affordance family {name!r} enumeration_rank is invalid")
    return FamilySpec(
        name=name,
        kinds=tuple(family_kinds),
        attention_kind=attention_kind,
        attention_params=MappingProxyType(attention_params),
        habit_members=members,
        generator=generator,
        generator_params=MappingProxyType(params),
        comfort=bool(body.get("comfort", False)),
        enumeration_rank=rank,
        unclassified=bool(body.get("unclassified", False)),
        content_kinds=content_kinds,
    )


def _parse_attention(
        name: str, body: Mapping) -> tuple[str, dict[str, Any], tuple[ContentKindSpec, ...]]:
    """@brief 校验家族注意力采样参数与内容品类声明。"""
    attention = body.get("attention")
    if not isinstance(attention, dict) or attention.get("kind") not in ATTENTION_KINDS:
        raise IntegrityError(f"Affordance family {name!r} attention is invalid")
    attention_params = {key: value for key, value in attention.items() if key != "kind"}
    content_raw = body.get("content_kinds", {})
    if attention["kind"] == "content_kind":
        if attention_params.get("sampling") != "per_kind_top_n":
            raise IntegrityError(
                f"Affordance family {name!r} attention sampling must be per_kind_top_n")
        for key in ("per_kind", "pool_cap"):
            value = attention_params.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise IntegrityError(
                    f"Affordance family {name!r} attention {key} must be a positive integer")
        if not isinstance(content_raw, dict) or not content_raw:
            raise IntegrityError(
                f"Affordance family {name!r} content attention requires content_kinds")
    elif content_raw:
        raise IntegrityError(
            f"Affordance family {name!r} declares content_kinds without content attention")
    content_kinds = tuple(
        _parse_content_kind(kind_name, kind_body)
        for kind_name, kind_body in content_raw.items())
    return attention["kind"], attention_params, content_kinds


def _parse_candidate(name: str, candidate: Any) -> tuple[str, dict[str, Any]]:
    """@brief 校验候选生成器原型及其参数，不注册环境来源。"""
    if not isinstance(candidate, dict) or "generator" not in candidate:
        raise IntegrityError(f"Affordance family {name!r} candidate generator is missing")
    generator = candidate["generator"]
    if generator not in GENERATOR_PARAM_KEYS:
        raise IntegrityError(
            f"Affordance family {name!r} references unregistered generator {generator!r}")
    params = {key: value for key, value in candidate.items() if key != "generator"}
    unknown_params = set(params) - GENERATOR_PARAM_KEYS[generator]
    if unknown_params:
        raise IntegrityError(
            f"Affordance family {name!r} generator {generator!r} rejects params "
            f"{sorted(unknown_params)}")
    _check_generator_values(name, generator, params)
    return generator, params


def _parse_habit_members(name: str, raw: Any) -> tuple[str, ...]:
    """@brief 解析习惯归并成员：一串动作名，逐个对闭集核对。"""
    if not isinstance(raw, list):
        raise IntegrityError(f"Affordance family {name!r} habit_members must be a list")
    for member in raw:
        if member not in HABIT_ACTIONS:
            raise IntegrityError(
                f"Affordance family {name!r} habit member action {member!r} is unknown")
    return tuple(raw)


def _check_generator_values(name: str, generator: str, params: Mapping[str, Any]) -> None:
    """@brief 校验原型参数取值（正数秒/份数、合法枚举、引用资源存在）。"""
    for key in ("cap_seconds", "fallback_seconds", "min_session_seconds",
                "probe_duration_seconds", "max_tasks"):
        if key in params and (not isinstance(params[key], int)
                              or isinstance(params[key], bool) or params[key] <= 0):
            raise IntegrityError(
                f"Affordance family {name!r} generator param {key} must be a positive integer")
    if generator == "clear_backlog" and params.get("resource") not in REFERENCEABLE_RESOURCES:
        raise IntegrityError(
            f"Affordance family {name!r} clear_backlog resource must reference the "
            f"resource registry")
    if params.get("destination") is not None and params["destination"] not in (
            "home", "task_location"):
        raise IntegrityError(f"Affordance family {name!r} cheapest_route destination is invalid")
    if params.get("slot_policy") is not None and params["slot_policy"] != "two_segments":
        raise IntegrityError(f"Affordance family {name!r} slot_policy is invalid")


def _parse_content_kind(name: str, body: Mapping) -> ContentKindSpec:
    """@brief 解析内容品类子条目的注意力要求，拒绝环境来源声明。"""
    if not isinstance(body, dict):
        raise IntegrityError(f"Affordance content kind {name!r} must be a table")
    _reject_unknown(name, body, frozenset({"attention_required"}), "content kind")
    if not isinstance(body.get("attention_required"), bool):
        raise IntegrityError(
            f"Affordance content kind {name!r} attention_required must be a boolean")
    return ContentKindSpec(name, body["attention_required"])


def _build_view(kinds: Mapping[str, KindSpec],
                families: Mapping[str, FamilySpec]) -> AffordanceView:
    """@brief 组装视图并展开习惯归并路由表（动作名 -> 家族名，一一对应）。"""
    routes: dict[str, str] = {}
    for family_name, family in families.items():
        for action in family.habit_members:
            if action in routes:
                raise IntegrityError(
                    f"Affordance habit route for action {action!r} is declared twice")
            routes[action] = family_name
    return AffordanceView(kinds=kinds, families=families,
                          _habit_routes=MappingProxyType(routes))


def _check_habit_coverage(view: AffordanceView) -> None:
    """@brief 规则 5：习惯覆盖完备——每个已声明 kind 的启动动作必须被某个
    family 的 habit_members 收走，或由该 kind/family 显式声明 unclassified。"""
    exempt: set[str] = {name for name, spec in view.kinds.items() if spec.unclassified}
    for family in view.families.values():
        if family.unclassified:
            exempt.update(family.kinds)
    for name, spec in view.kinds.items():
        if name in exempt or spec.action == "none":
            continue
        if spec.action not in view._habit_routes:
            raise IntegrityError(
                f"Affordance kind {name!r} ({spec.action}) is covered by no habit_members "
                f"and declares no unclassified flag")


def _check_claim_content_kinds(view: AffordanceView) -> None:
    """@brief 条件占用 content_kind:<品类> 只能引用某家族声明的内容品类（防拼写静默不生效）。"""
    declared = {kind.name for family in view.families.values() for kind in family.content_kinds}
    for spec in view.kinds.values():
        for condition, _ in spec.conditional_claims:
            dimension, _, value = condition.partition(":")
            if dimension == "content_kind" and value not in declared:
                raise IntegrityError(
                    f"Affordance kind {spec.name!r} conditional content kind {value!r} is not declared")
