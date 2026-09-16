"""@brief 规范文本文本生成：数值↔文本一致、确定性、带全覆盖。"""

from __future__ import annotations

import re

from worldmock.profile.textgen import render_persona_text
from tests.profile.test_mapper import make_profile


def test_render_is_deterministic():
    profile = make_profile(openness=6, conscientiousness=2, extraversion=7,
                           agreeableness=5, neuroticism=3)
    assert render_persona_text(profile) == render_persona_text(profile)


def test_aspiration_text_matches_seconds():
    profile = make_profile(conscientiousness=5, neuroticism=4)
    text = render_persona_text(profile)
    seconds = profile.aspiration.daily_effective_work_seconds
    hours = seconds / 3600
    assert f"{hours:g} 小时" in text and f"{seconds} 秒" in text


def test_comfort_text_matches_fen_and_kcal():
    profile = make_profile()
    text = render_persona_text(profile)
    yuan = profile.comfort.balance_fen / 100
    assert f"{yuan:g} 元" in text
    assert f"{profile.comfort.energy_kcal:g} kcal" in text


def test_weight_percentages_match_profile_and_ranking():
    profile = make_profile(extraversion=6, conscientiousness=2)
    text = render_persona_text(profile)
    weights = profile.weights.model_dump()
    ranked = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    # 文本中的 top3 顺序与数值排序一致；提及的四个权重（top3+最弱）百分比=数值×100
    positions = [text.index(f"（{value:.0%}）") for _, value in ranked[:3]]
    assert positions == sorted(positions)
    for _, value in list(ranked[:3]) + [ranked[-1]]:
        assert f"{value:.0%}" in text


def test_attention_percentages_match_profile():
    profile = make_profile(openness=7)
    text = render_persona_text(profile)
    attention = profile.attention_prior.model_dump()
    ranked = sorted(attention.items(), key=lambda item: (-item[1], item[0]))
    for _, value in list(ranked[:3]) + [ranked[-1]]:
        assert f"{value:.0%}" in text


def test_decision_text_carries_tau_and_beta():
    profile = make_profile(conscientiousness=7)
    text = render_persona_text(profile)
    assert f"tau={profile.tau:g}" in text
    assert f"beta={profile.beta_present_bias:g}" in text


def test_all_dimension_bands_appear():
    low = make_profile(openness=1)
    high = make_profile(openness=7)
    assert "保守务实，偏好熟悉确定的事物" in render_persona_text(low)
    assert "好奇心强，乐于尝试新鲜体验" in render_persona_text(high)
    assert "对新事物态度适中" in render_persona_text(make_profile())


def test_loss_aversion_strength_bands():
    weak = make_profile(neuroticism=1)
    strong = make_profile(neuroticism=7)
    assert "较钝" in render_persona_text(weak)
    assert "强烈" in render_persona_text(strong)
    text = render_persona_text(make_profile(neuroticism=4))
    assert f"λ={make_profile(neuroticism=4).loss_aversion.money_fen:g}" in text


def test_text_has_all_canonical_sections():
    """@brief 规范文本是 canonical 附注：包含全部八个小节标题。"""
    text = render_persona_text(make_profile())
    sections = re.findall(r"^## ([^：]+)：", text, flags=re.MULTILINE)
    assert sections == ["大五特质", "动机结构", "决策特性", "损失厌恶",
                        "舒适缓冲", "志向", "情绪敏感度", "注意力偏好"]
