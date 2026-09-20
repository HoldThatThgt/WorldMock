"""@brief 取得冻结生活来源并原子发布可追溯语料目录。"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from textworld import EnvInfos, GameOptions
from textworld.generator import make_game

from worldmock.corpora.manifest import CorpusEntry, CorpusManifest, CorpusSource, load_manifest
from worldmock.errors import IntegrityError

_OFF_CODES = (
    "6923644266066", "6928804010114", "6917878044729", "6924743919211",
    "6920152460061", "6920459902387", "6901209212215", "8935090914864",
)
_OFF_FIELDS = "code,product_name,product_name_zh,serving_size,nutriments"
_OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json?fields={fields}"
_SINTEL_URL = "https://durian.blender.org/wp-content/content/subtitles/sintel_en.srt"
_AMI_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
_OSM_URL = "https://api.openstreetmap.org/api/0.6/map?bbox=121.6000,31.1950,121.6400,31.2250"
_ROUTE_BASES = {
    "driving": "https://router.project-osrm.org",
    "walking": "https://routing.openstreetmap.de/routed-foot",
}
_USER_AGENT = "WorldMock/1.0 (research corpus preparation)"
_GAME_INFOS = EnvInfos(admissible_commands=True, score=True, won=True, lost=True, facts=True, moves=True)


def prepare_corpora(destination: Path) -> CorpusManifest:
    """@brief 从固定来源重新取得完整子集，校验后以单次改名发布。"""
    target = Path(destination).resolve()
    if target.exists():
        raise IntegrityError("Corpus destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    retrieved_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    try:
        _fetch_open_food_facts(staging, retrieved_at)
        _fetch_sintel(staging)
        _fetch_ami(staging)
        _fetch_geodata(staging)
        _generate_games(staging)
        _copy_scenario_materials(staging)
        manifest = _write_manifest(staging, retrieved_at)
        load_manifest(staging)
        staging.rename(target)
        return manifest
    except Exception:
        logging.exception("Corpus preparation failed")
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _fetch_open_food_facts(root: Path, retrieved_at: datetime) -> None:
    """@brief 逐条码取得固定白名单并要求实际份量和每份热量。"""
    raw_products = []
    normalized = []
    for code in _OFF_CODES:
        url = _OFF_URL.format(code=code, fields=urllib.parse.quote(_OFF_FIELDS, safe=","))
        response = _download_json(url)
        product = _off_product(response, code)
        raw_products.append({"url": url, "response": response})
        normalized.append(_normalize_off(product, code))
    folder = root / "open-food-facts"
    folder.mkdir(parents=True)
    _write_json(folder / "products-source.json", {"retrieved_at": retrieved_at.isoformat(),
                                                    "products": raw_products})
    _write_json(folder / "products.json", normalized)


def _off_product(response: dict, code: str) -> dict:
    """@brief 拒绝缺失、重定向或条码不一致的食品响应。"""
    product = response.get("product")
    if response.get("status") != 1 or not isinstance(product, dict):
        raise IntegrityError(f"Open Food Facts product {code} is unavailable")
    if str(product.get("code")) != code:
        raise IntegrityError(f"Open Food Facts product {code} identity mismatch")
    return product


def _normalize_off(product: dict, code: str) -> dict:
    """@brief 投影运行所需食品事实，不推断或换算份量单位。"""
    name = product.get("product_name_zh") or product.get("product_name")
    serving = product.get("serving_size")
    nutriments = product.get("nutriments")
    energy = nutriments.get("energy-kcal_serving") if isinstance(nutriments, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise IntegrityError(f"Open Food Facts product {code} has no name")
    if not isinstance(serving, str) or not serving.strip():
        raise IntegrityError(f"Open Food Facts product {code} has no serving size")
    if not isinstance(energy, (int, float)) or isinstance(energy, bool) or energy < 0:
        raise IntegrityError(f"Open Food Facts product {code} has no serving energy")
    return {"barcode": code, "name": name.strip(), "serving_size": serving.strip(),
            "energy_kcal_serving": energy, "source_kind": "open_food_facts"}


def _fetch_sintel(root: Path) -> None:
    """@brief 取得官方英文字幕并声明人物没有视觉接触。"""
    content = _download(_SINTEL_URL)
    if content.count(b" --> ") != 26 or b"00:10:29,800" not in content:
        raise IntegrityError("Sintel subtitle timeline is incomplete")
    folder = root / "sintel"
    folder.mkdir(parents=True)
    (folder / "sintel_en.srt").write_bytes(content)
    identity = {"title": "Sintel", "experience": "official_english_subtitle_timeline",
                "visual_experience": False,
                "movie_url": "https://download.blender.org/durian/movies/Sintel.2010.720p.mkv.zip",
                "license": "CC-BY-3.0"}
    _write_json(folder / "media-identity.json", identity)


def _fetch_ami(root: Path) -> None:
    """@brief 取得 AMI 手工标注包并分离人物输入与评估答案。"""
    content = _download(_AMI_URL)
    folder = root / "ami-es2002"
    folder.mkdir(parents=True)
    archive_path = folder / "ami_public_manual_1.6.2.zip"
    archive_path.write_bytes(content)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _normalize_ami(archive, folder)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise IntegrityError("AMI manual annotation archive is invalid") from error


def _normalize_ami(archive: zipfile.ZipFile, folder: Path) -> None:
    """@brief 为 ES2002a-d 形成完整转写和隐藏决定答案。"""
    (folder / "inputs").mkdir()
    (folder / "references").mkdir()
    names = set(archive.namelist())
    for stage in "abcd":
        meeting = f"ES2002{stage}"
        word_paths = [f"words/{meeting}.{speaker}.words.xml" for speaker in "ABCD"]
        summary_path = f"abstractive/{meeting}.abssumm.xml"
        if any(path not in names for path in (*word_paths, summary_path)):
            raise IntegrityError(f"AMI meeting {meeting} annotation is incomplete")
        transcript = _ami_transcript(archive, word_paths)
        summary = archive.read(summary_path)
        (folder / "inputs" / f"{meeting}.txt").write_text(transcript, encoding="utf-8")
        (folder / "references" / f"{meeting}.abssumm.xml").write_bytes(summary)
        _write_json(folder / "references" / f"{meeting}.oracle.json",
                    _ami_oracle(summary, meeting))


def _ami_transcript(archive: zipfile.ZipFile, paths: list[str]) -> str:
    """@brief 按明确说话人形成不含参考答案的连续转写。"""
    sections = []
    for speaker, path in zip("ABCD", paths, strict=True):
        root = ElementTree.fromstring(archive.read(path))
        words = [node.text.strip() for node in root if node.tag.endswith("w") and node.text and node.text.strip()]
        if not words:
            raise IntegrityError(f"AMI word annotation {path} is empty")
        sections.append(f"[Speaker {speaker}]\n{' '.join(words)}")
    return "\n\n".join(sections) + "\n"


def _ami_oracle(summary: bytes, meeting: str) -> dict:
    """@brief 从官方 abstractive 标注提取决定和行动项。"""
    root = ElementTree.fromstring(summary)
    groups = {_local_name(child.tag): child for child in root}
    decisions = _sentences(groups.get("decisions"))
    actions = _sentences(groups.get("actions"))
    if not decisions or not actions:
        raise IntegrityError(f"AMI meeting {meeting} oracle is incomplete")
    return {"source_reference": f"references/{meeting}.abssumm.xml",
            "expected": {"meeting_id": meeting, "decisions": decisions, "action_items": actions},
            "piece_count": len(decisions)}


def _sentences(group: ElementTree.Element | None) -> list[str]:
    """@brief 读取官方摘要分组内非空句子。"""
    if group is None:
        return []
    return ["".join(node.itertext()).strip() for node in group if "".join(node.itertext()).strip()]


def _local_name(tag: str) -> str:
    """@brief 去除 XML 命名空间前缀。"""
    return tag.rsplit("}", 1)[-1]


def _fetch_geodata(root: Path) -> None:
    """@brief 为当前冻结地图的完整地点范围重建真实 walking/driving 路由。"""
    from worldmock.corpora.map_prepare import prepare_map

    frozen_root = Path(__file__).resolve().parents[3] / "corpora"
    prepare_map(root, frozen_root)


def _generate_games(root: Path) -> None:
    """@brief 通过 TextWorld 真实生成并执行八个固定种子游戏。"""
    folder = root / "textworld"
    folder.mkdir(parents=True)
    for seed in range(1201, 1209):
        rooms, objects, length = (2, 3, 3) if seed <= 1204 else (3, 5, 5)
        options = GameOptions()
        options.nb_rooms, options.nb_objects, options.quest_length = rooms, objects, length
        options.seeds = seed
        game = make_game(options)
        path = folder / f"game-{seed}.json"
        game.save(str(path))
        walkthrough = list(game.metadata.get("walkthrough", ()))
        _exercise_game(path, walkthrough)
        _write_json(folder / f"game-{seed}-options.json",
                    {"textworld_version": "1.7.0", "seed": seed, "nb_rooms": rooms,
                     "nb_objects": objects, "quest_length": length, "walkthrough": walkthrough})


def _exercise_game(path: Path, walkthrough: list[str]) -> None:
    """@brief 真实 reset/step 到成功终态，拒绝不可执行生成物。"""
    import textworld

    if not walkthrough:
        raise IntegrityError(f"TextWorld game {path.name} has no walkthrough")
    environment = textworld.start(str(path), _GAME_INFOS)
    try:
        state = environment.reset()
        for command in walkthrough:
            if command not in state.admissible_commands:
                raise IntegrityError(f"TextWorld walkthrough for {path.name} is not admissible")
            state, _, done = environment.step(command)
        if not done or not state.won:
            raise IntegrityError(f"TextWorld walkthrough for {path.name} did not win")
    finally:
        environment.close()


def _copy_scenario_materials(root: Path) -> None:
    """@brief 安装随当前代码版本冻结的目录、邮件和许可材料。"""
    source = Path(__file__).resolve().parents[3] / "corpora"
    for relative in ("LICENSES.md", "scenario-catalog/catalog.json", "scenario-mail/threads.json"):
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise IntegrityError(f"Bundled scenario corpus {relative} is invalid")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _write_manifest(root: Path, retrieved_at: datetime) -> CorpusManifest:
    """@brief 对发布目录内每个普通文件形成完整哈希清单。"""
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    entries = tuple(_manifest_entry(root, path, retrieved_at) for path in paths)
    manifest = CorpusManifest(schema_version=1, corpus_id="life-full-2026-09-15",
                              created_at=retrieved_at,
                              transformer_version="worldmock-life-corpus-1", entries=entries)
    _write_json(root / "manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _manifest_entry(root: Path, path: Path, retrieved_at: datetime) -> CorpusEntry:
    """@brief 根据唯一目录前缀绑定来源、许可和用途。"""
    relative = path.relative_to(root).as_posix()
    source, purpose = _source_for(relative, retrieved_at)
    if relative.startswith("zhangjiang-map/routes/"):
        route_rows = json.loads((root / "zhangjiang-map/routes.json").read_text())["routes"]
        route = next(item for item in route_rows if item["response_path"] == relative)
        declared = route["source"]
        source = CorpusSource(kind="external_geodata", url=declared["url"],
                              retrieved_at=declared["retrieved_at"], license=declared["license"],
                              selector=f"route {route['route_id']}; profile={route['routing_profile']}",
                              fact_cutoff=declared["fact_cutoff"])
    content = path.read_bytes()
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix == ".srt":
        media_type = "application/x-subrip"
    return CorpusEntry(entry_id=relative.replace("/", ":"), path=relative,
                       sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content),
                       media_type=media_type, purpose=purpose, source=source)


def _source_for(relative: str, retrieved_at: datetime) -> tuple[CorpusSource, str]:
    """@brief 为各冻结材料返回无歧义来源描述。"""
    date = retrieved_at.date().isoformat()
    if relative.startswith("open-food-facts/"):
        return CorpusSource(kind="external_dataset", url="https://world.openfoodfacts.org/api/v2/product/",
                            retrieved_at=retrieved_at, license="ODbL-1.0",
                            selector="eight-barcode allowlist", fact_cutoff=date), "food facts"
    if relative.startswith("sintel/"):
        return CorpusSource(kind="external_media", url=_SINTEL_URL, retrieved_at=retrieved_at,
                            license="CC-BY-3.0", selector="official English SRT and movie identity",
                            fact_cutoff=date), "subtitle experience"
    if relative.startswith("ami-es2002/"):
        return CorpusSource(kind="external_dataset", url=_AMI_URL, retrieved_at=retrieved_at,
                            license="CC-BY-4.0", selector="manual 1.6.2 ES2002a-d",
                            fact_cutoff=date), "work input or hidden oracle"
    if relative.startswith("textworld/"):
        return CorpusSource(kind="generated_with_textworld", url="https://github.com/microsoft/TextWorld",
                            retrieved_at=retrieved_at, license="MIT", selector="seeds 1201 through 1208",
                            fact_cutoff=date), "interactive game"
    if relative.startswith("zhangjiang-map/routes/"):
        mode = "walking" if relative.endswith("walking.json") else "driving"
        return CorpusSource(kind="external_geodata", url=_ROUTE_BASES[mode], retrieved_at=retrieved_at,
                            license="ODbL-1.0", selector=f"frozen Zhangjiang route {Path(relative).stem}",
                            fact_cutoff=date), "offline route"
    if relative in {"zhangjiang-map/supplies.json", "zhangjiang-map/supply-assumptions.json"}:
        return CorpusSource(kind="scenario", url="scenario://worldmock/zhangjiang-storefronts",
                            retrieved_at=retrieved_at, license="WorldMock-project",
                            selector="reviewed simulation supply assumptions",
                            fact_cutoff=date), "storefront assumptions"
    if relative in {"zhangjiang-map/dataset.json", "zhangjiang-map/locations.json", "zhangjiang-map/routes.json"}:
        return CorpusSource(kind="derived_geodata", url=None, retrieved_at=retrieved_at,
                            license="ODbL-1.0 AND WorldMock-project",
                            selector="derived map facts and explicit scenario declarations; see per-record sources",
                            fact_cutoff=date), "frozen map declarations"
    if relative.startswith("zhangjiang-map/"):
        return CorpusSource(kind="external_geodata", url=_OSM_URL, retrieved_at=retrieved_at,
                            license="ODbL-1.0", selector="bbox 121.6000,31.1950,121.6400,31.2250",
                            fact_cutoff=date), "map provenance"
    return CorpusSource(kind="scenario", url=None, retrieved_at=None, license="WorldMock-project",
                        selector="complete frozen file", fact_cutoff=date), "scenario-authored material"


def _download_json(url: str) -> dict:
    """@brief 下载并拒绝非 JSON 对象响应。"""
    try:
        payload = json.loads(_download(url))
    except json.JSONDecodeError as error:
        raise IntegrityError(f"Source returned invalid JSON: {url}") from error
    if not isinstance(payload, dict):
        raise IntegrityError(f"Source returned non-object JSON: {url}")
    return payload


def _download(url: str) -> bytes:
    """@brief 对唯一指定 URL 发起有身份的有限时下载。"""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
    except Exception as error:
        raise IntegrityError(f"Source download failed: {url}") from error
    if not content:
        raise IntegrityError(f"Source download was empty: {url}")
    return content


def _write_json(path: Path, value: object, compact: bool = False) -> None:
    """@brief 在未发布 staging 中写稳定 UTF-8 JSON。"""
    separators = (",", ":") if compact else None
    content = json.dumps(value, ensure_ascii=False, indent=None if compact else 2,
                         sort_keys=True, separators=separators) + "\n"
    path.write_text(content, encoding="utf-8")


__all__ = ["prepare_corpora"]
