"""@brief 提取层：requests 打桩的固定假响应序列——三次一致性/缺省补齐/重试，不真实调 API。"""

from __future__ import annotations

import json

import pytest

from worldmock.profile import extractor
from tests.profile.conftest import make_payload


class FakeResponse:
    """@brief 固定假响应：可脚本化状态码与内容序列。"""

    def __init__(self, status_code=200, content="{}"):
        self.status_code = status_code
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class FakeSession:
    """@brief 顺序弹出脚本化响应；记录每次请求载荷供断言。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self.responses:
            raise AssertionError("FakeSession ran out of scripted responses")
        item = self.responses.pop(0)
        return item() if callable(item) else item


def variant_payload(**overrides):
    """@brief 深拷贝覆盖的载荷 JSON 文本。"""
    return make_payload(**overrides)


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch, tmp_path):
    """@brief 测试不依赖真实凭据。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", tmp_path / "absent.env")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")


def test_three_run_consensus_picks_field_modes():
    """@brief 三次一致性：两 run 一致的字段胜出，第三 run 的漂移被丢弃。"""
    majority_coping = variant_payload(coping="拼高达模型")
    drift = variant_payload(coping="去江边散步")
    session = FakeSession([FakeResponse(content=make_payload()),
                           FakeResponse(content=majority_coping),
                           FakeResponse(content=drift)])
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert result.card.coping == "拼高达模型"
    assert result.per_field["coping"] == pytest.approx(2 / 3, abs=1e-3)
    assert result.runs == 3
    assert len(session.calls) == 3


def test_unanimous_consensus_no_warning():
    session = FakeSession([FakeResponse(content=make_payload())] * 3)
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert result.agreement == 1.0 and result.warning is False
    assert all(share == 1.0 for share in result.per_field.values())


def test_low_agreement_field_flags_warning():
    """@brief 某字段三次三个值 -> 众数占比 1/3 < 2/3 -> 标黄。"""
    script = [
        variant_payload(coping="拼高达模型"),
        variant_payload(coping="去江边散步"),
        variant_payload(coping="听老歌"),
    ]
    session = FakeSession([FakeResponse(content=item) for item in script])
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert result.per_field["coping"] == pytest.approx(1 / 3, abs=1e-3)
    assert result.warning is True


def test_mode_tie_breaks_by_lexicographically_smallest_serialization():
    """@brief 平手（三值各一次）取 canonical 序列化字典序最小：确定性。"""
    script = [
        variant_payload(sleep_need_hours=8.0),
        variant_payload(sleep_need_hours=7.5),
        variant_payload(sleep_need_hours=8.5),
    ]
    session = FakeSession([FakeResponse(content=item) for item in script])
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    # canonical 序列化 "7.5" < "8.0" < "8.5"（同类型数值按文本序列化比较）。
    assert result.card.sleep_need_hours == 7.5


def test_big_five_median_across_runs():
    """@brief 大五逐维度中位数：[3,5,5] -> 5；[2,4,6] 偶数中位数不存在于样本。"""
    def scores(openness):
        payload = json.loads(make_payload())
        payload["big_five"]["openness"] = openness
        return json.dumps(payload, ensure_ascii=False)
    session = FakeSession([FakeResponse(content=scores(3)),
                           FakeResponse(content=scores(5)),
                           FakeResponse(content=scores(5))])
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert result.big_five.openness == 5
    assert result.big_five.extraversion == 6


def test_null_anchors_and_defaults_fall_back_to_toml():
    """@brief 故事未提及时输出 null -> TOML [defaults] 确定性补齐。"""
    payload = json.loads(make_payload())
    payload["chronotype_midsleep"] = None
    payload["sleep_need_hours"] = None
    payload["time_anchors"]["bedtime"] = None
    session = FakeSession([FakeResponse(content=json.dumps(payload, ensure_ascii=False))] * 3)
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert result.card.chronotype_midsleep == 4.0
    assert result.card.sleep_need_hours == 7.5
    assert (result.card.time_anchors.bedtime.start_hour,
            result.card.time_anchors.bedtime.end_hour) == (23.0, 24.0)


def test_cross_midnight_bedtime_window_parsed():
    payload = json.loads(make_payload())
    payload["time_anchors"]["bedtime"] = "23:30-01:00"
    session = FakeSession([FakeResponse(content=json.dumps(payload, ensure_ascii=False))] * 3)
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert result.card.time_anchors.bedtime.end_hour == 25.0


def test_relationships_and_time_anchors_extracted_into_card():
    """@brief 关系与时间锚点抽取：进入结构化卡片字段。"""
    session = FakeSession([FakeResponse(content=make_payload())] * 3)
    card = extractor.extract_persona("故事", n_runs=3, session=session).card
    assert card.identity.age == 32 and card.identity.occupation == "产品经理"
    assert card.relationships[0].name_or_appellation == "母亲"
    assert card.relationships[0].intimacy == 25
    assert card.relationships[0].channels == ("电话", "微信")
    assert (card.time_anchors.wake.start_hour, card.time_anchors.wake.end_hour) == (7.0, 7.5)
    assert (card.time_anchors.lunch.start_hour, card.time_anchors.lunch.end_hour) == (12.0, 13.0)


def test_retries_bad_json_then_succeeds():
    session = FakeSession([
        FakeResponse(content="not json at all"),
        FakeResponse(content=make_payload()),
    ])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert result.card.coping == "拼高达模型"
    assert len(session.calls) == 2


def test_fails_after_max_retries():
    session = FakeSession([FakeResponse(content="garbage")] * 3)
    with pytest.raises(extractor.ExtractionError, match="after 3 attempts"):
        extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 3


def test_retries_structurally_invalid_payload():
    """@brief 缺顶层键/越界值的载荷触发重试（结构校验走卡片模型）。"""
    broken = json.loads(make_payload())
    del broken["tastes"]
    session = FakeSession([
        FakeResponse(content=json.dumps(broken, ensure_ascii=False)),
        FakeResponse(content=make_payload()),
    ])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert result.card.tastes.content_genres == ("悬疑", "纪录片")


def test_rejects_empty_story_and_bad_runs():
    with pytest.raises(extractor.ExtractionError, match="empty"):
        extractor.extract_persona("   ", n_runs=1, session=FakeSession([]))
    with pytest.raises(extractor.ExtractionError, match="n_runs"):
        extractor.extract_persona("故事", n_runs=0, session=FakeSession([]))


def test_request_payload_shape():
    """@brief 请求载荷：温度 0.3、json_object 输出、system+user 双消息。"""
    session = FakeSession([FakeResponse(content=make_payload())])
    extractor.extract_persona("某段背景故事", n_runs=1, session=session)
    call = session.calls[0]
    assert call["url"] == extractor.API_URL
    payload = call["json"]
    assert payload["temperature"] == 0.3
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert "某段背景故事" in payload["messages"][1]["content"]
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_resolve_api_key_priority(monkeypatch, tmp_path):
    assert extractor.resolve_api_key("explicit") == "explicit"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert extractor.resolve_api_key() == "from-env"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    credentials = tmp_path / "deepseek.env"
    credentials.write_text("export DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", credentials)
    assert extractor.resolve_api_key() == "from-file"


def test_resolve_api_key_missing_everywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", tmp_path / "absent.env")
    with pytest.raises(extractor.ExtractionError, match="DEEPSEEK_API_KEY"):
        extractor.resolve_api_key()
