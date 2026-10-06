"""人设层习惯签名进候选相关度的真实新建路径（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ，裁决 S4；验收台账「呈现」）。

新建运行经 runtime 的真实准备与装配（画像编译走真实 compile_persona、只打桩三次一致性抽取，见 test_input_warnings）：
编译卡片的习惯签名反应文本经 create_options 冻结进运行清单 persona_habit_responses，引擎排序候选时据此给命中条目名或
品类的条目 +1；零点没有目录时间窗覆盖此刻、也没有当日目标，命中的生活活动因此排在生活活动窗口第一位。
"""

from __future__ import annotations

from tests.integration.test_input_warnings import _WC_RHYTHM, _create_run, _extraction, _scenario
from tests.life.conftest import context
from tests.profile.conftest import make_card, make_window
from worldmock.life.relevance import MANIFEST_KEY, manifest_habit_texts
from worldmock.profile.models import HabitSignature


async def test_run_creation_freezes_habit_responses_and_ranks_the_hit_entry_first(tmp_path, monkeypatch) -> None:
    """清单 persona_habit_responses 等于编译卡片（经场景作息覆盖后）的习惯反应文本，与 create_options 一致；运行开始的
    观察里，习惯反应「起床后先安静休息」命中的当前生活活动排在窗口第一位。"""
    habits = (HabitSignature(trigger="起床后", response="起床后先安静休息", time_window=make_window(7.0, 7.5)),
              HabitSignature(trigger="睡前", response="读几页小说", time_window=make_window(22.5, 23.0)))
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path, **_WC_RHYTHM),
                                _extraction(make_card(habits=habits)))
    env = created["env"]
    try:
        manifest = env.engine.context.store.manifest
        assert manifest[MANIFEST_KEY] == {"1": ["起床后先安静休息", "读几页小说"]}
        assert created["options"][MANIFEST_KEY] == {1: ["起床后先安静休息", "读几页小说"]}
        assert manifest_habit_texts(manifest, 1) == ("起床后先安静休息", "读几页小说")
        observed = env.engine.observe(context(env.engine, "habit-relevance"))
        lifestyle = [row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "lifestyle"]
    finally:
        await env.close()
    assert lifestyle[0] == "quiet-rest"
