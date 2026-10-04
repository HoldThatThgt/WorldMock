"""@brief 核对游戏内容与内容生成记录的身份。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from worldmock.corpora.manifest import safe_file
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
    return safe_file(root, config.game_path, "TextWorld game path is invalid")
