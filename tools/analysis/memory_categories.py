"""@brief 记忆类别表：列出一个环境包每条内容行与 app 行推导出的记忆类别、命中的规则编号与理由。

类别规则只在 worldmock.simulation_engine.observation.memory.categories 写一次（docs/design/06-agent.md「记忆的类别」）；
本工具加载包、调用 category_book 与 row_categories，只加名称、目录字段与理由文字，模块里没有任何类别判定。输出结构的
唯一说明在 docs/design/07-evaluation.md「记忆类别表」。只读一个环境包的冻结输入，不推进世界、不写入人物、不访问模型
服务；除命令行 --output 指定的文件外不写任何文件，同一个包两次输出逐字节相同。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from tools.analysis.errors import EvaluationError
from worldmock.configuration.inputs.environment import load_environment
from worldmock.configuration.inputs.models import ModelConfig, ModelMetadata
from worldmock.foundation.errors import WorldMockError
from worldmock.resource_engine.catalog.models import Catalog
from worldmock.simulation_engine.observation.memory.categories import RowCategory, category_book, row_categories
from worldmock.state.model.memory import MEMORY_CATEGORIES

#: 类别表的结构标识。
SCHEMA_VERSION = "memory-categories-1"
_FOOD, _WORK, _LIFE = MEMORY_CATEGORIES
#: 规则 0 的理由：内容行与 app 行、三个类别都是这一句。
_DECLARED = "目录行写了 memory_category"
#: (行种类, 规则编号, 类别) → 理由；{workplace_kind} 换成班次地点类型。键恰是静态推导表能给出的全部组合：
#: 地点内容 0、3、5、7，app 内容 0、6，app 行 0、6，规则 0 的三个类别各占一键。
ROW_REASONS: Mapping[tuple[str, int, str], str] = MappingProxyType({
    **{(row, 0, category): _DECLARED for row in ("content", "app") for category in MEMORY_CATEGORIES},
    ("content", 3, _FOOD): "属于食品单元：同一单元有一集写了 completion_effects.food，或凭的是食品状态",
    ("content", 5, _WORK): "在班次地点类型 {workplace_kind}：工作日在班次地点开始时归工作，休息日归生活",
    ("content", 7, _LIFE): "其余归生活；工作日在岗时段内（不含休息时段）在家以外的地点开始时归工作（规则 5）",
    ("content", 6, _WORK): "前提要求班次地点类型 {workplace_kind}",
    ("content", 6, _LIFE): "app 内容不要求班次地点，归生活",
    ("app", 6, _WORK): "有一条内容的前提要求班次地点类型 {workplace_kind}",
    ("app", 6, _LIFE): "没有内容要求班次地点，归生活",
})
#: 本工具自己查出的输入问题的错误码。
_INPUT = "memory_categories_input"


def _offline_metadata(model: ModelConfig) -> ModelMetadata:
    """@brief 加载包时的模型元数据：取配置里写明的容量，不访问模型服务；类别表不调用模型，输出上限不被读取。"""
    return ModelMetadata(requested_model=model.requested_model, metadata_url=model.metadata_url,
                         context_window=model.context_window, max_output_tokens=model.context_window)


def category_table(package: Path) -> dict:
    """@brief 一个环境包的记忆类别表：结构标识、包的身份与版本、班次地点类型与逐行的类别、规则和理由。

    :param package: 环境包目录（含 catalog/、persona.toml 与 config.toml）；加载失败时 load_environment 抛出的
        WorldMockError 原样上抛（构造时已写错误日志）。
    """
    inputs = load_environment(package, metadata_loader=_offline_metadata)
    book = category_book(inputs)
    catalog = inputs.catalog
    return {"schema_version": SCHEMA_VERSION, "environment_id": catalog.manifest.environment_id,
            "environment_version": catalog.manifest.environment_version, "workplace_kind": book.workplace_kind,
            "rows": [_row(catalog, row, book.workplace_kind) for row in row_categories(book)]}


def _row(catalog: Catalog, row: RowCategory, workplace_kind: str | None) -> dict[str, Any]:
    """@brief 类别表的一行：推导照抄 RowCategory，名称与内容行的地点类型、app 取自目录行，理由按 ROW_REASONS 取。"""
    if row.kind == "content":
        content = catalog.contents[row.row_id]
        name, location_kinds, app_ids = content.title, list(content.location_kinds), list(content.app_ids)
    else:
        name, location_kinds, app_ids = catalog.apps[row.row_id].name, [], []
    reason = ROW_REASONS[(row.kind, row.rule, row.category)].format(workplace_kind=workplace_kind)
    return {"row": row.kind, "id": row.row_id, "name": name, "location_kinds": location_kinds, "app_ids": app_ids,
            "category": row.category, "rule": row.rule, "reason": reason}


def main(argv: list[str] | None = None) -> int:
    """@brief 命令行：python -m tools.analysis.memory_categories --package DIR [--output FILE]。

    没有 --output 时把类别表写到标准输出；给了时文件必须不存在。包加载失败与输入问题返回 1（异常构造时已写错误日志）。
    """
    parser = argparse.ArgumentParser(prog="python -m tools.analysis.memory_categories",
                                     description="List the memory category, rule and reason of every content row "
                                                 "and app row of one environment package.")
    parser.add_argument("--package", required=True, type=Path, help="environment package directory")
    parser.add_argument("--output", type=Path, help="table file to create; stdout when omitted")
    args = parser.parse_args(argv)
    try:
        if args.output is not None and args.output.exists():
            raise EvaluationError(_INPUT, f"Output file already exists: {args.output}")
        text = json.dumps(category_table(args.package), ensure_ascii=False, indent=2) + "\n"
    except (EvaluationError, WorldMockError):
        return 1
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
