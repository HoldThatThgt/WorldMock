"""@brief 映射层：确定性（同输入 1000 次逐字节同输出）、方向锚定与配置校验。"""

from __future__ import annotations

import pytest

from worldmock.profile.mapper import DETERMINISTIC_SOURCE_MODEL, load_mapping_config, map_profile
from worldmock.profile.models import (
    ATTENTION_KEYS,
    WEIGHT_KEYS,
    BigFiveProfile,
    MotivationProfile,
)


def make_profile(**scores) -> MotivationProfile:
    """@brief 测试档案工厂：默认全 4（刻度中心），可覆盖任意维度。"""
    defaults = {"openness": 4, "conscientiousness": 4, "extraversion": 4,
                "agreeableness": 4, "neuroticism": 4}
    return map_profile(
        BigFiveProfile(**{**defaults, **scores}),
        config=load_mapping_config(),
        persona_text="测试画像",
        source_model="test-extractor",
        consensus_runs=3,
        consensus_agreement=1.0,
    )


def test_mapper_is_deterministic_across_1000_runs():
    """@brief 纯函数+格点：同输入 1000 次逐字节相同（零 LLM）。"""
    big_five = BigFiveProfile(openness=6, conscientiousness=2, extraversion=5,
                              agreeableness=3, neuroticism=6)
    config = load_mapping_config()
    reference = None
    for _ in range(1000):
        profile = map_profile(big_five, config=config)
        if reference is None:
            reference = profile.model_dump_json()
        assert profile.model_dump_json() == reference


def test_mapper_extremes_stay_on_lattice():
    """@brief 全 1 / 全 4 / 全 7 / 混合四角全部构造合法（格点由模型层兜底）。"""
    for scores in (
        {"openness": 1, "conscientiousness": 1, "extraversion": 1, "agreeableness": 1, "neuroticism": 1},
        {"openness": 4, "conscientiousness": 4, "extraversion": 4, "agreeableness": 4, "neuroticism": 4},
        {"openness": 7, "conscientiousness": 7, "extraversion": 7, "agreeableness": 7, "neuroticism": 7},
        {"openness": 7, "conscientiousness": 1, "extraversion": 1, "agreeableness": 7, "neuroticism": 7},
    ):
        profile = make_profile(**scores)
        assert round(sum(profile.weights.model_dump().values()), 2) == 1.0
        assert round(sum(profile.attention_prior.model_dump().values()), 2) == 1.0
        assert profile.tau in (0.5, 1.0, 2.0, 3.0)


def test_direction_anchors():
    """@brief 方向锚定：尽责性→tau 负向；神经质→情绪敏感/损失厌恶正向；外向→娱乐权重。"""
    assert make_profile(conscientiousness=7).tau == 0.5
    assert make_profile(conscientiousness=1).tau == 3.0
    assert make_profile(neuroticism=7).emotion_sensitivity == 0.9
    assert make_profile(neuroticism=1).emotion_sensitivity == 0.1
    assert make_profile(neuroticism=7).loss_aversion.money_fen > \
        make_profile(neuroticism=1).loss_aversion.money_fen
    assert make_profile(extraversion=7).weights.entertainment_need > \
        make_profile(extraversion=1).weights.entertainment_need


def test_aspiration_monotonic_in_conscientiousness():
    values = [make_profile(conscientiousness=score).aspiration.daily_effective_work_seconds
              for score in (1, 4, 7)]
    assert values == sorted(values) and values[0] < values[-1]


def test_attention_balances_with_extraversion():
    """@brief 外向性→注意力均衡：高外向的先验分布比低外向更平（熵更大）。"""
    import math
    def entropy(profile):
        return -sum(p * math.log(p) for p in profile.attention_prior.model_dump().values()
                    if p > 0)
    assert entropy(make_profile(extraversion=7)) > entropy(make_profile(extraversion=1))


def test_weights_and_attention_cover_contract_keys():
    profile = make_profile()
    assert tuple(profile.weights.model_dump()) == WEIGHT_KEYS
    assert tuple(profile.attention_prior.model_dump()) == ATTENTION_KEYS


def test_source_fields_carry_provenance():
    profile = make_profile()
    assert profile.source.model == "test-extractor"
    assert profile.source.prompt_version == "v1"
    assert profile.source.consensus.runs == 3
    assert len(profile.source.persona_text_sha256) == 64
    default = map_profile(BigFiveProfile(openness=4, conscientiousness=4, extraversion=4,
                                         agreeableness=4, neuroticism=4))
    assert default.source.model == DETERMINISTIC_SOURCE_MODEL


def test_default_config_resolves_repo_rules_file():
    from worldmock.profile.mapper import default_mapping_path
    path = default_mapping_path()
    assert path.name == "persona-mapping.toml" and path.is_file()


def test_config_validation_rejects_missing_sections(tmp_path):
    from worldmock.profile.mapper import default_mapping_path
    bad_path = tmp_path / "bad.toml"
    bad_path.write_text("version = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="missing sections"):
        load_mapping_config(bad_path)
    with pytest.raises(AssertionError, match="attention"):
        # 真实配置文本截去 [attention 起的全部段落 → 缺 attention section。
        full_text = default_mapping_path().read_text(encoding="utf-8")
        partial = tmp_path / "partial.toml"
        partial.write_text(full_text.split("[attention")[0], encoding="utf-8")
        load_mapping_config(partial)
