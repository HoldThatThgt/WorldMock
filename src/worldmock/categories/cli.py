"""@brief 品类检查、细化报告和新目录发布的命令参数与调用。"""

from __future__ import annotations

import json
from pathlib import Path


def add_category_arguments(commands) -> None:
    """@brief 为唯一 worldmock 命令面注册品类维护入口。"""
    category = commands.add_parser("category", help="inspect, refine, and publish category contracts")
    operations = category.add_subparsers(dest="category_command", required=True)
    check = operations.add_parser("check", help="validate a catalog and report explicit or requested pairings")
    check.add_argument("--catalog", type=Path, required=True)
    check.add_argument("--queries", type=Path, help="JSON array of directed category/action pairing queries")
    check.add_argument("--run-dir", type=Path, help="include observed category and rule counts")
    refine = operations.add_parser("refine", help="report affected objects and attribute-based category suggestions")
    refine.add_argument("--catalog", type=Path, required=True)
    refine.add_argument("--candidate", type=Path, required=True)
    refine.add_argument("--parent-category", required=True)
    refine.add_argument("--run-dir", type=Path)
    publish = operations.add_parser("publish", help="validate and atomically publish a new catalog directory")
    publish.add_argument("--catalog", type=Path, required=True)
    publish.add_argument("--candidate", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    publish.add_argument("--assignments", type=Path, required=True,
                         help="JSON object mapping explicitly confirmed object IDs to category IDs")
    publish.add_argument("--scenario", type=Path, action="append", required=True,
                         help="scenario to admit against the new catalog; repeat for every intended scenario")


def run_category_command(arguments) -> dict:
    """@brief 将命令参数交给真实品类维护功能，异常由主命令统一记录。"""
    from worldmock.categories.maintenance import check_catalog, publish_catalog, refinement_report

    if arguments.category_command == "check":
        queries = _read_json(arguments.queries) if arguments.queries else ()
        return check_catalog(arguments.catalog, queries, arguments.run_dir)
    if arguments.category_command == "refine":
        return refinement_report(arguments.catalog, arguments.candidate,
                                 arguments.parent_category, arguments.run_dir)
    return publish_catalog(arguments.catalog, arguments.candidate, arguments.output,
                           _read_json(arguments.assignments), tuple(arguments.scenario))


def _read_json(path: Path):
    """@brief 读取明确指定的维护请求文件，不猜测缺省内容。"""
    return json.loads(path.read_text(encoding="utf-8"))
