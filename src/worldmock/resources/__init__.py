"""@brief 多资源分录结算通道：模型、账本与域级审计。

资源定义见 docs/design/09-io-formats.md「资源与需求字段」；
本次分录校验见 docs/design/02-exchange-engine.md「校验资源分录」；
历史审计见 docs/design/05-recording-engine.md「资金守恒验证」。
"""

from worldmock.resources.audit import (
    ClosedDomainReport,
    LevelFlowRow,
    audit_closed_domain,
    audit_level_flows,
)
from worldmock.resources.ledger import ResourceLedger
from worldmock.resources.models import (
    ConvertEntry,
    FlowEntry,
    LedgerEntry,
    ResourceLeg,
    ResourceLedgerError,
    ResourceMeta,
    TransferEntry,
)

__all__ = [
    "ClosedDomainReport",
    "ConvertEntry",
    "FlowEntry",
    "LedgerEntry",
    "LevelFlowRow",
    "ResourceLeg",
    "ResourceLedger",
    "ResourceLedgerError",
    "ResourceMeta",
    "TransferEntry",
    "audit_closed_domain",
    "audit_level_flows",
]
