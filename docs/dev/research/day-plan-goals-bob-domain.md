# 当日目标 Uncle Bob 审查：domain

状态：**COMPLETE**。

本报告仅审查当日目标领域与节律中下表列出的十一项可观测条款。现有测试杀死十一项独立语义突变，未发现 survivor；不据此声称整份规格穷尽覆盖。

## 权威来源与隔离

- 调用方 checkout：`/private/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-day-goals-review-zit1ffsd/baseline`。
- 基线提交：`2d97d3dbe8cd6120e5e3cb707f8387fd22deb601`。
- 唯一行为权威：`docs/dev/SPEC-day-plan-goals.md`，提交与修订、实际推进与情绪、事件驱动的决策节律章节。
- 适用规则：调用方没有磁盘 AGENTS.md；遵守用户提供的 AGENTS.md 指令与叔鲍勃审查技能。调用方明确授权本地确定性引擎测试和临时生产语义突变，禁止网络、真实模型调用、测试修改及保留修复。
- 隔离审查 checkout：`/tmp/worldmock-bob-domain.Nrr8Vo/checkout`，由 mktemp 产生目录并以 detached worktree 从指定提交创建；审查后已删除。
- 创建前后调用方 `git status --porcelain=v1 --untracked-files=all` 均为空。
- 只改动隔离 worktree 的 schedule.py、rhythm.py、engine.py；没有修改测试、文档、依赖或调用方文件，没有 commit、stash、push。

## 预声明命令与基线

下列完整命令在任何突变前声明，十一项突变逐项使用同一命令，没有看到结果后替换 oracle。工作目录为上述隔离审查 checkout。`LITELLM_LOCAL_MODEL_COST_MAP=True` 使用库提供的本地成本表开关，避免导入时抓取远程成本表；模型键为测试占位值，没有真实模型调用。

```sh
PYTHONDONTWRITEBYTECODE=1 \
LITELLM_LOCAL_MODEL_COST_MAP=True \
AGENTSOCIETY_LLM_API_KEY=test-only \
PYTHONPATH=/tmp/worldmock-bob-domain.Nrr8Vo/checkout/src:.:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/agentsociety2/packages/agentsociety2:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/labelkit/src \
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest \
  tests/life/test_day_plan.py tests/life/test_wake_reasons.py -q --disable-warnings
```

基线退出码 0：**71 passed in 5.22s**。全部恢复后的同命令退出码 0：**71 passed in 4.48s**。每项突变修改前均进行 Python compile 检查，之后实际执行全部 71 个用例；没有用语法、导入或启动失败算 killed。

精确环境、命令、源片段及预声明顺序保存在 predeclared.json（原始记录 `predeclared.json` 见文末）；基线与恢复日志见 baseline.log（原始记录 `baseline.log` 见文末）、restored-baseline.log（原始记录 `restored-baseline.log` 见文末）。

## 实现映射与突变台账

下表每项测试命令均为上面的完整固定命令。源位置是所审查提交的行号；规格位置是 `docs/dev/SPEC-day-plan-goals.md` 的行号。

| 条款名称 | 规格行 | 生产位置与语义突变 | 结果与可观测失败 | 运行证据 |
| --- | --- | --- | --- | --- |
| 目标容量上限 | 34-36 | `src/worldmock/life/schedule.py:27`；Allow a ninth goal | **killed**；exit=1；第九条未抛 ValidationError；真实引擎超容量修订错误地 completed，error_code=None。 | 3 failed, 68 passed in 5.62s（原始记录 `capacity.log` 见文末）；diff（原始记录 `capacity.diff` 见文末） |
| 已推进目标修订保护 | 40-43 | `src/worldmock/life/schedule.py:338`；Drop progressed goals during revision | **killed**；exit=1；修订结果缺少已推进前缀；删除请求后已推进目标不再保留。 | 4 failed, 67 passed in 5.58s（原始记录 `revision.log` 见文末）；diff（原始记录 `revision.diff` 见文末） |
| 登记时刻约束 | 49-50 | `src/worldmock/life/schedule.py:363`；Count settlement before registration | **killed**；exit=1；登记前与登记时刻结束的结算错误进入推进分支，正常领域校验抛 goal progress must follow registration；原合同要求这些合法结算输入保持清单不变。 | 2 failed, 69 passed in 5.58s（原始记录 `registration.log` 见文末）；diff（原始记录 `registration.diff` 见文末） |
| 正时长证据 | 49-50 | `src/worldmock/life/rhythm.py:246`；Invent one second of progress for zero-duration settlement | **killed**；exit=1；零时长结算被补成一秒，目标与情绪发生变化，agent == original 断言失败。 | 1 failed, 70 passed in 5.50s（原始记录 `positive_duration.log` 见文末）；diff（原始记录 `positive_duration.diff` 见文末） |
| 活动证据只消费一次 | 52-53 | `src/worldmock/life/schedule.py:353`；Remove the consumed activity no-op guard | **killed**；exit=1；保存/恢复及修订后的同活动后续结算错误再次消费活动，正常领域校验抛 activity evidence must be unique；原合同要求合法重复结算保持清单不变。 | 2 failed, 69 passed in 4.79s（原始记录 `unique_activity.log` 见文末）；diff（原始记录 `unique_activity.diff` 见文末） |
| 情绪只奖励一次 | 54-55 | `src/worldmock/life/rhythm.py:247`；Reward repeated settlement even when the goal does not change | **killed**；exit=1；相同证据重复结算后 body 不再等于首次结果，单次奖励断言失败。 | 1 failed, 70 passed in 5.43s（原始记录 `single_pulse.log` 见文末）；diff（原始记录 `single_pulse.diff` 见文末） |
| 午夜先结算再失效 | 56-57 | `src/worldmock/life/engine.py:584`；Expire plans before settling the final old-day interval | **killed**；exit=1；真实引擎午夜 activity_progress.data.day_plan 变为 None；读取旧日 day_end 的测试失败，证明旧日推进证据丢失。 | 1 failed, 70 passed in 6.05s（原始记录 `midnight_order.log` 见文末）；diff（原始记录 `midnight_order.diff` 见文末） |
| 任务威胁精确边界 | 72,78 | `src/worldmock/life/schedule.py:329`；Delay the task threat candidate by one second | **killed**；exit=1；候选时刻实际 1000102、期望 1000101；真实引擎调度同时晚一秒。 | 2 failed, 69 passed in 4.74s（原始记录 `task_warning.log` 见文末）；diff（原始记录 `task_warning.diff` 见文末） |
| 闭店威胁精确边界 | 73,78 | `src/worldmock/life/schedule.py:331`；Delay the closing threat candidate by one second | **killed**；exit=1；候选时刻实际 1000102、期望 1000101；真实引擎闭店前预警同时晚一秒。 | 2 failed, 69 passed in 4.63s（原始记录 `closing_warning.log` 见文末）；diff（原始记录 `closing_warning.diff` 见文末） |
| 空闲重估间隔 | 65-78 | `src/worldmock/life/schedule.py:252`；Use the regular interval when no commitment exists | **killed**；exit=1；无承诺候选实际 now+5400、期望 now+10800，自定义 idle 1200 也错误返回 600。 | 3 failed, 68 passed in 4.71s（原始记录 `idle_interval.log` 见文末）；diff（原始记录 `idle_interval.diff` 见文末） |
| 睡眠唤醒限制 | 79 | `src/worldmock/life/schedule.py:302`；Let commitments bypass the sleep wake-reason filter | **killed**；exit=1；有承诺睡眠者 decision_offered=True，原有只允许睡眠唤醒原因的断言失败。 | 1 failed, 70 passed in 4.62s（原始记录 `sleep_filter.log` 见文末）；diff（原始记录 `sleep_filter.diff` 见文末） |

登记边界与重复活动突变的失败发生在已进入行为路径之后的领域状态校验：它们把本应无变化的合法结算变成运行期错误。午夜突变的 TypeError 来自测试读取已经丢失的事件 day_plan，而非生产源码不能解析或载入。这些因果链均保存在完整日志中，没有把环境故障混入 killed。

## 审查结论与边界

| 项目 | 数量 |
| --- | --- |
| 映射的可观测要求 | 11 |
| 选定范围内发现 implementation missing | 0 |
| 选定范围内发现 implementation diverged | 0 |
| killed | 11 |
| survived | 0 |
| invalid | 0 |
| inconclusive | 0 |

没有 survivor，因此本轮没有新增的缺失断言建议。表格中的失败提供正向证据：测试覆盖容量、修订保留、登记时间、零时长、活动去重、重复脉冲、午夜事件完整性、任务与闭店预警精确性、空闲间隔与睡眠限制。

本次没有单独突变文本长度、错误码、所有身份或活动家族组合、场景配置下限、全部事务故障或正式长时运行。相关测试随固定命令执行不等于每条都经过独立突变验证。工具接口、报告三层统计及文档一致性由其他审查负责，不在本报告的完成声明内。

## 恢复证据

- 每项突变都在下一项开始前恢复原始源码字节，执行 `git diff --exit-code` 得到 0，并执行 `git status --porcelain=v1 --untracked-files=all` 得到空输出；逐项记录见 results.json（原始记录 `results.json` 见文末）。
- 最后一次恢复再次运行固定命令得到 71 passed，之后 diff/status 仍为空。
- `git worktree remove /tmp/worldmock-bob-domain.Nrr8Vo/checkout` 成功，无 force；`git worktree list --porcelain` 已无该路径。日志与差异证据已复制到本报告旁的独立证据目录。
- 清理后调用方 HEAD 仍为 `2d97d3dbe8cd6120e5e3cb707f8387fd22deb601`，`git diff --exit-code` 为 0，完整 status 为空。调用方没有被修改。

## 原始命令、补丁与结果

### baseline.log

```text
.......................................................................  [100%]
71 passed in 5.22s
```

### capacity.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..eb4fbcb 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -24,7 +24,7 @@ CLOSING_WARNING_SECONDS = 900
 #: 可见工作任务的截止预警窗口。
 TASK_DUE_SOON_SECONDS = 1800
 #: 有序目标清单的容量上限。
-MAX_PLAN_GOALS = 8
+MAX_PLAN_GOALS = 9
 #: 单条自然语言目标的字符上限。
 PLAN_GOAL_MAX_CHARS = 300
```

### capacity.log

```text
F.................F...............F....................................  [100%]
=================================== FAILURES ===================================
__________ test_empty_and_eight_goals_are_valid_and_ninth_is_rejected __________

    def test_empty_and_eight_goals_are_valid_and_ninth_is_rejected():
        assert initial_day_plan([], 1000).goals == ()
        goals = tuple(goal(kind) for kind in INTENT_KINDS[:8])
        assert len(initial_day_plan(goals, 1000).goals) == 8
>       with pytest.raises(ValidationError):
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       Failed: DID NOT RAISE ValidationError

tests/life/test_day_plan.py:40: Failed
___________________ test_revision_merged_capacity_is_checked ___________________

    def test_revision_merged_capacity_is_checked():
        progressed = tuple(goal(kind, progressed_at=200, progress_activity_id=f"a-{kind}")
                           for kind in INTENT_KINDS[:8])
>       with pytest.raises(ValidationError):
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       Failed: DID NOT RAISE ValidationError

tests/life/test_day_plan.py:115: Failed
______ test_revision_merged_limit_returns_domain_error_and_preserves_plan ______

engine_factory = <function engine_factory.<locals>.create at 0x114387d80>

    def test_revision_merged_limit_returns_domain_error_and_preserves_plan(engine_factory):
        from tests.life.test_revision_contract import _commit_staged
        engine = engine_factory()
        now = engine.state.current_time
        goals = [goal(kind, registered=now - 60, progressed_at=now, progress_activity_id=f"a-{kind}")
                 for kind in INTENT_KINDS[:8]]
        staged = engine.state.model_copy(deep=True)
        staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": DayPlan(
            day_end=day_end_seconds(now, engine.scenario.timezone), goals=goals)})
        _commit_staged(engine, staged)
        original = engine.state.agents[1].day_plan
        result = act(engine, "too-many", "revise_day_plan", ReviseDayPlanRequest(
            goals=[{"intent_kind": INTENT_KINDS[8], "goal": "多一条"}]))
>       assert result.error_code == "PLAN_GOALS_INVALID"
E       AssertionError: assert None == 'PLAN_GOALS_INVALID'
E        +  where None = ActionResult(action_id='action-too-many', agent_id=1, status=<ActionStatus.COMPLETED: 'completed'>, occurred_at=176754...ntent_kind': '家务', 'goal': '多一条', 'registered_at': 1767542400, 'progressed_at': None, 'progress_activity_id': None}]}}).error_code

tests/life/test_day_plan.py:272: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_day_plan.py::test_empty_and_eight_goals_are_valid_and_ninth_is_rejected
FAILED tests/life/test_day_plan.py::test_revision_merged_capacity_is_checked
FAILED tests/life/test_day_plan.py::test_revision_merged_limit_returns_domain_error_and_preserves_plan
3 failed, 68 passed in 5.62s
```

### closing_warning.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..f3fc351 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -328,7 +328,7 @@ def next_decision_time(query: NextDecisionQuery) -> int | None:
     if not query.is_asleep and query.next_task_due_at is not None:
         candidates.append(query.next_task_due_at - TASK_DUE_SOON_SECONDS)
     if not query.is_asleep and query.has_commitments and query.location_closing_at is not None:
-        candidates.append(query.location_closing_at - CLOSING_WARNING_SECONDS)
+        candidates.append(query.location_closing_at - CLOSING_WARNING_SECONDS + 1)
     future = [moment for moment in candidates if moment > query.now]
     return min(future) if future else None
```

### closing_warning.log

```text
.................................................................F....F  [100%]
=================================== FAILURES ===================================
_ test_warning_candidates_are_exact_not_feedback_aligned[location_closing_at-1001001-1000101] _

field = 'location_closing_at', deadline = 1001001, expected = 1000101

    @pytest.mark.parametrize("field,deadline,expected", [
        ("next_task_due_at", NOW + 1901, NOW + 101),
        ("location_closing_at", NOW + 1001, NOW + 101),
    ])
    def test_warning_candidates_are_exact_not_feedback_aligned(field, deadline, expected):
        query = NextDecisionQuery(now=NOW, last_decision_time=NOW, has_commitments=True, **{field: deadline})
>       assert next_decision_time(query) == expected
E       assert 1000102 == 1000101
E        +  where 1000102 = next_decision_time(NextDecisionQuery(now=1000000, planned_end=None, felt_band_crossing_times=(), scheduled_arrival_times=(), last_decisio..._commitments=True, location_hours_change_times=(), next_task_due_at=None, location_closing_at=1001001, is_asleep=False))

tests/life/test_wake_reasons.py:95: AssertionError
_________ test_engine_schedules_exact_closing_warning_with_commitment __________

engine_factory = <function engine_factory.<locals>.create at 0x119a174c0>

    def test_engine_schedules_exact_closing_warning_with_commitment(engine_factory):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from tests.life.test_revision_contract import _commit_staged
        engine = engine_factory()
        state = engine.state.model_copy(deep=True)
        local = datetime.fromtimestamp(state.current_time, ZoneInfo(engine.scenario.timezone))
        closing_minute = local.hour * 60 + local.minute + 17
        closing = int(local.replace(second=0).timestamp()) + 17 * 60
        state.pending_events = []
        state.agents[1] = state.agents[1].model_copy(update={"visible_task_ids": set(),
            "day_plan": DayPlan(day_end=closing + 10000, goals=())})
        location_id = state.agents[1].location_id
        state.opening_exceptions[location_id][local.date().isoformat()] = [(0, closing_minute)]
        _commit_staged(engine, state)
        result = act(engine, "closing-wait", "start_basic_activity",
                     StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
        assert result.status == "in_progress"
>       assert engine.scheduling_state().next_event_at.timestamp() == closing - 900
E       AssertionError: assert 1767542521.0 == (1767543420 - 900)
E        +  where 1767542521.0 = <built-in method timestamp of datetime.datetime object at 0x118f159b0>()
E        +    where <built-in method timestamp of datetime.datetime object at 0x118f159b0> = datetime.datetime(2026, 1, 5, 0, 2, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')).timestamp
E        +      where datetime.datetime(2026, 1, 5, 0, 2, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')) = SchedulingState(current_time=datetime.datetime(2026, 1, 5, 0, 0, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')), ready..._event_at=datetime.datetime(2026, 1, 5, 0, 2, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')), world_state_version=2).next_event_at
E        +        where SchedulingState(current_time=datetime.datetime(2026, 1, 5, 0, 0, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')), ready..._event_at=datetime.datetime(2026, 1, 5, 0, 2, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')), world_state_version=2) = scheduling_state()
E        +          where scheduling_state = <worldmock.life.engine.LifeEngine object at 0x11420cf80>.scheduling_state

tests/life/test_wake_reasons.py:189: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_wake_reasons.py::test_warning_candidates_are_exact_not_feedback_aligned[location_closing_at-1001001-1000101]
FAILED tests/life/test_wake_reasons.py::test_engine_schedules_exact_closing_warning_with_commitment
2 failed, 69 passed in 4.63s
```

### idle_interval.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..a8561c2 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -249,7 +249,7 @@ def _reconsideration_due(snapshot: WakeSnapshot) -> bool:
 def reconsideration_interval(query: WakeSnapshot | NextDecisionQuery) -> int:
     """@brief 判定与调度共用同一在手承诺口径选择重估间隔。"""
     return (query.reconsideration_interval_seconds if query.has_commitments
-            else query.idle_reconsideration_interval_seconds)
+            else query.reconsideration_interval_seconds)
 
 
 def evaluate_wake_reasons(snapshot: WakeSnapshot) -> WakeVerdict:
```

### idle_interval.log

```text
...........................................................F..FF.......  [100%]
=================================== FAILURES ===================================
____ test_before_warning_and_reconsideration_boundaries_is_silent[fields7] _____

fields = {'last_decision_time': 989201}

    @pytest.mark.parametrize("fields", [
        {}, {"planned_end": NOW + 1}, {"next_task_due_at": NOW + 1801},
        {"next_task_due_at": NOW}, {"has_commitments": True, "location_closing_at": NOW + 901},
        {"has_commitments": True, "location_closing_at": NOW}, {"location_closing_at": NOW + 900},
        {"last_decision_time": NOW - 10799},
        {"has_commitments": True, "last_decision_time": NOW - 5399},
    ])
    def test_before_warning_and_reconsideration_boundaries_is_silent(fields):
        verdict = evaluate_wake_reasons(WakeSnapshot(now=NOW, **fields))
>       assert not verdict.decision_offered and verdict.wake_reasons == []
E       AssertionError: assert (not True)
E        +  where True = WakeVerdict(decision_offered=True, wake_reasons=[<WakeReason.RECONSIDERATION_INTERVAL: 'reconsideration_interval'>]).decision_offered

tests/life/test_wake_reasons.py:69: AssertionError
____________ test_candidate_and_wake_use_same_interval[False-10800] ____________

committed = False, interval = 10800

    @pytest.mark.parametrize("committed,interval", [(True, 5400), (False, 10800)])
    def test_candidate_and_wake_use_same_interval(committed, interval):
        query = NextDecisionQuery(now=NOW, last_decision_time=NOW, has_commitments=committed)
>       assert next_decision_time(query) == NOW + interval
E       assert 1005400 == (1000000 + 10800)
E        +  where 1005400 = next_decision_time(NextDecisionQuery(now=1000000, planned_end=None, felt_band_crossing_times=(), scheduled_arrival_times=(), last_decisio...as_commitments=False, location_hours_change_times=(), next_task_due_at=None, location_closing_at=None, is_asleep=False))

tests/life/test_wake_reasons.py:75: AssertionError
__________ test_custom_intervals_and_earlier_events_remain_effective ___________

    def test_custom_intervals_and_earlier_events_remain_effective():
        query = NextDecisionQuery(now=NOW, last_decision_time=NOW, reconsideration_interval_seconds=600,
                                  idle_reconsideration_interval_seconds=1200, has_commitments=False)
>       assert next_decision_time(query) == NOW + 1200
E       assert 1000600 == (1000000 + 1200)
E        +  where 1000600 = next_decision_time(NextDecisionQuery(now=1000000, planned_end=None, felt_band_crossing_times=(), scheduled_arrival_times=(), last_decisio...as_commitments=False, location_hours_change_times=(), next_task_due_at=None, location_closing_at=None, is_asleep=False))

tests/life/test_wake_reasons.py:82: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_wake_reasons.py::test_before_warning_and_reconsideration_boundaries_is_silent[fields7]
FAILED tests/life/test_wake_reasons.py::test_candidate_and_wake_use_same_interval[False-10800]
FAILED tests/life/test_wake_reasons.py::test_custom_intervals_and_earlier_events_remain_effective
3 failed, 68 passed in 4.71s
```

### midnight_order.diff

```diff
diff --git a/src/worldmock/life/engine.py b/src/worldmock/life/engine.py
index cc913f2..b1a64ca 100644
--- a/src/worldmock/life/engine.py
+++ b/src/worldmock/life/engine.py
@@ -581,9 +581,9 @@ class LifeEngine(ObservationMixin, StorefrontRuntimeMixin):
         drafts, results = self._advance_activities(staged, target_epoch)
         staged, background, arrivals = self._process_scheduled(staged, target_epoch)
         drafts.extend(background)
-        staged = apply_engine_rhythm(self, staged, target_epoch, interval, arrivals, drafts)
         for agent_id in expired_plan_agent_ids(staged):
             staged = _put_agent(staged, staged.agents[agent_id].model_copy(update={"day_plan": None}, deep=True))
+        staged = apply_engine_rhythm(self, staged, target_epoch, interval, arrivals, drafts)
         if not drafts:
             drafts.append(EventDraft(event_type="environment_event", occurred_at=target_epoch,
                                      visibility="system", source_kind="environment",
```

### midnight_order.log

```text
................................F......................................  [100%]
=================================== FAILURES ===================================
________ test_midnight_final_interval_progresses_old_plan_before_expiry ________

engine_factory = <function engine_factory.<locals>.create at 0x113ef0ae0>

    def test_midnight_final_interval_progresses_old_plan_before_expiry(engine_factory):
        from tests.life.test_revision_contract import _commit_staged
        engine = engine_factory()
        now = engine.state.current_time
        midnight = day_end_seconds(now, engine.scenario.timezone)
        staged = engine.state.model_copy(deep=True)
        staged.current_time = midnight - 60
        staged.pending_events = []
        staged.agents[1] = staged.agents[1].model_copy(update={"next_decision_at": staged.current_time,
            "day_plan": DayPlan(day_end=midnight, goals=[goal("休息", registered=staged.current_time)])})
        _commit_staged(engine, staged)
        started = act(engine, "last-minute", "start_basic_activity",
                      StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60))
        advance_next(engine)
        assert engine.state.current_time == midnight
        assert engine.state.agents[1].day_plan is None
        progress = engine.store.read_agent_events("activity_progress", 1)[-1].data["day_plan"]
>       assert progress["day_end"] == midnight
               ^^^^^^^^^^^^^^^^^^^
E       TypeError: 'NoneType' object is not subscriptable

tests/life/test_day_plan.py:241: TypeError
=========================== short test summary info ============================
FAILED tests/life/test_day_plan.py::test_midnight_final_interval_progresses_old_plan_before_expiry
1 failed, 70 passed in 6.05s
```

### positive_duration.diff

```diff
diff --git a/src/worldmock/life/rhythm.py b/src/worldmock/life/rhythm.py
index 618b32c..0c97f77 100644
--- a/src/worldmock/life/rhythm.py
+++ b/src/worldmock/life/rhythm.py
@@ -243,7 +243,7 @@ def _goal_and_deadline_pulses(agent: AgentState, inputs: RhythmInputs) -> AgentS
     data = inputs.progress_data.get(agent.agent_id)
     if plan is not None and data is not None and data["activity"]["agent_id"] == agent.agent_id:
         changed = rhythm_schedule.progress_day_plan(
-            plan, data["activity"], data["interval_start"], data["interval_end"])
+            plan, data["activity"], data["interval_start"], max(data["interval_end"], data["interval_start"] + 1))
         if changed != plan:
             body = apply_mood_pulse(body, "goal_progressed")
             plan = changed
```

### positive_duration.log

```text
...............................F.......................................  [100%]
=================================== FAILURES ===================================
____ test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time _____

engine_factory = <function engine_factory.<locals>.create at 0x11b670360>

    def test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time(engine_factory):
        engine = engine_factory()
        now = engine.state.current_time
        agent = engine.state.agents[1].model_copy(update={"day_plan": DayPlan(
            day_end=now + 1000, goals=[goal("休息", registered=now), goal("晨间例程", registered=now)])})
        data = {"activity": activity("wait"), "interval_start": now, "interval_end": now + 60}
        inputs = engine_rhythm_inputs(engine, engine.state, 60, progress_data={1: data})
        result = _goal_and_deadline_pulses(agent, inputs)
        from worldmock.life.body import apply_mood_pulse
        assert result.body == apply_mood_pulse(agent.body, "goal_progressed")
        assert _goal_and_deadline_pulses(result, inputs).body == result.body
        assert result.day_plan.goals[1].progressed_at is None
        for changed in ({"agent_id": 2}, {"status": "cancelled"}, {"status": "failed"}):
            data["activity"] = activity("wait", **changed)
            assert _goal_and_deadline_pulses(agent, inputs) == agent
        data["activity"] = activity("wait")
        data["interval_end"] = now
>       assert _goal_and_deadline_pulses(agent, inputs) == agent
E       assert AgentState(ag...al_reveals=[]) == AgentState(ag...al_reveals=[])
E         
E         Use -v to get more diff

tests/life/test_day_plan.py:220: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time
1 failed, 70 passed in 5.50s
```

### predeclared.json

```json
{
  "checkout": "/tmp/worldmock-bob-domain.Nrr8Vo/checkout",
  "commit": "2d97d3dbe8cd6120e5e3cb707f8387fd22deb601",
  "specification": "docs/dev/SPEC-day-plan-goals.md",
  "command": [
    "/Users/atishoo/Project/WorldMock/.venv/bin/python",
    "-m",
    "pytest",
    "tests/life/test_day_plan.py",
    "tests/life/test_wake_reasons.py",
    "-q",
    "--disable-warnings"
  ],
  "environment": {
    "PYTHONDONTWRITEBYTECODE": "1",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "AGENTSOCIETY_LLM_API_KEY": "test-only",
    "PYTHONPATH": "/tmp/worldmock-bob-domain.Nrr8Vo/checkout/src:.:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/agentsociety2/packages/agentsociety2:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/labelkit/src"
  },
  "mutations": [
    {
      "name": "capacity",
      "spec_lines": "34-36",
      "file": "src/worldmock/life/schedule.py",
      "old": "MAX_PLAN_GOALS = 8",
      "new": "MAX_PLAN_GOALS = 9",
      "violation": "Allow a ninth goal"
    },
    {
      "name": "revision",
      "spec_lines": "40-43",
      "file": "src/worldmock/life/schedule.py",
      "old": "kept = tuple(goal for goal in plan.goals if goal.progressed_at is not None)",
      "new": "kept = ()",
      "violation": "Drop progressed goals during revision"
    },
    {
      "name": "registration",
      "spec_lines": "49-50",
      "file": "src/worldmock/life/schedule.py",
      "old": "stop <= max(start, goal.registered_at)",
      "new": "stop <= start",
      "violation": "Count settlement before registration"
    },
    {
      "name": "positive_duration",
      "spec_lines": "49-50",
      "file": "src/worldmock/life/rhythm.py",
      "old": "plan, data[\"activity\"], data[\"interval_start\"], data[\"interval_end\"])",
      "new": "plan, data[\"activity\"], data[\"interval_start\"], max(data[\"interval_end\"], data[\"interval_start\"] + 1))",
      "violation": "Invent one second of progress for zero-duration settlement"
    },
    {
      "name": "unique_activity",
      "spec_lines": "52-53",
      "file": "src/worldmock/life/schedule.py",
      "old": "if any(goal.progress_activity_id == activity_id for goal in plan.goals):",
      "new": "if False and any(goal.progress_activity_id == activity_id for goal in plan.goals):",
      "violation": "Remove the consumed activity no-op guard"
    },
    {
      "name": "single_pulse",
      "spec_lines": "54-55",
      "file": "src/worldmock/life/rhythm.py",
      "old": "if changed != plan:",
      "new": "if True:",
      "violation": "Reward repeated settlement even when the goal does not change"
    },
    {
      "name": "midnight_order",
      "spec_lines": "56-57",
      "file": "src/worldmock/life/engine.py",
      "old": "        staged = apply_engine_rhythm(self, staged, target_epoch, interval, arrivals, drafts)\n        for agent_id in expired_plan_agent_ids(staged):\n            staged = _put_agent(staged, staged.agents[agent_id].model_copy(update={\"day_plan\": None}, deep=True))",
      "new": "        for agent_id in expired_plan_agent_ids(staged):\n            staged = _put_agent(staged, staged.agents[agent_id].model_copy(update={\"day_plan\": None}, deep=True))\n        staged = apply_engine_rhythm(self, staged, target_epoch, interval, arrivals, drafts)",
      "violation": "Expire plans before settling the final old-day interval"
    },
    {
      "name": "task_warning",
      "spec_lines": "72,78",
      "file": "src/worldmock/life/schedule.py",
      "old": "candidates.append(query.next_task_due_at - TASK_DUE_SOON_SECONDS)",
      "new": "candidates.append(query.next_task_due_at - TASK_DUE_SOON_SECONDS + 1)",
      "violation": "Delay the task threat candidate by one second"
    },
    {
      "name": "closing_warning",
      "spec_lines": "73,78",
      "file": "src/worldmock/life/schedule.py",
      "old": "candidates.append(query.location_closing_at - CLOSING_WARNING_SECONDS)",
      "new": "candidates.append(query.location_closing_at - CLOSING_WARNING_SECONDS + 1)",
      "violation": "Delay the closing threat candidate by one second"
    },
    {
      "name": "idle_interval",
      "spec_lines": "65-78",
      "file": "src/worldmock/life/schedule.py",
      "old": "else query.idle_reconsideration_interval_seconds)",
      "new": "else query.reconsideration_interval_seconds)",
      "violation": "Use the regular interval when no commitment exists"
    },
    {
      "name": "sleep_filter",
      "spec_lines": "79",
      "file": "src/worldmock/life/schedule.py",
      "old": "if snap.is_asleep:",
      "new": "if snap.is_asleep and not snap.has_commitments:",
      "violation": "Let commitments bypass the sleep wake-reason filter"
    }
  ]
}
```

### registration.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..d52b70a 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -360,7 +360,7 @@ def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int) -> Da
         family = f"lifestyle:{category}"
     stop = min(end, plan.day_end)
     for index, goal in enumerate(plan.goals):
-        if goal.progressed_at is not None or stop <= max(start, goal.registered_at):
+        if goal.progressed_at is not None or stop <= start:
             continue
         if family not in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]:
             continue
```

### registration.log

```text
....................FF.................................................  [100%]
=================================== FAILURES ===================================
_ test_progress_requires_positive_registered_day_matching_activity[payload1-0-99] _

payload = {'activity_id': 'activity-a', 'agent_id': 1, 'kind': 'work', 'status': 'active'}
start = 0, end = 99

    @pytest.mark.parametrize("payload,start,end", [
        (activity(), 100, 100), (activity(), 0, 99), (activity(), 0, 100),
        (activity(status="cancelled"), 100, 200), (activity(status="failed"), 100, 200),
        (activity("wait"), 100, 200), (activity(), 1000, 1100),
    ])
    def test_progress_requires_positive_registered_day_matching_activity(payload, start, end):
        plan = DayPlan(day_end=1000, goals=[goal()])
>       assert progress_day_plan(plan, payload, start, end) == plan
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/life/test_day_plan.py:126: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

plan = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind='工作', goal='推进手头工作', registered_at=100, progressed_at=None, progress_activity_id=None),))
activity = {'activity_id': 'activity-a', 'agent_id': 1, 'kind': 'work', 'status': 'active'}
start = 0, end = 99

    def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int) -> DayPlan:
        """@brief 正时长结算最多推进一个匹配目标，活动证据在当日只消费一次。"""
        activity_id = activity["activity_id"]
        if activity["status"] in {"cancelled", "failed"} or end <= start:
            return plan
        if any(goal.progress_activity_id == activity_id for goal in plan.goals):
            return plan
        family = activity["kind"]
        if family == "lifestyle":
            category = activity.get("category")
            if category is None:
                return plan
            family = f"lifestyle:{category}"
        stop = min(end, plan.day_end)
        for index, goal in enumerate(plan.goals):
            if goal.progressed_at is not None or stop <= start:
                continue
            if family not in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]:
                continue
            progressed = goal.model_copy(update={"progressed_at": stop, "progress_activity_id": activity_id})
            goals = plan.goals[:index] + (progressed,) + plan.goals[index + 1:]
>           return DayPlan(day_end=plan.day_end, goals=goals)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E           pydantic_core._pydantic_core.ValidationError: 1 validation error for DayPlan
E           goals.0
E             Value error, goal progress must follow registration [type=value_error, input_value=PlanGoal(intent_kind='工...ctivity_id='activity-a'), input_type=PlanGoal]
E               For further information visit https://errors.pydantic.dev/2.12/v/value_error

src/worldmock/life/schedule.py:369: ValidationError
------------------------------ Captured log call -------------------------------
ERROR    worldmock.life.schedule:schedule.py:192 Goal progress must follow registration
_ test_progress_requires_positive_registered_day_matching_activity[payload2-0-100] _

payload = {'activity_id': 'activity-a', 'agent_id': 1, 'kind': 'work', 'status': 'active'}
start = 0, end = 100

    @pytest.mark.parametrize("payload,start,end", [
        (activity(), 100, 100), (activity(), 0, 99), (activity(), 0, 100),
        (activity(status="cancelled"), 100, 200), (activity(status="failed"), 100, 200),
        (activity("wait"), 100, 200), (activity(), 1000, 1100),
    ])
    def test_progress_requires_positive_registered_day_matching_activity(payload, start, end):
        plan = DayPlan(day_end=1000, goals=[goal()])
>       assert progress_day_plan(plan, payload, start, end) == plan
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/life/test_day_plan.py:126: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

plan = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind='工作', goal='推进手头工作', registered_at=100, progressed_at=None, progress_activity_id=None),))
activity = {'activity_id': 'activity-a', 'agent_id': 1, 'kind': 'work', 'status': 'active'}
start = 0, end = 100

    def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int) -> DayPlan:
        """@brief 正时长结算最多推进一个匹配目标，活动证据在当日只消费一次。"""
        activity_id = activity["activity_id"]
        if activity["status"] in {"cancelled", "failed"} or end <= start:
            return plan
        if any(goal.progress_activity_id == activity_id for goal in plan.goals):
            return plan
        family = activity["kind"]
        if family == "lifestyle":
            category = activity.get("category")
            if category is None:
                return plan
            family = f"lifestyle:{category}"
        stop = min(end, plan.day_end)
        for index, goal in enumerate(plan.goals):
            if goal.progressed_at is not None or stop <= start:
                continue
            if family not in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]:
                continue
            progressed = goal.model_copy(update={"progressed_at": stop, "progress_activity_id": activity_id})
            goals = plan.goals[:index] + (progressed,) + plan.goals[index + 1:]
>           return DayPlan(day_end=plan.day_end, goals=goals)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E           pydantic_core._pydantic_core.ValidationError: 1 validation error for DayPlan
E           goals.0
E             Value error, goal progress must follow registration [type=value_error, input_value=PlanGoal(intent_kind='工...ctivity_id='activity-a'), input_type=PlanGoal]
E               For further information visit https://errors.pydantic.dev/2.12/v/value_error

src/worldmock/life/schedule.py:369: ValidationError
------------------------------ Captured log call -------------------------------
ERROR    worldmock.life.schedule:schedule.py:192 Goal progress must follow registration
=========================== short test summary info ============================
FAILED tests/life/test_day_plan.py::test_progress_requires_positive_registered_day_matching_activity[payload1-0-99]
FAILED tests/life/test_day_plan.py::test_progress_requires_positive_registered_day_matching_activity[payload2-0-100]
2 failed, 69 passed in 5.58s
```

### restored-baseline.log

```text
.......................................................................  [100%]
71 passed in 4.48s
```

### results.json

```json
[
  {
    "name": "capacity",
    "source_line": 27,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/capacity.log",
    "failures": [
      "FAILED tests/life/test_day_plan.py::test_empty_and_eight_goals_are_valid_and_ninth_is_rejected",
      "FAILED tests/life/test_day_plan.py::test_revision_merged_capacity_is_checked",
      "FAILED tests/life/test_day_plan.py::test_revision_merged_limit_returns_domain_error_and_preserves_plan"
    ],
    "summary": "3 failed, 68 passed in 5.62s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "revision",
    "source_line": 338,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/revision.log",
    "failures": [
      "FAILED tests/life/test_day_plan.py::test_revision_keeps_progressed_prefix_and_original_registration_for_unchanged_goals",
      "FAILED tests/life/test_day_plan.py::test_revision_merged_capacity_is_checked",
      "FAILED tests/life/test_day_plan.py::test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization",
      "FAILED tests/life/test_day_plan.py::test_revision_merged_limit_returns_domain_error_and_preserves_plan"
    ],
    "summary": "4 failed, 67 passed in 5.58s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "registration",
    "source_line": 363,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/registration.log",
    "failures": [
      "FAILED tests/life/test_day_plan.py::test_progress_requires_positive_registered_day_matching_activity[payload1-0-99]",
      "FAILED tests/life/test_day_plan.py::test_progress_requires_positive_registered_day_matching_activity[payload2-0-100]"
    ],
    "summary": "2 failed, 69 passed in 5.58s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "positive_duration",
    "source_line": 246,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/positive_duration.log",
    "failures": [
      "FAILED tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time"
    ],
    "summary": "1 failed, 70 passed in 5.50s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "unique_activity",
    "source_line": 353,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/unique_activity.log",
    "failures": [
      "FAILED tests/life/test_day_plan.py::test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization",
      "FAILED tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time"
    ],
    "summary": "2 failed, 69 passed in 4.79s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "single_pulse",
    "source_line": 247,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/single_pulse.log",
    "failures": [
      "FAILED tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time"
    ],
    "summary": "1 failed, 70 passed in 5.43s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "midnight_order",
    "source_line": 584,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/midnight_order.log",
    "failures": [
      "FAILED tests/life/test_day_plan.py::test_midnight_final_interval_progresses_old_plan_before_expiry"
    ],
    "summary": "1 failed, 70 passed in 6.05s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "task_warning",
    "source_line": 329,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/task_warning.log",
    "failures": [
      "FAILED tests/life/test_wake_reasons.py::test_warning_candidates_are_exact_not_feedback_aligned[next_task_due_at-1001901-1000101]",
      "FAILED tests/life/test_wake_reasons.py::test_engine_schedules_exact_task_warning_and_appends_threat_reason"
    ],
    "summary": "2 failed, 69 passed in 4.74s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "closing_warning",
    "source_line": 331,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/closing_warning.log",
    "failures": [
      "FAILED tests/life/test_wake_reasons.py::test_warning_candidates_are_exact_not_feedback_aligned[location_closing_at-1001001-1000101]",
      "FAILED tests/life/test_wake_reasons.py::test_engine_schedules_exact_closing_warning_with_commitment"
    ],
    "summary": "2 failed, 69 passed in 4.63s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "idle_interval",
    "source_line": 252,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/idle_interval.log",
    "failures": [
      "FAILED tests/life/test_wake_reasons.py::test_before_warning_and_reconsideration_boundaries_is_silent[fields7]",
      "FAILED tests/life/test_wake_reasons.py::test_candidate_and_wake_use_same_interval[False-10800]",
      "FAILED tests/life/test_wake_reasons.py::test_custom_intervals_and_earlier_events_remain_effective"
    ],
    "summary": "3 failed, 68 passed in 4.71s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  },
  {
    "name": "sleep_filter",
    "source_line": 302,
    "exit": 1,
    "log_path": "/tmp/worldmock-bob-domain.Nrr8Vo/sleep_filter.log",
    "failures": [
      "FAILED tests/life/test_wake_reasons.py::test_sleep_accepts_only_existing_sleep_reasons"
    ],
    "summary": "1 failed, 70 passed in 4.62s",
    "restored": {
      "diff_exit": 0,
      "status_exit": 0,
      "status": ""
    }
  }
]
```

### revision.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..59387e0 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -335,7 +335,7 @@ def next_decision_time(query: NextDecisionQuery) -> int | None:
 
 def revise_day_plan(plan: DayPlan, now: int, new_goals: Sequence[PlanGoal]) -> DayPlan:
     """@brief 保留已推进目标，整体替换未推进目标，未改写条目保留登记时刻。"""
-    kept = tuple(goal for goal in plan.goals if goal.progressed_at is not None)
+    kept = ()
     progressed_kinds = {goal.intent_kind for goal in kept}
     if any(goal.intent_kind in progressed_kinds for goal in new_goals):
         _LOGGER.error("A progressed day goal cannot be resubmitted")
```

### revision.log

```text
.................FF.......F.......F....................................  [100%]
=================================== FAILURES ===================================
_ test_revision_keeps_progressed_prefix_and_original_registration_for_unchanged_goals _

    def test_revision_keeps_progressed_prefix_and_original_registration_for_unchanged_goals():
        pending = goal("工作", "写报告")
        progressed = goal("休息", "放松", progressed_at=200, progress_activity_id="a")
        plan = DayPlan(day_end=1000, goals=[pending, progressed, goal("运动", "散步")])
        changed = revise_day_plan(plan, 300, [goal("运动", "散步", 300), goal("工作", "新报告", 300)])
>       assert changed.goals == (progressed, plan.goals[2], goal("工作", "新报告", 300))
E       AssertionError: assert (PlanGoal(int...vity_id=None)) == (PlanGoal(int...vity_id=None))
E         
E         At index 0 diff: PlanGoal(intent_kind='运动', goal='散步', registered_at=100, progressed_at=None, progress_activity_id=None) != PlanGoal(intent_kind='休息', goal='放松', registered_at=100, progressed_at=200, progress_activity_id='a')
E         Right contains one more item: PlanGoal(intent_kind='工作', goal='新报告', registered_at=300, progressed_at=None, progress_activity_id=None)
E         Use -v to get more diff

tests/life/test_day_plan.py:105: AssertionError
___________________ test_revision_merged_capacity_is_checked ___________________

    def test_revision_merged_capacity_is_checked():
        progressed = tuple(goal(kind, progressed_at=200, progress_activity_id=f"a-{kind}")
                           for kind in INTENT_KINDS[:8])
>       with pytest.raises(ValidationError):
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       Failed: DID NOT RAISE ValidationError

tests/life/test_day_plan.py:115: Failed
_ test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization _

    def test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization():
        plan = DayPlan(day_end=1000, goals=[goal("通勤"), goal("外出")])
        first = progress_day_plan(plan, activity("travel"), 100, 200)
        assert first.goals[0].progressed_at == 200
        assert first.goals[1].progressed_at is None
        loaded = DayPlan.model_validate_json(first.model_dump_json())
        revised = revise_day_plan(loaded, 250, [goal("外出", registered=250)])
>       assert progress_day_plan(revised, activity("travel"), 250, 300) == revised
E       AssertionError: assert DayPlan(day_e...ctivity-a'),)) == DayPlan(day_e...ty_id=None),))
E         
E         Use -v to get more diff

tests/life/test_day_plan.py:136: AssertionError
______ test_revision_merged_limit_returns_domain_error_and_preserves_plan ______

engine_factory = <function engine_factory.<locals>.create at 0x11430a5c0>

    def test_revision_merged_limit_returns_domain_error_and_preserves_plan(engine_factory):
        from tests.life.test_revision_contract import _commit_staged
        engine = engine_factory()
        now = engine.state.current_time
        goals = [goal(kind, registered=now - 60, progressed_at=now, progress_activity_id=f"a-{kind}")
                 for kind in INTENT_KINDS[:8]]
        staged = engine.state.model_copy(deep=True)
        staged.agents[1] = staged.agents[1].model_copy(update={"day_plan": DayPlan(
            day_end=day_end_seconds(now, engine.scenario.timezone), goals=goals)})
        _commit_staged(engine, staged)
        original = engine.state.agents[1].day_plan
        result = act(engine, "too-many", "revise_day_plan", ReviseDayPlanRequest(
            goals=[{"intent_kind": INTENT_KINDS[8], "goal": "多一条"}]))
>       assert result.error_code == "PLAN_GOALS_INVALID"
E       AssertionError: assert None == 'PLAN_GOALS_INVALID'
E        +  where None = ActionResult(action_id='action-too-many', agent_id=1, status=<ActionStatus.COMPLETED: 'completed'>, occurred_at=176754...ntent_kind': '家务', 'goal': '多一条', 'registered_at': 1767542400, 'progressed_at': None, 'progress_activity_id': None}]}}).error_code

tests/life/test_day_plan.py:272: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_day_plan.py::test_revision_keeps_progressed_prefix_and_original_registration_for_unchanged_goals
FAILED tests/life/test_day_plan.py::test_revision_merged_capacity_is_checked
FAILED tests/life/test_day_plan.py::test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization
FAILED tests/life/test_day_plan.py::test_revision_merged_limit_returns_domain_error_and_preserves_plan
4 failed, 67 passed in 5.58s
```

### single_pulse.diff

```diff
diff --git a/src/worldmock/life/rhythm.py b/src/worldmock/life/rhythm.py
index 618b32c..5a5f96c 100644
--- a/src/worldmock/life/rhythm.py
+++ b/src/worldmock/life/rhythm.py
@@ -244,7 +244,7 @@ def _goal_and_deadline_pulses(agent: AgentState, inputs: RhythmInputs) -> AgentS
     if plan is not None and data is not None and data["activity"]["agent_id"] == agent.agent_id:
         changed = rhythm_schedule.progress_day_plan(
             plan, data["activity"], data["interval_start"], data["interval_end"])
-        if changed != plan:
+        if True:
             body = apply_mood_pulse(body, "goal_progressed")
             plan = changed
     window_start = now - inputs.interval_seconds
```

### single_pulse.log

```text
...............................F.......................................  [100%]
=================================== FAILURES ===================================
____ test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time _____

engine_factory = <function engine_factory.<locals>.create at 0x119194360>

    def test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time(engine_factory):
        engine = engine_factory()
        now = engine.state.current_time
        agent = engine.state.agents[1].model_copy(update={"day_plan": DayPlan(
            day_end=now + 1000, goals=[goal("休息", registered=now), goal("晨间例程", registered=now)])})
        data = {"activity": activity("wait"), "interval_start": now, "interval_end": now + 60}
        inputs = engine_rhythm_inputs(engine, engine.state, 60, progress_data={1: data})
        result = _goal_and_deadline_pulses(agent, inputs)
        from worldmock.life.body import apply_mood_pulse
        assert result.body == apply_mood_pulse(agent.body, "goal_progressed")
>       assert _goal_and_deadline_pulses(result, inputs).body == result.body
E       assert BodyState(ene...ment_need=0.0) == BodyState(ene...ment_need=0.0)
E         
E         Use -v to get more diff

tests/life/test_day_plan.py:213: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time
1 failed, 70 passed in 5.43s
```

### sleep_filter.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..4e6e85c 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -299,7 +299,7 @@ def evaluate_wake_reasons(snapshot: WakeSnapshot) -> WakeVerdict:
         reasons.append(WakeReason.WAKE_FROM_SLEEP)
     if snap.run_start:
         reasons.append(WakeReason.RUN_START)
-    if snap.is_asleep:
+    if snap.is_asleep and not snap.has_commitments:
         reasons = [r for r in reasons if r in SLEEP_ALLOWED_REASONS]
     return WakeVerdict(decision_offered=bool(reasons), wake_reasons=reasons)
```

### sleep_filter.log

```text
..................................................................F....  [100%]
=================================== FAILURES ===================================
________________ test_sleep_accepts_only_existing_sleep_reasons ________________

    def test_sleep_accepts_only_existing_sleep_reasons():
        snapshot = WakeSnapshot(now=NOW, is_asleep=True, has_commitments=True,
                                last_decision_time=NOW - 20000, next_task_due_at=NOW + 100,
                                location_closing_at=NOW + 100, message_arrived=True, activity_infeasible=True)
>       assert not evaluate_wake_reasons(snapshot).decision_offered
E       AssertionError: assert not True
E        +  where True = WakeVerdict(decision_offered=True, wake_reasons=[<WakeReason.MESSAGE_ARRIVAL: 'message_arrival'>, <WakeReason.TASK_DUE... <WakeReason.LOCATION_HOURS_CHANGE: 'location_hours_change'>, <WakeReason.ACTIVITY_INFEASIBLE: 'activity_infeasible'>]).decision_offered
E        +    where WakeVerdict(decision_offered=True, wake_reasons=[<WakeReason.MESSAGE_ARRIVAL: 'message_arrival'>, <WakeReason.TASK_DUE... <WakeReason.LOCATION_HOURS_CHANGE: 'location_hours_change'>, <WakeReason.ACTIVITY_INFEASIBLE: 'activity_infeasible'>]) = evaluate_wake_reasons(WakeSnapshot(now=1000000, planned_end=None, felt_bands_now={}, felt_bands_at_last_decision={}, message_arrived=True, d...e=True, consecutive_rejections=0, discovery_result_ready=False, wake_from_sleep=False, run_start=False, is_asleep=True))

tests/life/test_wake_reasons.py:104: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_wake_reasons.py::test_sleep_accepts_only_existing_sleep_reasons
1 failed, 70 passed in 4.62s
```

### task_warning.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..f06b08e 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -326,7 +326,7 @@ def next_decision_time(query: NextDecisionQuery) -> int | None:
         )
     candidates.extend(query.location_hours_change_times)
     if not query.is_asleep and query.next_task_due_at is not None:
-        candidates.append(query.next_task_due_at - TASK_DUE_SOON_SECONDS)
+        candidates.append(query.next_task_due_at - TASK_DUE_SOON_SECONDS + 1)
     if not query.is_asleep and query.has_commitments and query.location_closing_at is not None:
         candidates.append(query.location_closing_at - CLOSING_WARNING_SECONDS)
     future = [moment for moment in candidates if moment > query.now]
```

### task_warning.log

```text
................................................................F....F.  [100%]
=================================== FAILURES ===================================
_ test_warning_candidates_are_exact_not_feedback_aligned[next_task_due_at-1001901-1000101] _

field = 'next_task_due_at', deadline = 1001901, expected = 1000101

    @pytest.mark.parametrize("field,deadline,expected", [
        ("next_task_due_at", NOW + 1901, NOW + 101),
        ("location_closing_at", NOW + 1001, NOW + 101),
    ])
    def test_warning_candidates_are_exact_not_feedback_aligned(field, deadline, expected):
        query = NextDecisionQuery(now=NOW, last_decision_time=NOW, has_commitments=True, **{field: deadline})
>       assert next_decision_time(query) == expected
E       assert 1000102 == 1000101
E        +  where 1000102 = next_decision_time(NextDecisionQuery(now=1000000, planned_end=None, felt_band_crossing_times=(), scheduled_arrival_times=(), last_decisio..._commitments=True, location_hours_change_times=(), next_task_due_at=1001901, location_closing_at=None, is_asleep=False))

tests/life/test_wake_reasons.py:95: AssertionError
______ test_engine_schedules_exact_task_warning_and_appends_threat_reason ______

engine_factory = <function engine_factory.<locals>.create at 0x11840bce0>

    def test_engine_schedules_exact_task_warning_and_appends_threat_reason(engine_factory):
        engine = engine_factory()
        state = engine.state.model_copy(deep=True)
        task = engine.scenario.tasks[0]
        due = int(task.due_at.timestamp())
        state.current_time = due - 1901
        state.pending_events = []
        state.agents[1] = state.agents[1].model_copy(update={"visible_task_ids": {task.task_id},
            "next_decision_at": state.current_time, "day_plan": DayPlan(day_end=due + 10000, goals=())})
        from tests.life.test_revision_contract import _commit_staged
        _commit_staged(engine, state)
        started = act(engine, "warning-wait", "start_basic_activity",
                      StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
        assert started.status == "in_progress"
>       assert engine.scheduling_state().next_event_at.timestamp() == due - 1800
E       AssertionError: assert 1767612601.0 == (1767614400 - 1800)
E        +  where 1767612601.0 = <built-in method timestamp of datetime.datetime object at 0x117569ce0>()
E        +    where <built-in method timestamp of datetime.datetime object at 0x117569ce0> = datetime.datetime(2026, 1, 5, 19, 30, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')).timestamp
E        +      where datetime.datetime(2026, 1, 5, 19, 30, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')) = SchedulingState(current_time=datetime.datetime(2026, 1, 5, 19, 28, 19, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')),...vent_at=datetime.datetime(2026, 1, 5, 19, 30, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')), world_state_version=2).next_event_at
E        +        where SchedulingState(current_time=datetime.datetime(2026, 1, 5, 19, 28, 19, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')),...vent_at=datetime.datetime(2026, 1, 5, 19, 30, 1, tzinfo=zoneinfo.ZoneInfo(key='Asia/Shanghai')), world_state_version=2) = scheduling_state()
E        +          where scheduling_state = <worldmock.life.engine.LifeEngine object at 0x119321730>.scheduling_state

tests/life/test_wake_reasons.py:165: AssertionError
=========================== short test summary info ============================
FAILED tests/life/test_wake_reasons.py::test_warning_candidates_are_exact_not_feedback_aligned[next_task_due_at-1001901-1000101]
FAILED tests/life/test_wake_reasons.py::test_engine_schedules_exact_task_warning_and_appends_threat_reason
2 failed, 69 passed in 4.74s
```

### unique_activity.diff

```diff
diff --git a/src/worldmock/life/schedule.py b/src/worldmock/life/schedule.py
index 39a1fd0..475d558 100644
--- a/src/worldmock/life/schedule.py
+++ b/src/worldmock/life/schedule.py
@@ -350,7 +350,7 @@ def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int) -> Da
     activity_id = activity["activity_id"]
     if activity["status"] in {"cancelled", "failed"} or end <= start:
         return plan
-    if any(goal.progress_activity_id == activity_id for goal in plan.goals):
+    if False and any(goal.progress_activity_id == activity_id for goal in plan.goals):
         return plan
     family = activity["kind"]
     if family == "lifestyle":
```

### unique_activity.log

```text
..........................F....F.......................................  [100%]
=================================== FAILURES ===================================
_ test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization _

    def test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization():
        plan = DayPlan(day_end=1000, goals=[goal("通勤"), goal("外出")])
        first = progress_day_plan(plan, activity("travel"), 100, 200)
        assert first.goals[0].progressed_at == 200
        assert first.goals[1].progressed_at is None
        loaded = DayPlan.model_validate_json(first.model_dump_json())
        revised = revise_day_plan(loaded, 250, [goal("外出", registered=250)])
>       assert progress_day_plan(revised, activity("travel"), 250, 300) == revised
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/life/test_day_plan.py:136: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

plan = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind='通勤', goal='推进手头工作', registered_at=100, progressed_at=200, progress_...vity-a'), PlanGoal(intent_kind='外出', goal='推进手头工作', registered_at=100, progressed_at=None, progress_activity_id=None)))
activity = {'activity_id': 'activity-a', 'agent_id': 1, 'kind': 'travel', 'status': 'active'}
start = 250, end = 300

    def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int) -> DayPlan:
        """@brief 正时长结算最多推进一个匹配目标，活动证据在当日只消费一次。"""
        activity_id = activity["activity_id"]
        if activity["status"] in {"cancelled", "failed"} or end <= start:
            return plan
        if False and any(goal.progress_activity_id == activity_id for goal in plan.goals):
            return plan
        family = activity["kind"]
        if family == "lifestyle":
            category = activity.get("category")
            if category is None:
                return plan
            family = f"lifestyle:{category}"
        stop = min(end, plan.day_end)
        for index, goal in enumerate(plan.goals):
            if goal.progressed_at is not None or stop <= max(start, goal.registered_at):
                continue
            if family not in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]:
                continue
            progressed = goal.model_copy(update={"progressed_at": stop, "progress_activity_id": activity_id})
            goals = plan.goals[:index] + (progressed,) + plan.goals[index + 1:]
>           return DayPlan(day_end=plan.day_end, goals=goals)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E           pydantic_core._pydantic_core.ValidationError: 1 validation error for DayPlan
E             Value error, day plan intent and activity evidence must be unique [type=value_error, input_value={'day_end': 1000, 'goals'...ivity_id='activity-a'))}, input_type=dict]
E               For further information visit https://errors.pydantic.dev/2.12/v/value_error

src/worldmock/life/schedule.py:369: ValidationError
------------------------------ Captured log call -------------------------------
ERROR    worldmock.life.schedule:schedule.py:210 Day plan contains duplicate intent or activity evidence
____ test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time _____

engine_factory = <function engine_factory.<locals>.create at 0x1190d4220>

    def test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time(engine_factory):
        engine = engine_factory()
        now = engine.state.current_time
        agent = engine.state.agents[1].model_copy(update={"day_plan": DayPlan(
            day_end=now + 1000, goals=[goal("休息", registered=now), goal("晨间例程", registered=now)])})
        data = {"activity": activity("wait"), "interval_start": now, "interval_end": now + 60}
        inputs = engine_rhythm_inputs(engine, engine.state, 60, progress_data={1: data})
        result = _goal_and_deadline_pulses(agent, inputs)
        from worldmock.life.body import apply_mood_pulse
        assert result.body == apply_mood_pulse(agent.body, "goal_progressed")
>       assert _goal_and_deadline_pulses(result, inputs).body == result.body
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/life/test_day_plan.py:213: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/worldmock/life/rhythm.py:245: in _goal_and_deadline_pulses
    changed = rhythm_schedule.progress_day_plan(
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

plan = DayPlan(day_end=1767543400, goals=(PlanGoal(intent_kind='休息', goal='推进手头工作', registered_at=1767542400, progressed_at=1... PlanGoal(intent_kind='晨间例程', goal='推进手头工作', registered_at=1767542400, progressed_at=None, progress_activity_id=None)))
activity = {'activity_id': 'activity-a', 'agent_id': 1, 'kind': 'wait', 'status': 'active'}
start = 1767542400, end = 1767542460

    def progress_day_plan(plan: DayPlan, activity: dict, start: int, end: int) -> DayPlan:
        """@brief 正时长结算最多推进一个匹配目标，活动证据在当日只消费一次。"""
        activity_id = activity["activity_id"]
        if activity["status"] in {"cancelled", "failed"} or end <= start:
            return plan
        if False and any(goal.progress_activity_id == activity_id for goal in plan.goals):
            return plan
        family = activity["kind"]
        if family == "lifestyle":
            category = activity.get("category")
            if category is None:
                return plan
            family = f"lifestyle:{category}"
        stop = min(end, plan.day_end)
        for index, goal in enumerate(plan.goals):
            if goal.progressed_at is not None or stop <= max(start, goal.registered_at):
                continue
            if family not in INTENT_ACTIVITY_FAMILIES[goal.intent_kind]:
                continue
            progressed = goal.model_copy(update={"progressed_at": stop, "progress_activity_id": activity_id})
            goals = plan.goals[:index] + (progressed,) + plan.goals[index + 1:]
>           return DayPlan(day_end=plan.day_end, goals=goals)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E           pydantic_core._pydantic_core.ValidationError: 1 validation error for DayPlan
E             Value error, day plan intent and activity evidence must be unique [type=value_error, input_value={'day_end': 1767543400, '...ivity_id='activity-a'))}, input_type=dict]
E               For further information visit https://errors.pydantic.dev/2.12/v/value_error

src/worldmock/life/schedule.py:369: ValidationError
------------------------------ Captured log call -------------------------------
ERROR    worldmock.life.schedule:schedule.py:210 Day plan contains duplicate intent or activity evidence
=========================== short test summary info ============================
FAILED tests/life/test_day_plan.py::test_progress_consumes_one_activity_once_in_priority_order_and_survives_serialization
FAILED tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time
2 failed, 69 passed in 4.79s
```

