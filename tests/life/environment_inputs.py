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
