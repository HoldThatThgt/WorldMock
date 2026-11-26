"""多模型档运行身份、提示与工具指纹以及多人物摘要校验的冻结契约。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.frozen_inputs import fixture_metadata
from worldmock.configuration.inputs.environment import load_environment
from worldmock.configuration.inputs.models import referenced_profile_ids
from worldmock.state.identity import (
    RUN_MANIFEST_SCHEMA_VERSION,
    ModelIdentity,
    RunIdentity,
    StageProfileIdentity,
    check_persona_sha256s,
    validate_persona_sha256s_input,
)

SOURCE = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment"
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


def _profile_identity(profile, metadata) -> StageProfileIdentity:
    """用配置档和权威元数据构造不含凭据的冻结模型档身份。"""
    return StageProfileIdentity(
        provider=profile.provider,
        base_url=profile.base_url,
        requested_model=profile.requested_model,
        metadata_url=profile.metadata_url,
        context_window=profile.context_window,
        thinking=profile.thinking,
        max_output_tokens=profile.max_output_tokens,
        compact_trigger_tokens=profile.compact_trigger_tokens,
        prompt_cache_key=profile.prompt_cache_key,
        max_retries=profile.max_retries,
        transport_max_retries=profile.transport_max_retries,
        max_react_turns=profile.max_react_turns,
        reflection_validation_max_retries=profile.reflection_validation_max_retries,
        day_plan_validation_max_retries=profile.day_plan_validation_max_retries,
        model_metadata=metadata,
        effective_max_tokens=min(profile.max_output_tokens, metadata.max_output_tokens),
    )


def _model_identity_from_fixture() -> ModelIdentity:
    loaded = load_environment(SOURCE, metadata_loader=fixture_metadata)
    profiles = {
        profile_id: _profile_identity(loaded.config.models[profile_id], loaded.model_metadata[profile_id])
        for profile_id in referenced_profile_ids(loaded.config.model_routing)
    }
    return ModelIdentity(
        profiles=profiles,
        routing=loaded.config.model_routing.model_dump(mode="json"),
        prompt_sha256s={"action": _SHA_A, "search": _SHA_B},
        tool_schema_sha256s={"action": _SHA_B, "search": _SHA_C},
    )


def test_run_manifest_schema_version_and_stage_profile_excludes_credentials():
    """运行清单结构版本为 9，StageProfileIdentity 冻结端点、压缩阈值、缓存键开关与有效输出上限且不含凭据字段。"""
    assert RUN_MANIFEST_SCHEMA_VERSION == 9
    loaded = load_environment(SOURCE, metadata_loader=fixture_metadata)
    reasoner = loaded.config.models["reasoner"]
    reasoner_meta = loaded.model_metadata["reasoner"]
    identity = _profile_identity(reasoner, reasoner_meta)
    dumped = identity.model_dump(mode="json")
    assert "api_key_env" not in dumped
    assert dumped["effective_max_tokens"] == min(16000, reasoner_meta.max_output_tokens) == 16000
    assert dumped["thinking"] is True
    assert dumped["compact_trigger_tokens"] == 80000
    assert dumped["prompt_cache_key"] is False
    assert dumped["max_react_turns"] == 16

    capped_meta = reasoner_meta.model_copy(update={"max_output_tokens": 8192})
    capped = _profile_identity(reasoner, capped_meta)
    assert capped.effective_max_tokens == 8192

    bad_payload = {**dumped, "effective_max_tokens": 8000}
    with pytest.raises(ValidationError, match="effective_max_tokens"):
        StageProfileIdentity.model_validate(bad_payload)

    bad_meta = {**dumped, "model_metadata": reasoner_meta.model_copy(update={"requested_model": "other"})}
    with pytest.raises(ValidationError, match="stage profile model metadata disagrees"):
        StageProfileIdentity.model_validate(bad_meta)

    limit = int(reasoner.context_window * 0.85)
    assert StageProfileIdentity.model_validate({**dumped, "compact_trigger_tokens": limit - 1}).compact_trigger_tokens == limit - 1
    assert StageProfileIdentity.model_validate({**dumped, "prompt_cache_key": True}).prompt_cache_key is True
    for bad_override, pattern in (
        ({"compact_trigger_tokens": 16383}, "greater than or equal to 16384"),
        ({"compact_trigger_tokens": True}, "valid integer"),
        ({"compact_trigger_tokens": limit}, "compact_trigger_tokens must be strictly less than"),
        ({"prompt_cache_key": 1}, "valid boolean"),
        ({"prompt_cache_key": "true"}, "valid boolean"),
    ):
        with pytest.raises(ValidationError, match=pattern):
            StageProfileIdentity.model_validate({**dumped, **bad_override})


def test_model_identity_requires_exact_routed_profiles_and_stage_digests():
    """ModelIdentity 核对 profiles 与 routing 引用集完全一致，且主/搜索提示与工具指纹成对完整。"""
    identity = _model_identity_from_fixture()
    assert set(identity.profiles) == {"reasoner", "fast_search"}
    assert identity.model_dump(mode="json")["routing"]["search"] == "fast_search"

    extra_profiles = {**identity.profiles, "unused": identity.profiles["reasoner"]}
    with pytest.raises(ValidationError, match="referenced by routing"):
        ModelIdentity(
            profiles=extra_profiles,
            routing=identity.routing,
            prompt_sha256s=identity.prompt_sha256s,
            tool_schema_sha256s=identity.tool_schema_sha256s,
        )

    for bad_digests in ({"action": _SHA_A}, {"action": _SHA_A, "search": "not-sha256"}):
        with pytest.raises(ValidationError, match="prompt_sha256s"):
            ModelIdentity(
                profiles=identity.profiles,
                routing=identity.routing,
                prompt_sha256s=bad_digests,
                tool_schema_sha256s=identity.tool_schema_sha256s,
            )


def test_run_identity_and_persona_sha256s_validation():
    """RunIdentity 严格校验人物集合、数字摘要键与提交元数据成对出现。"""
    model_identity = _model_identity_from_fixture()
    run = RunIdentity(
        run_id="run-multi",
        source_group_id="group-1",
        agent_ids=(1, 2),
        persona_sha256s={"1": _SHA_A, "2": _SHA_B},
        start_at="2026-01-05T08:00:00+08:00",
        end_at="2026-01-06T08:00:00+08:00",
        timezone="Asia/Shanghai",
        code_sha256=_SHA_C,
        model_identity=model_identity,
        git_commit="a" * 40,
        git_dirty=False,
    )
    assert run.persona_sha256s == {1: _SHA_A, 2: _SHA_B}
    assert validate_persona_sha256s_input([1, 2]) == [1, 2]

    for bad_keys in ({True: _SHA_A}, {"01": _SHA_A}, {0: _SHA_A}, {1.5: _SHA_A}):
        with pytest.raises((ValueError, ValidationError), match="positive integers"):
            validate_persona_sha256s_input(bad_keys)

    with pytest.raises(ValueError, match="agent_ids"):
        check_persona_sha256s({1: _SHA_A}, {1, 2})
    with pytest.raises(ValueError, match="SHA-256"):
        check_persona_sha256s({1: "bad"}, {1})

    base = run.model_dump(mode="python")
    for field_override, pattern in (
        ({"agent_ids": [True]}, "positive integers"),
        ({"agent_ids": []}, "positive integers"),
        ({"agent_ids": [1, 1], "persona_sha256s": {1: _SHA_A}}, "positive integers"),
        ({"git_commit": "a" * 40, "git_dirty": None}, "both be recorded"),
    ):
        with pytest.raises(ValidationError, match=pattern):
            RunIdentity.model_validate({**base, **field_override})
