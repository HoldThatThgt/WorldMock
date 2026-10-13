"""@brief 显示写法测试共用的名称来源：样例环境建出的名称表，以及把环境目录按原字节冻结进一个运行目录的素材库。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from tests.data_paths import ENVIRONMENT_ROOT


@lru_cache(maxsize=1)
def sample_catalog_rows():
    """@brief 样例环境五份目录经登记时的严格行模型读出的行，次序即 display_names 的入参次序。"""
    from worldmock.catalog.loader import read_catalog_rows
    from worldmock.catalog.models import App, Content, Goods, Location, TravelMethod

    directory = ENVIRONMENT_ROOT / "catalog"
    return tuple(tuple(read_catalog_rows(directory, name, model)) for name, model in (
        ("app-catalog.jsonl", App), ("content-catalog.jsonl", Content), ("location.jsonl", Location),
        ("merchant-catalog.jsonl", Goods), ("travel-method.jsonl", TravelMethod)))


@lru_cache(maxsize=1)
def sample_display_names():
    """@brief 样例环境五份目录建出的显示名称。"""
    from worldmock.footprints.display_wording import display_names

    return display_names(*sample_catalog_rows())


def freeze_environment(run_dir: Path, root: Path = ENVIRONMENT_ROOT, replaced: dict | None = None) -> dict:
    """@brief 把环境目录按原字节冻结进 run_dir/assets，返回运行清单的 environment_assets。

    素材名集合与新建运行时相同：人设、配置、目录清单、文件表里的每个目录文件，加一份地图素材（这里是占位字节，
    显示名称不读它）。replaced 按素材名给出替换的字节，用来构造与当前行模型不合的冻结目录。
    """
    from worldmock.footprints.assets import AssetStore

    store = AssetStore(Path(run_dir) / "assets")
    listed = json.loads((root / "catalog" / "manifest.json").read_bytes())["files"]
    names = ["persona.toml", "config.toml", "catalog/manifest.json", *(f"catalog/{row['path']}" for row in listed)]
    contents = {name: (root / name).read_bytes() for name in names}
    contents["map.json"] = b"{}"
    contents.update(replaced or {})
    return {name: store.put(content, {"media_type": "text/plain", "source": {"kind": "environment_input",
                                                                              "path": name}}).model_dump(mode="json")
            for name, content in sorted(contents.items())}
