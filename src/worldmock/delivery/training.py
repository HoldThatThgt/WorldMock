"""@brief 使用 Hugging Face Datasets 真实读取并按来源连通组划分训练数据。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import networkx as nx
from datasets import DatasetDict, Features, Json, Value, load_dataset, load_from_disk

from worldmock.errors import IntegrityError
from worldmock.footprints.export import write_jsonl
from worldmock.footprints.models import canonical_json


def load_training(records: list[dict], destination: Path) -> tuple[DatasetDict, dict]:
    """@brief 加载全部合格成品，核对字段守恒并输出明确对话格式。"""
    if not records:
        raise IntegrityError("Training delivery contains no records")
    destination.mkdir(parents=True, exist_ok=False)
    assignments = assign_splits(records)
    groups = {}
    for record, split in zip(records, assignments, strict=True):
        if set(record) != {"task", "input", "target", "provenance"}:
            raise IntegrityError("Training record has fields outside the canonical contract")
        groups.setdefault(split, []).append(record)
    text_rows = {split: [_json_text_row(row) for row in rows] for split, rows in groups.items()}
    descriptors = {split: write_jsonl(destination / f"{split}.jsonl", rows)
                   for split, rows in text_rows.items()}
    features = Features({"task": Value("string"), "input": Json(decode=False),
                         "target": Json(decode=False), "provenance": Json(decode=False)})
    files = {split: str(destination / descriptor["path"]) for split, descriptor in descriptors.items()}
    loaded = load_dataset("json", data_files=files, features=features, cache_dir=str(destination / "cache"))
    for split, rows in groups.items():
        if [_decode_json_record(row) for row in loaded[split]] != rows:
            raise IntegrityError("Dataset loader changed canonical training records")
    conversations = DatasetDict({split: dataset.map(_conversation) for split, dataset in loaded.items()})
    conversations.save_to_disk(str(destination / "dataset"))
    restored = load_from_disk(str(destination / "dataset"))
    for split in conversations:
        if list(restored[split]) != list(conversations[split]):
            raise IntegrityError("Saved DatasetDict changed the verified conversation records")
    receipt = {"loader": "datasets.load_dataset", "version": "5.0.1", "rows": len(records),
               "splits": {split: len(dataset) for split, dataset in conversations.items()},
               "features": {split: dataset.features.to_dict() for split, dataset in conversations.items()},
               "artifacts": descriptors, "source_group_disjoint": True, "saved_dataset_reloaded": True,
               "json_columns": {"encoding": "json", "automatic_decoding": False,
                                "verified_decoder": "json.loads"}}
    return restored, receipt


def _conversation(record: dict) -> dict:
    """@brief 训练时使用允许看到的输入与独立目标，不把答案放入提示。"""
    return {"prompt": [{"role": "user", "content": record["input"]}],
            "completion": [{"role": "assistant", "content": record["target"]}]}


def _json_text_row(record: dict) -> dict:
    """@brief 三个 JSON 列以文本落盘，避免加载器对对象列按 15 位有效数字重写浮点。"""
    return {"task": record["task"], "input": canonical_json(record["input"]).decode(),
            "target": canonical_json(record["target"]).decode(),
            "provenance": canonical_json(record["provenance"]).decode()}


def _decode_json_record(record: dict) -> dict:
    """@brief 用标准 JSON 解码核对原值，避免默认浮点解码改变规范记录。"""
    return {name: value if name == "task" else json.loads(value) for name, value in record.items()}


def assign_splits(records: list[dict]) -> list[str]:
    """@brief 共享运行、事件或实际素材的记录属于同一连通组，拒绝逐行随机泄漏。"""
    graph = nx.Graph()
    for index, record in enumerate(records):
        for identity in _source_identities(record):
            graph.add_edge(("record", index), ("source", identity))
    assignments = {}
    for component in nx.connected_components(graph):
        identities = sorted(value for kind, value in component if kind == "source")
        digest = hashlib.sha256(canonical_json(identities)).hexdigest()
        bucket = int(digest[:8], 16) % 10
        split = "validation" if bucket == 8 else "test" if bucket == 9 else "train"
        assignments.update({value: split for kind, value in component if kind == "record"})
    return [assignments[index] for index in range(len(records))]


def _source_identities(record: dict) -> list[str]:
    """@brief 使用运行、原事件与实际接触素材身份连接派生数据。"""
    provenance = record["provenance"]
    identities = [f"group:{provenance['source_group_id']}", f"run:{provenance['run_id']}"]
    identities.extend(f"event:{identity}" for identity in provenance["source_event_ids"])
    for event in record["input"]["events"]:
        for material in event["materials"]:
            identities.append(f"asset:{material['reference']['sha256']}")
    return identities
