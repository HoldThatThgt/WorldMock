"""@brief 与评分消费者解耦的固定动机变换参数加载。"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from pydantic import Field

from worldmock.base_models import FrozenModel
from worldmock.errors import IntegrityError


class MotivationTransforms(FrozenModel):
    """@brief catalog/rules/motivation.toml 解析出的通用变换参数。"""

    loss_exponent: float = Field(gt=0, lt=1)
    satiation_exponent: float = Field(gt=0, lt=1)
    money_scale_fen: float = Field(gt=0)
    energy_scale_kcal: float = Field(gt=0)
    sleep_scale_hours: float = Field(gt=0)
    entertainment_scale: float = Field(gt=0)
    goal_scale: float = Field(gt=0)
    time_value_fen_per_hour: float = Field(gt=0)
    habit_weight: float = Field(ge=0, le=1)
    habit_discount: float = Field(ge=0, lt=1)

    def scale_of(self, resource: str) -> float:
        """@brief 返回某资源的归一尺度。"""
        return {"money_fen": self.money_scale_fen,
                "energy_kcal": self.energy_scale_kcal,
                "sleep_debt_hours": self.sleep_scale_hours,
                "entertainment_need": self.entertainment_scale,
                "goal_task": self.goal_scale}[resource]


_TRANSFORM_GROUPS = ("prospect", "homeostatic", "normalization", "cost", "habit")


def load_motivation_transforms(rules_dir: Path) -> MotivationTransforms:
    """@brief 读取必须存在的通用动机变换参数，拒绝缺失或不合法的声明。

    文件在场但结构或取值不合约时 fail-closed 抛 IntegrityError——在场即声明意图，
    坏数据不得静默降级。键布局与 motivation.toml 的五个分组一一对应。
    """
    path = Path(rules_dir) / "motivation.toml"
    if not path.is_file():
        logging.error("Motivation rule snapshot is missing")
        raise IntegrityError("Motivation rule snapshot is missing")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        logging.error("Motivation rules cannot be read: %s", error)
        raise IntegrityError(f"Motivation rules TOML is malformed: {error}") from error
    flat: dict[str, float] = {}
    seen: set[str] = set()
    for group in _TRANSFORM_GROUPS:
        table = payload.get(group)
        if not isinstance(table, dict):
            raise IntegrityError(f"Motivation rules group [{group}] is missing")
        for key, value in table.items():
            if key in seen:
                raise IntegrityError(f"Motivation rules key {key} is declared twice")
            seen.add(key)
            flat[key] = value
    unknown = set(payload) - set(_TRANSFORM_GROUPS) - {"version", "provenance"}
    if unknown:
        raise IntegrityError(f"Motivation rules declare unknown groups: {sorted(unknown)}")
    try:
        return MotivationTransforms.model_validate({
            "loss_exponent": flat["loss_exponent"],
            "satiation_exponent": flat["satiation_exponent"],
            "money_scale_fen": flat["money_fen"],
            "energy_scale_kcal": flat["energy_kcal"],
            "sleep_scale_hours": flat["sleep_debt_hours"],
            "entertainment_scale": flat["entertainment_need"],
            "goal_scale": flat["goal_progress"],
            "time_value_fen_per_hour": flat["time_value_fen_per_hour"],
            "habit_weight": flat["weight"],
            "habit_discount": flat["discount"],
        })
    except Exception as error:
        raise IntegrityError(f"Motivation rules values are invalid: {error}") from error
