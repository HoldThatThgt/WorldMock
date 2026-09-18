"""游戏语料三件套（抽象游戏库/TextWorld 种子/视觉小说）完整性与场景接线测试。

数据契约以原始 JSON 与哈希核对为准；game_session 的模型准入
（CONTENT_KINDS）由 src 侧单元落地后由 test_entertainment_corpora 的
load_scenario 全链路覆盖。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

GAMES = json.loads(Path("corpora/games/library/items.json").read_text(encoding="utf-8"))
DRAMAS = json.loads(Path("corpora/drama/items.json").read_text(encoding="utf-8"))
SCENARIOS = ("scenarios/white-collar-12-weeks.json", "scenarios/multi-person.json")
ALL_SCENARIOS = SCENARIOS + ("scenarios/multi-person-half-day.json",)
REQUIRED_GENRES = ("FPS", "MOBA", "RPG", "Strategy", "Simulation", "Sports", "Indie")


def _manifest_index() -> dict[str, dict]:
    manifest = json.loads(Path("corpora/manifest.json").read_text(encoding="utf-8"))
    return {entry["path"]: entry for entry in manifest["entries"]}


def _scenario_docs() -> dict[str, dict]:
    return {path: json.loads(Path(path).read_text(encoding="utf-8")) for path in SCENARIOS}


def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_bytes().decode("utf-8")
    cues = []
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        lines = block.split("\n")
        match = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        values = [int(part) for part in match.groups()]
        # 起止秒均含毫秒（与生产 load_subtitles 同精度；新 drama 语料时长 =
        # round(末 cue 结束秒)，毫秒是判定输入）。
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        cues.append((start, end, "\n".join(lines[2:])))
    return cues


def test_game_library_size_fields_and_genre_coverage() -> None:
    """游戏库 60-100 部、字段齐全、单局时长 30-300 分钟、类型全覆盖。"""
    assert 60 <= len(GAMES) <= 100
    required = {"game_id", "title", "genres", "developers", "release_year",
                "typical_playtime_minutes", "description", "duration_seconds", "timeline_path"}
    ids, titles = set(), set()
    primary = {}
    for game in GAMES:
        assert required <= set(game)
        assert 30 <= game["typical_playtime_minutes"] <= 300
        assert game["duration_seconds"] == game["typical_playtime_minutes"] * 60
        assert 1990 <= game["release_year"] <= 2026
        assert len(game["description"]) >= 20
        ids.add(game["game_id"])
        titles.add(game["title"])
        token = game["genres"].split("/")[0]
        primary[token] = primary.get(token, 0) + 1
    assert len(ids) == len(GAMES) == len(titles)
    for genre in REQUIRED_GENRES:
        assert primary.get(genre, 0) >= 5, f"{genre} coverage {primary.get(genre, 0)} < 5"


def test_game_cards_are_single_synopsis_cues_matching_manifest() -> None:
    """每部游戏一张简介卡 cue 覆盖整个玩时长，manifest 哈希一致。"""
    index = _manifest_index()
    for game in GAMES:
        path = Path(f"corpora/{game['timeline_path']}")
        cues = _parse_srt(path)
        assert len(cues) == 1
        start, end, card = cues[0]
        assert start == 0 and end == game["duration_seconds"]
        assert f"《{game['title']}》（{game['release_year']}）" in card
        assert f"单局典型时长 {game['typical_playtime_minutes']} 分钟" in card
        assert game["developers"] in card and "游戏简介：" in card
        entry = index[game["timeline_path"]]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["size_bytes"] == path.stat().st_size


def test_both_scenarios_wire_game_sessions_and_preferences() -> None:
    """两场景 contents 均含全部 game_session 条目，字段与文件哈希一致，偏好全覆盖。"""
    docs = _scenario_docs()
    for path, doc in docs.items():
        by_id = {entry["item_id"]: entry for entry in doc["contents"]}
        for game in GAMES:
            entry = by_id.get(game["game_id"])
            assert entry is not None, f"{path} missing {game['game_id']}"
            assert entry["kind"] == "game_session"
            assert entry["duration_seconds"] == game["duration_seconds"]
            assert entry["timeline_path"] == game["timeline_path"]
            assert entry["timeline_sha256"] == hashlib.sha256(
                Path(f"corpora/{game['timeline_path']}").read_bytes()).hexdigest()
            assert entry["experience_label"] == "game_session_play"
            assert entry["entertainment_relief_per_hour"] == 0.35
        # 修订合同 L1661：随机偏好浮点删除——逐游戏 0.4-0.8 断言随合同移除，
        # 改为负向断言：场景人物不再携带随机偏好浮点键。
        for agent in doc["agents"]:
            assert "entertainment_preferences" not in agent, (
                f"{path} agent {agent['agent_id']} still carries random preference floats")


def test_textworld_library_has_30_seeds_wired_into_scenarios() -> None:
    """TextWorld 种子 1201-1230 共 30 部冻结，场景 games[] 哈希一致。"""
    index = _manifest_index()
    for seed in range(1201, 1231):
        game_path = f"textworld/game-{seed}.json"
        options_path = f"textworld/game-{seed}-options.json"
        for rel in (game_path, options_path):
            data = Path(f"corpora/{rel}").read_bytes()
            assert index[rel]["sha256"] == hashlib.sha256(data).hexdigest()
        options = json.loads(Path(f"corpora/{options_path}").read_text(encoding="utf-8"))
        assert options["seed"] == seed
        walkthrough = json.loads(Path(f"corpora/{game_path}").read_text(encoding="utf-8"))[
            "metadata"]["walkthrough"]
        assert options["walkthrough"] == walkthrough
    for path, doc in _scenario_docs().items():
        games = {entry["game_id"]: entry for entry in doc["games"]}
        assert len(games) == 30
        for seed in range(1201, 1231):
            entry = games[f"textworld-{seed}"]
            data = Path(f"corpora/textworld/game-{seed}.json").read_bytes()
            walkthrough = json.loads(data)["metadata"]["walkthrough"]
            encoded = json.dumps(walkthrough, ensure_ascii=False, separators=(",", ":")).encode()
            assert entry["game_sha256"] == hashlib.sha256(data).hexdigest()
            assert entry["walkthrough_sha256"] == hashlib.sha256(encoded).hexdigest()
        for agent in doc["agents"]:
            assert "entertainment_preferences" not in agent


def test_drama_library_grew_to_ten_with_parseable_streams() -> None:
    """视觉小说对白流 10 部：新增 7 部 cue 递增不重叠，末尾等于总时长。"""
    assert len(DRAMAS) == 10
    index = _manifest_index()
    docs = _scenario_docs()
    for drama in DRAMAS[3:]:
        path = Path(f"corpora/{drama['timeline_path']}")
        cues = _parse_srt(path)
        assert len(cues) == drama["lines_selected"]
        assert round(cues[-1][1]) == drama["duration_seconds"]
        assert all(left[1] <= right[0] for left, right in zip(cues, cues[1:]))
        assert all(cue[0] < cue[1] for cue in cues)
        assert index[drama["timeline_path"]]["sha256"] == hashlib.sha256(
            path.read_bytes()).hexdigest()
        for doc in docs.values():
            entry = next(item for item in doc["contents"] if item["item_id"] == drama["item_id"])
            assert entry["kind"] == "drama_series"
            assert entry["duration_seconds"] == drama["duration_seconds"]
            assert entry["timeline_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
            assert entry["experience_label"] == "drama_series_watch"


def test_scenario_manifest_pins_match_corpora_manifest() -> None:
    """三份场景（含 half-day）钉住的语料清单哈希与 manifest.json 一致。"""
    digest = hashlib.sha256(Path("corpora/manifest.json").read_bytes()).hexdigest()
    for path in ALL_SCENARIOS:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        assert doc["corpus_manifest_sha256"] == digest, path
