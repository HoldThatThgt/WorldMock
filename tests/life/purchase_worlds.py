"""指名购买与购买渠道测试共用的隔离世界（docs/dev/SPEC-dayrun-fidelity.md 3.2、3.3、3.5）。

白领场景的一份隔离副本：语料根复制到临时目录（同卷用硬链接，跨卷时复制），可改地点显示名与门店供给（地图文件）、在语料
清单里登记几份手机配置，也可改商品的目录名称（目录清单按真实算法重新钉扎）；每个场景文档钉定其中一份手机配置（或不钉定），
地图、语料清单与场景的钉扎都按真实准入重算，所以渠道经 load_runtime_scenario 的真实加载路径派生。一份语料副本可以写出
几份场景文档（各钉一份手机配置），供同一测试模块的多个用例共用。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import context
from tests.life.routing_fixtures import stage
from worldmock.categories.identity import refresh_manifest_identity
from worldmock.life.models import REQUEST_MODELS

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenarios" / "white-collar-12-weeks.json"
#: 语料根内登记手机配置的目录（每份配置一个文件）。
PHONE_DIR = "scenario-phone"
_MAP = "zhangjiang-map"


@dataclass(frozen=True)
class World:
    """@brief 一份隔离语料副本：根目录、语料根、语料清单与地图声明的新摘要，以及登记的手机配置（名称 → 摘要）。"""

    root: Path
    corpus: Path
    manifest_sha: str
    dataset_sha: str
    phones: dict[str, str] = field(default_factory=dict)

    def scenario(self, name: str, phone: str | None = None, pin: str | None = None) -> Path:
        """@brief 写出一份钉定这份语料的场景文档：phone 给出时钉定那份手机配置（pin 覆盖钉定的摘要，模拟过期钉扎）。"""
        document = json.loads(SCENARIO.read_text(encoding="utf-8"))
        document.update(corpus_root=str(self.corpus), corpus_manifest_sha256=self.manifest_sha)
        document["map_dataset"]["sha256"] = self.dataset_sha
        if phone is not None:
            document.update(phone_simulation_config=f"{PHONE_DIR}/{phone}.json",
                            phone_simulation_config_sha256=pin or self.phones[phone])
        path = self.root / "scenarios" / f"{name}.json"
        path.parent.mkdir(exist_ok=True)
        _write(path, document)
        return path


def phone_config(sellers: dict[str, str], names: dict[str, list[str]]) -> dict:
    """@brief 白领场景的一份手机配置：观看应用 B站（场景有内容，执行证据成立），给出的卖家账户 → App 与 App → 称呼。"""
    document = json.loads(SCENARIO.read_text(encoding="utf-8"))
    return {"schema_version": 1, "scenario_id": document["scenario_id"], "agent_id": 1,
            "timezone": document["timezone"], "activity_apps": {"watch": "bilibili"},
            "purchase_apps_by_seller": sellers, "app_display_names": {"bilibili": ["B站"], **names}}


def build_world(root: Path, phones: dict[str, dict], places: dict[str, str] | None = None,
                supplies: dict[str, dict] | None = None, products: dict[str, str] | None = None) -> World:
    """@brief 复制语料与目录到 root，按给出的改动改写地图与目录、登记手机配置，并重新钉扎。

    :param phones: 手机配置名称 → 配置内容（登记为 scenario-phone/<名称>.json）。
    :param places: 地点身份 → 新显示名。
    :param supplies: 供给身份 → 要改的字段（price_fen 改第一条报价的价格，其余照字段名改）。
    :param products: 商品身份 → 新的目录名称。
    """
    corpus = root / "corpora"
    shutil.copytree(ROOT / "corpora", corpus, copy_function=_link_or_copy)
    dataset = json.loads((corpus / _MAP / "dataset.json").read_text(encoding="utf-8"))
    if places:
        rows = json.loads((corpus / _MAP / "locations.json").read_text(encoding="utf-8"))
        for row in rows["locations"]:
            row["name"] = places.get(row["location_id"], row["name"])
        dataset["locations_file"]["sha256"] = _write(corpus / _MAP / "locations.json", rows)
    if supplies:
        dataset["supplies_file"]["sha256"] = _write(corpus / _MAP / "supplies.json", _edited_supplies(corpus, supplies))
    dataset_sha = _write(corpus / _MAP / "dataset.json", dataset)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        if entry["path"].startswith(f"{_MAP}/") and entry["path"].count("/") == 1:
            path = corpus / entry["path"]
            entry.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
    digests = {name: _register_phone(corpus, manifest, name, config) for name, config in phones.items()}
    manifest_sha = _write(corpus / "manifest.json", manifest)
    shutil.copytree(ROOT / "catalog", root / "catalog")
    if products:
        _rename_products(root / "catalog", products)
    return World(root, corpus, manifest_sha, dataset_sha, digests)


def at_clock(engine, hour: int, minute: int) -> None:
    """@brief 把世界移到场景首日（1 月 5 日）的某个时刻，人物都在那一刻有决策机会（只保留之后的后台事件）。"""
    zone = ZoneInfo(engine.scenario.timezone)
    now = int(datetime(2026, 1, 5, hour, minute, tzinfo=zone).timestamp())

    def change(state):
        """@brief 推到给定时刻。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = now
            agent.next_decision_at = now
    stage(engine, change)


def decide(engine, round_id: str, arguments: dict):
    """@brief 同一次冻结观察先经宿主本地校验，再原样登记给真实引擎并结算：（观察，宿主纠错串，环境结果）。"""
    from custom.agents.life_agent import LifeAgent

    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    host = LifeAgent()
    host.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                                  "data": observed}]))
    host.build_react_tool_schemas(readonly=False)
    error = host.validate_react_decisions([ReactDecision("", "start_activity", arguments, "")], False)
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", "start_activity",
                                        REQUEST_MODELS["start_activity"].model_validate(arguments)))
    [result] = engine.commit_decision_round(round_id)
    return observed, error, result


def _edited_supplies(corpus: Path, edits: dict[str, dict]) -> dict:
    """@brief 地图供给文件按给出的字段改写后的内容。"""
    rows = json.loads((corpus / _MAP / "supplies.json").read_text(encoding="utf-8"))
    for row in rows["supplies"]:
        change = dict(edits.get(row["supply_id"], {}))
        if "price_fen" in change:
            row["quotes"][0]["price_fen"] = change.pop("price_fen")
        row.update(change)
    return rows


def _register_phone(corpus: Path, manifest: dict, name: str, config: dict) -> str:
    """@brief 把一份手机配置写进语料根并登记进语料清单，返回它的 SHA-256。"""
    relative = f"{PHONE_DIR}/{name}.json"
    (corpus / PHONE_DIR).mkdir(exist_ok=True)
    digest = _write(corpus / relative, config)
    manifest["entries"].append({
        "entry_id": f"scenario-phone:{name}", "path": relative, "sha256": digest,
        "size_bytes": (corpus / relative).stat().st_size, "media_type": "application/json",
        "purpose": "phone simulation",
        "source": {"kind": "scenario", "license": "WorldMock-project", "selector": "complete frozen file"}})
    return digest


def _rename_products(catalog: Path, products: dict[str, str]) -> None:
    """@brief 改目录里商品的名称（objects.jsonl 中该身份的各行），再按真实算法刷新目录清单的身份摘要。"""
    path = catalog / "objects.jsonl"
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        row["name"] = products.get(row["object_id"], row["name"])
        lines.append(json.dumps(row, ensure_ascii=False))
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    refresh_manifest_identity(catalog)


def _link_or_copy(source: str, target: str) -> None:
    """@brief 隔离语料副本：同一卷上用硬链接，跨卷时复制（改写前先解除链接，正式语料字节不变）。"""
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _write(path: Path, value: dict) -> str:
    """@brief 解除硬链接后写入 JSON，返回新字节的 SHA-256。"""
    if path.exists():
        path.unlink()
    path.write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    return hashlib.sha256(path.read_bytes()).hexdigest()
