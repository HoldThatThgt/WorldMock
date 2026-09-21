#!/usr/bin/env python3
"""静态素材可达性检查：对任意对象给出至少一条到达路径，不可达即非零退出。

@file     check_reachability.py
@brief    WorldMock 素材可达性静态守卫；口径见 docs/dev/SPEC-life-realism.md「素材可达性」。
@note     本脚本是只读检查，不运行任何模拟、不写任何文件。判定与
          `worldmock validate` 的准入校验同源（`worldmock.life.retrieval`），
          额外覆盖运行期真正参与检索的对象——场景 `contents[]`（准入函数的
          入参面里没有场景）。**反塌缩，不是覆盖率**：只回答「从未可达」能
          否被判假，不承诺任何条目会被呈现。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from worldmock.life.retrieval import (  # noqa: E402
    DEFAULT_RETRIEVAL_INDEX_THRESHOLD,
    RetrievalEntry,
    entry_from_content,
    place_kinds,
    reachability_report,
)

#: 内容语料清单：路径与其条目装载方式（与 catalog/admission.py 的 CONTENT_CORPORA 同源）。
CONTENT_CORPORA: tuple[tuple[str, str], ...] = (
    ("movies/items.json", "list"),
    ("drama/items.json", "list"),
    ("podcasts/items.json", "list"),
    ("novels/items.json", "list"),
    ("feed/items.json", "list"),
    ("feed/feed-items.json", "list"),
    ("jokes/items.json", "list"),
    ("jokes/feed-items.json", "list"),
    ("games/library/items.json", "list"),
    ("social/weibo/feed-items.json", "list"),
    ("social/zhihu/feed-items.json", "list"),
    ("social/movie_reviews/feed-items.json", "list"),
    ("sintel/media-identity.json", "single"),
)


def _items(document, mode: str) -> list:
    """@brief 按装载方式把语料文档展开为条目列表。"""
    if mode == "single":
        return [document]
    return list(document) if isinstance(document, list) else []


def corpus_entries(corpora_root: Path) -> tuple[RetrievalEntry, ...]:
    """@brief 装载内容语料里全部可检索条目的分区侧事实（与准入校验同一池）。

    这是静态检索的对象池：条目身份取语料自身的 `item_id`，分区键三项全部来自
    条目自身（题材、语言、时长段）。`single` 装载方式的身份文件本身携带
    `item_id` 与分区键，与列表式语料同样逐条投影。

    :param corpora_root: 语料根目录。
    :return: 逐条目的检索条目。
    """
    entries: list[RetrievalEntry] = []
    for relative, mode in CONTENT_CORPORA:
        path = corpora_root / relative
        if not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        for item in _items(document, mode):
            if not isinstance(item, dict):
                continue
            object_id = str(item.get("item_id") or "")
            if not object_id:
                continue
            entries.append(entry_from_content(object_id, relative, item))
    return tuple(entries)


def _corpus_index(corpora_root: Path) -> dict[str, dict]:
    """@brief 内容语料条目的查找表（item_id -> 条目文档），供场景条目解析分区键。"""
    index: dict[str, dict] = {}
    for relative, mode in CONTENT_CORPORA:
        path = corpora_root / relative
        if not path.is_file():
            continue
        for item in _items(json.loads(path.read_text(encoding="utf-8")), mode):
            if isinstance(item, dict) and item.get("item_id"):
                index[str(item["item_id"])] = item
    return index


def scenario_entries(scenario_path: Path) -> tuple[RetrievalEntry, ...]:
    """@brief 装载场景内容条目的分区侧事实（运行期真正参与检索的对象池）。

    场景 `contents[]` 的条目只声明时长，题材与语言在同身份的内容语料条目上；
    本函数按 `item_id` 解析补齐，解析得到的条目与语料条目落在同一分区。解析
    不到的条目（语料里没有同身份条目）没有题材与语言声明，落不进任何分区；
    本脚本如实列出而不代为补默认值。
    """
    document = json.loads(scenario_path.read_text(encoding="utf-8"))
    root = (scenario_path.resolve().parent / document["corpus_root"]).resolve()
    index = _corpus_index(root)
    entries = []
    for item in document.get("contents", []):
        item_id = str(item.get("item_id") or "")
        if not item_id:
            continue
        merged = {**index.get(item_id, {}), **item}
        entries.append(entry_from_content(item_id, "scenario:contents", merged))
    return tuple(entries)


def main() -> int:
    """@brief 检查命令行指定的语料（与场景），发现不可达对象即列出并非零退出。"""
    parser = argparse.ArgumentParser(description="WorldMock static reachability check")
    parser.add_argument("--scenario", type=Path, default=None,
                        help="scenario file whose contents[] entries are also checked")
    parser.add_argument("--corpora", type=Path, default=REPO_ROOT / "corpora",
                        help="corpora root (default: repository corpora/)")
    parser.add_argument("--threshold", type=int, default=DEFAULT_RETRIEVAL_INDEX_THRESHOLD,
                        help="retrieval_index_threshold recorded in the index-path report")
    arguments = parser.parse_args()
    corpora_root = arguments.corpora.resolve()
    entries = corpus_entries(corpora_root)
    if arguments.scenario is not None:
        entries = entries + scenario_entries(arguments.scenario.resolve())
    payload = reachability_report(entries, tuple(place_kinds(corpora_root).values()),
                                  arguments.threshold)
    print(json.dumps({key: value for key, value in payload.items() if key != "entries"},
                     ensure_ascii=False, indent=2, sort_keys=True))
    unreachable = payload["unreachable"]
    infeasible = [row for row in payload["partitions"] if not row["feasible"]]
    if infeasible:
        print(f"[reachability] {len(infeasible)} infeasible partition(s):", file=sys.stderr)
        for row in infeasible:
            print(f"[reachability]   {row['partition']} (size={row['size']})", file=sys.stderr)
    if unreachable:
        by_source: dict[str, list[dict]] = {}
        for row in unreachable:
            by_source.setdefault(str(row["source"]), []).append(row)
        print(f"[reachability] {len(unreachable)} unreachable object(s): in no partition,"
              " or in no feasible partition:", file=sys.stderr)
        for source, rows in sorted(by_source.items()):
            print(f"[reachability]   from {source}: {len(rows)}", file=sys.stderr)
            for row in rows:
                print(f"[reachability]     {row['object_id']}:"
                      f" missing={row['missing_dimensions']}", file=sys.stderr)
        return 1
    print(f"[reachability] all {payload['entry_count']} entries reachable across"
          f" {payload['partition_count']} partition(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
