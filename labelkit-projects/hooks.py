"""@brief 连接 LabelKit 工程与不依赖仿真运行时的纯函数钩子。"""

from worldmock.delivery.semantics import complete_annotation, validate_annotation

__all__ = ["complete_annotation", "validate_annotation"]
