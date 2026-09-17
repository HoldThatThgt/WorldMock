"""@brief 映射层：确定性（同输入 1000 次逐字节同输出）、方向锚定与配置校验。"""

from __future__ import annotations

import re

import pytest

from worldmock.profile.mapper import DETERMINISTIC_SOURCE_MODEL, load_mapping_config, map_profile
from worldmock.profile.models import (
    WEIGHT_KEYS,
    BigFiveProfile,
    MotivationProfile,
    attention_keys,
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
    assert tuple(profile.attention_prior.model_dump()) == attention_keys()


def test_attention_round_trips_registry_key_set():
    """@brief round-trip：注册表键集 → mapper 编译 → 输出键集恰好匹配且归一。"""
    keys = attention_keys()
    for scores in ({"openness": 6, "extraversion": 2}, {"openness": 1, "extraversion": 7}):
        prior = make_profile(**scores).attention_prior.model_dump()
        assert tuple(prior) == keys
        assert round(sum(prior.values()), 2) == 1.0
    config = load_mapping_config()
    assert set(config["attention"]["base"]) == set(keys)


def test_source_fields_carry_provenance():
    profile = make_profile()
    assert profile.source.model == "test-extractor"
    assert profile.source.prompt_version == "v2"
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


def test_config_rejects_attention_key_set_mismatch(tmp_path):
    """@brief [attention.base] 键集必须精确等于注册表键集：少键/多键都断言失败。"""
    from worldmock.profile.mapper import default_mapping_path
    full_text = default_mapping_path().read_text(encoding="utf-8")
    first_key = attention_keys()[0]
    # 少键：删掉 [attention.base] 里的一个键（值不定，按行首键名定位该赋值行）。
    assignment = re.search(rf"^{first_key} = [0-9.]+\n", full_text, re.MULTILINE)
    assert assignment is not None, f"attention.base row for {first_key} not found"
    missing = tmp_path / "missing.toml"
    missing.write_text(full_text.replace(assignment.group(0), "", 1), encoding="utf-8")
    with pytest.raises(AssertionError, match="attention.base must exactly cover"):
        load_mapping_config(missing)
    # 多键：base 里塞一个注册表外（attention_required=false）的键。
    extra = tmp_path / "extra.toml"
    extra.write_text(full_text.replace(
        "[attention.slope", 'game_session = 0.01\n\n[attention.slope', 1), encoding="utf-8")
    with pytest.raises(AssertionError, match="attention.base must exactly cover"):
        load_mapping_config(extra)
