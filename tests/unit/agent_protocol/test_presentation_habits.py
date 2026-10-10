"""实际人物输入的习惯抽取结果完整冻结到真实运行。"""

def test_compiled_habit_responses_are_frozen_into_actual_run(engine_factory):
    texts = ["起床后买一包薯片", "睡前读几页小说"]
    engine = engine_factory(create_options={"persona_habit_responses": {1: texts}})
    assert engine.context.store.manifest["persona_habit_responses"] == {"1": texts}
