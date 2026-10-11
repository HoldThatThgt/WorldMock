"""用于实际准入反例和定向业务场景的冻结环境副本。"""

import json
import shutil

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.catalog.identity import refresh_manifest_identity


def copy_environment(destination):
    """复制输入原文，所有后续变化须发布新身份。"""
    shutil.copytree(ENVIRONMENT_ROOT, destination)
    return destination


def write_catalog_rows(root, name, rows):
    """写合法 LF JSONL 并通过生产身份发布器重算完整目录身份。"""
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    (root / "catalog" / name).write_text(text, encoding="utf-8", newline="\n")
    refresh_manifest_identity(root / "catalog")


def catalog_rows(root, name):
    """读取实际逐行对象用于只修改所需字段。"""
    return [json.loads(line) for line in (root / "catalog" / name).read_text(encoding="utf-8").splitlines()]


def walking_only(root):
    """去掉数据集定义的出行方式：删除 travel-method.jsonl，导航 app 只绑定引擎自带的步行，并按生产入口刷新清单。"""
    (root / "catalog" / "travel-method.jsonl").unlink()
    rows = catalog_rows(root, "app-catalog.jsonl")
    for row in rows:
        if "travel_method_ids" in row:
            row["travel_method_ids"] = ["walking"]
    write_catalog_rows(root, "app-catalog.jsonl", rows)
    return root


def travel_method_row(travel_method_id, name, speed, start_price_fen=0, price_per_km_fen=0, exertion="light"):
    """一行出行方式：六个字段都写出，缺省免费、轻体力。"""
    return {"travel_method_id": travel_method_id, "name": name, "speed_meters_per_second": speed,
            "start_price_fen": start_price_fen, "price_per_km_fen": price_per_km_fen, "exertion": exertion}


def replace_travel_methods(root, methods, navigation):
    """换一套数据集出行方式：按身份升序写 travel-method.jsonl，导航 app 改为绑定 navigation 列出的出行方式身份。"""
    write_catalog_rows(root, "travel-method.jsonl", sorted(methods, key=lambda row: row["travel_method_id"]))
    rows = catalog_rows(root, "app-catalog.jsonl")
    next(row for row in rows if row["app_id"] == "amap")["travel_method_ids"] = sorted(navigation)
    write_catalog_rows(root, "app-catalog.jsonl", rows)
    return root
