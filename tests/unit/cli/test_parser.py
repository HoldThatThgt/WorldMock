"""@brief 七项原生操作的 CLI 边界只有完整环境；人物卡片没有自己的命令。"""

import pytest

from worldmock.cli import build_parser


def test_current_commands_require_their_explicit_inputs():
    parser = build_parser()
    validated = parser.parse_args(["validate", "--environment", "environment"])
    assert str(validated.environment) == "environment"
    run = parser.parse_args(["run", "--environment", "environment", "--output", "run"])
    assert run.same_second_continuation == "on"
    commands = next(action for action in parser._actions if action.dest == "command")
    assert set(commands.choices) == {"validate", "run", "resume", "replay", "status", "export"}


@pytest.mark.parametrize("arguments", [
    ["category"], ["environment", "generate-map"], ["validate"],
    ["validate", "--environment", "env", "--scenario", "old.json"],
    ["run", "--scenario", "old.json", "--output", "run"],
    ["resume", "--environment", "env", "--output", "run"],
    ["profile"], ["profile", "compile", "--persona", "persona.toml"],
    ["profile", "check", "--persona", "persona.toml"], ["profile", "validate", "--environment", "env"],
])
def test_removed_commands_and_incomplete_inputs_are_rejected(arguments):
    with pytest.raises(SystemExit) as captured:
        build_parser().parse_args(arguments)
    assert captured.value.code == 2
