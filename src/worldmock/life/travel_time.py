"""@brief 从目录加载时快照解析封闭的出行时长系数表。"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from worldmock.errors import IntegrityError

TRAVEL_TIME_KEYS = frozenset(
    {"walking", *(f"driving_{hour:02d}" for hour in range(24))}
)
TRAVEL_TIME_FACTOR_BOUNDS = (1.0, 5.0)


@dataclass(frozen=True, slots=True)
class TravelTimeProfile:
    """@brief 目录加载时冻结的二十五分支出行时长系数。"""

    factors: Mapping[str, float]

    def factor(self, mode: str, hour: int) -> float:
        """@brief 按封闭方式和本地小时返回系数。"""
        branch = "walking" if mode == "walking" else f"{mode}_{hour:02d}"
        value = self.factors.get(branch)
        if value is None:
            raise IntegrityError(f"Travel time profile has no branch for {branch}")
        return value


def parse_travel_time_profile(content: bytes) -> TravelTimeProfile:
    """@brief 严格解析目录快照中的纯数值 TOML，不接受表达式或额外配置。"""
    try:
        payload = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise IntegrityError("Travel time profile is malformed") from error
    if set(payload) != {"version", "factors", "provenance"} or payload["version"] != 1:
        raise IntegrityError("Travel time profile has an unsupported shape or version")
    factors = payload.get("factors")
    if not isinstance(factors, dict) or set(factors) != TRAVEL_TIME_KEYS:
        raise IntegrityError("Travel time profile must declare the closed branch set")
    normalized = {key: _factor(key, value) for key, value in factors.items()}
    if normalized["walking"] != 1.0:
        raise IntegrityError("Travel time walking factor must be 1.0")
    _require_provenance(payload.get("provenance"))
    return TravelTimeProfile(MappingProxyType(normalized))


def _factor(branch: str, value: object) -> float:
    """@brief 校验一个分支是有限范围内的非布尔数值。"""
    low, high = TRAVEL_TIME_FACTOR_BOUNDS
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntegrityError(f"Travel time branch {branch} is not numeric")
    factor = float(value)
    if not math.isfinite(factor) or not low <= factor <= high:
        raise IntegrityError(f"Travel time branch {branch} is outside [{low}, {high}]")
    return factor


def _require_provenance(provenance: object) -> None:
    """@brief 要求数值表具有指向完整 factors 映射的来源记录。"""
    if not isinstance(provenance, dict) or set(provenance) != {"factors"}:
        raise IntegrityError("Travel time profile provenance is missing")
    record = provenance["factors"]
    required = {"type", "producer", "source", "artifact", "method", "paths"}
    if not isinstance(record, dict) or set(record) != required or record.get("paths") != ["/factors"]:
        raise IntegrityError("Travel time profile provenance does not cover /factors")
