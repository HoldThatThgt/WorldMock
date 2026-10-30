# Contracts and gates a memory redesign must respect (current system, HEAD f67c25e5)

## 0. Stale references and where the authority now lives
- `docs/CONTRACTS.md`, `docs/VERIFICATION.md`, `docs/ARCHITECTURE.md` and `docs/dev/ANALYSIS-spec-coverage.md` do not exist. The current authority is `docs/design/*.md` (docs/INDEX.md:15-31).
- No spec-clause → test matrix is maintained any more: "不维护…规格追溯矩阵" (docs/INDEX.md:57-59), and "不维护…条款归属的机器台账" (docs/design/01-architecture.md:243). The rule that replaces it: the module design doc, the implementation and the behaviour tests change in the same commit (docs/INDEX.md:18; 01-architecture.md:283).
- `docs/dev/SPEC-as2-integration.md:1` and `docs/dev/coverage-baseline.md:1` both carry the banner "历史记录…不再定义当前系统". The live AS2 boundary is docs/design/10-runtime.md:3-26 and :77-91.
- The style gate is at `tests/unit/engineering/test_style_gate.py`, not at `tests/` root. It has no `_OUTSTANDING` ledger ("不保留历史违规豁免", :7). It scans `src/worldmock`, `custom` and `tools/analysis` (:17).

## 1. Current memory surfaces (facts)
- **Memory layer**
  - Derived on every observation from four event types (memory_layer.py:38, :99-113).
  - Current shape: `{layer, earlier_days[{date, settled, self_notes}], earlier_today[{recorded_at, settled, self_notes}]}` (prompt_schemas.py:152-162).
  - `earlier_days` = still-open order lines plus bedtime reflections filed by `reflection.date` (memory_layer.py:225-237, :306-317).
  - `earlier_today` = one entry per session that ended today, not one per decision (memory_layer.py:105-107).
  - Window is 8000 chars (memory_layer.py:36, :320-336). Capacity fitting never condenses the memory layer (prompt_capacity.py:7).
- **Subjective store**
  - AS2 `episodes.jsonl` holds conclude_activity memories and views plus reflections (life_memory.py:94-125, :127-143).
  - Allowed request types: `ConclusionMemory.type` has 7 literals and `views` has relationship/preference (requests.py:455, :481). `SUBJECTIVE_TYPES` is a closed set of 9 (state/model/memory.py:16-17).
  - **There is no category field (food/life/work) anywhere.**
  - Real run (variety): 255 episodes — routine 138, preference 68, reflection 33, commitment 9, relationship 7.
- **Memory tools**
  - `memory_recent/search/time_range` (life_agent.py:169-188) are routed to AS2 `dispatch_tool`, which searches only `episodes.jsonl` (vendor/.../memory_runtime.py:263-301).
  - They never search the event-derived settled facts.
  - Usage across the two runs: 2 `memory_search` calls in variety, 0 in baseline (out of ~420 action turns each).
- **Existing model calls on memory**
  - Consolidation: no tools, threshold of 10 entries or importance 3.0 (life_memory.py:29-31, :240-262).
  - Reflection: tool `submit_reflection` (life_memory.py:175-200).
  - Both go through `_dispatch_memory_llm` with full model evidence (life_agent.py:411-435).
  - Reflection-phase call ids are `{decision_id}:reflection:NNNN`, outside the turn budget (life_agent.py:339-346; 06-agent.md:344-346).
  - The `MEMORY.md` summary is not read by any decision prompt (life_memory.py:79-92).
- **Turn budget:** `max_react_turns` default 16, range 1–64 (configuration/inputs/models.py:223). It is frozen into `stage_model_identity` (setup.py:98) and must match on resume (runner.py:418-420).
- **Terminology clash with the owner's vocabulary:**
  - The code already uses "session" for a location stay or a stretch of travel (`SessionState`; 05-recording-engine.md:117-129).
  - "round" already means the AS2 decision round (`decision_started.round_id`; models.py:103-105; engine.py:108).
  - The owner's "round" maps to the code's *decision* (`decision_id`), which ends with any world request (06-agent.md:59-66; life_prompt.py:29-33).

## 2. Checklist by change type

### (a) Memory-layer shape change (categories; move `earlier_days` to progress; per-day / month / year entries)
1. **Schema:** `MEMORY_LAYER_SCHEMA` (prompt_schemas.py:152-162). Any edit changes the prompt fingerprint, because `life_prompt_sha256` hashes `LAYER_SCHEMAS` (life_prompt.py:172-177). The fingerprint is frozen in `model_identity.prompt_sha256` (setup.py:81-89; identity.py:26) and compared on resume (runner.py:283-287).
2. **Builder:** memory_layer.py (whole file). Also `noted_label` (:147-152), which the recall query depends on, and `empty_memory_layer` (life_prompt.py:139-141).
3. **Observation part:** `memory_layer` is stored as one whole part (observation_parts.py:21-22; 05-recording-engine.md:138). Renaming or splitting the key requires changing `WHOLE_PART_KEYS`, plus `post_run.DROPPED_PAYLOADS` (post_run.py:13-16; 05-recording-engine.md ~660) and tests/unit/footprints/test_observation_parts.py:21,82 and test_post_run_view.py.
4. **Moving content into the progress layer:** the progress layer is host-only and not recorded in observation events (05-recording-engine.md ~114). Its records are condensable under capacity pressure, unlike memory (prompt_capacity.py:1-7). So anything moved there loses observation-event evidence unless that is redesigned.
5. **Same-second continuation** assumes "记忆层…在链内不变" (life_continuation.py:8). Chains break only on a time or session change (continuation.py:3-5). Per-decision memory entries would violate this unless `layer_changes` carries memory deltas or the chain breaks.
6. **Readonly snapshot:** `life_prompt_snapshot.json` stores the memory layer (life_persona.py:72-85). It lives in the checkpointed workspace.
7. **Consumers:**
   - Viewer prose and tests (tools/run-viewer-context.js:39-47, which still cites stale `src/worldmock/life/...`; tests/system/viewer/*.cjs).
   - compare_runs field paths (`earlier_today[].recorded_at`; 05-recording-engine.md:731; test_compare_runs.py:132,315-318).
8. **Design doc:** 06-agent.md:216-306 (memory window, reflection filing at :300-306).

### (b) A new model call for daily compaction (or per-category compaction)
1. **Evidence:** reuse `_dispatch_memory_llm` so request and response evidence are recorded (life_agent.py:411-435). Give it a call-id family outside the turn budget, as reflection has (06-agent.md:344-346).
2. **Call class:** quality classifies calls by request shape. No tools → `consolidation`; exactly `submit_reflection` → `reflection`; anything else → `unrecognized` (quality.py:49-50, :795-812; 05-recording-engine.md:635-650).
   - A new strict compaction tool means extending `CALL_CLASSES`, which changes `by_call_class` keys.
   - Export authentication recomputes quality and rejects any key difference (05-recording-engine.md:631-633). That implies a quality schema bump from 9 (quality.py:86).
3. **Validation-retry budget:** reflection has `reflection_validation_max_retries` in `ModelConfig` (configuration/inputs/models.py:224).
   - A new budget field changes the `stage_model_identity` key set (setup.py:249-265).
   - That requires `RUN_MANIFEST_SCHEMA_VERSION` 7→8 (identity.py:10-12), which LabelKit pins (§4).
4. **Trigger hook:** the precedent is `due_reflection` before observe (life_agent.py:311-322).
   - The Life extension channel adds read-only methods only: `read_query` and `due_reflection` (read_query.py:21,37; 10-runtime.md:87-91).
   - A new `due_*` method needs a thin forward on the proxy, the actor, LifeEnv and the engine.
5. **Persisting a model-written summary:** recovery and replay "读已提交的事件，不重写、不重抽" (05-recording-engine.md:103).
   - The summary must therefore be recorded, not regenerated: either in `episodes.jsonl` (snapshotted per step; checkpoint.py:33-54) with a `memory_updated` registration, or in event data.
   - It is model output, so it must stay subjective/unverified, separate from `settled` (life_prompt.py:75-78; 01-architecture.md:236).
6. **Idempotence on resume:** the reflection guard checks "本人在这一时刻还没有登记过反思" (evidence/model.py:173-185). A compaction call needs the same guard.
7. **Cost evidence:** consolidation was 31 calls and reflection 4 per 5-day run (variety quality.json `by_call_class`).

### (c) A `search_memory(category, time)` tool replacing the three memory tools
1. **Tool surface:** `_MEMORY_TOOL_MODELS`, `_QUERY_TOOL_MODELS`, `_MEMORY_RUNTIME_ACTIONS`, `_MEMORY_RESULT_FIELDS` (life_agent.py:169-195). Also the readonly path (life_agent.py:397-404) and `TOOL_DESCRIPTIONS` (life_prompt.py:122-124).
2. **Fingerprint:** `life_tool_schema_sha256` hashes every tool schema (life_agent.py:953-959) and is frozen as `tool_schema_sha256` (setup.py:89).
3. **Docs:** "十个原生工具" appears in 06-agent.md:52-57 and 10-runtime.md:79-81 ("工具定义指纹覆盖全部十个").
4. **Data source:** AS2 `dispatch_tool` only knows `memory_recent/search/range/read` over episodes (memory_runtime.py:282-301).
   - Searching by category/time over compacted entries or settled facts needs either a WorldMock-side implementation inside `_dispatch_strict_memory` (life_agent.py:665-683) or an AS2 patch.
   - The latter means a new SERIES entry and a recomputed `source_sha256` (patches/agentsociety2/SERIES; README.md:7-21). The AS2 package bytes are part of `code_sha256` (09-io-formats.md:75-77).
5. **Read-only contract:** the tool must not advance time or create facts (06-agent.md:281). It must remain a read-only query in the batch-shape rules (06-agent.md:61-66).
6. **Pinning tests:**
   - test_system_layer_text.py:122-132 (exact `TOOL_DESCRIPTIONS` key set)
   - test_seven_layer_request.py:16-21, :51
   - test_tool_description_references.py:45-46
   - test_batch_shapes.py, test_life_agent_protocol.py (30 hits), test_read_query_runtime.py
   - test_same_second_continuation.py

### (d) New system-prompt rules teaching the memory system
1. **Rule list:** `LIFE_SYSTEM_LAYER.rules` (life_prompt.py:21-99). Memory is touched today only at :25-27, :32, :75-78 and :90-91.
2. **Verbatim test:** test_system_layer_text.py:105-112 pins exactly 20 rules, verbatim and in order. :135 checks the fingerprint moves.
3. **Purpose-only descriptions:** tool descriptions may state purpose only, and interaction rules live solely in the system layer (06-agent.md:74 "规则文字只在系统层写一次"; test_tool_description_references.py:72-92).
4. **Readonly system layer** also describes memory (life_prompt.py:102-111).

## 3. Gates every variant hits
- **Event types:** `EventType` is a closed set of 14 (models.py:110-114). Precedent is to avoid new kinds: "不新增事件种类" (05-recording-engine.md:66) and "事件类型的闭集不变" (:99). Reusing `memory_updated` means:
  - Its data has a fixed key set and two registration routes (05-recording-engine.md:80-90).
  - It is validated by `_memory_evidence_data`, whose `operation` ∈ {append_episodes, refresh_summary} (memory_evidence.py:49-80), and by `_reflection_registration` / `_tail_registration` (evidence/model.py:139-206).
  - `MEMORY_WRITE_POINTS = ("activity_conclusion","reflection")` (memory_evidence.py:14) is mirrored in quality (quality.py:41-43, :528-600).
  - The AS2 `MemoryUpdateEvidence` dataclass fixes `operation` as a Literal and fixes the field set (vendor/.../react.py:98-118). `record_memory_update` is a registered direct-env method (vendor/.../router_direct.py:12-24).
  - New fields or operations therefore need WorldMock-side wrapping or an AS2 patch.
- **Dependency direction:** recording_engine must not import simulation_engine, agents or runtime (test_import_graph.py:382-394). Shared category enums belong in `worldmock.state` (the precedent is SUBJECTIVE_TYPES in state/model/memory.py).
- **Style:** lines ≤120 chars, functions ≤50 lines and ≤5 params, files ≤2000 lines, Chinese docstring (test_style_gate.py:18-22, :74-76). memory_layer.py is 353 lines and life_agent.py is 1016.
- **Coverage:** branch coverage ≥85% and zero uncovered public interfaces (scripts/coverage_gate.py:24; 01-architecture.md:254-257). Bare `...` stubs are not excluded (pyproject.toml:79-82).
- **Baselines and recordings:**
  - Any change to production source requires a two-candidate refreeze of tests/fixtures/dayrun-{activity,callback}-baseline.json (01-architecture.md:262-266).
  - `viewer-context.json` must be re-recorded with a real host when the system layer or tool definitions change (:268).
  - `recorded_response_run.py` rejects any change to model-visible fields, so a prompt change cannot be proven with recordings (:276). Real-model runs are needed (:270).
  - compare_runs classifies memory digests (scripts/compare_runs.py:81-92).
- **Checkpoint / resume / replay:** resume requires an identical `model_identity`, which includes prompt and tool fingerprints, and an identical `code_sha256` (runner.py:276-290).
  - So runs started before the change cannot be resumed with new code; branching under new code is only possible from a new run.
  - The workspace snapshot (MEMORY.md, episodes.jsonl, state.json, prompt snapshot) is checkpointed every step and restored as a tar (05-recording-engine.md:584-597).
  - The memory store is revalidated on restore (life_memory.py:67-77; runner.py:420-425).
- **Behaviour analysis:** `memory_repetition_ratio` reads `memory_updated` episodes and `memory_md_identity_residue` reads MEMORY.md (tools/analysis/behavior/metrics.py:109-110, :601-630).

## 4. LabelKit-DataSet export contract
- Memory reaches downstream in three ways:
  - inside events.jsonl: the full observation `memory_layer` restored from parts, plus `memory_updated` episode snapshots and the consolidation/reflection model requests (05-recording-engine.md:661-663);
  - inside the exported workspace gzip asset, which WorldMock does not open (export asset-index has one `{kind: workspace}` row);
  - never as a separate artifact: the six-artifact set is closed (export.py:39-41).
- LabelKit-DataSet `export_source.py:20` pins export 8 / run-manifest 7 / world 15 / checkpoint 1, and :150-152 pins the same 14-type event enum. Event `data` is a free object (:145).
- **Consequence:** reusing `memory_updated` with new data keys is invisible to LabelKit ingest. A new event type, a run-manifest bump or an export bump breaks ingest until LabelKit is updated.
- docs/design/00-overview.md:94 assigns offline review/reflection ("Dreaming") to LabelKit-DataSet, not written back to the persona.

## Open questions / uncertainties
- Whether a compaction call with no tools (classified as `consolidation`) is acceptable to the owner, or whether a separate call class with a quality bump is preferred.
- The readonly Q&A path (life_agent.py:397-404) has no production caller found in src/custom. Its replacement scope is unclear.
- I did not verify whether LabelKit pins the quality.json schema. No pin was seen in export_source.py.
- I did not check how per-category entries would interact with the 24h bigram-merge rule (life_memory_store.py:13-16, :123-136) or with consolidation thresholds.
- The 32-turn budget is config-only, within the 1–64 range. Changing the default edits models.py, which changes `code_sha256`.

Helper script (scratch only): /private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/contracts_evidence.py