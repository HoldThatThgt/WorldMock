"""@brief 命令参数解析的模块测试。"""

import pytest

from worldmock.cli import build_parser


def test_category_command_is_deleted_and_environment_has_required_arguments() -> None:
    """@brief 退役命令不再受理，新环境命令和验证来源都必须明确指定。"""
    parser = build_parser()
    arguments = parser.parse_args(["environment", "generate-map", "--locations", "locations.json",
                                   "--seed", "17", "--output", "map.json", "--check"])
    assert arguments.command == "environment" and arguments.environment_command == "generate-map"
    assert arguments.seed == 17 and arguments.check is True
    for command in (["category"], ["validate"], ["validate", "--scenario", "scenario.json",
                                               "--environment", "environment"]):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(command)
        assert error.value.code == 2
