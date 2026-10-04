"""@brief 新建运行与验证共用的画像覆盖目标构造。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from worldmock.catalog.admission import CONTENT_CORPORA
from worldmock.profile.coverage import CoverageTargets
from worldmock.profile.coverage_apps import verified_app_names

_MEDIA_KIND_LABELS = {
    "feature_film": "电影",
    "drama_series": "剧集",
    "podcast_episode": "播客",
    "webnovel_excerpt": "网文",
    "news_feed": "新闻",
    "joke_feed": "段子",
    "subtitle_experience": "字幕体验",
    "game_session": "游戏",
}


def build_coverage_targets(scenario: Any, catalog: Any, corpora_root: Path,
                           app_names: tuple[str, ...] | None = None) -> CoverageTargets:
    """@brief 从 catalog 品类/目录条目派生覆盖率目标集（确定性）。

    habit_response_keywords=生活活动目录名 + 场景地名；content_genres=内容
    语料条目 genre 闭集；food_kinds=正餐语料名 + 目录可进食商品名；
    media_kinds=世界实际可观看/游玩媒介的中文品类词；app_names 缺省时取场景钉定手机配置里有执行证据的应用别名
    （verified_app_names，核验失败即 IntegrityError，validate 因此失败），新建运行传入它自己核验的结果
    （runtime._coverage_targets，核验失败时为空：运行路径永不因输入警告失败，docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 ）。
    """
    root = Path(corpora_root)
    habit_keywords = sorted({item["name_zh"] for item in _corpus_items(root, "lifestyle/items.json")
                             if isinstance(item, dict) and item.get("name_zh")})
    habit_keywords += sorted({location.name for location in scenario.locations})
    genres = sorted({genre for relative, _ in CONTENT_CORPORA
                     for item in _corpus_items(root, relative) if isinstance(item, dict)
                     for genre in (item.get("genre") or [])})
    foods = sorted({item["name_zh"] for item in _corpus_items(root, "meals/items.json")
                    if isinstance(item, dict) and item.get("name_zh")})
    foods += sorted({obj.name for obj in catalog.objects.values()
                     if obj.object_kind == "product" and "eat" in obj.use_modes})
    media = sorted({_MEDIA_KIND_LABELS[kind] for kind in _scenario_content_kinds(scenario)})
    if app_names is None:
        app_names = verified_app_names(scenario, root)
    return CoverageTargets(habit_response_keywords=tuple(habit_keywords),
                           content_genres=tuple(genres),
                           food_kinds=tuple(foods),
                           media_kinds=tuple(media), app_names=app_names)


def _scenario_content_kinds(scenario: Any) -> set[str]:
    """@brief 场景实际可见的内容与游戏媒介品类闭集。"""
    kinds = {item.kind for item in scenario.contents}
    if getattr(scenario, "games", None):
        kinds.add("game_session")
    return kinds


def _corpus_items(root: Path, relative: str) -> list:
    """@brief 读取语料 JSON 并按装载方式展开条目（缺失返回空列表）。"""
    path = root / relative
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    mode = ("items" if relative in {"lifestyle/items.json", "meals/items.json"} else
            next((kind for candidate, kind in CONTENT_CORPORA if candidate == relative), "list"))
    if mode == "single":
        return [document]
    if mode == "items":
        return list(document.get("items", [])) if isinstance(document, dict) else []
    return list(document) if isinstance(document, list) else []

