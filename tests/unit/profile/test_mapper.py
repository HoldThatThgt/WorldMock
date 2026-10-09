"""@brief 大五只影响社交需要的固定增长映射，规则来自包内唯一来源。"""

from importlib.resources import files

import pytest

from worldmock.profile.mapper import load_mapping_config, map_world_dynamics
from worldmock.profile.models import BigFiveProfile


def scores(**updates):
    return BigFiveProfile(**{**dict.fromkeys(BigFiveProfile.model_fields, 4), **updates})


@pytest.mark.parametrize("extraversion,expected", [(1, .02), (2, .03), (3, .05), (4, .06), (5, .07), (6, .09), (7, .10)])
def test_extraversion_is_the_only_growth_route(extraversion, expected):
    dynamics = map_world_dynamics(scores(extraversion=extraversion))
    assert dynamics.model_dump() == {"social_need_growth_per_hour": expected}
    assert dynamics.model_dump_json() == map_world_dynamics(scores(extraversion=extraversion)).model_dump_json()


@pytest.mark.parametrize("dimension", ["openness", "conscientiousness", "agreeableness", "neuroticism"])
def test_other_traits_do_not_change_world_dynamics(dimension):
    assert map_world_dynamics(scores(**{dimension: 1})) == map_world_dynamics(scores(**{dimension: 7}))


def test_mapping_loads_packaged_current_rules():
    resource = files("worldmock.data").joinpath("persona-mapping.toml")
    assert resource.is_file()
    assert load_mapping_config()["version"] == 3


@pytest.mark.parametrize("text,reason", [("version = 3\n", "missing sections"),
                                         ("version = 2\n", "version = 3")])
def test_mapping_rejects_incomplete_or_stale_rules(tmp_path, text, reason):
    path = tmp_path / "invalid.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError, match=reason):
        load_mapping_config(path)


def test_mapping_rejects_an_extra_dynamics_route(tmp_path):
    text = files("worldmock.data").joinpath("persona-mapping.toml").read_text(encoding="utf-8")
    path = tmp_path / "invalid.toml"
    path.write_text(text + "\n[world_dynamics.mood]\nintercept = 0.1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="world_dynamics must exactly cover"):
        load_mapping_config(path)


def test_mapping_rejects_unknown_trait_slope(tmp_path):
    text = files("worldmock.data").joinpath("persona-mapping.toml").read_text(encoding="utf-8")
    path = tmp_path / "invalid.toml"
    path.write_text(text.replace("slope_extraversion = 0.04", "slope_greed = 0.04"), encoding="utf-8")
    with pytest.raises(AssertionError, match="must name big-five dimensions"):
        load_mapping_config(path)
