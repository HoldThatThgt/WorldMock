"""@brief 引擎测试只消费人工最小环境，路径随测试目录跨机器解析。"""

from pathlib import Path


ARTIFICIAL_ENVIRONMENT_ROOT = Path(__file__).resolve().parent / "fixtures" / "environment"
ENVIRONMENT_ROOT = ARTIFICIAL_ENVIRONMENT_ROOT
CATALOG_ROOT = ENVIRONMENT_ROOT / "catalog"
SCENARIOS_ROOT = ENVIRONMENT_ROOT / "scenarios"


def select_environment(root):
    """@brief 测试显式选定发布环境，拒绝缺失输入，不在机器上寻找数据仓。"""
    global ENVIRONMENT_ROOT, CATALOG_ROOT, SCENARIOS_ROOT
    directory = Path(root).resolve()
    required = ("catalog/manifest.json", "scenarios/single-person.json")
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise ValueError(f"Test environment is incomplete: {', '.join(missing)}")
    ENVIRONMENT_ROOT = directory
    CATALOG_ROOT = directory / "catalog"
    SCENARIOS_ROOT = directory / "scenarios"
