"""真实字幕、邮件与 TextWorld 内容接触边界测试。"""

from __future__ import annotations

from pathlib import Path

from agentsociety2.society import DirectToolCall

from conftest import advance_next, context
from worldmock.life.engine import LifeEngine
from worldmock.life.game import GameOutcomeUnknown
from worldmock.life.models import (
    GameActionRequest,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
)


def _observed_action(engine, round_id: str, tool_name: str, request):
    """按真实观察、登记和结算顺序执行一个人物动作。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    action_id = f"action-{round_id}"
    engine.submit_action(DirectToolCall(ctx, action_id, tool_name, request))
    return observation, engine.commit_decision_round(round_id)[0]


def _progress_events(engine, action_id: str) -> list:
    """返回一个原始活动的全部实际进度事实。"""
    return [event for event in engine.store.read_agent_events("activity_progress", 1)
            if event.action_id == action_id]


def test_subtitles_reveal_only_played_intervals_across_pause_and_repeat(engine_factory) -> None:
    """字幕只按播放位置揭示，暂停空档不越界，重复观看从头开始并递减效果。"""
    engine = engine_factory()
    catalog = engine.observe(context(engine, "catalog"))["content_catalog"]
    assert catalog == [{"item_id": "sintel-en-subtitles", "kind": "subtitle_experience",
                        "duration_seconds": 630, "entertainment_relief_per_hour": 0.3,
                        "experience_label": "subtitle_experience"}]
    request = StartTargetedActivityRequest(
        activity_type="watch", target_id="sintel-en-subtitles",
        planned_duration_seconds=1260, playback_speed=0.5)
    _, started = _observed_action(engine, "watch", "start_targeted_activity", request)
    activity_id = started.activity_id

    advance_next(engine)
    first = _progress_events(engine, "action-watch")[-1]
    revealed = first.data["activity"]["data"]["revealed"]
    assert revealed["position_before_seconds"] == 0
    assert revealed["position_after_seconds"] == 450
    assert all(cue["start_seconds"] < 450 for cue in revealed["cues"])
    assert all(cue["cue_id"] <= 20 for cue in revealed["cues"])
    _observed_action(
        engine, "pause-watch", "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="pause"))
    paused_at = engine.state.current_time
    advance_next(engine)
    assert engine.state.current_time == paused_at + 1
    _observed_action(
        engine, "resume-watch", "set_activity_state",
        SetActivityStateRequest(activity_id=activity_id, operation="resume"))

    advance_next(engine)
    second = _progress_events(engine, "action-watch")[-1]
    later = second.data["activity"]["data"]["revealed"]
    assert later["position_before_seconds"] == 450
    assert later["position_after_seconds"] == 630
    assert all(cue["end_seconds"] > 450 for cue in later["cues"])
    assert engine.state.activities[activity_id].elapsed_seconds == 1260
    progress = engine.state.agents[1].content_progress["sintel-en-subtitles"]
    assert progress.position_seconds == 630 and progress.exposure_count == 1

    repeat = StartTargetedActivityRequest(
        activity_type="watch", target_id="sintel-en-subtitles",
        planned_duration_seconds=240, playback_speed=0.5)
    _, repeated = _observed_action(engine, "watch-repeat", "start_targeted_activity", repeat)
    advance_next(engine)
    repeat_event = _progress_events(engine, "action-watch-repeat")[-1]
    repeat_revealed = repeat_event.data["activity"]["data"]["revealed"]
    assert repeat_revealed["position_before_seconds"] == 0
    assert repeat_revealed["position_after_seconds"] == 120
    assert [cue["cue_id"] for cue in repeat_revealed["cues"]] == [1, 2, 3]
    assert repeated.data["activity"]["data"]["repeat"] is True
    progress = engine.state.agents[1].content_progress["sintel-en-subtitles"]
    assert progress.position_seconds == 120 and progress.exposure_count == 2


def test_mail_body_requires_arrival_and_completed_read_activity(engine_factory) -> None:
    """未来邮件不可读，到达后只见标题，完成阅读才把正文加入人物知识。"""
    engine = engine_factory()
    message = engine.scenario.messages[0]
    initial, rejected = _observed_action(
        engine, "early-mail", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="read_mail", target_id=message.message_id,
                                     planned_duration_seconds=60, playback_speed=1))
    assert initial["messages"] == []
    assert rejected.status == "rejected" and rejected.error_code == "MESSAGE_NOT_VISIBLE"
    advance_next(engine)

    _, sleeping = _observed_action(
        engine, "sleep-to-mail", "start_basic_activity",
        StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=28_799))
    assert sleeping.status == "in_progress"
    advance_next(engine)
    assert engine.state.current_time == int(message.sent_at.timestamp())
    visible = engine.observe(context(engine, "mail-arrived"))["messages"]
    header = next(item for item in visible if item["message_id"] == message.message_id)
    assert header == {"message_id": message.message_id, "thread_id": message.thread_id,
                      "subject": message.subject, "read": False}

    _, reading = _observed_action(
        engine, "read-mail", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="read_mail", target_id=message.message_id,
                                     planned_duration_seconds=60, playback_speed=1))
    assert reading.status == "in_progress"
    assert message.message_id not in engine.state.agents[1].read_message_ids
    advance_next(engine)
    observation = engine.observe(context(engine, "mail-read"))
    record = next(item for item in observation["messages"] if item["message_id"] == message.message_id)
    assert record["read"] is True and record["body"] == message.body
    terminal = _progress_events(engine, "action-read-mail")[-1]
    assert terminal.data["activity"]["data"]["revealed"]["body"] == message.body


def test_textworld_checkpoint_replays_commands_and_rejects_invalid_input(engine_factory) -> None:
    """游戏命令通过真实 TextWorld 执行，检查点恢复重放相同公开状态。"""
    engine = engine_factory()
    _, started = _observed_action(
        engine, "game-start", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="play_game", target_id="textworld-1201",
                                     planned_duration_seconds=3600, playback_speed=1))
    advance_next(engine)
    session_id = started.data["activity"]["data"]["game_session_id"]
    progress = engine.state.agents[1].game_sessions[session_id]
    command = progress.admissible_commands[0]
    _, first = _observed_action(
        engine, "game-first", "game_action",
        GameActionRequest(game_session_id=session_id, command=command))
    assert first.data["game"]["moves"] == 1

    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "game_restore_test"})
    engine.checkpoint({"agent:1": reference})
    run_dir = engine.store.run_dir
    expected = engine.state.agents[1].game_sessions[session_id].model_copy(deep=True)
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    try:
        assert restored.state.agents[1].game_sessions[session_id] == expected
        advance_next(restored)
        before = restored.state.agents[1].game_sessions[session_id]
        invalid = GameActionRequest(game_session_id=session_id, command="not an admissible command")
        _, rejected = _observed_action(restored, "game-invalid", "game_action", invalid)
        assert rejected.status == "rejected" and rejected.error_code == "GAME_COMMAND_NOT_ADMISSIBLE"
        assert restored.state.agents[1].game_sessions[session_id] == before
        advance_next(restored)
        valid = restored.state.agents[1].game_sessions[session_id].admissible_commands[0]
        _, second = _observed_action(
            restored, "game-second", "game_action",
            GameActionRequest(game_session_id=session_id, command=valid))
        assert second.data["game"]["moves"] == 2
    finally:
        restored.close()


def test_unknown_game_outcome_is_explicit_and_preserves_session(engine_factory, monkeypatch) -> None:
    """TextWorld 执行结果不确定时不虚构进度，并在一秒后允许人物重新决策。"""
    engine = engine_factory()
    _, started = _observed_action(
        engine, "unknown-start", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="play_game", target_id="textworld-1201",
                                     planned_duration_seconds=3600, playback_speed=1))
    advance_next(engine)
    session_id = started.data["activity"]["data"]["game_session_id"]
    before = engine.state.agents[1].game_sessions[session_id].model_copy(deep=True)

    def fail_command(*_args, **_kwargs):
        """模拟 TextWorld 已接收但没有可确认返回。"""
        raise GameOutcomeUnknown("TextWorld command outcome is unknown")

    monkeypatch.setattr("worldmock.life.engine.run_game_command", fail_command)
    request = GameActionRequest(game_session_id=session_id, command=before.admissible_commands[0])
    _, result = _observed_action(engine, "unknown-command", "game_action", request)
    assert result.status == "unknown" and result.error_code == "GAME_OUTCOME_UNKNOWN"
    assert engine.state.agents[1].game_sessions[session_id] == before
    assert engine.state.agents[1].next_decision_at == engine.state.current_time + 1
