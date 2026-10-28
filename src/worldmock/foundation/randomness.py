"""@brief 应用选择和推送共用的确定性随机流。"""

import hashlib
import random


def random_stream(random_state: str, key: str) -> random.Random:
    """@brief 按冻结字节材料创建独立随机流，不推进世界随机状态。"""
    digest = hashlib.sha256(f"{random_state}:{key}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big", signed=False))
