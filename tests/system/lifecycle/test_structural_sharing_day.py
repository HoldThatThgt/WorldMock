"""结构共享的永久对照：共享条目的世界副本与完整深复制录下的脚本化完整日逐项相同。

对照在同一台机器、同一份代码上录两遍，所以不依赖冻结基线，任何平台都成立。
"""

from pathlib import Path

import pytest
from pydantic import BaseModel

from tests.life.day_baseline_runtime import record_complete_day
from worldmock.life.models import WorldState

PROJECT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("kind", ["activity", "callback"])
def test_shared_entry_copies_record_the_same_day_as_full_deep_copies(monkeypatch, tmp_path, kind):
    """先用共享条目的世界副本录一天，再换回 pydantic 自己的完整深复制录同一天：人物与配置摘要、环境身份、种子和整份
    签名（全部导出工件含每条观察的事件流、每个检查点正文、每个运行素材）都相同。"""
    shared = record_complete_day(monkeypatch, tmp_path / "shared" / kind, PROJECT, kind)
    monkeypatch.setattr(WorldState, "__deepcopy__", BaseModel.__deepcopy__)
    deep = record_complete_day(monkeypatch, tmp_path / "deep" / kind, PROJECT, kind)
    assert shared == deep
