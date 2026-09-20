"""@brief 从已审阅地点与供给准备新一版冻结地图，运行期间不调用此命令。"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
from pathlib import Path

from worldmock.corpora.map_prepare import prepare_map


def main() -> None:
    """@brief 要求独立新目录，全部路线核验成功后才发布地图。"""
    parser = argparse.ArgumentParser(description="Prepare a frozen map release from reviewed inputs")
    parser.add_argument("--source-root", type=Path, required=True, help="Reviewed corpus root")
    parser.add_argument("--destination", type=Path, required=True, help="New corpus root for the map release")
    arguments = parser.parse_args()
    target = arguments.destination.resolve()
    if target.exists():
        parser.error("Destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        reference = prepare_map(staging, arguments.source_root)
        staging.rename(target)
    except Exception:
        logging.exception("Frozen map preparation failed")
        shutil.rmtree(staging)
        raise
    print(json.dumps(reference.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
