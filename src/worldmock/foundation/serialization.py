"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
import hashlib
import json
import logging
from typing import Any




def canonical_json(value: Any) -> bytes:
    """@brief 使用唯一 JSON 编码，使哈希与重放身份稳定。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        logging.getLogger(__name__).exception("Canonical JSON encoding failed")
        raise



def sha256_json(value: Any) -> str:
    """@brief 对唯一规范 JSON 字节计算 SHA-256；非有限值直接拒绝。"""
    return hashlib.sha256(canonical_json(value)).hexdigest()



def is_sha256(value: Any) -> bool:
    """@brief 仅接受恰好六十四个小写十六进制字符的字符串。"""
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
