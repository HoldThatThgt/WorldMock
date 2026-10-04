"""@brief Linux 语料验收真实生成和执行 TextWorld，不使用替身或跳过。"""

import json


def test_locked_textworld_generates_and_executes_all_frozen_seeds(tmp_path):
    from worldmock.corpora.fetch import _generate_games

    _generate_games(tmp_path)
    folder = tmp_path / "textworld"
    assert len(list(folder.glob("game-*-options.json"))) == 8
    for seed in range(1201, 1209):
        options = json.loads((folder / f"game-{seed}-options.json").read_text(encoding="utf-8"))
        assert options["textworld_version"] == "1.7.0"
        assert options["seed"] == seed
        assert options["walkthrough"]
        assert (folder / f"game-{seed}.json").is_file()
