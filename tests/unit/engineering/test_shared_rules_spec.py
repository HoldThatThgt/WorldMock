"""@brief 安装输入和运行身份使用同一 AS2 源摘要。"""


from __future__ import annotations


import json


from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_frozen_current_as2_identity_agrees_with_installation_and_runtime():
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256
    series = json.loads((ROOT / "patches/agentsociety2/SERIES").read_text(encoding="utf-8"))
    assert series["source_sha256"] == AS2_LOCKED_SOURCE_SHA256
