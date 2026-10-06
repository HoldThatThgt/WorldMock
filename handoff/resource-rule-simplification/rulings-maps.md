# Rulings on the open questions raised by the subsystem maps (orchestrator, 2026-10-06)

Companion to rulings-draft.md. Every line is a decision the spec must record verbatim (as 裁决记录 rows) and the
implementation must follow. Where a map recommended an option, the recommendation was adopted unless stated.

## Map and travel (map_travel)

- M1 Connectivity: the published graph must be connected (random tree + extra edges guarantees it); the loader checks ≥ 2 nodes, connected, no isolated node, no self-loop, no duplicate edge, every length a positive integer (metres).
- M2 Generator ownership: WorldMock owns the generator (`worldmock map generate --locations <file> --seed N` writes `catalog/map.json`); the dataset publishes the output. Environment validation (`worldmock validate --environment <root>`) and world setup both check structure AND that the published edge set equals the deterministic regeneration from (sorted location ids, seed). Runtime and restore read the published graph only.
- M3 `location_kinds` stays a per-location dataset field (activity environment closed set unchanged).
- M4 Every registered location is a world location: scenario `locations` (LocationReference) is deleted; shortest paths run over the whole graph.
- M5 Next-step hints: with full connectivity, multi-hop hints and the UNREACHABLE constants are deleted; a next step names the location only.
- M6 Ordering en route: the receiving location is still not a parameter. En route, the receiving location is the destination of the current transit session (the order arrives where the person is going); the system writes the chosen receiving location in `details.notes`. No request-shape change.
- M7 Constants (WorldMock, registered in EXPERIMENT.md, bound into run identity by `RULES_VERSION`): WALKING_METERS_PER_SECOND, DRIVING_METERS_PER_SECOND, DELIVERY_METERS_PER_SECOND, DELIVERY_PREPARATION_SECONDS, DELIVERY_FEE_FEN, DRIVING_FARE_MIN_FEN=500, DRIVING_FARE_FEN_PER_METER=0.2 (ceil), walking fare 0, EXTRA_EDGES = floor((n−1)/2), EDGE_LENGTH_MIN_METERS, EDGE_LENGTH_MAX_METERS (values from references.md).
- M8 Travel fare: computed from WorldMock constants; TravelFareRule, travel-service object and travel pairings deleted; an implicit `transport` ledger account receives fares.
- M9 Opening hours deleted (see D6).
- M10 Trip identity in evidence: `trip_id = "<origin>-><destination>:<mode>"`, activity data keeps `path` (location ids) and `distance_meters`; `route_id` disappears from events, DeliveryState and forbidden-key lists.
- M11 Viewer: the run viewer shows origin, destination and path names; no graph drawing is required.
- M12 Fixture: the artificial environment grows to ≥ 5 locations (home, office, two restaurants of the same kind, one cafe, one convenience store), generated edges, two apps, foods in all three categories, contents in all five kinds, one work topic, so every behaviour test runs without `--environment-root`.

## Storefront and commerce (storefront_commerce)

- S1 `opening_hours.py`, `MERCHANT_CLOSED`, `MERCHANT_OPENING_UNKNOWN`, `ELIGIBILITY_CLOSED_HOURS`, `location_hours_change` are deleted.
- S2 Delivery ETA constants as M7; EXPERIMENT.md's delivery assumptions rewritten.
- S3 Seller accounts: implicit `store:<location_id>` per food store and `app:<app_id>` per app, created at world setup with balance 0; scenario `accounts` keeps only the employer and the external scenario account; `required_qualifications` and `LOCATION_ACCESS_DENIED` are deleted.
- S4 Delivery fee goes to the app's implicit account.
- S5 App choice when `from` is null and the chosen store binds several apps: the first app in the store's declared `apps` order (the dataset's order is the priority), written into `details.notes`. `from=<app>` with way 到店 is accepted when the person is in a store bound to that app; no app is recorded for in-store purchases.
- S6 Visibility credentials (`visible_supply_ids`, `supply_browsed_at`, `supply_browsed_location`, `SUPPLY_NOT_VISIBLE`) are deleted. Goods presentation uses the existing candidate window and rotation keyed by `food_id`; named purchase remains unrestricted.
- S7 In-store consumption: per-location holdings already implement "buy and eat in the store"; no merged buy-and-eat action.
- S8 En route orders: M6.
- S9 Binding: explicit `stores` list or `"all"`; `FOOD_BINDING_KIND_INCONSISTENT` rejection.
- S10 Cooking deleted (D7).
- S11 Food constants adopted (D8).
- S12 Error codes: keep the names `SUPPLY_NOT_FOUND`, `SUPPLY_NOT_AT_STORE`, `SUPPLY_SOURCE_MISMATCH`, `FULFILLMENT_NOT_SUPPORTED`, `SOURCE_NOT_FOUND` (stable codes, same meanings over foods and stores). A store without any app cannot deliver: `FULFILLMENT_NOT_SUPPORTED` with reason "This store has no delivery app." No `APP_NOT_BOUND`.
- S13 Run manifest: `environment_id`, `environment_manifest_sha256`, `rules_version` replace `map_dataset`/catalog identity; `purchases.jsonl` carries `app_id` (null for in-store) instead of `channel_id`; only real orders are rows.

## Food and body (food_body)

- F1 Body constants live in WorldMock (`worldmock.exchange.body_rules`); `resources.jsonl` deleted.
- F2 Food utilization efficiency, the energy cap and drains stay: raw intake is the fixed category kcal, net energy goes through the existing efficiency and cap.
- F3 Cooking deleted.
- F4 Tool-layer food `state` writes the category and the fixed kcal: "零食，每份约 200 千卡".
- F5 Constants adopted; the 60 s minimum and the 14400 s consume cap stay.
- F6 Durables leave the catalog; `InventoryItemConfig`/`ProductConfig` become `FoodConfig`; `initial_inventory` holds foods only.
- F7 Motivation: the kcal floor for meal candidates is replaced by the category rule (正餐 counts as a meal; 零食 and 饮品 are light options).
- F8 System-layer rules 15 and 16 keep their meaning and name the three categories where they name foods; `prompt_sha256` is re-pinned by this feature anyway.
- F9 Home pantry: `initial_inventory` convention stays; the engine floor check is deleted.
- F10 Portion phrases and the 1e-9 inventory precision stay.
- F11 `RULES_VERSION` (string constant in `worldmock.exchange`) is recorded in the run manifest and checkpoints; a mismatch rejects open, resume and replay.

## Work (work)

- W1 Actual seconds (D10): `effective_work_seconds`, `work_efficiency_factors`, `work_notes_multiplier`, efficiency evidence and the workload `effective_work_seconds` key are deleted; fatigue remains a body rule with no work product.
- W2 Completion: `AgentState.work_progress: dict[topic_id, int]` accumulates seconds while a work activity on that topic is active; the derived duration of a work activity is min(remaining seconds, seconds to deadline, 86400); when the accumulated seconds reach the target the activity completes and an `environment_event` with `data.kind = "work_topic_completed"` is written; a completed topic cannot be started again (`WORK_TOPIC_COMPLETED`).
- W3 Deadlines stay: topics carry `opens_at` and `due_at`; the task target keeps `contract{opens, deadline, place, remaining, outlook}` with `remaining` as remaining hours; `task_due_soon`, `commitment_threatened`, `TASK_EXPIRED`, overdue listing until the local day end stay.
- W4 Skills: `required_skill`, `minimum_skill` and `AgentConfig.skill_levels` are deleted (no remaining consumer).
- W5 Wake vocabulary: `work_review_result` stays as a retained member with no source (precedent `activity_infeasible`); `task_available` and `task_due_soon` keep their sources (topic opening and deadline). `app_push` is appended after `break_end`.
- W6 Narration inputs: the work activity target's `state` writes the topic, hours done, hours remaining and the previous narration (`ActivityState.progress_note`); no new state.
- W7 Narration persistence: unchanged (`report_status` summary → `progress_note` and progress layer; pause/conclude → `self_notes`); no new export artifact.
- W8 Employer account stays a declared scenario account used by `payrolls`.
- W9 Work admission: the materials rule and `WorkMaterialRequirements` are deleted; topics are optional per agent.
- W10 Fixture ships artificial topics, payrolls and a work schedule.
- W11 Ledger rows that used `submit_work`, work materials or reviews are retired together with their tests; the acceptance JSON, owners fixture and public-api baseline are updated in the same commit.

## Clock, sessions, events (clock_sessions_events)

- C1 Session reuse: push eligibility is the session current at issue time; a push issued at an arrival second belongs to the new stay session.
- C2 Retained wake members: as W5; `location_hours_change` loses its source and is retained with no source.
- C3 `app_push` is a decision opportunity for awake persons (like `message_arrival`); content completion inside an app-use activity is silent (next draw, no decision).
- C4 Pushes are issued to sleeping persons too (stored on the session, no wake: not in the sleep-allowed set).
- C5 DST: the hourly grid follows the `duty_edges`/`clock_epoch` precedent (skipped hour uses the pre-jump offset, repeated hour takes the first).
- C6 Window: on the hour 08:00 … 21:00 local (14 per day); compiled rows obey start < due < end.
- C7 The push `environment_event` follows the existing `_process_scheduled` conventions and is visible to the pushed person only.
- C8 `price_change`, `supply_quote_change`, `product_release` scheduled kinds are deleted; `APP_PUSH` is added; the rest stay.
- C9 Work accrual: elapsed seconds; `activity_efficiency` stays a body fact on `activity_progress`.
- C10 No eligible app at an hour: the scheduled event resolves with `pushes: []` and no person-visible record.
- C11 Ledgers: delete mutation rows tied to deleted features; keep the idle-fillers registered source texts byte-for-byte.

## Observation, tools, prompt (observe_tools_prompt)

- O1 App-use duration: derived constant `APP_USE_SECONDS` (30 min); when a content ends with time left the next content is drawn and consumption continues; `report_status` applies at the usual interval.
- O2 App choice: S5.
- O3 Apps are always-listed tool-layer targets (`kind = app`, name `应用：<name>`), not candidates; an app with contents lists way 「打开」; an app with only stores lists no action (purchases go through food targets with `from=<app>`); `state` writes the pending push summary for this session or null.
- O4 Per-content progress is not shown in the tool layer; results and `settled` sentences say what was consumed and whether it finished; `ContentProgress` keeps `position_seconds` and `completed_count`.
- O5 Work: topics are scenario-level, visible from `opens_at` via the existing `WORK_AVAILABLE` event (no mail needed); mail no longer references tasks.
- O6 Product-catalog browse is deleted (`browse` kind, `BROWSE_TARGETS`, pinned results, catalog target kind, the D17 visibility clause).
- O7 Opening hours and stock deleted.
- O8 Cooking deleted.
- O9 Contents carry no genre; `taste_weights`, `entertainment_relief_preference_floor` and the effective-preference relief formula are deleted; relief is a fixed per-kind rate (WorldMock constant table by content kind). Persona `tastes` stay prose in the persona layer.
- O10 The progress-layer `app_push` record carries `app` and the full `summary` (summaries capped at 200 characters at registration) and folds under prompt capacity like other records.
- O11 `app_use` claims `app_use` and `screen` for every content kind (driving excludes it).
- O12 `PlaceKind` as ruled (Q2).
- O13 Last narration: `ActivityState.progress_note`.

## Action execution (actions_execution)

- A1 Content kinds: `article`, `image_text`, `short_video`, `long_video`, `live`.
- A2 Way 「打开」 → (`interact`, `use_app`); synonyms 刷、刷一刷、刷手机、用一会 map to 「打开」; object prefix 「应用：」.
- A3 Duration: O1.
- A4 `app_push` wake reason: C3.
- A5 En route: M6.
- A6 Appointments: `appointment` and `related_task_id` are deleted; topics carry `opens_at`/`due_at`; `WORK_AVAILABLE` scheduled at `opens_at`.
- A7 Actual seconds.
- A8 Cooking deleted.
- A9 Constants; 60 s minimum and consume cap stay.
- A10 Relief: O9.
- A11 Random draws are derived from `sha256(random_seed, key)` where key is the push's `scheduled_id` or the activity id plus draw sequence; `random_state` is not advanced; restore and replay therefore draw identically.
- A12 Pushes issued on the road expire at arrival (session switch); eligibility is checked when the app is opened; no session-end hook.
- A13 New code `APP_NO_CONTENT` (app has no entertainment content); the tool layer marks 「打开」 unavailable with the same reason. Ordering through an app whose stores do not sell the food: `SUPPLY_NOT_AT_STORE`; store without app for delivery: `FULFILLMENT_NOT_SUPPORTED`.
- A14 Identity bumps: `WORLD_SCHEMA_VERSION` 9→10, scenario structure 6→7, environment manifest (new, replaces catalog 5.x and corpora manifests), `map.json` structure 1, tool and prompt fingerprints change, the fixture `affordances.toml` is regenerated byte-identical to the package resource.

## Footprints, export, delivery (footprints_export_delivery)

- E1 `ActivityKind.watch` is replaced by `app_use` (target_id = app_id); `play_game` and `browse` are deleted; `ActivityChange.change_speed` and `playback_speed` are deleted.
- E2 `app_push` joins `WAKE_REASON_NAMES`; the idle-fillers sync points (count 25→26) are updated per that spec's sync-point list.
- E3 LabelKit windows: environment events keep projecting as today; app facts reach the phone synthesis through `app_usage.jsonl` and `purchases.jsonl`.
- E4 Pushes live on `SessionState.pushes: list[PushRecord{push_id, app_id, content_id, summary, issued_at, consumed_at}]`; per-app-per-session limit = app_ids already in `pushes`.
- E5 Frequency: C6 plus one per app per session.
- E6 Content progress: `completed_count`; re-watch allowed; `repeat` = completed_count ≥ 1 at start.
- E7 Playback speed deleted.
- E8 Persona coverage counts app mentions against the environment app catalog names; `coverage_apps.py`, `PhoneSimulationCoverage` and `phone_simulation_config` are deleted.
- E9 Export: new `app_usage.jsonl` (one row per consumed content interval: activity, agent, app, content, kind, position before/after, completed); `purchases.jsonl` carries `app_id`; `activities.jsonl` travel rows keep origin, destination, path, distance.
- E10 Versions: world 10, export manifest 3, quality report 5, delivery manifest 3, run manifest 3; event and checkpoint schemas stay 1.
- E11 Fixtures: historical complete-day proofs whose tests only verify bytes stay; fixtures that drive behaviour tests of deleted features are deleted with those tests.
