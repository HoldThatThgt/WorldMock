"""@brief 提取层：requests 打桩的固定假响应序列——三次一致性/缺省补齐/重试，不真实调 API。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from worldmock.profile import extractor
from worldmock.profile.compile import compile_persona
from worldmock.profile.mapper import load_mapping_config
from worldmock.profile.textgen import render_card
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
    monkeypatch.delenv("DEEPSEEK_NODE_KEYS", raising=False)
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", tmp_path / "absent.env")
    monkeypatch.setenv("DEEPSEEK_NODE_KEYS", "test-key")


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


def test_extra_explanation_field_does_not_change_persona_payload():
    """@brief 忽略模型额外说明字段，必需的画像字段仍逐一校验。"""
    payload = json.loads(make_payload())
    payload["time_anchors_note"] = "source explanation"
    session = FakeSession([FakeResponse(content=json.dumps(payload, ensure_ascii=False))])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert result.runs == 1


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


def test_retry_explains_invalid_overnight_bedtime_without_accepting_it():
    """@brief 整夜睡眠误填就寝窗口必须拒绝，并把具体错误反馈给下一次真实请求。"""
    broken = json.loads(make_payload())
    broken["time_anchors"]["bedtime"] = "00:00-08:00"
    fixed = json.loads(make_payload())
    fixed["time_anchors"]["bedtime"] = "23:30-00:30"
    session = FakeSession([FakeResponse(content=json.dumps(broken, ensure_ascii=False)),
                           FakeResponse(content=json.dumps(fixed, ensure_ascii=False))])
    result = extractor.extract_persona("约零点十分就寝，需要八小时睡眠", n_runs=1, session=session)
    assert result.card.time_anchors.bedtime.start_hour == 23.5
    assert result.card.time_anchors.bedtime.end_hour == 24.5
    first, retry = (call["json"]["messages"] for call in session.calls)
    assert len(first) == 2 and len(retry) == 3
    assert retry[:2] == first
    assert "time anchors out of order" in retry[-1]["content"]
    assert "不能写" in retry[0]["content"] and "00:00-08:00" in retry[0]["content"]


def test_rejects_empty_story_and_bad_runs():
    with pytest.raises(extractor.ExtractionError, match="empty"):
        extractor.extract_persona("   ", n_runs=1, session=FakeSession([]))
    with pytest.raises(extractor.ExtractionError, match="n_runs"):
        extractor.extract_persona("故事", n_runs=0, session=FakeSession([]))


def test_request_payload_shape():
    """@brief 请求载荷：发到与运行相同的 strict 端点（/beta），温度 0.3、json_object 输出、system+user 双消息、思考关闭。"""
    session = FakeSession([FakeResponse(content=make_payload())])
    extractor.extract_persona("某段背景故事", n_runs=1, session=session)
    call = session.calls[0]
    assert call["url"] == extractor.API_URL == "https://api.deepseek.com/beta/chat/completions"
    payload = call["json"]
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == extractor.MAX_COMPLETION_TOKENS == 8000
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert "某段背景故事" in payload["messages"][1]["content"]
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_none_content_is_invalid_payload_and_retries():
    """@brief content=None（reasoning 烧穿补全预算）归类无效 payload：ValueError 走既有重试，不裸 TypeError。"""
    session = FakeSession([
        FakeResponse(content=None),
        FakeResponse(content=make_payload()),
    ])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 2
    assert result.card.coping


def test_empty_content_is_invalid_payload_and_retries():
    """@brief 空串/纯空白正文同型归类无效 payload 并在重试预算内恢复。"""
    session = FakeSession([
        FakeResponse(content=""),
        FakeResponse(content="   "),
        FakeResponse(content=make_payload()),
    ])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 3
    assert result.card.coping


def test_all_attempts_none_content_raise_extraction_error():
    """@brief 持续 content=None -> 明确 ExtractionError 且错误类型为 ValueError 口径。"""
    session = FakeSession([FakeResponse(content=None)] * 3)
    with pytest.raises(extractor.ExtractionError,
                       match="empty or non-string model content"):
        extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 3


def test_resolve_api_key_priority(monkeypatch, tmp_path):
    assert extractor.resolve_api_key("explicit") == "explicit"
    monkeypatch.setenv("DEEPSEEK_NODE_KEYS", "from-env")
    assert extractor.resolve_api_key() == "from-env"
    monkeypatch.setenv("DEEPSEEK_NODE_KEYS", "pool-1, pool-2")
    assert extractor.resolve_api_key() == "pool-1"
    monkeypatch.delenv("DEEPSEEK_NODE_KEYS")
    credentials = tmp_path / "deepseek.env"
    credentials.write_text("DEEPSEEK_NODE_KEYS=file-pool-1,file-pool-2\n", encoding="utf-8")
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", credentials)
    assert extractor.resolve_api_key() == "file-pool-1"


def test_resolve_api_key_missing_everywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_NODE_KEYS", raising=False)
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", tmp_path / "absent.env")
    with pytest.raises(extractor.ExtractionError, match="DEEPSEEK_NODE_KEYS"):
        extractor.resolve_api_key()


def test_null_habit_time_window_is_invalid_payload_and_retries():
    """@brief habits.time_window=null 是无效 payload：走既有重试路径，不炸 AttributeError。"""
    payload = json.loads(make_payload())
    payload["habits"][0]["time_window"] = None
    session = FakeSession([
        FakeResponse(content=json.dumps(payload, ensure_ascii=False)),
        FakeResponse(content=make_payload()),
    ])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 2
    assert result.card.habits[0].time_window.start_hour == 19.0


def test_all_attempts_null_habit_time_window_raise_extraction_error():
    """@brief 持续 null 窗口 -> 明确 ExtractionError（不给 null 编默认窗口）。"""
    payload = json.loads(make_payload())
    payload["habits"][0]["time_window"] = None
    session = FakeSession([FakeResponse(
        content=json.dumps(payload, ensure_ascii=False))] * 3)
    with pytest.raises(extractor.ExtractionError, match="invalid time range"):
        extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 3


def test_non_string_habit_time_window_is_invalid_payload():
    """@brief 非字符串窗口（数组）同型归类无效 payload 并重试。"""
    payload = json.loads(make_payload())
    payload["habits"][1]["time_window"] = ["22:30", "23:00"]
    session = FakeSession([
        FakeResponse(content=json.dumps(payload, ensure_ascii=False)),
        FakeResponse(content=make_payload()),
    ])
    extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 2


def test_null_big_five_dimension_is_invalid_payload_and_retries():
    """@brief big_five null 维度在 _parse_payload 即拒（否则穿透到共识中位数排序处 TypeError）。"""
    payload = json.loads(make_payload())
    payload["big_five"]["openness"] = None
    session = FakeSession([
        FakeResponse(content=json.dumps(payload, ensure_ascii=False)),
        FakeResponse(content=make_payload()),
    ])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 2
    assert result.big_five.openness == 5


def _work(leave_work, arrive_home, **overrides) -> str:
    """@brief 带给定 work_anchors（下班、到家）的完整载荷文本；其余字段取工厂值（出门 8 点、就寝 23 点）。"""
    return make_payload(work_anchors={"leave_work": leave_work, "arrive_home": arrive_home}, **overrides)


#: 下班 7 点早于出门 8 点、到家 23 点半晚于就寝 23 点：两个锚点在共识后都被降级时的说明（按核对顺序）。
_BOTH_DEMOTED = ("work_anchor_demoted: leave_work start 7 is outside leave_home 8..bedtime 23",
                 "work_anchor_demoted: arrive_home start 23.5 is outside leave_home 8..bedtime 23")
#: 载荷里不出现 work_anchors 键的参数化标记。
_ABSENT = "absent"


def test_stated_work_anchors_are_extracted_into_the_card():
    """@brief 故事写明固定的下班与到家时刻：抽取提示要求只在写明时填写、不推测；三次一致的 work_anchors 落进卡片的两个
    窗口，单独占 per_field 一项，没有降级说明。"""
    session = FakeSession([FakeResponse(content=_work("18:00-18:30", "19:00-19:30"))] * 3)
    result = extractor.extract_persona("每天六点下班，七点到家", n_runs=3, session=session)
    anchors = result.card.time_anchors
    assert (anchors.leave_work.start_hour, anchors.leave_work.end_hour) == (18.0, 18.5)
    assert (anchors.arrive_home.start_hour, anchors.arrive_home.end_hour) == (19.0, 19.5)
    assert result.per_field["work_anchors"] == 1.0 and result.demotion_notes == ()
    prompt = session.calls[0]["json"]["messages"][0]["content"]
    assert "故事写明固定的下班或到家时刻才填" in prompt
    assert "没有固定工作地点、时间不固定、落在次日或未提及写 null，不要推测" in prompt


def test_unmentioned_work_anchors_stay_null_without_toml_defaults():
    """@brief 故事未提及下班与到家：两键为 null 就保持 null，不读映射表缺省（映射表本就没有这两个锚点）；同一载荷里为 null
    的五个锚点照旧取 TOML 缺省。"""
    payload = json.loads(make_payload())
    payload["time_anchors"] = dict.fromkeys(payload["time_anchors"])
    session = FakeSession([FakeResponse(content=json.dumps(payload, ensure_ascii=False))] * 3)
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    anchors = result.card.time_anchors
    assert (anchors.leave_work, anchors.arrive_home) == (None, None)
    assert (anchors.leave_home.start_hour, anchors.bedtime.start_hour) == (8.0, 23.0)
    assert result.demotion_notes == ()
    assert set(load_mapping_config()["defaults"]["time_anchors"]) == {"wake", "leave_home", "lunch", "dinner",
                                                                      "bedtime"}


def test_wavering_leave_work_does_not_change_the_five_anchors():
    """@brief 三次采样的下班各不相同而 time_anchors 一致：work_anchors 单独取共识（众数占比 1/3，平手取字典序最小的序列化），
    五个锚点的值与全票一致时相同，time_anchors 的占比仍为 1。"""
    script = [_work(leave_work, None) for leave_work in ("18:30-19:00", "18:00-18:30", "19:00-19:30")]
    result = extractor.extract_persona("故事", n_runs=3, session=FakeSession(
        [FakeResponse(content=item) for item in script]))
    unanimous = extractor.extract_persona("故事", n_runs=3, session=FakeSession(
        [FakeResponse(content=make_payload())] * 3))
    five = ("wake", "leave_home", "lunch", "dinner", "bedtime")
    assert [getattr(result.card.time_anchors, name) for name in five] == [
        getattr(unanimous.card.time_anchors, name) for name in five]
    assert result.per_field["time_anchors"] == 1.0
    assert result.per_field["work_anchors"] == pytest.approx(1 / 3, abs=1e-3)
    assert result.card.time_anchors.leave_work.start_hour == 18.0


def test_out_of_order_sample_is_admitted_without_a_parse_retry():
    """@brief 单次采样的下班早于出门：只为放行校验而降级，不消耗解析重试（会话只被调用一次），卡片照常编译并带一条说明。"""
    session = FakeSession([FakeResponse(content=_work("07:00-07:30", None))])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 1
    assert result.card.time_anchors.leave_work is None
    assert result.demotion_notes == _BOTH_DEMOTED[:1]


def test_out_of_order_consensus_demotes_leave_work_then_arrive_home_and_still_compiles():
    """@brief 共识组合乱序（下班早于出门、到家晚于就寝）：先降下班，再对出门与就寝核到家并降级，卡片照常编译；说明只在共识
    后的最终卡片上生成，每个被置 null 的锚点一条（三次采样不各留一份），三次采样都不触发重试。"""
    session = FakeSession([FakeResponse(content=_work("07:00-07:30", "23:30-24:00"))] * 3)
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert len(session.calls) == 3
    anchors = result.card.time_anchors
    assert (anchors.leave_work, anchors.arrive_home) == (None, None)
    assert (anchors.leave_home.start_hour, anchors.bedtime.start_hour) == (8.0, 23.0)
    assert result.demotion_notes == _BOTH_DEMOTED


def test_samples_out_of_order_on_their_own_but_legal_in_consensus_are_not_demoted():
    """@brief 三次采样各自乱序而共识合法时不降级：前两次的下班晚于就寝，第三次出门改到 10 点而下班 9 点；单次采样只为放行
    而降级、不写回载荷，共识对原始 work_anchors 取众数（三值平手取字典序最小的「09:00-09:30」），time_anchors 取前两次的
    出门 8 点，组合合法，没有降级说明、没有重试。"""
    leave_later = json.loads(make_payload())["time_anchors"] | {"leave_home": "10:00-10:30"}
    script = [_work("23:30-24:00", None), _work("23:30-00:30", None),
              _work("09:00-09:30", None, time_anchors=leave_later)]
    session = FakeSession([FakeResponse(content=item) for item in script])
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert len(session.calls) == 3
    assert result.demotion_notes == ()
    assert result.card.time_anchors.leave_work.start_hour == 9.0
    assert result.card.time_anchors.leave_home.start_hour == 8.0
    assert result.per_field["work_anchors"] == pytest.approx(1 / 3, abs=1e-3)


@pytest.mark.parametrize("work_anchors", [
    pytest.param(_ABSENT, id="missing_key"),
    pytest.param({"leave_work": None}, id="missing_sub_key"),
    pytest.param({"leave_work": None, "arrive_home": None, "commute": None}, id="extra_sub_key"),
    pytest.param({"leave_work": "傍晚六点", "arrive_home": None}, id="not_a_clock_range"),
    pytest.param(None, id="null_instead_of_object"),
    pytest.param(["18:00-18:30", None], id="array_instead_of_object"),
])
def test_malformed_or_missing_work_anchors_take_the_parse_retry(work_anchors):
    """@brief 格式错误照常走解析重试：缺 work_anchors 键、缺子键、多子键、不是 HH:MM-HH:MM 的值、不是对象；第一次响应被拒并把
    校验错误附回，第二次合法响应被采用。"""
    broken = json.loads(make_payload())
    if work_anchors == _ABSENT:
        del broken["work_anchors"]
    else:
        broken["work_anchors"] = work_anchors
    session = FakeSession([FakeResponse(content=json.dumps(broken, ensure_ascii=False)),
                           FakeResponse(content=_work("18:00-18:30", None))])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 2
    assert len(session.calls[1]["json"]["messages"]) == 3
    assert result.card.time_anchors.leave_work.start_hour == 18.0


def test_null_work_anchors_keep_the_after_midnight_bedtime_normalised():
    """@brief 两个工作锚点为 null 时凌晨一点就寝（01:00-02:00）照样回绕归一为次日 25 点：新锚点不进回绕归一的名字元组，
    null 不会让五锚点的归一整体放弃。"""
    payload = json.loads(make_payload())
    payload["time_anchors"]["bedtime"] = "01:00-02:00"
    session = FakeSession([FakeResponse(content=json.dumps(payload, ensure_ascii=False))])
    result = extractor.extract_persona("凌晨一点才睡", n_runs=1, session=session)
    bedtime = result.card.time_anchors.bedtime
    assert (bedtime.start_hour, bedtime.end_hour) == (25.0, 26.0)
    assert (result.card.time_anchors.leave_work, result.card.time_anchors.arrive_home) == (None, None)
    assert len(session.calls) == 1


def test_a_work_anchor_past_midnight_is_demoted_even_before_a_later_bedtime():
    """@brief 首版不支持跨午夜的下班与到家（规格 4.1）：就寝「00:30-01:30」回绕为次日 24.5 点，写明的到家「23:50-00:30」
    吸附为 24–24.5 点、起点不晚于就寝，仍因越过当日午夜在共识后被置 null 并留一条说明，同日的下班照常落进卡片；单次
    采样只为放行而降级，不消耗解析重试，卡片文本不出现「到家」分句。"""
    payload = json.loads(_work("18:00-18:30", "23:50-00:30"))
    payload["time_anchors"]["bedtime"] = "00:30-01:30"
    session = FakeSession([FakeResponse(content=json.dumps(payload, ensure_ascii=False))] * 3)
    result = extractor.extract_persona("六点下班，快零点才到家，凌晨才睡", n_runs=3, session=session)
    anchors = result.card.time_anchors
    assert (anchors.bedtime.start_hour, anchors.leave_work.start_hour, anchors.arrive_home) == (24.5, 18.0, None)
    assert result.demotion_notes == ("work_anchor_demoted: arrive_home window 24..24.5 runs past midnight",)
    assert len(session.calls) == 3
    text = render_card(result.card)
    assert "；下班18点到18点半。" in text and "；到家" not in text


def test_a_work_anchor_past_midnight_is_demoted_before_an_ordinary_bedtime():
    """@brief 首版不支持跨午夜的下班与到家（规格 4.1）在就寝没有回绕时同样成立：工厂就寝 23 点，写明的到家「22:30-00:30」
    起点不晚于就寝、终点越过当日 24 点，共识后被置 null 并留一条「runs past midnight」说明，同日的下班照常落进卡片；
    三次采样都不触发重试，卡片文本不出现「到家」分句。"""
    session = FakeSession([FakeResponse(content=_work("18:00-18:30", "22:30-00:30"))] * 3)
    result = extractor.extract_persona("六点下班，夜里十点半到零点半之间到家", n_runs=3, session=session)
    anchors = result.card.time_anchors
    assert (anchors.bedtime.start_hour, anchors.leave_work.start_hour, anchors.arrive_home) == (23.0, 18.0, None)
    assert result.demotion_notes == ("work_anchor_demoted: arrive_home window 22.5..24.5 runs past midnight",)
    assert len(session.calls) == 3
    text = render_card(result.card)
    assert "；下班18点到18点半。" in text and "；到家" not in text


def test_an_out_of_order_work_anchor_past_midnight_keeps_the_order_note():
    """@brief 降级说明每个锚点只取一条，先后不合格时取起点越界的格式（接口合同「人格与卡片字段」）：下班「23:30-00:30」
    起点晚于就寝 23 点、终点又越过当日 24 点，说明是「start … is outside …」而不是「runs past midnight」；真实编译的
    warnings 原样带这一条。"""
    note = "work_anchor_demoted: leave_work start 23.5 is outside leave_home 8..bedtime 23"
    session = FakeSession([FakeResponse(content=_work("23:30-00:30", None))] * 3)
    result = extractor.extract_persona("夜里十一点半才下班", n_runs=3, session=session)
    assert len(session.calls) == 3
    assert result.card.time_anchors.leave_work is None
    assert result.demotion_notes == (note,)
    assert compile_persona("夜里十一点半才下班", extraction=result).warnings == (note,)


def test_demotion_notes_are_read_only_on_the_extraction_result():
    """@brief 抽取结果承载降级说明的字段只读（规格 4.1）：在真实抽取结果上改写或清空 demotion_notes 被拒（冻结实例），
    说明原样保留。"""
    session = FakeSession([FakeResponse(content=_work("07:00-07:30", None))])
    result = extractor.extract_persona("故事", n_runs=1, session=session)
    with pytest.raises(ValidationError) as error:
        result.demotion_notes = ()
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        (("demotion_notes",), "frozen_instance")]
    assert result.demotion_notes == _BOTH_DEMOTED[:1]
