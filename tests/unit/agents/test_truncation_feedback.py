"""截断纠错的固定正文由错误结构直接生成：导入人物适配器不写「输出被截断」的错误日志，正文与真实截断回合的纠错逐字相同。"""

import importlib
import json
import logging

from worldmock.agents.protocol import life_truncation
from worldmock.agents.protocol.life_feedback import FEEDBACK_INSTRUCTION, feedback_content, report_content
from worldmock.state.interaction.errors import ErrorItem, host_report


def test_reimporting_the_truncation_module_writes_no_error_log_and_keeps_the_fixed_content(caplog):
    """重新执行模块导入期间没有任何错误日志；固定正文等于真实截断纠错编出的正文。"""
    before = life_truncation.TRUNCATION_CONTENT
    with caplog.at_level(logging.ERROR):
        module = importlib.reload(life_truncation)
    assert caplog.records == []
    assert module.TRUNCATION_CONTENT == before
    with caplog.at_level(logging.ERROR):
        feedback = module.truncation_feedback()
    assert module.TRUNCATION_CONTENT == feedback_content(feedback)
    assert [record.getMessage() for record in caplog.records] == [
        f"Life tool protocol rejected: OUTPUT_TRUNCATED: {module.TRUNCATION_DETAIL}"]


def test_report_content_carries_the_five_error_fields_and_the_instruction():
    """由错误结构编出的正文能解析出五个错误字段加固定的修正指示。"""
    item = ErrorItem(("tool_calls",), None, "OUTPUT_TRUNCATED", why="probe")
    content = json.loads(report_content(host_report("OUTPUT_TRUNCATED", items=(item,))))
    assert set(content) == {"error_code", "reason", "fix", "items", "unmet", "instruction"}
    assert content["error_code"] == "OUTPUT_TRUNCATED"
    assert content["instruction"] == FEEDBACK_INSTRUCTION
    assert content["items"][0]["why"] == "probe"
