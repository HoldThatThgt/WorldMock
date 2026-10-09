"""测试消费仓内真实、完整的单人环境，反例通过独立复制构造。"""

from pathlib import Path

ARTIFICIAL_ENVIRONMENT_ROOT = Path(__file__).resolve().parent / "fixtures/dataset-environment"
ENVIRONMENT_ROOT = ARTIFICIAL_ENVIRONMENT_ROOT
CATALOG_ROOT = ENVIRONMENT_ROOT / "catalog"
