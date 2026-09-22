"""@brief 通过 TextWorld 1.7 的真实环境执行并重放游戏命令。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import textworld
from textworld import EnvInfos

from worldmock.errors import IntegrityError
from worldmock.life.scenario import GameConfig

GAME_INFOS = EnvInfos(admissible_commands=True, score=True, won=True, lost=True, facts=True, moves=True)


def validate_game(config: GameConfig, corpus_root: Path) -> None:
    """@brief 校验游戏文件、walkthrough 身份和可启动性。"""
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
    environment = textworld.start(str(path), GAME_INFOS)
    try:
        environment.reset()
    finally:
        environment.close()


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
