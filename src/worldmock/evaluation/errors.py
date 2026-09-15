"""@brief 评估错误类型。"""


class EvaluationError(RuntimeError):
    """@brief 表示可归因到具体评估合同的失败。"""

    def __init__(self, code: str, message: str) -> None:
        """@brief 保存稳定错误码与英文错误信息。"""
        super().__init__(message)
        self.code = code

