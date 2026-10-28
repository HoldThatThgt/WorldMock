"""@brief 环境登记和只读对象目录的公共入口。"""

from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.map.graph import Graph
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.resource_engine.catalog.models import Catalog, CatalogManifest

__all__ = ["Catalog", "CatalogManifest", "Graph", "RegistrationError", "load_catalog"]
