"""真实长内容目录允许宿主在活动尚未完成时暂停和恢复。"""
import json
import shutil
import pytest
from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.catalog.identity import refresh_manifest_identity


@pytest.fixture
def long_content_engine(tmp_path, engine_factory):
    root = tmp_path / "long-content-environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "catalog/content-catalog.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row.get("app_ids"):
            row["duration_seconds"] = 14400
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8", newline="\n")
    refresh_manifest_identity(root / "catalog")
    return engine_factory(environment_root=root)
