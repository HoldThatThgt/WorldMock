"""@brief 多资源分录测试共享夹具：货币 closed 域与身体 level 域的元信息。"""

import pytest

from worldmock.exchange_engine.ledger.ledger import ResourceLedger
from worldmock.exchange_engine.ledger.ledger_models import ResourceMeta

MONEY = "money_fen"
ENERGY = "energy_kcal"
AGENT = "agent:1"
EMPLOYER = "employer:zhangjiang"
TRANSPORT = "transport:service"
ANCHOR = "external:scenario"


@pytest.fixture
def metas() -> list[ResourceMeta]:
    """@brief 一份覆盖两种资源形态的注册表。"""
    return [
        ResourceMeta(MONEY, "account", "closed", 0, None),
        ResourceMeta(ENERGY, "level", "open", 0, 2400),
    ]


@pytest.fixture
def ledger(metas) -> ResourceLedger:
    """@brief 含外部锚点的多资源账本。"""
    return ResourceLedger(metas, anchor_owners={ANCHOR})
