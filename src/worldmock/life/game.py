"""@brief 通过 TextWorld 1.7 的真实环境执行并重放游戏命令。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import textworld
from textworld import EnvInfos

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.models import GameProgress
from worldmock.life.scenario import GameConfig

GAME_INFOS = EnvInfos(admissible_commands=True, score=True, won=True, lost=True, facts=True, moves=True)


class GameOutcomeUnknown(IntegrityError):
    """@brief TextWorld 已接收命令但没有返回可确认的结果。"""


@dataclass(frozen=True, slots=True)
class GameCommandOutcome:
    """@brief TextWorld 执行一个命令后的持久进度和公开结果。"""

    progress: GameProgress
    reward: int
    done: bool


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


def new_game_session(session_id: str, config: GameConfig, corpus_root: Path) -> GameProgress:
    """@brief 从冻结初态创建只保存公开状态和命令日志的会话。"""
    state = _replay(config, corpus_root, ())
    return _progress(session_id, config.game_id, (), state)


def run_game_command(progress: GameProgress, command: str, config: GameConfig,
                     corpus_root: Path) -> GameCommandOutcome:
    """@brief 重放既有命令并执行一个可接受的新命令。"""
    if progress.won or progress.lost:
        raise BusinessRuleError("GAME_ALREADY_TERMINAL")
    environment, state = _open_replayed(config, corpus_root, tuple(progress.commands))
    try:
        if _state_hash(state) != progress.public_state_sha256:
            raise IntegrityError("TextWorld replay state hash mismatch")
        normalized = command.strip().lower()
        allowed = {item.lower(): item for item in state.admissible_commands}
        if normalized not in allowed:
            raise BusinessRuleError("GAME_COMMAND_NOT_ADMISSIBLE")
        try:
            state, reward, done = environment.step(allowed[normalized])
        except Exception as error:
            raise GameOutcomeUnknown("TextWorld command outcome is unknown") from error
        commands = tuple(progress.commands) + (allowed[normalized],)
        return GameCommandOutcome(_progress(progress.session_id, config.game_id, commands, state), reward, done)
    finally:
        environment.close()


def verify_game_progress(progress: GameProgress, config: GameConfig, corpus_root: Path) -> None:
    """@brief 恢复前重放完整命令日志并核对全部公开状态。"""
    state = _replay(config, corpus_root, tuple(progress.commands))
    replayed = _progress(progress.session_id, config.game_id, tuple(progress.commands), state)
    if replayed != progress:
        raise IntegrityError("TextWorld checkpoint replay disagrees with stored progress")


def _open_replayed(config: GameConfig, root: Path, commands: tuple[str, ...]):
    """@brief 打开冻结游戏并重放已经接受的命令。"""
    environment = textworld.start(str(_game_path(config, root)), GAME_INFOS)
    try:
        state = environment.reset()
        for command in commands:
            if command not in state.admissible_commands:
                raise IntegrityError("Stored TextWorld command is no longer admissible")
            state, _, done = environment.step(command)
            if done and command != commands[-1]:
                raise IntegrityError("Stored TextWorld log continues after terminal state")
        return environment, state
    except Exception:
        environment.close()
        raise


def _replay(config: GameConfig, root: Path, commands: tuple[str, ...]):
    """@brief 返回完整命令日志后的公开 TextWorld 状态。"""
    environment, state = _open_replayed(config, root, commands)
    environment.close()
    return state


def _progress(session_id: str, game_id: str, commands: tuple[str, ...], state) -> GameProgress:
    """@brief 将 TextWorld 状态投影为可持久和可比较的数据。"""
    return GameProgress(session_id=session_id, game_id=game_id, commands=list(commands),
                        moves=state.moves, score=state.score or 0, won=state.won, lost=state.lost,
                        admissible_commands=tuple(sorted(state.admissible_commands)),
                        public_state_sha256=_state_hash(state))


def _state_hash(state) -> str:
    """@brief 哈希稳定的公开分数、终态、命令和事实集合。"""
    payload = {"moves": state.moves, "score": state.score or 0, "won": state.won, "lost": state.lost,
               "admissible_commands": sorted(state.admissible_commands),
               "facts": sorted(str(fact) for fact in state.facts)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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
