# Final rulings (orchestrator, 2026-10-06) — authoritative input for the spec

Precedence: this file > `understand/SYNTHESIS.md` §3/§4 > `rulings-maps.md` > `rulings-draft.md`. The spec writer must
record every ruling below in 裁决记录 (keeping the identifiers) and must treat SYNTHESIS.md §3 (D-1 … D-52) and §4 (data
contracts) as adopted unless a line here overrides it. The references for 依据 are in `references.md` (constants,
conventions) and in the proposal's evidence files.

## A. Proposal decision table — adopt every recommended option (D1–D10 of the proposal = D-1 … D-10 of SYNTHESIS §3)

## B. Synthesis decisions D-11 … D-52 — adopted, with these overrides

| ruling | override of SYNTHESIS | value / rule | 依据 |
| --- | --- | --- | --- |
| C-1 constants | D-5, §4.9 | `WALKING_METERS_PER_SECOND = 1.2`; `DRIVING_METERS_PER_SECOND = 7.0`; `DELIVERY_METERS_PER_SECOND = 5.0`; `DELIVERY_PREP_SECONDS = 600`; `DELIVERY_FEE_FEN = 500`; driving fare `max(500, ceil(0.2 × metres))` fen, walking 0 | references.md §1 and "Suggested constants" (MUTCD/Browning walking band; Amap 2024 as upper bound for city driving; GB 17761-2018 e-bike cap; Meituan ETA components and per-order delivery revenue) |
| C-2 map constants | §4.9 | `MAP_MIN_NODES = 2`; extra edges `⌊(n−1)/2⌋` (so a two-node map has one edge and no duplicate); edge lengths uniform integers in `[200, 2000]` metres; generator = random labeled tree (NetworkX `random_labeled_tree`, seeded) + extra random non-duplicate non-loop edges | references.md §5 (Shanghai 7.5-minute access, Amap 1,185 m transit access); industry-evidence.md (NetworkX) |
| C-3 relief rates | §4.9 | `CONTENT_RELIEF_PER_HOUR`: article 0.2, image_text 0.2, short_video 0.3, long_video 0.4, live 0.3 (entertainment-need units per hour of consumption) | 实验假设; today's environment items cluster at 0.2 per hour; registered in EXPERIMENT.md 实验假设声明 |
| C-4 food constants | D-8 | 零食 200 kcal / 300 s, 饮品 100 kcal / 180 s, 正餐 600 kcal / 1200 s per portion; `portion_duration`, the 60–14400 s consume window, food utilization efficiency and the energy cap stay | references.md §2 (中国居民膳食指南 2022 lunch share 30–40 % of 2000 kcal; 学龄儿童膳食指南 snack ≤ 10 %; NBS 2024 time-use 103 min/day eating); drink kcal and the snack/drink durations are 实验假设 |
| C-5 app use | D-15 | `APP_USE_DEFAULT_SECONDS = 1800` with content chaining | references.md (CNSA 2025: 156 min/day short video over several sessions); 实验假设 |
| C-6 push grid | D-4, D-16, D-17 | hourly on the hour 08:00 … 21:00 local; ≤ 1 per person per hour; ≤ 1 per app per session; `app_push` is a wake reason for awake persons; issued and stored while asleep without wake; summary ≤ 200 characters at registration | references.md §4 (Braze quiet hours and frequency capping; Apple HIG / Android notification tap opens the content; Pielot 2014 63.5/day shows the cap is conservative); the 08:00–22:00 window is the user's requirement |
| C-7 third day baseline | D-46 | the third complete-day baseline is generated on this machine (Windows) from two matching runs on the artificial environment, the platform is recorded; POSIX and macOS receipts are listed as outstanding in 门禁与平台 (the cross-platform proposal is unimplemented) | house rule from SPEC-idle-fillers 门禁与平台; honesty about platform coverage |
| C-8 summary provenance | D-42 | `summary_source{kind: real|synthetic, url: str|null, notice: str|null}` required on every content (url required when real); foods carry no summary field (name and price only) | proposal 第二章「真实摘要保留来源信息，合成摘要明确标记合成」; the user's text does not ask for food summaries |

## C. Superseded lines of the earlier memos (do not use)

- rulings-maps M6 (en-route orders to the transit destination) → D-11: self-delivery orders in transit are refused with `ACTIVITY_PRECONDITION_UNMET`, `unmet = delivery_destination`, "Place the order before departing or after arriving."; orders for others go to the recipient's receive location.
- rulings-maps W2/W3 (`AgentState.work_progress`, deadlines kept) → D-20: `work_topics.json` + `agents[].work_topic_ids`; no deadline, appointment, notice mail or required location; place = `work_schedule.workplace_location_id`; completion derived from the elapsed seconds of the topic's non-failed work activities; `work_topic_completed` environment event and `work_completed` progress record; `WORK_AVAILABLE`, `TASK_EXPIRED`, `task_available`, `task_due_soon`, `related_task_id` deleted.
- rulings-maps M3 / rulings-draft R-D (`location_kinds` per location) → D-28: derived from `kind` via `PLACE_KIND_ENVIRONMENTS` (home→home, office→office, park→outdoor, fitness→fitness, restaurant/cafe/convenience_store/supermarket→venue); lifestyle items keep their own `location_kinds`.
- rulings-maps M4 (delete scenario `locations[]`) → D-30: the scenario keeps its `locations[]` selection; the whole graph is loaded; paths may traverse unselected nodes; registration checks run on the full catalog.
- rulings-maps M2 (loader regenerates) → D-31: the runtime loader validates structure only; `worldmock environment generate-map --check` and `worldmock validate --environment` byte-compare the published `map.json` with the regeneration.
- rulings-maps S3 / rulings-draft Q11 (implicit accounts) → D-33: scenario `accounts[]` declares one `merchant:<location_id>` account per selected food store (load check rejects missing ones) plus `employer:*`, `transport:service`, `external:scenario`; the delivery fee is paid to the merchant in the same transfer; `DeliveryState.seller_account_id` stays as evidence.
- rulings-maps S5 (first declared app) → D-34: lowest `app_id`; `from=<app>` with way 到店 is `SUPPLY_SOURCE_MISMATCH`.
- rulings-maps S12 / A13 (`APP_NO_CONTENT`, no `APP_NOT_BOUND`) → D-35: `APP_CONTENT_EMPTY` and `APP_NOT_BOUND` are added; registration codes per §4.8.
- rulings-draft Q10 (one manifest) → §4.1: `catalog/manifest.json{format_version: 1, environment_id, environment_version, rules_version, files[], catalog_content_sha256}` and `corpora/manifest.json` stay two files; `ENVIRONMENT_STALE_FILE` for any unlisted file under `catalog/`.
- rulings-draft Q15 / R-M (scenario pins manifest sha; version numbers) → D-25 and §4.3: scenario pins `environment{environment_id, environment_version}`; run manifest 3 carries `environment_identity{environment_id, environment_version, rules_version, catalog_content_sha256, map_sha256}`; world 10, scenario 7, export manifest 3, quality 5, delivery manifest 3, `map.json` schema 1, `RULES_VERSION = 1`.
- rulings-maps W5 / C2 (retain dead wake members) → D-23: delete `task_available`, `task_due_soon`, `work_review_result`, `location_hours_change`; add `app_push`; 22 members; the frozen "members are never deleted" sentence is rewritten to "added or deleted only through the version table and the nine sync points".
- rulings-draft R-H (`work_topics` in the scenario with opens/due) → D-20 (catalog file; scenario only binds ids).
- rulings-draft R-O (`app_id` column on purchases only) → D-44: `app-usage.jsonl` artifact plus `purchases.jsonl.app_id`.
- rulings-maps O9 wording "relief per kind" → C-3 values.

## D. Still-open points the spec must settle (writer decides, smallest consistent option, add to 裁决记录 with 依据 "本规格补定")

- Exact progress-layer and `details` field names where SYNTHESIS §4 leaves alternatives.
- The exact English error strings for new codes (keep the house pattern: one sentence, no internal ids).
- The exact Chinese text of the rewritten system-layer rules (2, 7, 8, 11, 12, 15, 16, 17 as needed), respecting the existing test constraints (SPEC-idle-fillers A list; SPEC-parallel-activities 丙).
- Fixture environment contents (names of the artificial locations, apps, foods, contents, topic) — plain test names, may use real-world-style app names such as 美团 and 抖音 since the fixture is a declared artificial world.
