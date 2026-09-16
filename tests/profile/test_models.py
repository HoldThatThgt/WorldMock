"""@brief 模型层格点合同正反例：格点外即拒绝，格点内即通过。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.profile.models import (
    TAU_GRID,
    AttentionPrior,
    BigFiveProfile,
    LossAversion,
    MotivationProfile,
    Weights,
    normalize_to_grid,
    persona_text_sha256,
    quantize,
    quantize_tau,
)
from tests.profile.test_mapper import make_profile


def test_big_five_accepts_bounds_and_rejects_out_of_scale():
    assert BigFiveProfile(openness=1, conscientiousness=7, extraversion=4,
                          agreeableness=4, neuroticism=4)
    for bad in (0, 8, -1):
        with pytest.raises(ValidationError):
            BigFiveProfile(openness=bad, conscientiousness=4, extraversion=4,
                           agreeableness=4, neuroticism=4)


def test_big_five_rejects_float_scores():
    with pytest.raises(ValidationError):
        BigFiveProfile(openness=4.5, conscientiousness=4, extraversion=4,
                       agreeableness=4, neuroticism=4)


def test_quantize_clamps_and_snaps():
    assert quantize(0.63, 0.5, 1.0, 0.05) == 0.65
    assert quantize(0.61, 0.5, 1.0, 0.05) == 0.6
    assert quantize(0.1, 0.5, 1.0, 0.05) == 0.5
    assert quantize(9.9, 0.5, 1.0, 0.05) == 1.0
    assert quantize(2.14, 1.0, 3.0, 0.25) == 2.25


def test_quantize_tau_nearest_and_tie_to_smaller():
    assert quantize_tau(0.4) == 0.5
    assert quantize_tau(0.75) == 0.5  # 0.75 到 0.5/1.0 等距，平手取更小
    assert quantize_tau(1.4) == 1.0
    assert quantize_tau(1.6) == 2.0
    assert quantize_tau(2.9) == 3.0
    assert TAU_GRID == (0.5, 1.0, 2.0, 3.0)


def test_normalize_to_grid_sums_exactly_one():
    result = normalize_to_grid({"a": 1.0, "b": 2.0, "c": 3.0, "d": 0.5, "e": 0.5})
    assert round(sum(result.values()), 10) == 1.0
    assert all(round(value / 0.01) * 0.01 == pytest.approx(value, abs=1e-9)
               for value in result.values())


def test_normalize_to_grid_deterministic_tiebreak():
    first = normalize_to_grid({"x": 1.0, "y": 1.0, "z": 1.0})
    second = normalize_to_grid({"x": 1.0, "y": 1.0, "z": 1.0})
    assert first == second == {"x": 0.34, "y": 0.33, "z": 0.33}


def test_normalize_to_grid_clamps_negatives_and_rejects_empty_total():
    assert normalize_to_grid({"a": -5.0, "b": 1.0}) == {"a": 0.0, "b": 1.0}
    with pytest.raises(ValueError):
        normalize_to_grid({"a": 0.0})
    with pytest.raises(ValueError):
        normalize_to_grid({})


def test_weights_reject_off_grid_and_unnormalized():
    with pytest.raises(ValidationError, match="off the 0.01 grid"):
        Weights(money_fen=0.3333, energy_kcal=0.17, sleep_debt_hours=0.17,
                entertainment_need=0.17, goal_task=0.17)
    with pytest.raises(ValidationError, match="not normalized"):
        Weights(money_fen=0.30, energy_kcal=0.30, sleep_debt_hours=0.20,
                entertainment_need=0.10, goal_task=0.05)


def test_attention_prior_rejects_off_grid_and_unnormalized():
    with pytest.raises(ValidationError, match="off the 0.01 grid"):
        AttentionPrior(feature_film=0.2077, news_feed=0.2, joke_feed=0.2,
                       webnovel_excerpt=0.2, drama_series=0.2)
    with pytest.raises(ValidationError, match="not normalized"):
        AttentionPrior(feature_film=0.5, news_feed=0.2, joke_feed=0.1,
                       webnovel_excerpt=0.1, drama_series=0.05)


def test_loss_aversion_grid():
    assert LossAversion(money_fen=2.25, energy_kcal=1.0)
    with pytest.raises(ValidationError, match="off the 0.25 grid"):
        LossAversion(money_fen=2.3, energy_kcal=1.0)
    with pytest.raises(ValidationError):
        LossAversion(money_fen=2.25, energy_kcal=3.25)


def _profile_with(**overrides):
    """@brief 构造档案变体：走 model_validate 保证格点校验真正执行。"""
    profile = make_profile()
    return MotivationProfile.model_validate({**profile.model_dump(), **overrides})


def test_motivation_profile_rejects_off_lattice_values():
    with pytest.raises(ValidationError, match="tau"):
        _profile_with(tau=1.5)
    with pytest.raises(ValidationError, match="off the 0.05 grid"):
        _profile_with(beta_present_bias=0.63)
    with pytest.raises(ValidationError, match="off the 0.1 grid"):
        _profile_with(emotion_sensitivity=0.55)
    with pytest.raises(ValidationError):
        _profile_with(beta_present_bias=0.45)


def test_motivation_profile_round_trips_through_json():
    profile = make_profile()
    parsed = MotivationProfile.model_validate_json(profile.model_dump_json())
    assert parsed == profile
    payload = profile.model_dump()
    assert payload["schema_version"] == 1
    assert set(payload) == {"schema_version", "big_five", "weights", "tau",
                            "beta_present_bias", "loss_aversion", "comfort",
                            "aspiration", "emotion_sensitivity", "attention_prior",
                            "source"}


def test_persona_text_sha256_is_stable_sha256():
    digest = persona_text_sha256("hello")
    assert len(digest) == 64 and digest == persona_text_sha256("hello")
    assert digest != persona_text_sha256("hellp")
