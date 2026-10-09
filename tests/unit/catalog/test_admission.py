"""当前目录清单的原始字节漂移必须在真实加载入口被拒绝。"""

import json

import pytest

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog
from worldmock.footprints.models import canonical_json


@pytest.mark.parametrize("change", ["bytes", "digest", "size", "missing", "manifest_hash"])
def test_file_identity_drift_is_rejected(catalog_dir, change):
    manifest = json.loads((catalog_dir / "manifest.json").read_bytes())
    path = catalog_dir / manifest["files"][0]["path"]
    if change == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "missing":
        path.unlink()
    else:
        if change == "digest":
            manifest["files"][0]["sha256"] = "0" * 64
        elif change == "size":
            manifest["files"][0]["size_bytes"] += 1
        else:
            manifest["catalog_content_sha256"] = "0" * 64
        (catalog_dir / "manifest.json").write_bytes(canonical_json(manifest) + b"\n")
    with pytest.raises(RegistrationError):
        load_catalog(catalog_dir, 7)
