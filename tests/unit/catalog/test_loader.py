"""当前目录不可变索引与纯加载边界。"""

import subprocess
import sys

import pytest

from tests.data_paths import CATALOG_ROOT
from worldmock.catalog.loader import load_catalog


def test_catalog_is_deeply_immutable():
    catalog = load_catalog(CATALOG_ROOT, 7)
    with pytest.raises(TypeError):
        catalog.apps["invented"] = next(iter(catalog.apps.values()))
    with pytest.raises(TypeError):
        catalog.goods_by_store["home"] = ("invented",)
    assert isinstance(catalog.contents_by_app["bilibili"], tuple)


def test_pure_catalog_loading_does_not_import_runtime_engine():
    source = ("from pathlib import Path; import sys; from worldmock.catalog.loader import load_catalog; "
              "catalog=load_catalog(Path(sys.argv[1]),7); assert 'worldmock.life.engine' not in sys.modules; "
              "print(len(catalog.locations))")
    result = subprocess.run([sys.executable, "-c", source, str(CATALOG_ROOT)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "8"
