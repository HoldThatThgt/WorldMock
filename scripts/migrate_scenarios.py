"""Migrate legacy life scenarios to the slim catalog-pinned format (schema 2).

Reads the old-format scenarios/multi-person.json and
scenarios/white-collar-12-weeks.json, cross-checks every semantic value against
the frozen catalog (catalog/), strips semantic fields (price / kcal /
thresholds / MET / fare formula knowledge / curves — now catalog-owned) and
rewrites both files keeping all instance data (personas, calendar, mail,
tasks, routes, per-product merchant bindings and stock overrides) verbatim.

One-shot migration: refuses to run on files already in schema 2.
Parity assertions guarantee the catalog fully covers every stripped value.

Usage:
  python3 scripts/migrate_scenarios.py           # rewrite scenarios in place
  python3 scripts/migrate_scenarios.py --check   # compare in memory, exit 1 on drift
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    ROOT / "scenarios/multi-person.json",
    ROOT / "scenarios/white-collar-12-weeks.json",
)
sys.path.insert(0, str(ROOT / "src"))

from worldmock.catalog import (  # noqa: E402
    ObjectKind,
    SourceEdge,
    TransferEdge,
    load_catalog,
)


def load_json(path: Path) -> dict:
    """读取一个 JSON 文件。"""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def purchase_edge(obj) -> TransferEdge:
    """取得商品对象的 purchase 边。"""
    return next(e for e in obj.edges if isinstance(e, TransferEdge) and e.on == "purchase")


def consume_edge(obj) -> SourceEdge | None:
    """取得商品对象的 consume 边（可为空）。"""
    matches = [e for e in obj.edges if isinstance(e, SourceEdge) and e.on == "consume"]
    return matches[0] if matches else None


def check_product_parity(product: dict, obj) -> None:
    """断言目录对象完整覆盖旧商品的全部静态语义。"""
    assert obj.object_kind == ObjectKind.PRODUCT, f"{product['product_id']}: not a product"
    assert purchase_edge(obj).amount_fen == product["price_fen"], f"{product['product_id']}: price"
    edge = consume_edge(obj)
    expected_kcal = edge.amount if edge else 0
    assert expected_kcal == product["energy_kcal_per_unit"], f"{product['product_id']}: kcal"
    assert obj.use_duration_seconds == product["use_duration_seconds"]
    assert obj.delivery_delay_seconds == product["delivery_delay_seconds"]
    assert obj.use_modes == tuple(product["use_modes"])
    assert obj.consumable == product["consumable"]
    assert (obj.legacy_effect or obj.effect) == product["effect"]
    assert obj.required_location_id == product["required_location_id"]
    if obj.serving:
        assert obj.serving.quantity == product["serving_quantity"]
        assert obj.serving.unit == product["serving_unit"]
    else:
        assert product["serving_quantity"] is None
    assert purchase_edge(obj).account_roles.to_role == product["merchant_account_id"]


def check_body_parity(body: dict, catalog) -> None:
    """断言目录资源与合同模板覆盖旧身体参数。"""
    energy = catalog.resources["energy_kcal"]
    sleep = catalog.resources["sleep_debt_hours"]
    fun = catalog.resources["entertainment_need"]
    rules = energy.drain_rules
    assert rules.basal_kcal_per_hour == body["basal_kcal_per_hour"]
    assert dict(rules.activity_met) == body["activity_met"]
    assert [p.model_dump() for p in rules.food_efficiency_curve] == body["food_efficiency_curve"]
    assert energy.bounds.max == body["maximum_energy_kcal"]
    alerts = {r.code: r.below for r in energy.alert_rules}
    assert alerts["HUNGRY"] == body["hunger_threshold_kcal"]
    assert alerts["EMERGENCY_ENERGY"] == body["emergency_threshold_kcal"]
    assert sleep.drain_rules.awake_sleep_debt_per_hour == body["awake_sleep_debt_per_hour"]
    assert sleep.drain_rules.sleep_recovery_per_hour == body["sleep_recovery_per_hour"]
    assert fun.drain_rules.entertainment_need_per_awake_hour == body["entertainment_need_per_awake_hour"]
    assert dict(fun.drain_rules.entertainment_relief_per_hour) == body["entertainment_relief_per_hour"]
    contract = catalog.objects["contract-hourly"].efficiency
    factor_curves = {factor.input_resource: [p.model_dump() for p in factor.curve]
                     for factor in contract.work_efficiency_factors}
    assert factor_curves["sleep_debt_hours"] == body["work_efficiency_curve"]
    assert contract.work_notes_multiplier == body["work_notes_multiplier"]


def migrate_scenario(old: dict, catalog) -> dict:
    """把旧格式场景转换为新格式文档，实例数据逐项保留。"""
    body = old["body"]
    check_body_parity(body, catalog)
    accounts, merchants = build_accounts(old)
    products = []
    for product in old["products"]:
        obj = catalog.objects.get(product["product_id"])
        assert obj is not None, f"product {product['product_id']} missing from catalog"
        check_product_parity(product, obj)
        entry = {
            "product_id": product["product_id"],
            "merchant": product["merchant_account_id"],
            "merchant_location_id": product["merchant_location_id"],
        }
        if obj.stock is not None and product["initial_stock"] != obj.stock.initial:
            entry["stock"] = product["initial_stock"]
        products.append(entry)
    for account in accounts:
        if account["role"].startswith("merchant:"):
            assert account["account_id"] in merchants
    keep = ("schema_version", "scenario_id", "timezone", "start_at", "end_at", "random_seed",
            "activity_feedback_seconds", "max_consecutive_rejections", "corpus_root",
            "corpus_manifest_sha256", "agents", "locations", "routes", "tasks", "messages",
            "contents", "games", "calendar")
    document = {key: old[key] for key in keep if key != "schema_version"}
    document.update({
        "schema_version": 2,
        "catalog_version": {"major": catalog.version_major, "minor": catalog.version_minor},
        "accounts": accounts,
        "body": {
            "mass_kg": body["mass_kg"],
            "initial_energy_kcal": body["initial_energy_kcal"],
            "initial_sleep_debt_hours": body["initial_sleep_debt_hours"],
            "initial_entertainment_need": 0,
        },
        "products": products,
    })
    order = ("schema_version", "scenario_id", "timezone", "start_at", "end_at", "random_seed",
             "activity_feedback_seconds", "max_consecutive_rejections", "corpus_root",
             "corpus_manifest_sha256", "catalog_version", "accounts", "body", "agents",
             "locations", "routes", "products", "tasks", "messages", "contents", "games",
             "calendar")
    return {key: document[key] for key in order}


def build_accounts(old: dict) -> tuple[list[dict], set[str]]:
    """从旧账户字段与服务角色推导账户实例声明。"""
    merchants = {p["merchant_account_id"] for p in old["products"]}
    accounts = [{"account_id": merchant, "money_fen": old["merchant_initial_balance_fen"],
                 "role": merchant} for merchant in sorted(merchants)]
    accounts.append({"account_id": old["employer_account_id"],
                     "money_fen": old["employer_initial_balance_fen"],
                     "role": old["employer_account_id"]})
    accounts.append({"account_id": old["transport_account_id"],
                     "money_fen": old["transport_initial_balance_fen"],
                     "role": old["transport_account_id"]})
    accounts.append({"account_id": "external:scenario", "money_fen": 0,
                     "role": "external:scenario"})
    return accounts, merchants


def main(argv: list[str]) -> int:
    """执行迁移或漂移检查。"""
    catalog = load_catalog(ROOT / "catalog")
    check_only = "--check" in argv
    status = []
    for path in SCENARIOS:
        old = load_json(path)
        if old.get("schema_version") == 2:
            status.append(f"SKIP {path.name} (already schema 2)")
            continue
        content = json.dumps(migrate_scenario(old, catalog), ensure_ascii=False, indent=2) + "\n"
        if check_only:
            status.append(f"WOULD-WRITE {path.name} ({len(content.splitlines())} lines)")
            continue
        path.write_text(content, encoding="utf-8")
        status.append(f"WROTE {path.name} ({len(content.splitlines())} lines)")
    for line in status:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
