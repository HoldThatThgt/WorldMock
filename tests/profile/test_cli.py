"""@brief profile CLI：compile 输出合同 + check 一致性门禁（提取层打桩，不真实调 API）。"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from worldmock import cli
from worldmock.errors import IntegrityError
from worldmock.profile.extractor import BigFiveExtraction
from worldmock.profile.models import BigFiveProfile, attention_keys


def fake_extraction(**scores):
    """@brief 固定假提取：全 4 基线，可覆盖维度。"""
    defaults = {"openness": 4, "conscientiousness": 4, "extraversion": 4,
                "agreeableness": 4, "neuroticism": 4}
    profile = BigFiveProfile(**{**defaults, **scores})
    per_dimension = {dimension: {"values": [profile.model_dump()[dimension]],
                                 "median": profile.model_dump()[dimension],
                                 "mode_share": 1.0}
                     for dimension in defaults}
    return BigFiveExtraction(profile=profile, runs=3, agreement=1.0,
                             per_dimension=per_dimension, warning=False,
                             model="fake-model", prompt_version="v2")


def run_cli(argv):
    """@brief 执行 CLI 并捕获 stdout JSON。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = cli.main(argv)
    return exit_code, json.loads(buffer.getvalue())


def test_compile_outputs_contract_shape(tmp_path, monkeypatch):
    text_file = tmp_path / "persona.txt"
    text_file.write_text("一位测试画像", encoding="utf-8")
    monkeypatch.setattr("worldmock.profile.extractor.extract_big_five",
                        lambda text, n_runs: fake_extraction(openness=6))
    _, payload = run_cli(["profile", "compile", "--text", str(text_file), "--runs", "3"])
    profile = payload["profile"]
    assert profile["schema_version"] == 1
    assert profile["big_five"]["openness"] == 6
    assert set(profile["weights"]) == {"money_fen", "energy_kcal", "sleep_debt_hours",
                                       "entertainment_need", "goal_task"}
    assert tuple(profile["attention_prior"]) == attention_keys()
    assert round(sum(profile["attention_prior"].values()), 2) == 1.0
    assert profile["source"]["consensus"] == {"runs": 3, "agreement": 1.0}
    assert profile["source"]["prompt_version"] == "v2"
    assert profile["source"]["model"] == "fake-model"
    assert payload["canonical_persona_text"].startswith("# 规范文本人格")
    assert payload["warnings"] == []


def test_compile_writes_out_file(tmp_path, monkeypatch):
    text_file = tmp_path / "persona.txt"
    text_file.write_text("画像", encoding="utf-8")
    out_file = tmp_path / "result.json"
    monkeypatch.setattr("worldmock.profile.extractor.extract_big_five",
                        lambda text, n_runs: fake_extraction())
    _, receipt = run_cli(["profile", "compile", "--text", str(text_file),
                          "--out", str(out_file)])
    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert written["profile"]["schema_version"] == 1
    assert receipt["written"] == str(out_file) and receipt["warnings"] == []


def test_check_passes_on_stable_extraction(tmp_path, monkeypatch):
    text_file = tmp_path / "persona.txt"
    text_file.write_text("画像", encoding="utf-8")
    monkeypatch.setattr("worldmock.profile.extractor.extract_big_five",
                        lambda text, n_runs: fake_extraction(conscientiousness=6))
    _, report = run_cli(["profile", "check", "--text", str(text_file)])
    assert report["grid_consistency"] == 1.0
    assert all(rate == 1.0 for rate in report["big_five_mode_hit"].values())
    assert len(report["profiles"]) == 3 and len(report["canonical_texts"]) == 3


def test_check_accepts_mode_hit_two_of_three(tmp_path, monkeypatch):
    """@brief 3 次编译中 2 次一致即过线（≥2/3）。"""
    text_file = tmp_path / "persona.txt"
    text_file.write_text("画像", encoding="utf-8")
    script = iter([fake_extraction(openness=5), fake_extraction(openness=5),
                   fake_extraction(openness=6)])
    monkeypatch.setattr("worldmock.profile.extractor.extract_big_five",
                        lambda text, n_runs: next(script))
    _, report = run_cli(["profile", "check", "--text", str(text_file)])
    assert report["big_five_mode_hit"]["openness"] == pytest.approx(2 / 3)


def test_check_fails_when_dimension_scatter(tmp_path, monkeypatch):
    """@brief 单维度三次全不同 → 众数命中 1/3 < 2/3 → PROFILE_CHECK_FAILED。"""
    text_file = tmp_path / "persona.txt"
    text_file.write_text("画像", encoding="utf-8")
    script = iter([fake_extraction(openness=3), fake_extraction(openness=5),
                   fake_extraction(openness=7)])
    monkeypatch.setattr("worldmock.profile.extractor.extract_big_five",
                        lambda text, n_runs: next(script))
    args = cli.build_parser().parse_args(["profile", "check", "--text", str(text_file)])
    with pytest.raises(IntegrityError, match="PROFILE_CHECK_FAILED"):
        cli._profile_command(args)


def test_compile_missing_text_file_fails(tmp_path):
    args = cli.build_parser().parse_args(
        ["profile", "compile", "--text", str(tmp_path / "absent.txt")])
    with pytest.raises(IntegrityError, match="not found"):
        cli._profile_command(args)


def test_compile_reads_stdin(monkeypatch):
    monkeypatch.setattr("worldmock.profile.extractor.extract_big_five",
                        lambda text, n_runs: fake_extraction())
    monkeypatch.setattr("sys.stdin", io.StringIO("标准输入画像"))
    _, payload = run_cli(["profile", "compile", "--text", "-", "--runs", "1"])
    assert payload["profile"]["schema_version"] == 1
