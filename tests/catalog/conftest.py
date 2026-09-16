"""@brief 目录测试共享的真实 schema 夹具与载荷构造器。

夹具对齐裁决后的目录形态：类型级资源（money_fen 等）+ 数组溯源 +
占位端点/角色标注 + rules/ 下 TOML 公式声明 + overrides/ 墓碑。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from worldmock.catalog.admission import (
    ScenarioAccountRef,
    ScenarioCatalogRefs,
    ScenarioObjectRef,
    ScenarioResourceRef,
    ScenarioScheduleRef,
    ScheduleRefKind,
)
from worldmock.catalog.loader import load_catalog

MONEY_RESOURCE = {
    "resource_id": "money_fen",
    "kind": "account",
    "unit": "fen",
    "owner": "global",
    "bounds": None,
    "conservation": "closed",
    "alert_rules": [],
    "provenance": [
        {
            "part": "type semantics (closed conservation, non-negative via ledger invariant)",
            "paths": [],
            "type": "scenario",
            "source": "test fixture ledger semantics",
        }
    ],
    "version": 1,
}
ENERGY_RESOURCE = {
    "resource_id": "energy_kcal",
    "kind": "level",
    "unit": "kcal",
    "owner": "agent",
    "bounds": {"min": 0, "max": 2400},
    "conservation": "open",
    "alert_rules": [{"below": 800, "code": "HUNGRY"}, {"below": 400, "code": "EMERGENCY_ENERGY"}],
    "drain_rules": {
        "basal_kcal_per_hour": 65,
        "activity_met": {"sleep": 0.95, "wait": 1.3, "travel:walking": 3.5},
        "food_efficiency_curve": [
            {"input_value": 0, "output_value": 0.9},
            {"input_value": 8, "output_value": 0.9},
            {"input_value": 16, "output_value": 0.85},
        ],
    },
    "provenance": [
        {
            "part": "drain_rules.basal_kcal_per_hour",
            "paths": ["drain_rules.basal_kcal_per_hour"],
            "type": "fact",
            "source": "Mifflin-St Jeor BMR 65kg male",
            "artifact": "corpora/references/energy-requirements.md",
        },
        {
            "part": "drain_rules.activity_met",
            "paths": ["drain_rules.activity_met"],
            "type": "fact",
            "source": "Compendium of Physical Activities 2011",
        },
        {
            "part": "drain_rules.food_efficiency_curve",
            "paths": ["drain_rules.food_efficiency_curve"],
            "type": "assumption",
            "source": "experiment design note",
        },
        {"part": "bounds", "paths": ["bounds.min", "bounds.max"], "type": "scenario", "source": "test fixture"},
        {"part": "alert_rules", "paths": ["alert_rules"], "type": "scenario", "source": "test fixture"},
    ],
    "version": 1,
}
SLEEP_RESOURCE = {
    "resource_id": "sleep_debt_hours",
    "kind": "level",
    "unit": "hours",
    "owner": "agent",
    "bounds": {"min": 0, "max": None},
    "conservation": "open",
    "drain_rules": {"awake_sleep_debt_per_hour": 1, "sleep_recovery_per_hour": 2},
    "provenance": [
        {"part": "bounds.min", "paths": ["bounds.min"], "type": "scenario", "source": "test fixture"},
        {
            "part": "drain_rules",
            "paths": ["drain_rules.awake_sleep_debt_per_hour", "drain_rules.sleep_recovery_per_hour"],
            "type": "assumption",
            "source": "experiment design note",
        },
    ],
    "version": 1,
}
FARE_RULE_TOML = """# 测试夹具费率公式声明（travel-fare 同构）。

[formula.walking]
amount_fen = 0

[formula.driving]
amount_fen = "max(500, ceil(m * 0.2))"

[params]
m = "route.distance_meters"
mode = "route.mode"

[provenance]
fare = { type = "scenario", source = "test fixture routes" }
"""
MILK_OBJECT = {
    "object_id": "food-milk",
    "name": "纯牛奶",
    "object_kind": "product",
    "effect": "nutrition",
    "consumable": True,
    "use_modes": ["eat"],
    "edges": [
        {
            "on": "purchase",
            "type": "transfer",
            "from": "{buyer}",
            "to": "{merchant}",
            "amount_fen": 680,
            "account_roles": {"from": "buyer", "to": "merchant:grocery"},
        },
        {
            "on": "consume",
            "type": "source",
            "resource": "energy_kcal",
            "amount": 185,
            "duration_s": 900,
        },
    ],
    "stock": {"initial": 120, "restock": "none"},
    "use_duration_seconds": 900,
    "delivery_delay_seconds": 1800,
    "serving": {"quantity": 1, "unit": "250ml"},
    "required_location_id": None,
    "provenance": [
        {
            "part": "consume energy edge",
            "paths": ["edges[1].amount"],
            "type": "fact",
            "source": "openfoodfacts:product/6923644266066",
            "artifact": "corpora/open-food-facts/products.json",
        },
        {
            "part": "purchase price edge",
            "paths": ["edges[0].amount_fen"],
            "type": "anchored",
            "source": "numbeo:shanghai/2026-01/milk",
            "method": "point-in-distribution p60",
        },
        {"part": "stock", "paths": ["stock.initial"], "type": "scenario", "source": "test fixture"},
        {
            "part": "legacy timing/serving fields",
            "paths": ["edges[1].duration_s", "use_duration_seconds", "delivery_delay_seconds", "serving.quantity"],
            "type": "scenario",
            "source": "test fixture",
        },
    ],
    "version": 1,
}
SODA_OBJECT = {
    "object_id": "food-soda",
    "name": "zero 汽水",
    "object_kind": "product",
    "effect": "nutrition",
    "consumable": True,
    "use_modes": ["eat"],
    "edges": [
        {
            "on": "purchase",
            "type": "transfer",
            "from": "{buyer}",
            "to": "{merchant}",
            "amount_fen": 350,
            "account_roles": {"from": "buyer", "to": "merchant:grocery"},
        },
        {
            "on": "consume",
            "type": "source",
            "resource": "energy_kcal",
            "amount": 0,
            "duration_s": 300,
        },
    ],
    "stock": {"initial": 80, "restock": "none"},
    "use_duration_seconds": 300,
    "delivery_delay_seconds": 1800,
    "serving": {"quantity": 1, "unit": "500ml"},
    "required_location_id": None,
    "provenance": [
        {
            "part": "consume energy edge",
            "paths": ["edges[1].amount"],
            "type": "fact",
            "source": "openfoodfacts:product/soda",
        },
        {
            "part": "purchase price edge",
            "paths": ["edges[0].amount_fen"],
            "type": "anchored",
            "source": "numbeo:shanghai/2026-01/soda",
        },
        {"part": "stock", "paths": ["stock.initial"], "type": "scenario", "source": "test fixture"},
        {
            "part": "legacy timing/serving fields",
            "paths": ["edges[1].duration_s", "use_duration_seconds", "delivery_delay_seconds", "serving.quantity"],
            "type": "scenario",
            "source": "test fixture",
        },
    ],
    "version": 1,
}
TAXI_OBJECT = {
    "object_id": "travel-taxi",
    "name": "出租车出行",
    "object_kind": "service",
    "edges": [
        {
            "on": "depart",
            "type": "transfer",
            "from": "{traveler}",
            "to": "{transport_operator}",
            "amount_fen": {
                "formula_ref": "fare",
                "params": {"m": "route.distance_meters", "mode": "route.mode"},
            },
            "account_roles": {"from": "traveler", "to": "transport:service"},
        }
    ],
    "formula_ref": "fare",
    "provenance": [
        {
            "part": "fare formula (declared in rules/fare.toml)",
            "paths": [],
            "type": "anchored",
            "source": "numbeo:shanghai/taxi",
        }
    ],
    "version": 1,
}
WORK_OBJECT = {
    "object_id": "work-ami",
    "name": "AMI 会议整理合同",
    "object_kind": "contract",
    "edges": [
        {
            "on": "settle",
            "type": "convert",
            "from": "{employer}",
            "to": "{worker}",
            "formula": "min(pay_fen * worked_seconds // 3600, pay_fen * maximum_duration_seconds // 3600)",
            "account_roles": {"from": "employer:zhangjiang", "to": "worker"},
        }
    ],
    "acceptance": {
        "required_effective_seconds": None,
        "materials_fully_revealed": True,
        "review_pass_required": True,
    },
    "efficiency": {
        "work_efficiency_factors": [
            {
                "input_resource": "sleep_debt_hours",
                "curve": [
                    {"input_value": 0, "output_value": 1.0},
                    {"input_value": 8, "output_value": 1.0},
                    {"input_value": 16, "output_value": 0.85},
                ],
            },
        ],
        "work_notes_multiplier": 1.1,
    },
    "provenance": [
        {
            "part": "settlement formula",
            "paths": [],
            "type": "scenario",
            "source": "test fixture task definitions",
        },
        {
            "part": "acceptance gates",
            "paths": [],
            "type": "scenario",
            "source": "test fixture task definitions",
        },
        {
            "part": "efficiency params",
            "paths": ["efficiency"],
            "type": "assumption",
            "source": "experiment design note",
        },
    ],
    "version": 1,
}
SODA_TOMBSTONE = {
    "target_id": "food-soda",
    "kind": "object",
    "at_version": 1,
    "reason": "superseded by sugar-free line",
}


def default_resources() -> list[dict]:
    """@brief 返回默认资源夹具的深拷贝。"""
    return copy.deepcopy([MONEY_RESOURCE, ENERGY_RESOURCE, SLEEP_RESOURCE])


def default_objects() -> list[dict]:
    """@brief 返回默认对象夹具的深拷贝。"""
    return copy.deepcopy([MILK_OBJECT, SODA_OBJECT, TAXI_OBJECT, WORK_OBJECT])


def default_rules() -> dict[str, str]:
    """@brief 返回默认规则文件夹具的深拷贝（公式 TOML）。"""
    return {"fare.toml": FARE_RULE_TOML}


def default_tombstones() -> list[dict]:
    """@brief 返回默认墓碑夹具的深拷贝。"""
    return copy.deepcopy([SODA_TOMBSTONE])


def write_catalog(
    root: Path,
    resources: list[dict],
    objects: list[dict],
    tombstones: list[dict],
    manifest: tuple[int, int] = (1, 0),
    rules: dict[str, str] | None = None,
) -> Path:
    """@brief 把一套夹具载荷写成完整目录文件并返回目录路径。"""
    root.mkdir(parents=True, exist_ok=True)
    _dump_jsonl(root / "resources.jsonl", resources)
    _dump_jsonl(root / "objects.jsonl", objects)
    _dump_jsonl(root / "overrides/tombstones.jsonl", tombstones)
    major, minor = manifest
    (root / "manifest.json").write_text(
        json.dumps({"version_major": major, "version_minor": minor}, indent=2) + "\n",
        encoding="utf-8",
    )
    for filename, content in (rules if rules is not None else default_rules()).items():
        rule_path = root / "rules" / filename
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text(content, encoding="utf-8")
    return root


def _dump_jsonl(path: Path, rows: list[dict]) -> None:
    """@brief 逐行写出 JSONL 载荷。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """@brief 一套含墓碑对象的最小合法目录。"""
    return write_catalog(
        tmp_path / "catalog",
        default_resources(),
        default_objects(),
        default_tombstones(),
    )


@pytest.fixture
def loaded_catalog(catalog_dir: Path):
    """@brief 已通过加载校验的冻结目录视图。"""
    return load_catalog(catalog_dir)


def valid_refs() -> ScenarioCatalogRefs:
    """@brief 一套对默认目录全绿的场景引用闭集（类型级资源 + 账户声明）。"""
    return ScenarioCatalogRefs(
        catalog_major=1,
        catalog_minor=0,
        resources=[
            ScenarioResourceRef(resource_id="energy_kcal", initial_value=1500, unit="kcal"),
        ],
        accounts=[
            ScenarioAccountRef(
                account_id="account:agent:1", resource_id="money_fen",
                role="buyer", initial_value=100000, unit="fen",
            ),
            ScenarioAccountRef(
                account_id="account:agent:1", resource_id="money_fen",
                role="traveler", initial_value=100000, unit="fen",
            ),
            ScenarioAccountRef(
                account_id="account:agent:1", resource_id="money_fen",
                role="worker", initial_value=100000, unit="fen",
            ),
            ScenarioAccountRef(
                account_id="merchant:grocery", resource_id="money_fen",
                role="merchant:grocery", initial_value=0, unit="fen",
            ),
            ScenarioAccountRef(
                account_id="transport:service", resource_id="money_fen",
                role="transport:service", initial_value=0, unit="fen",
            ),
            ScenarioAccountRef(
                account_id="employer:zhangjiang", resource_id="money_fen",
                role="employer:zhangjiang", initial_value=5000000, unit="fen",
            ),
        ],
        objects=[
            ScenarioObjectRef(object_id="food-milk"),
            ScenarioObjectRef(object_id="travel-taxi"),
            ScenarioObjectRef(object_id="work-ami"),
        ],
        schedule=[
            ScenarioScheduleRef(
                kind=ScheduleRefKind.PRICE_CHANGE,
                due_at=100,
                sequence=1,
                object_id="food-milk",
                object_version=1,
            ),
            ScenarioScheduleRef(
                kind=ScheduleRefKind.BILL_DUE,
                due_at=500,
                sequence=1,
                account_id="employer:zhangjiang",
            ),
        ],
    )
