# (A) one 今天 entry per decision, (B) no views in the prompt: required code and spec changes

## Summary

1. **(A) silently breaks every same-second chain unless the chain builder is decoupled from the progress layer.** `same_second_chain` finds each previous tool result by looking for a matching progress-layer record (`simulation_engine/decisions/continuation.py:62-69`); if it finds none (`len(own) != 1`), it returns `None` and the chain breaks. Under (A), the previous decision's control request or rejected request belongs to the 今天 entry and is no longer a progress record. Every chain continuation in V follows a conclude (96) or a rejection (5); B is 96 conclude, 2 rejected, 1 revise, 1 pause. So without the decoupling, 100% of chains break.
2. **Nothing enforces the "memory constant in a chain" assumption, and no test pins it.** It holds only by construction. Under (A) the chain needs a new `memory` key in the changes. In V/B, each continuation adds exactly one entry (V: 95 single continuations and 3 double ones; median entry about 155 characters including JSON).
3. **An entry per decision means up to 89 entries a day in V (85 in B), not 67.** The 67 counts only fresh decisions; chain decisions get entries too. With the 120字 cap and about 35 characters of JSON per entry, 今天 reaches about 8.7–11.7k characters by day end in V and 10.2–10.7k in B. This is an estimate: each 核验 line is counted at the 59-character average from brief F16.
4. **The 120字 entry cap combined with (B) removes almost all views from the prompt.** V has 76 看法 items. If the summary comes first, 0 of them fit whole within 120; if 看法 come first, 49 do. Conclude entries are almost always cut (only 11/137 in V and 17/137 in B stay whole), so recall's "已在记忆层" dedup will fire in at most about 12% of cases.
5. **(A) can ship in P1, but only together with the progress layer's new lower bound, which moves from P2 to P1.** Otherwise each fact and each 正文 appears twice. (B) should ship in P1 as well. P2 shrinks to the layer reorder only.

Interpretation used throughout (as given): 字 means characters, counted as the `len()` of all 核验 and 自述 strings in an entry. I assumed 核验 is kept whole and 自述 fills whatever room is left; entries where 核验 alone exceeds 120 are rare (2 in V, 9 in B).

## (a) Continuation chains and the memory layer

**How the assumption holds today.** The docstring at `life_continuation.py:7-8` says the memory layer and the current place do not change within a chain, because both change only with time or a session change, and both end the chain. It holds by construction, not by a check:
- 今天 entries change only when a session ends today (`memory_layer.py:105-107`), or when the date changes.
- A chain exists only within one second (`continuation.py:74-82`; the observation read is bounded by `last_event_seq_before(current_time)` at :77).
- A chain ends on any session change (`continuation.py:54`, `:85-88`).

**Nothing enforces or tests it.**
- `_view` never reads 记忆层 (`life_continuation.py:111-122`), so a change inside a chain would simply be invisible: the head user message `messages[1]` is reused as is (`:70`, `:73`).
- No test compares the memory layer across a chain. The fixtures use a bare `{"layer":"记忆层"}` (`tests/integration/runtime/test_same_second_continuation.py:998,1043,1066`).
- The "100% constant in chains" check in the proposal's P1 acceptance list does not exist yet.

**Current `layer_changes` shape** (`life_continuation.py:33-53`), in this key order:
1. `targets` with `updated` and `removed`
2. `paused_activities` with `added` and `removed`
3. `errands` with `added` and `removed`
4. `day_plan`, replaced whole
5. `body`, replaced whole
6. `relationships`, a list of updated items
7. `preferences`, a list of updated items

Changes are computed against the head snapshot with each earlier tool message's changes overlaid (`_overlay` at :125-140; earlier changes are read only from tool messages that have a `result` key, :71-72 and :102-108). They are written only on the last tool message (:76). The proposal adds an `orders` key.

**What a `memory` key needs**
- **Shape and position.** Spec `changes.memory = {"added": [entries in 今天 order]}` and place it after `targets`, before `paused_activities`, following the user-layer order. Use an English key, like `orders` and the other changes keys.
- **`_view`.** Add the 今天 list, normalised the same way for the head and the current layers (a JSON round trip through `render_layer_json`, `narration.py:163-175`).
- **`_overlay`.** Append `added`.
- **`layer_changes`.** If the seen list is a prefix of the current list, `added` is the remainder. Anything else (the overflow backstop firing, or an earlier entry re-rendered) must end the continuation for that decision: the host uses the fresh messages instead of `continued` (`life_agent.py:488-492`).

**Being append-only is a requirement the builder must meet.**
- **Activity names.** Entry text must depend only on events up to that entry's commit. `activity_sentence` takes names from the observation's `context.names` (`settled_text.py:70`). Those names come from `visible_names`/`_unique` (`activity_control.py:193-205`, `:497-505`), counted against goal texts that change during the day (`observe.py:136-138`). Render from the activity's own frozen `name` instead.
- **Labels.** Labels must be fixed first-come when an entry is published, not unique per entry as `_labels` does now (`memory_layer.py:299-303`). On a single day, 26 of 111 short labels in V (21/112 in B) name two different activities.
- **Clock text.** `clock_phrase` is stable within one day (`foundation/time/readable.py:45-50`), so it needs no change.

**Environment side (critical).** `same_second_chain` currently uses `records_after`, which is bounded by the session (`progress_layer.py:91-97`, called at `continuation.py:63`).
- Replace it with a direct projection of the previous decision's requests and their results, using the same `_tool_record` and `public_result` (`progress_layer.py:175-180`).
- `events` become the non-memory world records with sequence number above the previous observation.
- Rewrite the docstrings at `continuation.py:1-10` and `life_continuation.py:1-11`.

**Capacity step 0.** `fresh_messages` are built from the current layers (`life_agent.py:491`), so they already contain every published entry. Step 0 replaces the whole chain prefix, including the tool messages that carry the deltas (`prompt_capacity.py:93-97`; `life_continuation.py:93-99`). Nothing is duplicated or lost. The memory layer is never condensed (`prompt_capacity.py:7`, `:98-107`), so the builder's own overflow backstop is the only cap. When the backstop fires it rewrites earlier entries, which ends the chain anyway.

## (b) Published bound, the progress layer, and duplicates

**How earlier_today is built today.** There is one entry per session that ended today (`memory_layer.py:99-113`):
- `recorded_at` is the session end (`:216-222`).
- `settled` holds ending sentences, purchases and deliveries inside the session's range (lower, upper] (`:240-257`).
- `self_notes` holds pause and conclude summaries plus plain memories; views are excluded (`:283-296`).
- The 8000-character trimming is at `:320-336`.

The effective published bound is the current session's lower bound (`session_bounds.py:21-29`); the progress layer reads everything after it (`progress_layer.py:77`). The docstring at `session_bounds.py:1-8` claims the two layers never overlap.

**Switching to a per-decision bound.**
- **The bound.** For decision k, the bound P_k is C_{k-1}, the last event sequence number of the previous decision's commit. Every action_requested gets an action_result in its commit, rejected ones included. So C_j = `store.commit_last_seq(first action_result seq of decision j)` (`store.py:357-373`). It never changes once committed, so it can be cached next to `lowers` (`memory_layer.py:51-61`).
- **The window.** Entry j covers (C_{j-1}, C_j], cut at the day-start sequence number. That is the same bound `rejected_today` uses (`progress_layer.py:322`). V and B each have 3 windows that cross midnight.
- **Entry content.**
  - Ending sentences, paid purchases, deliveries, and errands done.
  - New code lines: a start line per accepted start ("HH:MM 开始〈短名〉"), a rejection line naming the tool only, and a plan-revised line.
  - 自述: conclude/pause items, the report_status summary as "进展·〈短名〉：…" (this forces a change to item 2), and 正文 from the decision's own `action_requested.self_note` (`decision_coordinator.py:99-127`).
- **Plumbing.** `memory_view` returns P_k. `observe.py:57-58` passes it to `progress_view`, which uses it as the lower bound and for the `here` facts (`progress_layer.py:77`, `:80-82`).

**What the progress layer keeps** (to rewrite 06-agent.md:267-271 and `progress_layer.py:1-4`):

| Kind of record | Bound / rule | Code |
| --- | --- | --- |
| Memory-fact records (deliveries, errands done) | sequence number above P_k | — |
| Carried records: activities running or waiting to be concluded, any kind (sleep, travel, purchase and query included) | `slot_activities(context.current)` replaces the interact-only rule | `activity_control.py:82-84`; replaces `progress_layer.py:158-166` (:164) |
| Carried records: queries finished in the current stay, with their answers | Keeps 06-agent.md:265; the entry gets only the end sentence, not the answer, as today (pinned by `test_query_answer.py::test_answer_stays_for_the_stay_and_leaves_a_settled_line_after_departure`). Queries end themselves (04-simulation-engine.md:705), so they need an explicit rule. | — |
| Non-memory world records (app_push, callback_reminder, balance_decreased, errand_reminder) | sequence number above the previous decision's **observation**, not P_k | Action callbacks fire inside commits (`decision_coordinator.py:171`, `:342-347`, `:413-435`; `callbacks.py:111,320`). Measured 0 such events in V/B, but the code allows it. |
| Derived plan reminders | Time bound: `reminded_at` later than the previous decision's time; replaces the session window | `progress_layer.py:284` |

**Avoiding duplicate text**
- Carried records keep the structured request and latest result, and no `self_note` (as `_carried_calls` already does, `progress_layer.py:143-155`).
- Remove `self_note` from session records (`:137-138`). 正文 now lives only in the entry.
- The latest report_status is no longer kept in progress; it is in 今天.
- Two overlaps remain and must be documented:
  1. A running activity's start line in the entry and its carried record share only the name and start time.
  2. A finished query's end sentence and its carried answer record.

**rejected_today** stays unchanged (`progress_layer.py:316-347`). The rejection line in the entry gives no reason, so the `why` text exists only there. All 7 rejected-only decisions in V/B were followed by a chain decision in the same second, so the full result also reaches the model through the tool message.

**recall_task_summary dedup** (`memory_layer.py:147-152`; `dispatcher.py:511-518`):
- Rescan the 今天 自述 for the prefixes "〈label〉：" and "进展·〈label〉：". Under the 120字 cap, a cut item ends with "…" and fails the exact-text compare with `progress_note`, so the "only untruncated counts" rule follows automatically. The measured hit rate is at most about 12%.
- The note_labels pipeline (`memory_layer.py:116-124` → `observe.py:151` → `dispatcher.py:499-505`) must take labels from those prefixes, assigned first-come.

**Observation storage.** `memory_layer` remains one whole part (`observation_parts.py:21-22`). Today it changes rarely: 22 distinct parts in V (186 KB), 30 in B. Per-decision publication gives about 406 parts of about 7k characters, roughly 7–8 MB more per 5-day run against a 131 MB database. That estimate assumes about 2.7 bytes per character. Splitting 今天 into one part per entry (like `DAY_PART_KEY`, `:23-24`, `:48-51`) is optional.

**Prompt snapshot.** `life_agent.py:487` passes `layers[2]`. That stays correct while the layer order is unchanged; the by-name lookup belongs with the reorder. The `todo` parameter of `_visible_names` is unused (`:765-785`).

## (c) Phasing and risky interactions

**(A) needs a phase change.** Publishing per decision while the progress layer still holds the whole session duplicates facts and 正文. So the proposal's P2 items "已发布界" and "进度层只留未了结的事" move into P1, together with the new chain `memory` delta (neither phase had it).
- Ship (B) in P1 as well: `search_memory` and the deletion of the `memory_*` tools are already in P1, and the views must leave the persona layer at the same time as they enter 今天, or they appear twice.
- P2 is left with the layer reorder, the by-name layer lookup and its assertion, plus the cache acceptance criteria.
- P2's acceptance criteria move to P1: progress layer median ≤3k characters, and the M9 guardrails against re-starting just-finished activities.
- There is no cache reason to wait. The tool layer comes first and changes on almost every request, so a fresh request caches only the system message and tools; chain deltas are appended after a byte-identical prefix.

**Risky interactions**
1. Chains break at `continuation.py:65-68` unless decoupled (see (a)).
2. The append-only requirements: frozen names, first-come labels, and no folding. The overflow backstop must end the chain.
3. Hints generated inside a commit are lost if the progress bound is C_{k-1} for every kind of record.
4. With the 120 cap, item order decides whether views survive (0 of 76 with the summary first). Under (B) and ruling 23, a past day's view text can be searched only through digest paraphrase. The 全部 search scope (proposal :358, :361) needs a ruling: either return 看法 verbatim for past days, or accept the paraphrase.
5. In fresh snapshots, the full result details of control requests (`resolved`, `notes`, errand changes) are no longer shown.
6. Two system rules and one readonly rule change in P1 (prompt fingerprint changes):
   - `life_prompt.py:25-27`: 进度层 "这段停留或路程中".
   - `:96-97`: 正文 "留到这段停留或路程结束".
   - `:107` (readonly).
7. The progress layer's `session_started_at` key and its schema meaning, plus `_protected` and the condensation steps, become nearly no-ops (`prompt_capacity.py:3-8`).
8. The search tool's result must never have a top-level `result` key (`life_continuation.py:102-108`). The proposal's own result keys already avoid it (proposal :370).

## (d) Tests that pin current behaviour

**`tests/integration/runtime/test_same_second_continuation.py`**
- These assert the exact changes and now gain `memory`:
  - `test_the_continued_request_starts_with_the_previous_request_byte_for_byte` (:223, asserts at :238,242)
  - `test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once` (:255, :275)
  - `test_duty_is_constant_within_a_same_second_chain_and_never_enters_changes` (:282, :301)
  - `test_errand_changes_reach_the_next_tool_message_as_added_and_removed` (:837, :860)
  - `test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte` (:747, :778)
- These assert the previous request appears as a progress record:
  - `test_a_start_group_is_continued_with_one_tool_message_per_call` (:628, :657-665)
  - `test_a_rejected_request_continues_with_the_rejection_reason` (:568, :578-579)
  - `test_a_carried_record_is_not_repeated_in_the_continuation` (:591, `records_after` at :615-619)
- `test_after_time_advances_the_request_has_no_native_turns_or_earlier_thinking` (:323): `self_note` in progress at :342-343.
- Need new fixtures and/or the (B) change:
  - `test_changes_overlay_relationships_and_preferences_so_later_tool_messages_do_not_repeat_them` (:795): delete for (B) and replace with a memory-overlay test.
  - `test_continuation_messages_read_earlier_changes_and_skip_memory_and_feedback_tool_messages` (:989)
  - `test_layer_changes_overlay_earlier_changes_and_keep_the_latest` (:1038)
  - `test_layer_changes_report_errand_rows_added_changed_and_removed` (:1060)

**Progress layer bound, carried records and `self_note`**
- `tests/integration/life/test_carried_records.py`:
  - `test_departure_and_purchase_records_are_not_carried` (:229)
  - `test_paused_activity_is_not_carried_until_resumed` (:243, :261)
  - `test_activity_finishing_at_the_arrival_second_is_told_by_the_memory_layer` (:283)
  - Re-verify the rest of the file.
- `tests/integration/runtime/test_conclude_memories.py::test_memory_queries_and_the_reply_body_reach_the_next_progress_layer` (:701)
- `tests/integration/runtime/test_errand_prompt_separation.py::test_errand_arguments_never_become_memory_or_progress_parameters` (:10, :33-35)
- `tests/integration/runtime/test_decision_context_roundtrip.py::test_real_context_roundtrip_preserves_evidence_and_delays_results` (:98, :120)
- `tests/integration/life/test_progress_layer_schema.py::test_real_progress_layer_passes_layer_validation` (:183, through `_location_activity_ended_early` at :72)
- `tests/integration/life/test_query_activity.py::test_concluded_query_has_no_answer_and_frees_slot_two` (:400, :415)

**Readers of the old memory-layer shape**
- `tests/integration/start_interfaces/test_location_activities.py::test_memory_sentence_keeps_the_unfinished_half_only_for_app_content` (:529)
- `tests/integration/life/test_autoplay.py::test_a_concluded_app_use_reads_end_of_use` (:437)
- `tests/integration/life/test_autoplay_display.py` (:230, :243)
- `tests/unit/agent_protocol/test_decision_context_protocol.py::test_readonly_loop_uses_only_persona_memory_and_question` (:188)
- The `_layers` fixture in `tests/integration/runtime/test_life_agent_protocol.py` (:188)
- `tests/unit/life/test_narration.py:96`
- `tests/unit/engineering/test_compare_runs.py::test_nested_recorded_at_is_compared` (:313)
- `tests/unit/life/test_current_lifecycle_coverage.py::test_activity_recall_is_readonly_and_returns_real_progress_and_note_history` (:245)

**For (B)**
- `tests/unit/start_interfaces/test_system_layer_text.py::test_system_layer_is_the_twenty_rules_verbatim_in_order` (:105; rules at :64-67 and :85)
- `tests/unit/life/test_shared_observation_contract.py::test_actual_conclusion_views_update_persona_without_duplicate_memory_prose` (:225): flips at :239, because the view now appears as 看法·.
- `tests/integration/runtime/test_persona_profile.py` tests at :56, :87, :453 and the `_persona_rhythm` helper at :408; `tests/unit/profile/test_textgen.py::test_card_text_and_persona_layer_write_the_same_window_text` (:148); `tests/integration/runtime/test_public_runtime_boundaries.py::test_persona_layer_rejects_actual_overlong_card_without_truncation` (:35) and `::test_readonly_prompt_uses_saved_snapshot_and_requires_the_persona_card` (:44). These change only because the `persona_layer` signature changes.
- `tests/integration/runtime/test_agent_branch_coverage.py::test_corrupt_observation_fails_before_any_model_tool` (:142, cases at :120-121) changes only if `_persona_views` is narrowed.

**Guards that must keep passing**
- `tests/integration/runtime/test_existing_relationship_subject.py::test_host_accepts_card_or_existing_relationship_and_rejects_unknown_subject` (:12)
- `tests/unit/life/test_memory_progress_contract.py::test_rejected_today_counts_only_evidence_from_local_midnight_to_now` (:268)
- `tests/integration/life/test_query_answer.py::test_answer_stays_for_the_stay_and_leaves_a_settled_line_after_departure` (:301)

## (e) Doc owners and the persona-layer pieces to delete

**Doc owners**
- `docs/design/06-agent.md`:
  - :74, the rule summary that puts views in 人设层.
  - :153-155 and :184: document the full `changes` key set, including `memory.added` and the condition that ends the chain.
  - :216-283: :265 stays as is; :267-271 becomes the published bound plus the carry rules; :273-283 becomes the entry shape, the cap and the code lines.
  - :285-303 and :322-337, the reflection text, already being retired in P1.
- `docs/design/05-recording-engine.md`: :145 and :138 (only if 今天 is split into parts); :676 stays as long as `persona_views` is kept.
- `docs/design/00-overview.md:138`: the preference subject is said to point to "人设层中的对象".
- `docs/design/04-simulation-engine.md` owns nothing about chains. The test docstring that cites its 「同秒续接」 section (`test_same_second_continuation.py:1061`) points at nothing.
- `docs/CONTRACTS.md` does not exist (checked with `ls`; brief F17). The owners are `docs/design/*.md`, per `docs/INDEX.md:17-18`.
- Module docstrings that act as spec: `life_continuation.py:1-11`, `continuation.py:1-10`, `progress_layer.py:1-4`, `session_bounds.py:1-8`, `memory_layer.py:1`, `life_persona.py:1-8`.
- In the proposal itself, the forced edits are: :119, :127, :129, :132, :139-141, :156-168, :184-198, :202, :247, :358-361, :392, :400, :408, :418-419, all of :424-434, :436-449, :453-529, :540, :543, :547, :550, :603-604, :636, :649-693, :739, and Chapter 5 items 2 and 4 (the "关系" sub-list goes away, :756). The other open items keep their recommendations.

**Persona-layer pieces to delete for (B)**
- Schema: `prompt_schemas.py:144-146`, the relationship `view` key; `:147-148`, `preferences`.
- `life_persona.py`: the `views` parameter and its merge (:29-54, especially :39 and :46-47); view-only persons in `_relationships` (:117-123); the readonly fallback call (:84). Relationships then come from the card only.
- `life_agent.py:479-480`: views no longer reach the persona layer. Keep `known_persons` (:484) fed from the private `persona_views` relationships.
- `life_continuation.py`: :49-52, :121-122, :128, :138-139, and the docstring at :7.
- Rules: `life_prompt.py:75-78` and `:107`.
- Docstrings that say views appear only in 人设层: `progress_layer.py:176`, `:183-188`.
- Keep `persona_views` as private evidence (`memory_layer.py:127-144`; observation part `observation_parts.py:22`; export `post_run.py:14-15`) so the observation and export formats do not change. Its `preferences` half then has no prompt reader.
- Drop the parts of the proposal that were never built: the 「长期」 block, `longterm.py`, and the planned test that 「长期」 stays byte-identical within a day.

Scripts and outputs are in `/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/code/`. The notes are in `notes.md` there; the scripts are `windows.py`, `entries120.py`, `views120.py`, `incommit.py`, `labels.py` and `parts.py`; their data is in `windows_V.json`, `windows_B.json`, `windows_summary.json` and `entries120.json`.