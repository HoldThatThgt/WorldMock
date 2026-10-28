"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
import hashlib
from pathlib import Path
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import AssetRef
from worldmock.foundation.serialization import canonical_json

from worldmock.foundation.atomic_write import atomic_write


class AssetStore:
    """@brief 素材路径只由 SHA-256 决定，调用方不能指定任意读取路径。"""

    def __init__(self, root: Path):
        """@brief 绑定当前运行的素材目录。"""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, metadata: dict) -> AssetRef:
        """@brief 完整发布素材，保留模态和来源。"""
        reference = AssetRef(
            sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content),
            media_type=metadata["media_type"], source=metadata["source"],
        )
        path = self.root / reference.sha256
        if not path.exists():
            atomic_write(path, content)
        self.read(reference)
        return reference

    def put_json(self, value: object, source: dict) -> AssetRef:
        """@brief 按唯一编码保存已脱敏的结构化证据。"""
        return self.put(canonical_json(value), {"media_type": "application/json", "source": source})

    def read(self, reference: AssetRef) -> bytes:
        """@brief 每次实际读取都复核路径、大小与内容身份。"""
        path = self.root / reference.sha256
        if path.is_symlink() or not path.is_file():
            raise IntegrityError("Artifact is missing or is a symbolic link")
        content = path.read_bytes()
        if len(content) != reference.size_bytes or hashlib.sha256(content).hexdigest() != reference.sha256:
            raise IntegrityError("Artifact hash or byte count mismatch")
        return content
