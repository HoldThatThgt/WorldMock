"""中文逐字摘录按字数核对（docs/dev/SPEC-dayrun-fidelity.md 7.1）。

规则：NFKC 与大小写归一，非词字符换成空格，再删去与汉字、假名相邻的空格；片段有效长度 = 字母数字串个数 + 汉字与假名
字数 ÷ 2，至少为 3。边界：5 个汉字拒绝，6 个汉字通过，改一个字不匹配；纯英文行为逐位不变；评审模型判接受也须经此核对。
回归样本取 4a5b0e6 两次全天运行的真实评审记录（tests/fixtures/dayrun_work_reviews.json）：BIZ 第 2、3、4、6 次提交
（评审模型判接受）按新规则 17/17、17/17、17/17、18/18 通过，第 1、5 次仍拒；WC 的英文评审结果不变。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from worldmock.life.work_review import (
    EXCERPT_MINIMUM_LENGTH,
    PreparedWorkReview,
    _excerpt_in_source,
    _normalize_excerpt,
    parse_work_review,
    public_work_review,
)

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = json.loads((_ROOT / "tests/fixtures/dayrun_work_reviews.json").read_text(encoding="utf-8"))
_SOURCE = "张总：我们下周三之前把报价单发过来，2026 年 9 月 28 日上午确认。"


def _legacy_normalize(value: str) -> str:
    """改动前的归一（只做大小写归一、非词字符换空格）：英文逐位不变的对照。"""
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _legacy_excerpt_in_source(excerpt: str, source: str) -> bool:
    """改动前的核对（按空格切分至少 3 个词）：英文逐位不变的对照。"""
    normalized = _legacy_normalize(source)
    segments = [_legacy_normalize(item) for item in re.split(r"(?:\.{3}|…)", excerpt)]
    meaningful = [item for item in segments if len(item.split()) >= 3]
    return bool(meaningful) and all(item in normalized for item in meaningful)


def _prepared(source: str, submission: dict) -> PreparedWorkReview:
    """真实评审请求的身份闭集与来源；哈希只作决定的绑定字段。"""
    return PreparedWorkReview(
        request={}, source=source, expected_reference_ids=tuple(submission["reference_ids"]),
        expected_delivery_paths=tuple(submission["delivery_paths"]), input_sha256="0" * 64,
        oracle_sha256="1" * 64, deliverable_sha256="2" * 64, prompt_sha256="3" * 64, schema_sha256="4" * 64)


def _raw(arguments: str) -> dict:
    """评审模型的原始响应形状：唯一一次 submit_work_review 调用。"""
    return {"choices": [{"message": {"tool_calls": [{
        "id": "review-call", "type": "function",
        "function": {"name": "submit_work_review", "arguments": arguments}}]}}]}


def _excerpts(arguments: str) -> list[str]:
    """一次评审的全部来源摘录（必需事实在前、交付项在后）。"""
    payload = json.loads(arguments)
    return [item["source_excerpt"]
            for item in (*payload["reference_assessments"], *payload["delivery_assessments"])]


def _white_collar_source() -> str:
    """WC 评审的来源即仓库 AMI 语料的 ES2002a 转写（按哈希核对是同一份字节）。"""
    record = _FIXTURE["white_collar"]
    path = _ROOT / "corpora" / record["source_path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record["source_sha256"]
    return path.read_text(encoding="utf-8")


def test_five_han_characters_are_rejected_and_six_accepted() -> None:
    """5 个汉字有效长度 2.5 不参与核对（整段没有可核对片段即拒），6 个汉字有效长度 3 通过。"""
    assert EXCERPT_MINIMUM_LENGTH == 3
    assert not _excerpt_in_source("我们下周三", _SOURCE)
    assert _excerpt_in_source("我们下周三之", _SOURCE)
    assert _excerpt_in_source("我们下周三之前把报价单发过来", _SOURCE)
    assert not _excerpt_in_source("张总：我们", _SOURCE)


def test_one_changed_character_does_not_match() -> None:
    """改动一个字（三→四）就不再逐字出现在来源里。"""
    assert _excerpt_in_source("我们下周三之前把报价单发过来", _SOURCE)
    assert not _excerpt_in_source("我们下周四之前把报价单发过来", _SOURCE)
    assert not _excerpt_in_source("我们下周三之后把报价单发过来", _SOURCE)


def test_spaces_beside_han_are_removed_and_punctuation_is_ignored() -> None:
    """与汉字相邻的空格删去：来源写「2026 年 9 月 28 日」、摘录写「2026年9月28日」也逐字相符；标点不影响核对。"""
    assert _normalize_excerpt("2026 年 9 月 28 日上午") == "2026年9月28日上午"
    assert _normalize_excerpt("张总：我们下周三") == "张总我们下周三"
    assert _excerpt_in_source("2026年9月28日上午确认", _SOURCE)
    assert _excerpt_in_source("张总 我们下周三之前", _SOURCE)


def test_alphanumeric_runs_and_han_characters_add_up() -> None:
    """数字串与汉字合计：「9月28日」是 2 个数字串加 2 个汉字，有效长度 3，通过；「28日」只有 1.5，不参与核对。"""
    assert _excerpt_in_source("9月28日", _SOURCE)
    assert not _excerpt_in_source("28日", _SOURCE)


def test_kana_count_like_han_and_nfkc_folds_width() -> None:
    """假名与汉字同样按字计：5 个假名拒绝、6 个通过；NFKC 把全角字母数字折成半角后再比较。"""
    source = "かいぎのきろくをおくります。ＡＢＣ報告 42 件"
    assert not _excerpt_in_source("かいぎのき", source)
    assert _excerpt_in_source("かいぎのきろ", source)
    assert _normalize_excerpt("ＡＢＣ報告") == "abc報告"
    assert _excerpt_in_source("abc報告 42件", source)


def test_ellipsis_joined_segments_are_checked_one_by_one() -> None:
    """省略号分隔的每个够长的片段都须在来源里；过短的片段不参与核对。"""
    assert _excerpt_in_source("我们下周三之前……2026年9月28日上午", _SOURCE)
    assert not _excerpt_in_source("我们下周三之前……2026年9月29日上午", _SOURCE)
    assert _excerpt_in_source("张总……我们下周三之前", _SOURCE)


def test_english_normalization_and_check_are_bit_for_bit_unchanged() -> None:
    """纯英文：WC 来源（AMI 转写）每一行的归一结果与改动前逐位相同，逐词滑窗与改一词的片段核对结论也相同。"""
    source = _white_collar_source()
    lines = [line for line in source.splitlines() if line.strip()]
    assert lines and all(_normalize_excerpt(line) == _legacy_normalize(line) for line in lines)
    words = source.split()
    samples = []
    for size in (1, 2, 3, 4, 6):
        samples += [" ".join(words[start:start + size]) for start in range(0, len(words) - size, 97)]
    samples += [sample.replace("the", "a") for sample in samples]
    samples += ["remote control ... twenty five Euro", "Hi , I'm David", "HI I'M DAVID AND"]
    assert len(samples) > 200
    assert [_excerpt_in_source(item, source) for item in samples] == [
        _legacy_excerpt_in_source(item, source) for item in samples]
    assert any(_excerpt_in_source(item, source) for item in samples)
    assert not all(_excerpt_in_source(item, source) for item in samples)


def test_reviewer_acceptance_still_requires_the_excerpt_check() -> None:
    """评审模型判接受、逐项结论也都通过，但一条摘录只有 5 个汉字：本地核对不通过，最终仍拒绝。"""
    submission = {"reference_ids": ["summary:0"], "delivery_paths": ["summary"]}
    row = {"required_fact_covered": True, "source_supported": True, "contradiction": False, "rationale": "summary ok"}
    arguments = {"reference_assessments": [{**row, "reference_id": "summary:0", "source_excerpt": "我们下周三"}],
                 "delivery_assessments": [{"delivery_path": "summary", "source_supported": True,
                                           "contradiction": False, "source_excerpt": "我们下周三之前把报价单发过来",
                                           "rationale": "summary ok"}],
                 "accepted": True}
    decision = parse_work_review(_raw(json.dumps(arguments, ensure_ascii=False)), _prepared(_SOURCE, submission))
    assert decision.reported_accepted and not decision.accepted
    feedback = public_work_review(decision, _SOURCE)
    assert feedback["required_fact_assessments"][0]["source_excerpt"] is None
    assert feedback["delivery_assessments"][0]["source_excerpt"] == "我们下周三之前把报价单发过来"
    arguments["reference_assessments"][0]["source_excerpt"] = "我们下周三之前"
    decision = parse_work_review(_raw(json.dumps(arguments, ensure_ascii=False)), _prepared(_SOURCE, submission))
    assert decision.accepted


@pytest.mark.parametrize(("number", "accepted", "total"), [
    (1, False, 18), (2, True, 17), (3, True, 17), (4, True, 17), (5, False, 19), (6, True, 18)],
    ids=[f"submission-{number}" for number in range(1, 7)])
def test_business_day_run_reviews_recomputed(number: int, accepted: bool, total: int) -> None:
    """BIZ 纪要 6 次提交的真实评审响应重算：评审模型判接受的第 2、3、4、6 次摘录全部通过（17/17、17/17、17/17、
    18/18）并被接受；第 1、5 次评审模型判不接受，仍拒。运行时记录的结论 6 次全是拒绝（旧规则下中文整句算 1 个词）。"""
    business = _FIXTURE["business"]
    submission = business["submissions"][number - 1]
    assert submission["recorded_accepted"] is False
    assert submission["reported_accepted"] is accepted
    excerpts = _excerpts(submission["arguments"])
    assert len(excerpts) == total
    passed = sum(_excerpt_in_source(item, business["source"]) for item in excerpts)
    assert passed == total
    decision = parse_work_review(_raw(submission["arguments"]), _prepared(business["source"], submission))
    assert decision.reported_accepted is accepted
    assert decision.accepted is accepted
    feedback = public_work_review(decision, business["source"])
    assert feedback["accepted"] is accepted
    rows = [*feedback["required_fact_assessments"], *feedback["delivery_assessments"]]
    assert all(row["source_excerpt"] is not None for row in rows)


def test_business_reviews_failed_under_the_old_word_count() -> None:
    """对照：旧规则（按空格切词至少 3 个）在同样的 4 次被接受的评审上各有摘录不通过，所以运行记录全是拒绝。"""
    business = _FIXTURE["business"]
    for number in (2, 3, 4, 6):
        excerpts = _excerpts(business["submissions"][number - 1]["arguments"])
        assert not all(_legacy_excerpt_in_source(item, business["source"]) for item in excerpts)


@pytest.mark.parametrize(("number", "total"), [(1, 18), (2, 17)], ids=["submission-1", "submission-2"])
def test_white_collar_english_reviews_are_unchanged(number: int, total: int) -> None:
    """WC 两次英文评审：每条摘录的核对结论与改动前相同且全部通过，最终接受与否与运行记录一致（拒、收）。"""
    source = _white_collar_source()
    submission = _FIXTURE["white_collar"]["submissions"][number - 1]
    excerpts = _excerpts(submission["arguments"])
    assert len(excerpts) == total
    assert [_excerpt_in_source(item, source) for item in excerpts] == [
        _legacy_excerpt_in_source(item, source) for item in excerpts] == [True] * total
    decision = parse_work_review(_raw(submission["arguments"]), _prepared(source, submission))
    assert decision.accepted is submission["recorded_accepted"]
    assert decision.reported_accepted is submission["reported_accepted"]
