"""@brief 约束 LabelKit 生成分支不能改写原始经历与冻结时限。"""

import logging


def validate_state(value) -> list[str]:
    """@brief 只允许声明业务路径变化，来源和规则保持冻结。"""
    errors = []
    for key in ("origin", "sla"):
        if value.state_before.get(key) != value.state_after.get(key):
            errors.append(f"Frozen {key} was modified")
    for error in errors:
        logging.error("Generated state validation failed: %s", error)
    return errors
