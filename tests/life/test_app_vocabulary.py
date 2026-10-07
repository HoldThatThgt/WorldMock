"""@brief 应用方式闭集直接通过唯一词表的真实解析入口验证。"""

from worldmock.life.vocabulary import WAYS, way_options, way_verb
from pathlib import Path
import tomllib


def test_game_way_cannot_be_restored_in_the_public_way_table():
    assert "游玩" not in WAYS
    assert way_options("游玩") == frozenset()
    assert way_verb("游玩") == ""
    assert "打开" not in WAYS and way_options("打开") == frozenset()
    assert WAYS["进行"] == ("interact", "do")


def test_game_runtime_and_textworld_dependencies_are_removed():
    """@brief 三份真实依赖声明和实际生产模块同时退出 TextWorld 游戏链。"""
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert not any(item.lower().startswith("textworld") for item in dependencies)
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    assert "textworld" not in {item["name"] for item in lock["package"]}
    install_lock = (root / "requirements/lock.txt").read_text(encoding="utf-8")
    assert not any(line.lower().startswith("textworld") for line in install_lock.splitlines())
    assert not (root / "src/worldmock/life/game.py").exists()
