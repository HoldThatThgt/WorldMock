"""@brief 核对游戏内容与内容生成记录的身份。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from worldmock.errors import IntegrityError
from worldmock.life.scenario import GameConfig


def validate_game(config: GameConfig, corpus_root: Path) -> None:
    """@brief 校验游戏文件和生成记录的内容身份。"""
    path = _game_path(config, corpus_root)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != config.game_sha256:
        raise IntegrityError("TextWorld game hash mismatch")
    try:
        payload = json.loads(content)
        walkthrough = payload["metadata"]["walkthrough"]
    except Exception as error:
        raise IntegrityError("TextWorld game metadata is invalid") from error
    encoded = json.dumps(walkthrough, ensure_ascii=False, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != config.walkthrough_sha256:
        raise IntegrityError("TextWorld walkthrough hash mismatch")


def _game_path(config: GameConfig, root: Path) -> Path:
    """@brief 将游戏读取限制在冻结语料目录。"""
    base = Path(root).resolve()
    candidate = base / config.game_path
    path = candidate.resolve()
    if _has_symlink(candidate, base) or base not in path.parents or not path.is_file():
        raise IntegrityError("TextWorld game path is invalid")
    return path


def _has_symlink(path: Path, root: Path) -> bool:
    """@brief 在解析前拒绝游戏文件及父目录符号链接。"""
    current = path
    while current != root and root in current.parents:
        if current.is_symlink():
            return True
        current = current.parent
    return current != root
