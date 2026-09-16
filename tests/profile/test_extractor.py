"""@brief 提取层：requests 打桩的固定假响应序列——共识/中位数/标黄/重试，不真实调 API。"""

from __future__ import annotations

import json

import pytest

from worldmock.profile import extractor
from worldmock.profile.extractor import (
    AGREEMENT_WARNING_THRESHOLD,
    ExtractionError,
    extract_big_five,
    resolve_api_key,
)


class FakeResponse:
    """@brief 固定假响应：可脚本化状态码与内容序列。"""

    def __init__(self, status_code=200, content='{"openness": 4}'):
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


def scores_payload(**scores):
    defaults = {"openness": 4, "conscientiousness": 4, "extraversion": 4,
                "agreeableness": 4, "neuroticism": 4}
    return json.dumps({**defaults, **scores}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch, tmp_path):
    """@brief 测试不依赖真实凭据：env 清空、凭据文件指向不存在路径。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", tmp_path / "absent.env")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")


def test_extract_median_and_agreement_from_fixed_sequence():
    """@brief 5 次运行：中位数取整 + agreement=各维度众数占比均值。"""
    sequence = [
        scores_payload(openness=2, conscientiousness=2, extraversion=2, agreeableness=2, neuroticism=2),
        scores_payload(openness=4, conscientiousness=4, extraversion=4, agreeableness=4, neuroticism=4),
        scores_payload(openness=4, conscientiousness=6, extraversion=4, agreeableness=4, neuroticism=4),
        scores_payload(openness=6, conscientiousness=6, extraversion=6, agreeableness=6, neuroticism=6),
        scores_payload(openness=6, conscientiousness=6, extraversion=6, agreeableness=6, neuroticism=6),
    ]
    session = FakeSession([FakeResponse(content=item) for item in sequence])
    result = extract_big_five("画像", n_runs=5, session=session)
    # openness [2,4,4,6,6] → median 4, mode share 2/5；conscientiousness [2,4,6,6,6] → 6, 3/5
    assert result.profile.openness == 4
    assert result.profile.conscientiousness == 6
    assert result.per_dimension["openness"]["mode_share"] == pytest.approx(0.4)
    assert result.per_dimension["conscientiousness"]["mode_share"] == pytest.approx(0.6)
    # extraversion [2,4,4,6,6] → 0.4；agreeableness 同；neuroticism 同 → mean = (0.4+0.6+0.4*3)/5
    expected_agreement = (0.4 + 0.6 + 0.4 + 0.4 + 0.4) / 5
    assert result.agreement == pytest.approx(expected_agreement, abs=1e-3)
    assert result.runs == 5 and result.model == extractor.DEFAULT_MODEL
    assert len(session.calls) == 5


def test_extract_even_run_median_rounds_half_up():
    sequence = [
        scores_payload(openness=2, neuroticism=2),
        scores_payload(openness=2, neuroticism=3),
        scores_payload(openness=4, neuroticism=2),
        scores_payload(openness=6, neuroticism=2),
    ]
    session = FakeSession([FakeResponse(content=item) for item in sequence])
    result = extract_big_five("画像", n_runs=4, session=session)
    # openness [2,2,4,6] → (2+4)/2=3；neuroticism [2,2,2,3] → median 2
    assert result.profile.openness == 3
    assert result.profile.neuroticism == 2


def test_extract_flags_warning_when_mode_share_below_threshold():
    sequence = [
        scores_payload(openness=1), scores_payload(openness=2), scores_payload(openness=3),
        scores_payload(openness=4), scores_payload(openness=5),
    ]
    session = FakeSession([FakeResponse(content=item) for item in sequence])
    result = extract_big_five("画像", n_runs=5, session=session)
    assert result.per_dimension["openness"]["mode_share"] == pytest.approx(0.2)
    assert result.per_dimension["openness"]["mode_share"] < AGREEMENT_WARNING_THRESHOLD
    assert result.warning is True


def test_extract_no_warning_on_unanimous_consensus():
    session = FakeSession([FakeResponse(content=scores_payload(openness=7))] * 5)
    result = extract_big_five("画像", n_runs=5, session=session)
    assert result.agreement == 1.0 and result.warning is False
    assert result.profile.openness == 7


def test_extract_retries_bad_json_then_succeeds():
    session = FakeSession([
        FakeResponse(content="not json at all"),
        FakeResponse(content=scores_payload(openness=5)),
    ])
    result = extract_big_five("画像", n_runs=1, session=session)
    assert result.profile.openness == 5
    assert len(session.calls) == 2


def test_extract_fails_after_max_retries():
    session = FakeSession([FakeResponse(content="garbage")] * 3)
    with pytest.raises(ExtractionError, match="after 3 attempts"):
        extract_big_five("画像", n_runs=1, session=session)
    assert len(session.calls) == 3


def test_extract_retries_out_of_range_scores():
    session = FakeSession([
        FakeResponse(content=scores_payload(openness=9)),
        FakeResponse(content=scores_payload(openness=3)),
    ])
    result = extract_big_five("画像", n_runs=1, session=session)
    assert result.profile.openness == 3


def test_extract_retries_http_error_then_gives_up():
    session = FakeSession([FakeResponse(status_code=429, content="rate limited")] * 3)
    with pytest.raises(ExtractionError, match="HTTP 429"):
        extract_big_five("画像", n_runs=1, session=session)


def test_extract_rejects_empty_text_and_bad_runs():
    with pytest.raises(ExtractionError, match="empty"):
        extract_big_five("   ", n_runs=1, session=FakeSession([]))
    with pytest.raises(ExtractionError, match="n_runs"):
        extract_big_five("画像", n_runs=0, session=FakeSession([]))


def test_extract_payload_shape():
    """@brief 请求载荷：温度 0.3、json_object 输出、system+user 双消息。"""
    session = FakeSession([FakeResponse(content=scores_payload())])
    extract_big_five("某画像文本", n_runs=1, session=session)
    call = session.calls[0]
    assert call["url"] == extractor.API_URL
    payload = call["json"]
    assert payload["temperature"] == 0.3
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert "某画像文本" in payload["messages"][1]["content"]
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_resolve_api_key_priority(monkeypatch, tmp_path):
    assert resolve_api_key("explicit") == "explicit"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert resolve_api_key() == "from-env"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    credentials = tmp_path / "deepseek.env"
    credentials.write_text("export DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", credentials)
    assert resolve_api_key() == "from-file"


def test_resolve_api_key_missing_everywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", tmp_path / "absent.env")
    with pytest.raises(ExtractionError, match="DEEPSEEK_API_KEY"):
        resolve_api_key()
