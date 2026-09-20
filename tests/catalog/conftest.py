"""@brief 新品类目录测试共享夹具。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.categories.conftest import write_catalog
from worldmock.catalog import load_catalog
from worldmock.catalog.admission import (
    ScenarioAccountRef,
    ScenarioCatalogRefs,
    ScenarioObjectRef,
    ScenarioResourceRef,
)


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """@brief 返回一份摘要绑定的最小目录。"""
    return write_catalog(tmp_path / "catalog")


@pytest.fixture
def loaded_catalog(catalog_dir: Path):
    """@brief 返回真实加载并编译的目录视图。"""
    return load_catalog(catalog_dir)


def valid_refs() -> ScenarioCatalogRefs:
    """@brief 返回覆盖最小目录购买与进食边的合法场景引用。"""
    return ScenarioCatalogRefs(
        catalog_major=3,
        catalog_minor=0,
        resources=[ScenarioResourceRef(resource_id="energy_kcal", initial_value=1500, unit="kcal")],
        accounts=[
            ScenarioAccountRef(
                account_id="account:agent", resource_id="money_fen",
                role="buyer", initial_value=10000, unit="fen",
            ),
            ScenarioAccountRef(
                account_id="merchant", resource_id="money_fen",
                role="merchant:grocery", initial_value=0, unit="fen",
            ),
        ],
        objects=[ScenarioObjectRef(object_id="milk")],
    )
