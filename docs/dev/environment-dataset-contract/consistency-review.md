# Internal-consistency review of the environment dataset contract spec

Reviewed: `SPEC-environment-dataset-contract.md` (2807 lines), with `open-questions.md` as its stated source. Line numbers refer to the uncommitted spec as of 2026-10-09.

## How it was checked

- Links and anchors were checked mechanically: 48 anchors, no duplicates, all 15 internal links resolve, and both relative file links exist.
- 20 topic auditors each read the whole spec. Topics cover package files, locations, apps, goods, money, time and slots, content, actions, deleted subsystems, wake and sleep, callbacks, persona and config, records and evaluation, and global inventories of error codes, identifiers and numbers. Others covered open-item bookkeeping, the summaries and implementation section, and fidelity to the owner answers. A completeness critic then added 6 cross-cutting angles.
- Each auditor's findings went to a separate adversarial verifier that re-read the cited lines in context. Of 176 raw findings, 47 were refuted and 129 survived. Merging duplicates left **68 distinct issues**.
- Explicitly open items (【待裁决】 placeholders and the 待确认 lists) were not counted as inconsistencies unless another passage treats them as decided.

## Overall

No high-severity contradictions: the core rulings hold throughout the spec. These include a single persona, the deleted subsystems, the anchor account, sleep ending only at its planned end, and two activity slots. The issues are 15 medium and 53 low, and they cluster in five areas:

1. **Purchase became a timed activity, but not everywhere.** The activity has no name, no metabolic factor and no payment-settlement event. Closed-store routing is double-coded, and the "5-minute pickup" depends on which content gets picked (#1–3, 7, 34, 35, 45, 68).
2. **Footprint and event definitions have holes.** The ordering (下单) footprint, the step value for a sleep-bound app, the weather event key, `calendar_flag`, and the `scheduled_id` anchor formats are all undefined (#4, 5, 8, 10, 12, 59, 66).
3. **Document ownership is assigned twice.** Callback fields and codes are assigned to both 03 and 04, and event fields to both 05 and 09, while line 2747 requires each rule to have one owner (#13, 14).
4. **Names left behind by merges and deletions.** Examples: `run_start` vs `session_open`, `plan_reminder_due` vs `reminder_due`, the location menu, the order address, "unshown locations", and "behaviour families" (#25, 29, 31, 48, 54, 67).
5. **Open-item bookkeeping drift.** The section-level lists, the handbook list and the final table disagree with each other and with what the body already decides (#30, 33, 38, 39, 42, 47, 49, 51, 60–62).

## Drift vs wording

Two more judges classified each issue independently. The implementer judge asked whether they would know exactly what to build. The skeptic judge called drift only when two concrete implementations were both defensible from the text.

- **drift (24)**: readers would build different behaviour, formats, codes or tests. Issues 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 17, 19, 23, 27, 32, 34, 35, 45, 46, 56, 58, 66.
- **borderline (6)**: the judges split. Issues 4, 13, 43, 57, 65, 68.
- **bookkeeping (12)**: these only blur what is decided vs open. Issues 16, 30, 33, 38, 39, 42, 47, 49, 51, 60, 61, 62.
- **wording (25)**: the intended meaning is clear from context and the fix is editorial. Issues 1, 18, 20, 21, 22, 24, 26, 28, 29, 31, 36, 37, 40, 41, 44, 48, 50, 52, 53, 54, 55, 59, 63, 64, 67.
- **not an issue (1)**: issue 25 is withdrawn.

Note: severity and classification are independent. Issue 1 (the five-minute pickup) is medium in the original pass, but it is classified as wording. The engine rule is clear, and the 300 s is a dataset authoring convention.

Verdict key: **confirmed** means the verifier found a real inconsistency as described. **plausible** means real textual tension whose resolution depends on interpretation.

## Medium-severity issues

### 1. Five-minute pickup is stated as fixed, but pickup starts whatever content the normal recommendation picks

**Classification: wording**. The mechanism is stated plainly and repeated. Pickup takes all ready parcels and starts one location content chosen by the normal recommendation order, even when the kind binds other contents (527, 1005-1006). There is no engine pickup constant and no zero-second path (526, 1007). '由数据集声明' (the dataset declares it) puts the five minutes on the dataset author: bind a 300 s content to the station kind. Engine code, load validation (no check, since a check would be the forbidden system constant) and the fixture-based test (1087 '由目录声明', declared by the catalog) all come out the same. The 'five minutes' lines at 67, 1037, 1542 and 2794 are loose summaries of that authoring convention. Rewording them as 'the pickup lasts as long as the picked station content; the base fixture declares 300 s' is purely editorial.

*medium · plausible · ambiguity* · lines 67, 526, 527, 556, 589, 613, 704, 1005, 1006, 1007, 1037, 1087, 1542, 2496, 2794

Several passages say pickup takes five minutes because the dataset declares a 300 s pickup content: the overview, the goods section, the actions section, the system-layer prompt text (1037), the callback section, the open-items table and the tests (556, 1087). The mechanism says something different. A pickup at an accepts_parcels location starts whichever content the normal recommendation order picks among all contents bound to the station's kind, even when that kind binds other contents (527, 1005-1006). No field marks a 'pickup content', and no load check requires 300 s. The picked content can be any length of at least 60 s (589), a partly consumed content resumed through shared progress (704), or a series episode that auto-continues (613). The five-minute claim therefore holds only for fixtures whose station kind binds a single 300 s content.

Evidence:

- L526: 取件内容占五分钟，由数据集声明 `duration_seconds = 300`，不另设系统取件时长常数。
- L527: 即使地点类型还绑定其他内容，也仍然取走全部待取件并按通常规则选择内容
- L1005: 在 `accepts_parcels` 当前地点 `interact`，一次取走本站全部已到站件，并按正常推荐顺序开始该地点类型的一条内容。
- L1007: 取件内容的时长由数据集声明为 5 分钟，不写引擎取件常量
- L1037: 取件内容由数据集写 5 分钟
- L1087: 取件 5 分钟由目录声明
- L67: 取件内容为五分钟

Fix: Decide between two options. (a) Designate a pickup content, for example by requiring each accepts_parcels kind to bind exactly one non-series 300 s content, enforced at load or through a selection rule. (b) State that pickup lasts as long as the normally picked content, and change the five-minute wording at 67, 526, 1007, 1037, 1542, 2794 and in tests 556/1087 to describe it as a fixture or authoring convention.

### 2. A valid package can contain a parcel station where pickup can never succeed, yet the tool layer still offers pickup there

**Classification: drift**. Implementation A follows row 900 literally and lists the station's pickup interact, marked PARCEL_NOT_READY or available, even though admission will always reject it with CONTENT_EXHAUSTED. Implementation B applies 905 and 900's first clause and omits interact at a station kind with no bound content, so the tool layer never shows pickup there.

*medium · plausible · contradiction* · lines 248, 249, 270, 273, 597, 676, 900, 905, 1008, 2472

Admission only checks that some accepts_parcels location exists when app-side goods exist (249, 2472). Pickup always has to start a content bound to the station's kind (1005-1008), but location kinds have no lower bound on bound content (597). With no startable content, pickup is rejected as the empty content set (CONTENT_EXHAUSTED), and a content-free pickup is forbidden (1008). A package that passes validation can therefore leave paid courier goods that can never be collected. The tool layer lists the pickup interact at every parcel location and gives PARCEL_NOT_READY as its only unavailability reason (900), although line 905 says permanently impossible actions are not listed.

Evidence:

- L249: 存在 app 侧商品而没有驿站时，加载报 `PARCEL_STATION_REQUIRED`。
- L597: 地点类型没有必须承载内容的下界。
- L1008: 缺少可开始内容时仍遵守内容集合为空的拒绝与整组原子性，不能悄悄成功执行无内容的领取。
- L900: 收件地点提供取件的 `interact`，无件时标 `PARCEL_NOT_READY`
- L905: 永久不能满足的不列，暂时不可行的保留并写原因。

Fix: Add a load rule, for example by extending PARCEL_STATION_REQUIRED, that requires the station kind (or at least the home-nearest station's kind) to bind startable content. Alternatively, allow content-free pickup at stations as an explicit exception to 597/1008. Then make the tool-layer row at 900 consistent with 905.

### 3. Closed stores: conflicting error codes and routing for in-store and remote purchases

**Classification: drift**. A persona standing in a closed store that sells the item, with another open app-bound seller elsewhere: one implementation rejects with LOCATION_CLOSED (273/469), another treats the in-store condition at 444 as failed and routes to delivery from the open store. When every app-bound seller is closed: one implementation returns ACTIVITY_PRECONDITION_UNMET (466, after filtering by 445), another returns LOCATION_CLOSED (469 '外卖' closed).

*medium · confirmed · ambiguity* · lines 273, 274, 432, 433, 434, 436, 444, 445, 466, 469, 917

One side: lines 273, 469 and 917 reject a closed in-store purchase, and a takeout/remote order from a closed place, with LOCATION_CLOSED. Other side: delivery candidates are filtered to open, app-bound selling stores (434, 445), and line 274 says candidate selection and acceptance share one opening check. A closed store can therefore never be the chosen remote store. When every app-bound seller is closed, row 466 (no remote-orderable store, ACTIVITY_PRECONDITION_UNMET) and row 469 (closed 外卖, LOCATION_CLOSED) both apply, and the flowchart refusal (436) names no code. For a closed store the person is standing in, the flowchart sends them straight to an in-store purchase (433), while the fulfilment table makes '有售且营业' the in-store condition (444). That leaves room to fall through to delivery. No precedence is stated.

Evidence:

- L273: 闭店时，到店购买、从该地点远程下单、在该地点 `interact` 都以 `LOCATION_CLOSED` 拒绝。
- L445: 从营业、绑定 app 的有售地点中选最早预计送达者
- L466: | 不在有售地点且没有可远程下单的店铺 | `ACTIVITY_PRECONDITION_UNMET`，说明最近有售地点及前往要求 |
- L469: | 闭店的到店购买、外卖或地点 interact | `LOCATION_CLOSED` |
- L436: Remote -- 否 --> Refused[拒绝并说明到店或营业条件]
- L444: | 到店 `pickup` | 当前停留地点有售且营业；`store_id` 为该地点，`app_id` 为 null |

Fix: Choose one model and state the precedence. Either (a) select the store without an opening filter and then reject with LOCATION_CLOSED, or (b) filter closed stores out, use ACTIVITY_PRECONDITION_UNMET when no open candidate exists, and restrict LOCATION_CLOSED to an in-store purchase at a closed current location. Also state whether a closed current seller falls back to delivery. Align the flowchart (432-436), rows 444/466/469, 273 and 917.

### 4. An 'ordering' (下单) app_touch and 'existing shopping footprints' are referenced but never defined

**Classification: borderline**. The judges split: the implementer judge chose drift and the skeptic chose wording.

*medium · confirmed · undefined-reference* · lines 315, 316, 352, 357, 968, 2023, 2037, 2069, 2137

Line 968 lists 下单 (ordering) as a producer of app_touch footprints, and line 2069 says '已有购物足迹' keep being recorded. The normative footprint producers are only function-app automatic records and callback footprint_chain steps (2023, 2137). Ordering apps are content apps (316), which never produce automatic records, the shopping purpose has been deleted (315), and the step closed set browse/payment/navigation (2037) has no ordering value. No passage defines when an ordering footprint is written, for which app, or with which step, so it is unclear whether app-usage.jsonl gets an extra row per order.

Evidence:

- L968: 下单、支付、导航和回调生成的 `app_touch` 是足迹，不另占 app 内容名额，也不创建额外活动。
- L2069: 真实下单 app、已有购物足迹和付款分录继续记录，不因支付选择为空而丢弃。
- L316: 只有内容 app 能被地点、商品或内容绑定；只有功能 app 能绑定具体动作
- L2023: 功能 app 自动记录、回调 `footprint_chain` | 统一使用一种系统可见 `environment_event`
- L2037: `step` 为 `browse`、`payment`、`navigation` 之一

Fix: Remove 下单 from line 968 and the 已有购物足迹 clause from line 2069, so the ordering app is recorded only on the order fact. Alternatively, define an ordering footprint (producer, timing, app and step value) in the records event table and decide whether it extends the step closed set.

### 5. No footprint step value exists for automatic function-app records of sleep, and there is no action-to-step mapping

**Classification: drift**. Implementation A records an automatic sleep-app footprint with step = browse, the only value not already claimed. Implementation B treats sleep as having no valid step: it skips the record, rejects sleep bindings at load, or adds a new step value, so app-usage.jsonl has no row, or a row the other implementation would reject.

*medium · confirmed · undefined-reference* · lines 318, 352, 364, 1599, 2035, 2037, 2042

A function app may bind sleep (318), and every accepted sleep draws an app and records one unified footprint carrying a step (352, 364). The step must be one of browse, payment or navigation (2037). The spec assigns navigation to automatic travel (1599) and payment to the purchase payment jump (2042). It gives no step for a sleep-bound app, and it gives no general action-to-step mapping. The step also can't be inferred from the app alone, because one app may bind several actions. Implementers would have to invent a value, which the closed-set validation forbids, reuse browse arbitrarily, or drop the record.

Evidence:

- L318: 功能 app 可绑定 `walk`、`drive`、`take_taxi`、`take_bus`、`purchase`、`sleep`。
- L352: 出行和睡眠每个成功受理的活动抽取一次；只有一个候选时必选它，无候选时不记录。
- L364: 自动功能足迹和回调足迹使用同一事件形状，包含 app、具体动作和步骤，每条导出为 `app_touch`。
- L1599: 统一事件还容纳自动导航的 `navigation` 步骤，这不扩大回调步骤闭集。
- L2037: `step` 为 `browse`、`payment`、`navigation` 之一

Fix: Add an explicit action-to-step mapping for automatic records (walk/drive/take_taxi/take_bus to navigation, purchase to payment). For sleep, either add a step value to the closed set (in both the callback and records sections) or remove sleep from the bindable actions.

### 6. What a platform app bound only by locations shows on interact is undefined

**Classification: drift**. Implementation A shows, inside 美团 (Meituan), the location-side goods of open stores that bind 美团, with delivery quotes for those stores. Implementation B shows only app-side goods bound to 美团, so it displays an empty catalog, or nothing, for the base-fixture 美团.

*medium · plausible · ambiguity* · lines 322, 380, 542, 543, 657, 2518, 2524, 2525, 2806, 2807

In the base fixture, 美团 and 饿了么 meet the content lower bound only through location app_ids (380, 322). Line 2806 requires a goods-only platform to show goods when the persona interacts with it, and line 657 defers this to the display path in 「商品与履约」. That section defines only the display of app-side (courier) goods on interaction (543). It never says whether a location-bound platform shows the location-side delivery goods of the open stores that bind it. One implementer could show those delivery goods; another could show nothing. Lines 2806-2807 rule out CONTENT_EXHAUSTED, which leaves no defined display. The same gap applies to a content app bound by a location that sells no goods.

Evidence:

- L380: 美团、饿了么由地点供给满足内容下界。
- L657: 平台 app 的商品目录由「商品与履约」的展示路径提供，
- L543: app 侧商品只在人物与对应内容 app 交互时展示价格、运费和预计到站
- L2806: 只有商品目录、没有显式内容行的平台 app 也已被确认为合法内容 app，必须能通过交互展示商品。

Fix: State which goods a platform displays on interact: app-side goods bound to it plus location-side delivery goods at open stores that bind it. Also state whether a location reference with no goods still satisfies the content-app lower bound.

### 7. The deferred purchase-payment event, which payment_notification.caused_by must point to, is never defined

**Classification: drift**. Implementation A writes a new system environment_event (for example data.kind = purchase_payment) at segment end, carrying the group's resource_changes and order_ids, and points the notification's caused_by at it. Implementation B puts the transfer's resource_changes on the purchase activity's progress or completion event at that boundary and points caused_by at that activity event.

*medium · plausible · undefined-reference* · lines 365, 495, 496, 1595, 1610, 2008, 2021, 2033, 2049, 2117, 2164, 2166

Purchase payment now settles at the end of each payment segment, or at the end of the activity for a null choice (495-496), and acceptance must not create a payment-success fact (1595). payment_notification.caused_by must point to 'the corresponding real payment fact' (2049), and the export invariants check payment causality (2164, 2166). The records event table, which the spec makes the only place that defines event fields (365, 1610), has no row for this settlement event. It gives no kind, no data fields, no carrier for its resource_changes and no link to the orders. EventType stays at 14 members (2008). The causal target is therefore used but not specified.

Evidence:

- L2049: `caused_by` 指向对应的真实付款事实。该通知本身不转账，不能用通知重复扣款。
- L495: 支付段开始时写该 app 的跳转 `app_touch`，段结束时结算该组货款和运费；成功后同一时刻写一条系统消费推送。
- L1595: 购买受理不提前生成付款成功事实
- L2166: 付款关联真实购买及对应支付段结算，到站、收货不再扣一次款
- L2033: `body_settlement`、`delivery_arrival`、日历、当日目标、个人事务、活动生命周期和初始资金证据继续按所属模块记录

Fix: Add a row to the environment-event table for each purchase group's payment settlement, giving its type, data, resource_changes, order links and timing for both segment-end and null-choice activity-end. Name it as the caused_by target at 2049 and in the invariant at 2166.

### 8. Callback anchors for time, weather, delivery and calendar events use a scheduled_id whose format is never defined

**Classification: drift**. Implementation A uses ids like time-{rule_or_key}-{local_date}T{HH} and weather-{local_date}. Implementation B uses the absolute due instant, for example time:hourly:{epoch} and weather:{local_date}:{agent_id}. The same seed then gives different callback footprint app picks and different ledger cause strings.

*medium · confirmed · undefined-reference* · lines 517, 1558, 1577, 1583, 1856, 1861, 1870, 1891, 1896

The spec says every random-key format belongs to RULES_VERSION and is defined only in the random-source table (1856), and that stable anchors are defined only in 「人设与运行配置」 (1583). For callbacks fired by time or scheduled events, the anchor is 'that scheduled row's scheduled_id' (1891). That anchor feeds the callback footprint random key (1870) and the ledger cause (1896). The table gives a scheduled_id format only for ordinary app pushes (1861). The new per-persona time-event kind (hourly/daily/monthly/run_start/run_stop, 1558), the new daily weather fact (1577), merged delivery rows (517) and calendar_flag have none. Their random keys and ledger causes cannot be derived from the spec. A local-date-and-hour id like the push format would also repeat across DST fall-back hours.

Evidence:

- L1891: | 时间或预排事件 | 该预排行的 `scheduled_id` |
- L1861: | 普通 app 推送 | 保留 `scheduled_id = push-{agent_id}-{local_date}T{HH}` |
- L1856: 每个随机源经 `random_stream(random_state, key)` 取独立流，键格式属于 `RULES_VERSION`，只在下表定义。
- L1870: | 回调足迹 | `callback:{rule_id}:{anchor}:{step_index}`，步骤下标从零起 |
- L1558: 统一使用一个新增、带人物身份的时间事件种类，

Fix: Add scheduled_id formats for every scheduled-row kind that can be a callback anchor to the random-key/anchor tables: each time key including run_start/run_stop, merged delivery, the weather fact and calendar_flag. Make each unique per instant, for example by basing it on the absolute due instant. Alternatively, define those anchors directly.

### 9. A platform app with both explicit contents and app-side goods: whether interact shows the goods is undecided

**Classification: drift**. Implementation A follows the flowchart: interacting with a platform that has a browsing content starts that content and shows no goods, so the courier goods are visible only on platforms without content. Implementation B follows 543/2525: the result lists the app-side goods with prices, fees and arrival, and also starts the content.

*medium · plausible · ambiguity* · lines 542, 543, 666, 668, 681, 901, 942, 2525, 2526

In the content-selection flowchart, only a catalog-only app takes the goods-display branch. An app with explicit content rows goes to '取该 app 的显式内容' (666-668). The goods section, the tool-layer table and the handbook say app-side goods (price, fee, arrival) are shown whenever the persona interacts with the platform (543, 901, 2525). The handbook also invites authors to add ordinary browsing content to a platform (2526). Under the flowchart reading, adding such content would hide the platform's courier goods; under 543/2525 they remain visible. Line 681 says catalog display 'follows 商品与履约' but does not say whether it runs alongside a content activity.

Evidence:

- L666: K -->|内容 app| G{"是否只有商品目录"}
- L668: G -->|否| A["取该 app 的显式内容"]
- L543: app 侧商品只在人物与对应内容 app 交互时展示价格、运费和预计到站，不独立列入工具层商品目标；
- L2525: 人物互动平台时看到可购商品的价格、费用与预计到站；app 侧商品不单独铺在工具层。
- L2526: 数据集可额外编写普通浏览内容描述商品和使用体验

Fix: State whether a platform that has both explicit contents and app-side goods shows its goods on interact, and if so whether alongside or instead of starting a content. Align the flowchart (661-681), line 942 and handbook 2525-2526.

### 10. Unclear whether a no-eligible-app push record (pushed: false) triggers app_push event subscriptions

**Classification: drift**. Implementation A matches subscriptions on the written event's kind, so an event: app_push rule fires on every 08:00-21:00 hour, including pushed:false records. Implementation B fires only when a content push is actually delivered, so hours with no eligible app produce no callback effects.

*medium · plausible · ambiguity* · lines 725, 1361, 1576, 1579, 1625, 1707

A subscription fires whenever an environment_event fact is actually written (1579), and the only exclusion given is an empty delivery, which writes no fact. An hourly app_push row with no eligible app does write a system-visible fact of kind app_push with pushed:false (725). Read literally, `event: app_push` rules would then fire on every 08:00-21:00 hour. Line 1576 limits subscriptions to an 'ordinary' app_push, and the matching wake trigger requires an ordinary content push to actually be generated (1361). The two readings give materially different firing frequencies, and the test row (1707) covers only the empty-delivery case.

Evidence:

- L725: | 无合格 app | 系统可见 `{kind: "app_push", pushed: false, session_id}` |
- L1579: 只有实际写下 `environment_event` 的结果触发订阅；空配送不写事实，也不触发。
- L1576: 可订阅 `delivery_arrival`、普通 `app_push`、保留的 `calendar_flag` 与每日天气事件。
- L1361: 本次推进实际生成普通内容推送且人物醒着；不包含回调阈值提醒或系统消费推送

Fix: State explicitly whether a pushed:false record triggers `event: app_push` (for example, 'only an actually delivered ordinary content push triggers it'), and add a test next to the empty-delivery case.

### 11. Visible balance-decrease hint: the body counts any paid persona expense, the handbook only a paid callback transfer

**Classification: drift**. Implementation A reads L1615/1617 literally: for a take_taxi footprint_chain with visible=true it writes 'balance decreased by <fare>' from the commit's balance delta. Implementation B follows L2571 and sums only paid callback transfer effects, so it writes no hint for the same dataset, and the visibility tests at L1705 disagree.

*medium · plausible · ambiguity* · lines 938, 995, 1602, 1603, 1615, 1617, 1705, 2565, 2571

The body writes the hint when the same trigger has 'an actually paid persona expense' (1615), and defines X as 'the balance decrease this trigger actually caused' (1617). The handbook allows the hint only when the same trigger has 'a paid transfer' (2571). Engine fares for taxi, bus and drive are paid at departure acceptance, not by a callback (938, 1602). So the handbook's own example, a take_taxi footprint_chain with visible=true and no transfer (2565), shows the fare under 1615/1617 and shows nothing under 2571. A further open point is whether X is the gross outflow or the net change when an income transfer occurs in the same trigger.

Evidence:

- L1615: 同次触发有 `visible=true` 且实际已付的人物支出时，全部规则结算后至多写一条人物可见事件，
- L1617: `X` 是本次触发实际造成的人物余额减少量，工资不能写成减少。
- L2571: 可见提示仅在同次触发有已付转账时出现一次。
- L2565: | 打车的浏览和支付足迹 | `take_taxi` 上额外声明 `footprint_chain`；支付步骤只记足迹，不转账、不再次收车费 |
- L938: 非零车费在出发受理时付款

Fix: State whether engine-charged fares (and any other non-callback payment committed by the same trigger) count toward X, or only callback transfer effects do, and whether X is gross outflow or net decrease. Align 1615/1617, 1705 and 2571.

### 12. The subscribable daily weather event, and the new time-event kind, have no event name or record definition

**Classification: drift**. One loader accepts `trigger: {class: event, key: weather}`, matched against an invented `weather` environment_event kind. Another uses `weather_drawn` (or `daily_weather`), so a dataset that works on one implementation is rejected as KIND_INVALID on the other.

*medium · confirmed · undefined-reference* · lines 986, 1558, 1561, 1576, 1577, 1669, 1862, 2021, 2033, 2567

Lines 1576-1577 and handbook 2567 make the daily weather scheduled fact a subscribable callback `event` key. Event keys form a closed set, and unknown keys are rejected with ENVIRONMENT_KIND_INVALID (1669). Every other subscribable event has an identifier (delivery_arrival, app_push, calendar_flag), but the weather event's kind is never named or given a payload. The weather section (986-990) defines only the draw, the only weather identifier is the random key weather:{local_date} (1862), and the records event table (2021-2033) has no weather row. Dataset authors cannot write a valid subscription, and loaders would choose incompatible names. A lesser gap: the new per-persona time-event kind (1558) is also unnamed, though 1561 says it is not an event key.

Evidence:

- L1576: 可订阅 `delivery_arrival`、普通 `app_push`、保留的 `calendar_flag` 与每日天气事件。
- L1577: 天气本地零点生成每日预排事实，回调订阅事实，不使用状态阈值。
- L1669: | 未知字段、类、键、动作类型、退役事件，非法 `once_per`、步骤、`pick`、`channel` 或 `visible` | `ENVIRONMENT_KIND_INVALID` |
- L2567: | 天气订阅 | 订阅每日天气预排事件；天气按本地零点抽取，不把天气注册为回调状态 |
- L1862: | 每日天气 | `weather:{local_date}` |

Fix: Name the weather scheduled-event kind (e.g. `weather_drawn`) with its payload (state, speed factor), add it to the records environment-event table and the callback event-key list, and name the time-trigger carrier kind.

### 13. Two different design documents own the callback/footprint event fields (IO design vs recording engine)

**Classification: borderline**. The judges split: the implementer judge chose wording and the skeptic chose drift.

*medium · confirmed · contradiction* · lines 1610, 1695, 2005, 2362, 2740, 2743, 2747

The callback section assigns callback event fields to the IO design, 09-io-formats (1695). The records section says it maintains the field closure for callback evidence and footprints itself (1610, 2005), names 05-recording-engine as its landing document, and limits 09 to world and manifest structure (2362). The implementation table gives events and footprints to 05 (2740) and gives 09 only state and strict serialization (2743). Line 2747 requires each rule to live in exactly one module document, so the two ownership statements cannot both hold.

Evidence:

- L1695: 正式正文归模拟引擎的回调规则表；状态归属、事件字段归 IO 设计
- L1610: 字段闭集和导出投影只在「记录、导出与评估」维护
- L2362: `docs/design/05-recording-engine.md`、`07-evaluation.md` 为本节主要落点；04 只链接导出规则，09 维护世界和清单结构
- L2740: | 记录 | `docs/design/05-recording-engine.md`：事件、足迹、导出、不变量和查看器 |

Fix: Change line 1695 to place callback event fields in the recording design (05) and keep only state ownership/serialization in 09, or narrow 2362/2740 to give 09 the event schemas.

### 14. Callback field shapes and CALLBACK_* registration codes are given two owners: the simulation-engine callback table and the resource-engine design

**Classification: drift**. One implementer puts the callbacks.jsonl row schema and the CALLBACK_* codes and reasons in 04-simulation-engine.md's callback table, and the handbook links there. Another puts them in 03-resource-engine.md's catalog-field and registration-error sections, with 04 holding only evaluation semantics. The handbook links then point at different documents.

*medium · confirmed · contradiction* · lines 1695, 2399, 2444, 2558, 2669, 2738, 2739, 2747

Lines 1695 and 2558 put the formal callback field shapes and registration codes in the simulation-engine (04) callback rule table, and the handbook links only there. Line 2444 sends every JSONL field table to the resource-engine design. Line 2669 sends all environment registration-code links to `resource-engine-注册校验与错误`. Line 2399 keeps English reasons only in the resource-engine design, and line 2738 gives 03 catalog fields and registration errors. Line 2739 gives 04 only callback evaluation. With the one-owner rule at 2747, implementers would place callbacks.jsonl shapes and the CALLBACK_* codes and reasons in different documents.

Evidence:

- L2558: 字段的准确形状与登记码只链接「回调规则表」，避免手册维护另一套语法。
- L1695: 正式正文归模拟引擎的回调规则表；状态归属、事件字段归 IO 设计
- L2444: 手册应说明每个 JSONL 的一行含义和编写入口，正式字段表链接资源引擎设计。
- L2669: 环境登记码链接统一指向 `resource-engine-注册校验与错误`，不再误链到文件列表。
- L2738: `docs/design/03-resource-engine.md`：包闭集、目录字段、绑定、登记错误、内存地图与发布身份

Fix: Pick one owner for callbacks.jsonl field shapes and the CALLBACK_* codes and reasons. For example, put file shape and registration codes in 03 and evaluation semantics in 04. Then align 1695, 2444, 2558, 2669 and 2738.

### 15. Validation must compile the persona card, yet pure validation must not call the model

**Classification: drift**. Implementation A makes `validate --environment` compile the card (three model extractions) to satisfy L1791, which breaks the L1981 test 'qualified pure validation does not call the model'. Implementation B compiles only on new-run creation and `profile validate`, and has `validate --environment` skip compilation, which a test written from L1791 would flag as skipping the required card.

*medium · plausible · ambiguity* · lines 1751, 1791, 1822, 1834, 1848, 1925, 1931, 1981

Line 1791 requires new-run creation and validation to compile the single persona's card. Card or profile compilation is tied to a model identity and an extraction retry budget (1822, 1834, 1931), which implies a model call. Lines 1848 and 1981 forbid model requests during pure validation. Environment validation and profile validate go through the same full load (1751, 1925). The spec does not say which validation path line 1791 means, so validate --environment cannot satisfy both rules if compilation needs the model.

Evidence:

- L1791: | 新建与校验 | 唯一人物必须编译卡片，不再跳过缺故事或卡片的情况 |
- L1848: 失败不写清单和工作区，纯校验不启动模型请求或为此加载完整引擎。
- L1981: | 上下文 | 低于下限、元数据缺失、实际不足均无运行资产；合格纯校验不调用模型 |
- L1931: 画像命令从 `--persona` 指向文件的同一环境根读取 `config.toml` 的模型身份和重试配置，
- L1822: | 重试预算 | `transport_max_retries`、`max_react_turns`、画像抽取和就寝反思重试预算由配置提供 |

Fix: State whether card compilation calls the model. If it does, say which commands compile the card (e.g. new-run creation and profile compile only) and that pure validate checks the persona without compiling.

## Low-severity issues

### 16. The header and records intro name only open-questions.md answers as sources, but the body settles unanswered follow-ups from later conversation

**Classification: bookkeeping**. The mismatch is about provenance and freeze status only. L4 and L7-8 list open-questions.md answers as the source and say unanswered follow-ups stay open. L2786 adds later explicit conversational corrections, and the L2792-2797 table states both the decided boundary and the open detail for Q44, Q46 and Q49. The body rules (payment segment, payment_notification, initial location) are stated normatively wherever they apply, so an implementer builds the same thing. The only risk is someone misjudging which items are settled when they ask the owner.

*low · plausible · summary-mismatch* · lines 4, 7, 8, 2004, 2786, 2792, 2797

The status block says the spec incorporates the answered entries in open-questions.md and that unanswered follow-ups stay open (4, 7-8). The final table nonetheless records decided boundaries for unanswered Part 7 questions (Q44, Q46, Q49(a)) 'from later replies'. Line 2786 adds later conversational corrections as a source, which the header never mentions. The records section says it was revised per open-questions.md answers (2004), yet it contains payment-segment and payment_notification rules that come from later corrections.

Evidence:

- L4: 已纳入 `open-questions.md` 中已填写的回答；尚未回答的追加问题继续保留待确认，未冻结。
- L2786: 后续对话的明确修正写入所属正文；
- L2004: 本节按 `open-questions.md` 的已答复结论修订事件、五类导出工件、质量报告、评估与只读查看工具。
- L2797: 初始位置不要求为住处，人设声明的地点名称必须实际存在；营业含每周和特殊日期；提醒不能唤醒；天气由环境提供

Fix: Add 'later explicit conversational corrections' to the header's sources and to 2004. Reword line 4 so it says unanswered follow-up details stay open unless a later correction settled them.

### 17. callbacks.jsonl ordering and row shape are delegated to 「回调规则表」, which defines only rule_id ordering

**Classification: drift**. Loader A requires all state rows (sorted by state) before all rule rows, with `trigger: {class, key}` and `effect: {type: ...}`, and rejects interleaved files as KIND_INVALID. Loader B accepts any interleaving with each kind sorted on its own, and uses e.g. `trigger: {class: action, action: drive}` and `effect: {state_delta: {...}}`, so the same dataset file validates on only one of them.

*low · plausible · undefined-reference* · lines 109, 111, 1502, 1511, 1512, 1523, 2558

The package table delegates the shape, identity and ordering of callbacks.jsonl to 「回调规则表」 (109), and the handbook says the exact field shapes are there too (2558). Unsorted identity lists are rejected (111). The callback section orders only rule rows (by rule_id, 1511). It never says whether state rows are sorted by `state`, or whether state rows and rule rows may interleave in the single file or must come states-first. No discriminator field or trigger-key/effect-type field names are given beyond `trigger.class`, although the two row kinds are structurally distinguishable. Loaders could reject different files.

Evidence:

- L109: | `callbacks.jsonl` | 状态声明与回调规则 | 形状、身份与排序见「回调规则表」 |
- L111: 各身份列表与绑定列表必须无重复、按身份字符串升序排列。
- L1511: 状态登记与规则行分别使用严格模型和身份校验；状态与规则身份各自唯一，规则仍按 `rule_id` 升序。
- L2558: 字段的准确形状与登记码只链接「回调规则表」

Fix: In 「回调规则表」, state the state-row order, how state rows are placed relative to rule rows (e.g. all states sorted by state, then rules sorted by rule_id), how the row kinds are distinguished, and the field names for the trigger key and effect type.

### 18. Handbook error-code guide rows are narrower than the codes they describe

**Classification: wording**. The handbook table (L2645-2655) is an author-facing checklist, not where codes are defined. The normative rules are explicit elsewhere: unknown goods references such as reset_on_goods give REF_UNKNOWN (L1507, L1672); collisions between display names that must be unique give ID_DUPLICATE (L145, L233-234); unsorted identity or binding lists give KIND_INVALID (L111-112, L141). No implementer would emit a different code. Widening the handbook rows is purely editorial.

*low · plausible · summary-mismatch* · lines 112, 145, 234, 1507, 1672, 2647, 2648

The handbook's REF_UNKNOWN cause covers only unregistered app references, but the callback table also uses REF_UNKNOWN for unknown goods such as reset_on_goods (1507, 1672). The ID_DUPLICATE row omits display-name collisions, for example same-name branches without branch_label (145, 234). The KIND_INVALID row omits unsorted identity and binding lists (112). An author following the guide would look in the wrong place.

Evidence:

- L2648: | `ENVIRONMENT_ID_DUPLICATE`、`ENVIRONMENT_REF_UNKNOWN` | 重复身份、重复绑定，或未登记的 app 引用 |
- L1672: | app、商品等目录引用不存在 | `ENVIRONMENT_REF_UNKNOWN` |

Fix: Widen the handbook rows. REF_UNKNOWN should cover any unregistered app or goods reference, ID_DUPLICATE should include display-name collisions, and KIND_INVALID should include unsorted lists.

### 19. Error code for a wrongly typed integer or version field is ambiguous between KIND_INVALID and NUMBER_INVALID

**Classification: drift**. Test A asserts that a manifest with `"rules_version": "3"` or a missing rules_version is rejected with ENVIRONMENT_KIND_INVALID, per the generic 'wrong type' row at L141. Test B, extending the numeric-field pattern at L142/L641/L1673, asserts ENVIRONMENT_NUMBER_INVALID for the same manifest, so one of the two implementations fails the other's test.

*low · plausible · ambiguity* · lines 141, 142, 150, 157, 407, 641, 784, 1673, 2649

The generic table assigns every 'wrong type' (错误类型) to ENVIRONMENT_KIND_INVALID (141). It gives NUMBER_INVALID to out-of-domain numbers and to booleans in strict integers (142). The content, callback and handbook tables send numeric type errors such as 'not a strict integer' to NUMBER_INVALID (641, 1673, 2649). Line 157 says a missing, string or boolean rules_version is rejected 'by the model' without naming a code. It also maps an old integer format_version to KIND_INVALID, although 142 could read that as an out-of-domain number. The exact-code tests (784) depend on a precedence the spec never states.

Evidence:

- L141: | `ENVIRONMENT_KIND_INVALID` | 未知字段、非法闭集、错误类型或排序；
- L142: | `ENVIRONMENT_NUMBER_INVALID` | 数字超出允许域；严格整数不接受布尔 |
- L157: 缺失、字符串或布尔版本由模型拒绝；规则版本相符而目录格式为旧值时，报 `ENVIRONMENT_KIND_INVALID`。
- L641: | `ENVIRONMENT_NUMBER_INVALID` | 时长缺失、不是严格整数或低于下界，或系列集号非法 |
- L1673: | 数字类型或范围不符，包括零增量、无效阈值、非正金额、非法初值或上限 | `ENVIRONMENT_NUMBER_INVALID` |

Fix: In the generic table, state that type, missing and boolean errors on integer fields give NUMBER_INVALID and other type errors give KIND_INVALID. Name the code for a malformed manifest rules_version/format_version at line 157.

### 20. ENVIRONMENT_IDENTITY_MISMATCH is reused for a manifest write failure outside its tabled condition

**Classification: wording**. L191-192 explicitly say that a refresh_manifest_identity write failure keeps the old manifest and reports ENVIRONMENT_IDENTITY_MISMATCH, and L207 lists atomic refresh failure among the acceptance tests. Every implementer emits that code there. The registration table row at L144 is simply an incomplete summary of the code's conditions, and it gives no English reason that would conflict. Adding the refresh case to the row, or choosing a dedicated code, is an editorial or design-taste fix, not a divergence.

*low · plausible · contradiction* · lines 136, 144, 191, 192

The registration-code table, maintained in one place, defines ENVIRONMENT_IDENTITY_MISMATCH as a file-boundary, size, digest or catalog-identity mismatch (144). The refresh section uses the same code for an atomic write failure in refresh_manifest_identity (192). That is an I/O failure, not an identity mismatch, and the table does not cover it.

Evidence:

- L144: | `ENVIRONMENT_IDENTITY_MISMATCH` | 文件边界、大小、摘要或目录身份不符 |
- L192: 写失败保留旧清单并报 `ENVIRONMENT_IDENTITY_MISMATCH`；不把人设或配置塞进目录清单。

Fix: Add 'manifest refresh write failure' to the ENVIRONMENT_IDENTITY_MISMATCH row and its English reason, or give refresh write failures their own code.

### 21. The five-field environment identity is called catalog-scoped, but its map_sha256 field depends on the config.toml seed

**Classification: wording**. The build is fully pinned. L163-165 list map_sha256 as one of the five identity fields and say it is computed from all locations plus the config.toml seed. L166 says a seed-only change changes the map identity but not the catalog bytes. L207 makes 'identity change when only the seed changes' an acceptance case. L1749 and L1911 mean only that the persona/config file bytes and hashes are not folded into environment_identity, which stays true. Nobody would drop map_sha256 from the identity or stop it depending on the seed. Only the 'catalog scope' description is loose.

*low · confirmed · contradiction* · lines 163, 164, 166, 207, 1748, 1749, 1753, 1910, 1911

Lines 163-166 keep map_sha256 as one of the five environment-identity fields. The map is generated from all locations plus the config.toml seed, so changing only the seed changes map_sha256 (166). Line 1911 says the five-field identity 'stays within catalog scope and does not secretly absorb persona/config'. Line 1749 says the two TOML files do not enter environment_identity, which is literally true for their bytes but not for the seed. The behaviour is unambiguous; only the description of the identity's scope is wrong. Any cross-run comparison of environment identity will see a seed-only change.

Evidence:

- L163: 环境身份仍为 `environment_id`、`environment_version`、`rules_version`、`catalog_content_sha256`、
- L164: `map_sha256`。地图在加载时由全量地点和 `config.toml` 的单一种子生成，规范序列化后计算
- L166: 单独改变配置种子可以改变地图身份，不改变目录字节身份；恢复仍须核对配置文件身份。
- L1749: 或五字段 `environment_identity`；放入 `catalog/` 按清单外文件拒绝。
- L1911: | 环境 | 既有五字段身份保持目录范围，不暗中并入人设配置 |

Fix: Either move map_sha256 out of the five-field environment identity into the run-input identity, or reword 1749/1911 to say the environment identity includes the seed-derived map digest (though not the config file bytes).

### 22. The version table puts map generation and the map fingerprint in 「地点」, but map_sha256 and the generate-map removal are specified elsewhere

**Classification: wording**. This is a wrong section pointer in the summary table (L2326). The rules themselves are complete and consistent where they actually live: generation in 地点/内存地图 (L279-283), map_sha256 and the generate-map/--check removal in 包与目录格式 (L163-169), and freezing in 人设与运行配置 (L1752-1753, L1910). An implementer builds the same thing whichever section owns the text.

*low · plausible · summary-mismatch* · lines 164, 168, 282, 1753, 1910, 2326

The version table says map generation and the fingerprint belong to 「地点」 (2326). The 地点 section's 内存地图 subsection holds only the generation algorithm and a determinism statement (282). The map_sha256 definition, its storage and its role in the environment identity are in 包与目录格式 (163-166), with freezing in 人设与运行配置 (1753, 1910). The removal of generate-map and --check is also in 包与目录格式 (168).

Evidence:

- L2326: 生成与指纹归「地点」，删除发布地图校验与生成命令
- L164: `map_sha256`。地图在加载时由全量地点和 `config.toml` 的单一种子生成，规范序列化后计算
- L168: 删除 `worldmock environment generate-map`、`--check`、独立地图种子参数、发布地图结构号及
- L282: 相同地点集合与种子得到相同规范图和摘要

Fix: Change 2326 to say generation belongs to 「地点」 and map_sha256 plus the removal of the published map and command belong to 「包与目录格式」. Alternatively, move the map_sha256 rule into 内存地图.

### 23. validate output requires a code for every error, but persona/config failures have no codes

**Classification: drift**. One implementation raises ValueError or IntegrityError for persona/config failures, so validate reports code "ValueError" or "IntegrityError". Another adds coded errors such as PERSONA_INVALID or RUN_CONFIG_INVALID, so validate output and the tests asserting on it differ.

*low · confirmed · undefined-reference* · lines 187, 188, 1937, 1939, 1959, 2454

The package section defers the 'types and reasons' of persona/config failures to 「人设与运行配置」 (188). That section's rejection table (1939-1959) gives only conditions and English reasons. Only package-closure and registration errors reuse package codes (1937). The handbook says validate output carries path, code and reason for every error (2454). The `code` for failures such as a BOM in persona.toml, a short background_story or a context window that is too small is therefore undefined.

Evidence:

- L188: 人设与配置失败的类型和原因见「人设与运行配置」。
- L1937: 所有异常先记英文错误日志，包闭集和登记错误复用包格式的码。
- L2454: 失败输出继续包含 `valid: false` 及 `errors` 的 `path`、`code`、`reason`
- L187: 目录失败形状继续为 `{path, code, reason}`

Fix: Give each persona/config condition in the 1939-1959 table a code, or a fixed code per condition. Alternatively, state that these validate entries carry a fixed code (or null) plus the listed English reason.

### 24. Visible-name collisions are assigned to three different failure mechanisms

**Classification: wording**. Each collision pair has one outcome, set by the load order. Catalog registration runs before world creation (load flow L174-183), so location-vs-location gets ENVIRONMENT_ID_DUPLICATE (L233-234) and app-vs-location gets APP_NAME_COLLIDES_LOCATION (L311, L328). The retained per-agent IntegrityError label check (L888-890) therefore only ever catches the remaining pairs, goods-vs-location and goods-vs-app. That check exists in today's code (life/load_checks.py::check_visible_labels), and 'failure stays' keeps it as is. Handbook row L2653 is a loose author hint, not a code mapping. Two competent implementers would produce the same codes.

*low · plausible · ambiguity* · lines 187, 233, 234, 328, 888, 889, 890, 2653

Location-vs-location display-name collisions are rejected at registration with ENVIRONMENT_ID_DUPLICATE (233-234), and app-vs-location collisions with APP_NAME_COLLIDES_LOCATION (328). Lines 888-889 add a load-time check across location display names, goods names and content-app names. That check raises an uncoded IntegrityError, it overlaps the first two cases, and it is the only rule for goods-vs-location and goods-vs-app collisions. validate must report catalog failures as {path, code, reason} (187), and the handbook points every visible-name conflict to APP_NAME_COLLIDES_LOCATION (2653). No order between these checks is stated.

Evidence:

- L233: 登记时比较的是经 `channel_key` 处理的显示名，碰撞报
- L234: `ENVIRONMENT_ID_DUPLICATE`。
- L328: app 名与地点显示名碰撞继续报 `APP_NAME_COLLIDES_LOCATION`。
- L888: 可见名称唯一性继续在加载期检查地点显示名、商品名、内容 app 名，失败仍为
- L889: `IntegrityError("Agent <id> would see non-unique object labels: …")`。

Fix: Give each collision pair one owner and one code, including goods-vs-location and goods-vs-app. Alternatively, state that the IntegrityError check runs only after the registration codes pass. Align handbook row 2653.

### 25. Orders still keep an 'address', although generated addresses have been deleted

**Classification: not-an-issue** (withdrawn). Both judges found that the 地址 kept on orders is the existing `delivery_address_id` location field, not the deleted generated address.

*low · confirmed · stale-reference* · lines 61, 216, 515, 1877, 2470, 2606

Address generation, instance addresses and the address: random key are deleted, and location rows have no address field (61, 216, 1877, 2470, 2606). Line 515 still lists 地址 (address) as an order field kept alongside the destination. Nothing in the spec defines a source or meaning for it.

Evidence:

- L515: 订单保留履约、实际 app、支付 app、目的地、地址、费用与预期时刻，恢复时读取已提交订单，不重新选择。
- L216: 没有 `count`，不生成实例身份、虚构地址或地址随机源。
- L1877: 删除地址生成，因此无 `address:`；

Fix: Remove 地址 from line 515, or define it, for example as the destination's display name.

### 26. The location deletion surface removes a '重复显示名守卫' while the body keeps display-name uniqueness checks

**Classification: wording**. L233-234 (registration, ENVIRONMENT_ID_DUPLICATE) and L888-890 (load-time IntegrityError, explicitly 'continues') both state the retained checks normatively. The deleted guard at L292 can only be the redundant selected-locations check _check_place_names in life/load_checks.py. It sits next to check_location_kinds in the same deletion list, and the full-catalog registration check now subsumes it. Keeping or deleting that helper makes no observable difference, because registration rejects the same collision first. The fix is purely editorial: name the helper.

*low · plausible · ambiguity* · lines 233, 255, 292, 299, 888

The location deletion list removes the '重复显示名守卫' (duplicate display-name guard) (292). The body requires display-name collisions to be rejected both at registration (ENVIRONMENT_ID_DUPLICATE, 233) and at load (IntegrityError, 888), and acceptance still covers same-name branches (299). Unless the phrase means some specific redundant guard, an implementer could delete a check the body requires.

Evidence:

- L292: `check_location_kinds`、重复显示名守卫及没有调用者的地点辅助代码。
- L233: 同名地点必须用不同 `branch_label` 区分；登记时比较的是经 `channel_key` 处理的显示名，碰撞报
- L888: 可见名称唯一性继续在加载期检查地点显示名、商品名、内容 app 名，失败仍为

Fix: Name the specific redundant guard being deleted (function or module), and state that the uniqueness checks at 233-234 and 888-889 remain.

### 27. An unregistered or ambiguous start or work location must be rejected at load, but has no code or English reason

**Classification: drift**. One implementation reuses HOME_LOCATION_REQUIRED or ENVIRONMENT_REF_UNKNOWN for an unresolvable start/work location. Another raises an uncoded IntegrityError or 'Persona file validation failed'. The validate output and the L301 acceptance assertions then differ.

*low · plausible · undefined-reference* · lines 188, 243, 244, 246, 301, 1764, 1771, 1943

Lines 246, 1764 and 1771 require load to reject a persona start or work location that is unregistered or ambiguous. Only the home failure has a code (HOME_LOCATION_REQUIRED, 244). Line 188 promises that persona failure reasons are listed in 人设与运行配置, but its reason table has no row for this case. 'Persona file validation failed' covers parse or model failures only.

Evidence:

- L244: 两种失败均报 `HOME_LOCATION_REQUIRED`。
- L246: 环境加载时必须核对这些名称在地点目录中实际存在，并解析为唯一地点身份；不自动改成住处或补建地点。
- L1771: 加载时拒绝未登记或有歧义的名称。
- L1943: | 人设解析或模型失败 | `Persona file validation failed` |

Fix: Define a code or English reason for an unresolvable start or work location (or generalise HOME_LOCATION_REQUIRED), and add the work location to the acceptance cases at 301.

### 28. An unregistered destination is reported as DESTINATION_NOT_FOUND in one place and NAME_NOT_FOUND in another

**Classification: wording**. The request-level outcome is fixed. Name resolution always runs first (L848), travel accepts only location targets (L868), and an unknown non-purchase name yields NAME_NOT_FOUND with a fixed correction contract (flowchart L853-866, table L878). DESTINATION_NOT_FOUND 'still' exists (L253, L994) as the existing internal route-planning BusinessRuleError (src/worldmock/life/travel.py L59, basket.py L96), and the spec keeps it unchanged. A model-written unknown destination gets NAME_NOT_FOUND in any faithful implementation. The guard needs only a note that it is internal.

*low · plausible · ambiguity* · lines 253, 848, 868, 878, 932, 994, 1059, 1062

The 地点 section (253) and the travel table (994) say an unregistered destination reports DESTINATION_NOT_FOUND. Name resolution runs first in both host and environment (848, 932), travel accepts only location targets (868), and an unknown non-purchase name yields NAME_NOT_FOUND (878). DESTINATION_NOT_FOUND is not in the host or environment name-error sets (1059-1062). A request therefore cannot reach DESTINATION_NOT_FOUND, yet nothing marks it as an internal guard.

Evidence:

- L253: 所有匹配商品在全量地点展开；未登记目的地仍报 `DESTINATION_NOT_FOUND`。
- L878: | 名称未找到且不是购买 | `NAME_NOT_FOUND`；路径 `items[i].target`；
- L994: 目的地未知 `DESTINATION_NOT_FOUND`
- L1062: 宿主名称集合为名称未找到、名称歧义、动作未提供、关系对象未知

Fix: State that unknown destination names are rejected as NAME_NOT_FOUND during name resolution, and either mark DESTINATION_NOT_FOUND as an internal route-plan guard or delete it.

### 29. The name book still covers 'unshown locations', though every other location is now always listed

**Classification: wording**. L254 and L899 list every other location with no rotation or truncation, the map must be connected (L281), and the current location has its own row (L900). The set of 'unshown locations' in L869 is therefore always empty. Keeping or dropping the clause gives identical behaviour; it is a stale leftover of the old rotating window. L882 likewise rules out any special channel for unshown objects.

*low · confirmed · stale-reference* · lines 254, 281, 869, 899, 900

The tool layer now lists every location other than the current one, with no rotation or truncation (254, 899), and the map must be connected (281). The current location has its own row (900). Line 869 still builds the name book from '未展示的地点' (unshown locations) and gives them travel actions, which is a leftover of the deleted rotating destination window.

Evidence:

- L254: 工具层列当前地点以外的全部地点，不轮转或截断。
- L869: 名称簿包含本轮绑定行、未展示的地点和未展示的内容 app；未展示地点提供出行，未展示内容 app 提供 `interact`。
- L899: | 目的地 | 所有可达的其他地点，依次提供四个出行动作；关闭地点仍允许前往 |

Fix: Drop '未展示的地点' from line 869 and keep only the unshown-content-app clause.

### 30. A pointer to 「地点」的待确认项 leads to a section with no open-item list

**Classification: bookkeeping**. This is a wrong section pointer, not a disagreement about status. The open detail sits in 地点's 营业时段 subsection: the exact opens JSON structure and whether special dates can declare shortened hours are still unconfirmed (L227, L271, L301). The 动作 待确认项 (L1097) and the final table (L2797) agree that this is open, under Q49. The implemented rules (defaults, closed weekdays, LOCATION_CLOSED, travel to a closed place allowed) are clear at L269-274. The fix is retargeting the L918 pointer.

*low · plausible · open-item-mismatch* · lines 227, 271, 301, 918, 1097

Line 918 sends readers to the 地点 section's 待确认项 for the exact opens structure, and 1097 says opening hours stay open 'in its own section'. The 地点 section (211-301) has no 待确认项 subsection, unlike 内容目录, 动作, 回调 and 人设. The open detail appears only inline (227, 271, 301).

Evidence:

- L918: 营业时间在工具层可见，精确 `opens` 结构见「地点」的待确认项。
- L271: 确切 JSON 结构以及特殊日期是否还能声明缩短后的营业时段尚未确认
- L301: 营业时段细节待确认。

Fix: Add a 待确认项 subsection to 地点 listing the opens structure, special-date hours (Q49b) and the other money accounts (Q47). Alternatively, point 918 to 「地点」的营业时段 or to the final table.

### 31. The goods section still refers to a 'location menu' after menus were deleted

**Classification: wording**. L410 '不进入地点菜单' sits right after '地点侧商品展开到全量匹配地点', and its intended meaning is plain: app-side goods are not expanded onto locations or listed as location goods rows. L901 and L2525 say the same thing ('app 侧商品…不单独铺到工具层'), and L1246-1247 delete menus outright. Nobody would rebuild a menu from this phrase, so it is a stale term only.

*low · confirmed · stale-reference* · lines 229, 237, 291, 410, 1246, 1247

The spec deletes Catalog.menus_by_kind and the location menu field (229, 237, 291), and says that goods discovery and the tool layer no longer go through menus (1247). Line 410 still defines app-side goods as 'not entering the location menu' (不进入地点菜单).

Evidence:

- L410: 地点侧商品展开到全量匹配地点，按 `goods_id` 排序；app 侧商品不进入地点菜单。
- L291: `GOODS_CATEGORY_STORE_KINDS`、`Catalog.menus_by_kind`、`Catalog.location_kinds`、
- L1247: 受理、工具层、商品发现、可调用索引与宿主均不再经过菜单；

Fix: Replace '地点菜单' with the current concept, for example '地点侧商品展开' or '工具层地点商品行'.

### 32. A threshold reminder's display app may be a function app, whose name would then reach the persona

**Classification: drift**. One implementation accepts any registered app as the display app and renders it in the progress-layer reminder (like the content-push record {app,title,summary}), so a fuel reminder under 高德地图 leaks a function-app name. Another rejects non-content display apps with APP_ROLE_BINDING_INVALID, or omits the app name from the persona projection, so the same dataset fails to load or shows no app.

*low · plausible · contradiction* · lines 316, 372, 1622, 1628, 1672, 2030, 2625, 2631, 2697

A threshold reminder declares a display app (1622, 2030). Validation only checks that the referenced app exists (1672), and the role rule at 316 does not cover this reference, so a function app can be named. Function-app names must never reach the persona (372-374, 2631, 2697). The spec does not say whether the display app is projected to the persona.

Evidence:

- L1622: `notify` 带 `when.state`、`when.below`、`channel: app_push`，并声明提醒文字和显示 app
- L2030: 使用独立提醒载荷，规则给出文字与所属 app，不伪造 `content_id`
- L1672: | app、商品等目录引用不存在 | `ENVIRONMENT_REF_UNKNOWN` |
- L2697: 不出现余额、内部身份、哈希、身体数值、功能 app 或订单代选渠道

Fix: Either require the reminder display app to have role content (rejecting otherwise, e.g. with APP_ROLE_BINDING_INVALID), or state that the display app is not projected to the persona.

### 33. The handbook's open list omits the courier-fee merge question and the social persona parameters

**Classification: bookkeeping**. The rules an implementer builds are stated clearly as open: L456, L1884 and L2802-2804 forbid inventing a fee probability or merge rule, and L1802-1806 and L2542 keep the persona parameters pending. The only problem is that the handbook's pending list (L2711-2713) leaves these out, and L2495 states the decided stable-policy part without flagging the open merge question. That affects how open items are tracked, not what anyone would build.

*low · confirmed · open-item-mismatch* · lines 456, 1805, 1884, 2495, 2542, 2711, 2712, 2793, 2802, 2803, 2804

The final 待确认事项 section keeps open the 0-vs-500 courier-fee probability and how differing fee policies combine in a merged order (2802-2804; also 456). It also keeps open where the five social persona parameters go (2793). The handbook body itself says the persona parameters are pending (2542). The handbook's list of items it must not fix yet (2711-2713) names neither, while the handbook's goods table states the fee rule without noting the open merge question (2495).

Evidence:

- L456: 运费两种结果的概率和同一合单中不同商品运费策略如何合并，回答尚未给出，见「待确认事项」。
- L2495: 费用按商品和下单 app 稳定抽为免运费或 500 分
- L2711: 手册暂不写定统一心理量的形式与数值、删除内容分类后的未决行为、内容心理效果档位、
- L2542: 具体档位和画像参数待确认
- L2793: 心理效果档位字段与数值、五个人设社交参数的具体去向

Fix: Add the courier-fee probability and merge rule and the social persona parameters to the handbook pending list at 2711-2713, and note the open merge rule beside 2495.

### 34. An in-store purchase grouped with travel has two possible rejections: the presence-with-travel message or slot RESOURCE_CONFLICT

**Classification: drift**. Implementation A runs the retained presence check first and returns 'X requires presence at P and cannot start with travel.' with next step 'Go to P.'. Implementation B runs the slot check first (per L468/L950/L915) and returns RESOURCE_CONFLICT naming the travel item as the slot occupant, so tests written to either expectation fail against the other.

*low · plausible · ambiguity* · lines 461, 468, 915, 932, 939, 945, 946, 950, 964, 965

For a place-bound item (in-store purchase, location interact, sleep) submitted with travel, the goods failure table gives RESOURCE_CONFLICT naming the slot occupant (468). Line 950 says travel/purchase conflicts are handled uniformly by the slots. The admission rules keep a separate presence rejection, '<label> requires presence at <place> and cannot start with travel.', for place-bound items grouped with travel (945-946). Lines 915 and 932 order the checks differently. The persona-visible code and text are therefore undetermined, although both outcomes reject the group atomically.

Evidence:

- L468: | 购买与已有或同组的地点互动、出行、另一购买活动冲突 | `RESOURCE_CONFLICT`，说明地点／出行／购买名额的占用者 |
- L946: 与出行同组为 "<label> requires presence at <place> and cannot start with travel."，下一步 "Go to <place>."。
- L950: 删除为瞬时远程下单设置的手动操作标记和驾车专用拒绝分支；出行与购买的冲突统一由活动名额处理。

Fix: State which check governs a place-bound item grouped with travel, and align 468/950 or 945-946 with that choice.

### 35. The timed purchase activity has no defined activity name, yet control tools and rejection messages need one

**Classification: drift**. One implementer names it 「购买（M月D日 HH:MM 起）」. Another names it after the goods or store, e.g. 「购买咖啡（…起）」 or 「在<店>购买（…）」. The control-tool names, rejection texts and title tests then differ.

*low · plausible · undefined-reference* · lines 468, 752, 843, 895, 903, 939, 948, 999

Purchase is now a real 1-5 minute activity that occupies the location/travel/purchase slot (939). Activity-name formats are defined only for interact (752), travel and sleep (999). Control tools address activities by activity_name (843), activity rows are listed by name (903), the departure rejection interpolates current activity names (948), and RESOURCE_CONFLICT must name the slot occupant (468). No purchase name format is defined, and the Q46 open items do not list it.

Evidence:

- L752: `interact` 活动名称为「进行<app 名或地点显示名>（M月D日 HH:MM 起）」，开始时确定，
- L999: 出行活动名为「<方式>前往<地点>（M月D日 HH:MM 起）」；睡眠活动名为「睡觉（M月D日 HH:MM 起）」。
- L939: 同请求购买项组成一个随机 1–5 分钟的购买活动，占地点／出行／购买名额
- L948: "Conclude or pause the current activities <names> before departure."。

Fix: Define the purchase activity name next to line 999 (for example 「购买（M月D日 HH:MM 起）」), or add it to the Q46 open items.

### 36. The order merge key has three fields in most places but four in the random-key definition

**Classification: wording**. Within one request the destination follows from the fulfilment and the current position: the store for pickup, the current location for delivery, the nearest station to home for courier (L444-446). So grouping by store+app+fulfilment (L480, L1879, L2500) gives the same partition as the 4-field key. L1882 is the only exact definition of the group_key serialization used in random keys (L1864-1865, L493, L497), so implementers would serialize all four fields from it. The difference is editorial; at most it makes ORDER_LOCATION_MISMATCH a defensive check.

*low · confirmed · contradiction* · lines 448, 480, 485, 939, 1879, 1882, 2109, 2500

The goods section, dispatch table, randomness text, records and handbook merge items by store + app + fulfilment, and treat a matching destination as a constraint on the merged order (ORDER_LOCATION_MISMATCH, 480/485). Line 1882 defines the merge key itself as [store_id, destination_id, app_id, fulfillment]. The destination normally follows from the other fields, so the practical impact is small, but the key definitions differ.

Evidence:

- L480: 逐件派生店铺、下单 app 和履约，再把相同店铺、app、履约的项合成一单；同单目的地必须一致。
- L1882: 购买的 `group_key` 是合单键 `[store_id, destination_id, app_id, fulfillment]` 的规范 JSON，

Fix: Use one key definition everywhere. Either drop destination_id from 1882, or add it to the merge key elsewhere and remove the mismatch constraint.

### 37. The handbook says chat exertion is 'the same as work', but the body never fixes a tier for work content

**Classification: wording**. The intended meaning is that chat content uses exertion light and goes through the same content mechanism as work. L745 and L1200 say chat and work share one consumption mechanism, and L2542 says 与工作使用同一内容机制. L569 and L608 fix only chat at light, and L585 and L698 leave every other content's tier to the author. The validator enforces no tier for work, and the normative field table is unambiguous. Still, the handbook phrasing could nudge an author toward marking work light, so the fix is to reword 2535.

*low · plausible · summary-mismatch* · lines 569, 585, 608, 698, 2535, 2543

The body fixes only chat-topic content at `light` (569, 608). Every other content, work included, takes whatever exertion tier the dataset author chooses (585, 698). The handbook phrase 与工作相同 (same as work) implies a rule that work is light, which the normative body never states.

Evidence:

- L2535: 聊天话题为轻度费力，与工作相同。
- L608: 聊天话题内容取 `light`；数据集不填任意代谢数值。
- L698: 一个容器允许绑定不同档位的内容，数据集作者负责绑定语义合理。

Fix: Remove 与工作相同 from 2535, or add an explicit body rule about the exertion of work content.

### 38. The final open-item table does not list all the psychological-effect details the body leaves open

**Classification: bookkeeping**. The body clearly leaves these open: L620 (field, tier names, values, which need is affected) and L797 (content effects on the unified quantity). It also clearly decides that chat relieves social_need by duration and tier (L747, L1200-1201, L1802). An implementer would build the decided parts and not invent the open ones. The mismatch is only that final-table rows L2791-2792 under-list the open details and do not separate the decided chat/social_need part.

*low · plausible · open-item-mismatch* · lines 620, 747, 797, 1201, 1802, 2791, 2792

The content section leaves open the tier field, tier names, values and which psychological need a tier affects (620), and its own open list puts content effects on the unified quantity under Q43 (797). The final table's Q43 row lists only drink and goods amounts (2791), and its Q44 row lists only 'tier field and values' (2792). Line 620's 'which need' also sits beside the decided rule that chat relieves social_need (747, 1201, 1802).

Evidence:

- L620: 具体字段、档名、数值和影响哪项心理需要仍待确认。
- L797: 统一心理量的名称、范围、随时间变化、商品与内容效果、体感标签待确认
- L2791: 名称、范围、增长或衰减、饮料与商品的量值、原有脉冲和感受标签
- L2792: 心理效果档位字段与数值、查询与展示

Fix: Align rows 2791/2792 with 620/797. Add content effects on the unified quantity, tier names and which need non-chat tiers affect, and state that chat relieving social_need is decided.

### 39. Platform browse time is open in the body but missing from the section and handbook open-item lists

**Classification: bookkeeping**. Every place that discusses it agrees that browse-time settlement for a catalog-only platform is undecided: L667, L942 and L2806-2807. Those lines also fix the constraints: no rejection for an empty index, no fabricated content, no zero-second browse. L681 ('不伪造…浏览时长') matches those constraints. The only gap is the missing entry in the content open list (L793-800), the actions open list (L1091-1098), the handbook list (L2711-2713) and the final table.

*low · confirmed · open-item-mismatch* · lines 667, 681, 793, 795, 942, 1091, 1096, 2709, 2711, 2806, 2807

Several passages mark the time settlement for browsing a catalog-only platform as open: the content flowchart (667), the dispatch table (942), and a trailing paragraph after the final table (2806-2807). It is missing from the content section's 待确认项 (793-800), the actions section's 待确认项 (1091-1098) and the handbook's pending list (2709-2714), and it is not a row of the final table. Line 681 ('不伪造…浏览时长') is consistent with 2807 but could be misread as a decision.

Evidence:

- L667: 浏览如何结算时间待确认
- L942: 只有商品目录的平台走目录展示路径，不要求虚构内容，其浏览时长仍待确认
- L795: 删除类别后，心理效果档位的字段与数值、查询品类尚未确定；
- L1096: 删除内容类别后的查询品类仍待确认；并行按两个固定活动名额处理
- L2807: 这种浏览如何结算时间尚未说明，不能因显式内容索引为空而拒绝，也不能临时伪造内容或赋予零秒浏览。

Fix: Add platform browse-time settlement to the content and actions 待确认项, to the handbook pending list and as a row in the final table. Optionally reword 681 so it does not read as a decision.

### 40. A content-selection test still expects a post-selection conflict that the slot model removes

**Classification: wording**. The normative rule is unambiguous: the slot comes from the action and the target type alone, with no content draw first (L700, L916). The flowchart and admission order check slots before selection (L663-664, L932-933), and conflicts reject the whole group atomically with no state or events (L664, L702). The test row phrase '选后冲突整组原子拒绝' (L786) is a leftover from the category-slot design. Its intent, atomic group rejection that leaves no selection or draw, is clear from those lines, so no engine behaviour would diverge. Rewording the test row is editorial.

*low · plausible · stale-reference* · lines 663, 664, 700, 786, 916, 932, 933

Under the two-fixed-slot model, slot use is decided by the action and the target type alone (700, 916). The flowchart and admission order check slots before any content is selected (663-664, 932-933). The content-selection test row still expects '选后冲突' (a conflict after selection), which belongs to the earlier slot-by-category design.

Evidence:

- L786: 选后冲突整组原子拒绝
- L700: 名额直接由动作和解析后的 target 类型决定，不需要先抽取内容或读取额外使用方式。
- L916: `interact` 的并行资格直接由 target 类型和对应名额决定；工具层与实际受理同源，不需要预演内容抽取。

Fix: Reword the test row to say slot conflicts are rejected atomically before content selection, leaving no selection, draw or event.

### 41. Chat progress is tied to a fixed 'every thirty minutes', but the report interval is configurable

**Classification: wording**. The mechanism is clear: report opportunities follow progress_report_interval_seconds, which defaults to 1800 and must be positive (732-733). Chat progress is written at each report_status opportunity (746, 799: '模型在播报时编写聊天进展'). '每三十分钟' at 746/1039/1201/2542/2793 is the default interval written out in words. The table at 1026-1039 lists rules whose meaning goes into the system layer; it does not fix prompt text verbatim. With the default config the engine and tests behave the same under either reading. Changing the words to 'at each progress-report boundary' is an editorial fix.

*low · confirmed · contradiction* · lines 732, 746, 799, 1039, 1201, 1819, 2232, 2542, 2793

progress_report_interval_seconds is a config value that defaults to 1800 and only has to be positive (732, 1819, 2232). The chat passages, including the system-layer prompt rule (1039), the deleted-subsystems section (1201), the handbook (2542) and the open-item table (2793), hard-code '每三十分钟的 report_status'. Line 799 ties chat progress to the report boundary instead. With a non-default interval, the prompt would state the wrong cadence.

Evidence:

- L732: `config.toml` 中 `progress_report_interval_seconds` 缺省为 1800、必须为正值，
- L746: 在每三十分钟的 `report_status` 机会，模型根据话题编写这段时间聊了什么。
- L1039: 模型在每三十分钟的 `report_status` 编写聊天进展，没有对方回复
- L799: 聊天按话题消费内容，模型在播报时编写聊天进展

Fix: Replace 每三十分钟 with 'at each progress-report boundary (progress_report_interval_seconds, default 1800 s)', or fix the interval at 1800 and remove it from config.

### 42. Content-category labels are decided (not shown) in the body but listed as pending

**Classification: bookkeeping**. The display side is settled and stated normatively: app and location rows show no content category (906), no category is invented for a container (698), and the tool-layer test says '选前不虚构类别' (1085). Only the query side is genuinely open: the category request key, query_categories and counts (907, 922, 926; section lists 795 and 1096). '内容标签' at 926 and '展示' at 2792 make the open item look wider than it is. They affect what is asked of the owner, not what an implementer builds for row display.

*low · confirmed · open-item-mismatch* · lines 698, 770, 795, 906, 907, 922, 926, 1096, 2010, 2792

The body settles the display half of Q44(e). App and location rows show no content category (906, 698), ACTION_CATEGORY_LABELS is deleted (770), and the content category no longer exists (2010). Line 926 still lists the final handling of '内容标签' (content labels) as pending, and the final table lists '查询与展示' (query and display) as unconfirmed. Only the query side (the category request key and counts, 907/922) is genuinely open, which matches the section lists at 795 and 1096.

Evidence:

- L906: app 与地点行不显示内容类别或已删除的使用方式；推送标题、营业时间、只读时长范围仍可以展示。
- L926: 内容类别已经删除，`category` 请求键、品类计数和内容标签的最终处理待确认。
- L2792: 心理效果档位字段与数值、查询与展示

Fix: Narrow the Q44 open item to query handling (the category request key, query categories and counts), and drop 内容标签 from 926 and 展示 from 2792.

### 43. TOO_MANY_TARGETS is kept in the host error set but never given a trigger

**Classification: borderline**. The judges split: the implementer judge chose drift and the skeptic chose wording.

*low · plausible · undefined-reference* · lines 819, 820, 825, 1059

The only count rule the request contract defines is 1-2 items, enforced by the strict model and reported by the host as ARGUMENTS_INVALID (819-825). TOO_MANY_TARGETS is listed as a retained host code (1059) and appears nowhere else, so its trigger, and how it differs from ARGUMENTS_INVALID for a three-item request, are undefined.

Evidence:

- L819: `items` 为一至两项，整组原子受理。项数非法时拒绝为
- L825: 宿主报 `ARGUMENTS_INVALID`，环境报 `ACTION_REQUEST_INVALID`，路径指向非法字段。
- L1059: 宿主错误闭集保留 `ARGUMENTS_NOT_JSON`、`ARGUMENTS_INVALID`、`TOOL_CALL_REQUIRED`、`TOO_MANY_TARGETS`、

Fix: Give TOO_MANY_TARGETS a trigger, or remove it from the host closed set.

### 44. Name lookup scope is unclear: exact label match across all object types, or only the action's allowed types

**Classification: wording**. Line 870 gives the precise, ordered algorithm and says it carries over (沿用): an exact label match against all objects first, then lossless normalization and home aliases only within the action's allowed types. This is exactly what the current name_resolution.lookup does (lines 112-115). The table row at 877, 'The known object <label> does not support the written action', is written for the case where a known object of another type is named. The flowchart box at 857 is a simplified overview. So {walk, 抖音} gives ACTION_NOT_OFFERED with interact candidates. Aligning the flowchart label is editorial, though an implementer who trusts only the flowchart could slip.

*low · plausible · ambiguity* · lines 857, 870, 877, 878, 885

The flowchart looks names up only within the action's allowed target types (857), which matches NAME_NOT_FOUND's expected text 'in the selected action's category' (878). Line 870 can be read as an unrestricted exact-label match first, with only the normalization step restricted to allowed types. For {walk, 抖音}, the first reading gives NAME_NOT_FOUND at items[i].target, and the second gives ACTION_NOT_OFFERED at items[i].action (877). The two readings produce different codes, paths and correction candidates.

Evidence:

- L857: 按动作允许的目标类型查名称
- L870: `lookup` 先精确匹配对象标签，再在动作允许类型中沿用三级无损归一与住处写法。
- L877: 对象存在但不提供动作 | 同码同路径；允许值为该对象的动作
- L878: `expected = "A known object name in the selected action's category is required."`

Fix: State whether the exact-label step searches all object types or only the action's allowed types, and make the flowchart and line 870 agree.

### 45. No metabolic factor is defined for the timed purchase activity, though body settlement needs explicit factors

**Classification: drift**. Implementer A gives a lone 60-300 s purchase no factor, so the resting met=1.0 of the empty set applies (1144). Implementer B gives purchase its own MET, for example 1.5 like light exertion or standing, so energy and body-boundary predictions and settlement differ.

*low · plausible · undefined-reference* · lines 599, 601, 978, 980, 1142, 1143, 1144, 1145, 1146, 1308

Body prediction and settlement take explicit ActivityFactors (1142-1143). METs are fixed per travel mode (978-983) and per content exertion tier (599-608), and the wait MET of 1.3 is deleted (1145). Purchase is now a 60-300 s activity with a named duration source (1146), but no passage gives its MET or factors. The empty-set resting factor met=1.0 is described as not being a time-advancing activity (1144), so it is unclear whether it applies. The acceptance test that prediction matches settlement (1308) cannot be written for purchase.

Evidence:

- L1142: `next_body_boundary_seconds` 以显式 `ActivityFactors` 为输入，
- L1144: 空集合静息因子 `met=1.0`、缓解为零不构成一个可推进时间的隐藏活动。
- L1145: 删除等待代谢值 1.3，能量恢复豁免只保留睡眠
- L1146: 睡眠、路线、内容、购买各读自己已定义的时长来源，

Fix: State the purchase activity's ActivityFactors, either a fixed MET or explicitly 'contributes no factor; resting met=1.0 applies', next to the travel MET table.

### 46. Weather for the first, partial run day is undefined

**Classification: drift**. Implementation A draws the start date's weather at run start and writes a weather scheduled fact, so weather-subscribed callbacks fire on day 1. Implementation B derives the day-1 factor lazily from weather:{local_date} and writes no fact until the first local midnight, so no day-1 weather callback fires.

*low · plausible · ambiguity* · lines 986, 987, 988, 1577, 1841, 1862, 2567

Weather is drawn, and written as a scheduled fact, only at local midnight (986, 1577, 2567). Trip durations and the environment line need today's weather from the first decision (987-988), and runs start at whole hours such as 08:00 (1841). The spec never says how the start date's weather is produced, or whether a day-1 weather fact, and therefore a weather-subscribed callback, exists. The deterministic key weather:{local_date} (1862) allows lazy derivation of the factor but does not settle whether the fact is written.

Evidence:

- L986: 天气每日本地零点等概率取晴 1.0、多云 1.0、
- L1577: 天气本地零点生成每日预排事实，回调订阅事实，不使用状态阈值。
- L988: 环境层给出一行真实天气，例如「今天天气：小雨」。
- L1841: `start_time = 2026-01-05T08:00:00+08:00` 合法

Fix: State that the run-start date's weather is drawn at run start, and whether a weather fact is also written then, so that day-1 callbacks fire or explicitly do not.

### 47. The actions section's open-item list omits the weather-prompt constraint that its body leaves open

**Classification: bookkeeping**. The body at 989 states plainly that whether the weather rule is enforced only by the prompt is still open, and 2619, 2713 and 2797 list it as open. Only the section's own 待确认项 list (1091-1098) omits it. The rule to build is clear: environment-layer weather line plus a system-layer rule, with no extra filtering (2619). Only the completeness of the open-item list is affected.

*low · confirmed · open-item-mismatch* · lines 989, 1091, 1097, 2713, 2797

The travel and weather text leaves open whether the weather-source rule is enforced only by the prompt (989). The section's own 待确认项 (1091-1098) cites Q49 only for opening hours and run_start/sleep. The final table row (2797) and the handbook list (2713) do include the weather constraint.

Evidence:

- L989: 明确环境是天气的唯一来源；是否仅靠提示约束模型文字仍待确认。
- L1097: 资金账户集合、营业时间精确结构及 `run_start` 与睡眠的细节按所属章节保留待确认
- L2797: 营业时段精确结构及特殊时段、睡眠追加答复的解释、天气约束是否仅靠提示

Fix: Add the weather-prompt constraint (Q49 d) to the actions section's 待确认项.

### 48. `run_start` is named as a sleep-ending source after it was merged into `session_open`

**Classification: wording**. The wake section settles this unambiguously. Only the sleep's own planned end and the run's first start end sleep (1336, 1343). session_open's 'never had a decision' source carries can_wake_sleep (1356). run_start is merged into session_open as a wake reason (1376) and survives only as a time-callback key that is not a wake_reasons member (1380). Lines 1003 and 2800 use `run_start` in the owner's own phrasing from Q12 ('plus run_start'), meaning the first run start. The fix is to swap in the current name, which is purely editorial.

*low · confirmed · stale-reference* · lines 1003, 1336, 1356, 1376, 1380, 1555, 2800

The wake section merges the wake reason run_start into session_open (1376). run_start survives only as a time-callback key, which is not a valid wake_reasons member (1380), and callbacks never wake the person. The rule that may end sleep is session_open's first-start / never-had-a-decision branch (1336, 1356). Line 2800, and more mildly line 1003, still name the code identifier `run_start` as what can end sleep. That conflates the retired wake name with the surviving callback key.

Evidence:

- L2800: 睡眠的主规则已经明确：只有自己的计划结束或 `run_start` 可以结束睡眠；收尾确认不能被解释为允许提醒打断睡眠。
- L1376: | `wake_from_sleep`、`run_start` | 合并为 `session_open` |
- L1380: `run_start` 仍可作为时间回调键，但不再是合法的 `wake_reasons` 成员。
- L1003: `run_start` 的运行开启例外与合并触发见「唤醒触发」

Fix: At 2800 and 1003, replace `run_start` with '运行首次启动 (the `session_open` first-decision source)'.

### 49. The Q49 sleep open item is described several different ways, while the body already settles the rule

**Classification: bookkeeping**. Open item Q49(c) only asks the owner to confirm that his 'Yes' on Q12.a is moot (open-questions.md 1094-1095). The sleep rule itself, including the first-run exception, is stated as settled at 1336, 1343, 1356 and 2800. Lines 1003, 1097, 1415, 2713 and 2797 phrase the pending item differently, and some make the run_start exception look open. Every passage forbids reminder wake-ups, so only the description of what is still open is inconsistent.

*low · plausible · open-item-mismatch* · lines 1003, 1097, 1336, 1343, 1356, 1415, 2713, 2797, 2800

Q49(c) asks only whether the owner's 'Yes' on Q12.a is moot. The spec describes this item inconsistently: as the 'run_start exception' explanation (1003), as 'run_start and sleep details' (1097), as the 'sleep exception' (2713), and as 'the reading of the earlier sleep follow-up answer' (1415, 2797). Lines 1336/1343/1356 and 2800 state the sleep rule, including the first-run exception, as settled. This is bookkeeping drift only, since every passage forbids reminders from waking the persona.

Evidence:

- L1003: 该例外的解释仍保留原追加问题中的待确认说明。
- L1097: 资金账户集合、营业时间精确结构及 `run_start` 与睡眠的细节按所属章节保留待确认
- L1415: 早期睡眠附加确认的解释保留为【待裁决 Q49：睡眠附加确认的解释】，不能据此重新引入提醒唤醒。
- L2713: 以及营业时段精确形状、睡眠例外与天气来源约束的追加确认。
- L1336: 只有睡眠自己的计划结束，以及运行首次启动能结束睡眠

Fix: Describe the Q49(c) item the same way everywhere, as confirming the reading of the Q12.a answer. Remove the wording at 1003, 1097 and 2713 that says the run_start exception is pending, or mark the item closed by Q12 B.

### 50. The day-plan section calls 'plan reminders do not wake sleep' a retained rule, while the wake section deletes a reminder-ends-sleep path

**Classification: wording**. Every passage specifies the same target behaviour: day-plan reminders never end sleep (1036, 1162, 1336, 1381, 1389-1390). Line 1389 explicitly deletes activities.end_sleep_by_reminder. Q12 in open-questions.md confirms that today day-plan reminders can wake sleep, so 1231's word '保留' (retained) mislabels a change as kept behaviour. The deletion is stated normatively elsewhere, so an implementer still removes that path. Rewording 1231 as a change is editorial.

*low · plausible · contradiction* · lines 1036, 1231, 1381, 1389

Line 1231 lists 'sleep is not woken by plan reminders' among rules kept unchanged. Line 1389 deletes activities.end_sleep_by_reminder and every reminder-ends-sleep path, and 1036 stops suggesting reminders as a way to control waking. Both imply this is a change from current behaviour. The target behaviour is the same everywhere; only the framing as change or retained rule differs.

Evidence:

- L1231: 零点生成、跨日失效和睡眠不被计划提醒唤醒的规则保留
- L1389: 删除 `activities.end_sleep_by_reminder` 及任何由提醒或边界兜底提前完成睡眠的路径。
- L1036: 今日计划提醒不会叫醒睡眠，不再建议用提醒控制起床

Fix: At 1231, describe this as a change (day-plan reminders no longer wake sleep) and point to 「唤醒触发」.

### 51. The records section says purchase-duration details are unconfirmed, but the body has decided them

**Classification: bookkeeping**. The purchase duration is decided in normative rules and tests: a random 60-300 s draw (1093, 1115, 1542, 1863), 10-20 s payment segments (1864), and tests that must assert both (1087, 1308, 556). The open part of Q46 is named explicitly as receipt timing, pause/interrupt and insufficient funds (1094, 1543, 1595). Line 2369 forbids inventing numeric assertions for unconfirmed items. Asserting 60-300 s is not invention, so a test writer following 1087 and 1308 still asserts the duration range. 购买耗时细节 at 2369 is a loose label for the Q46 leftovers (557 calls them 购买活动细节).

*low · confirmed · summary-mismatch* · lines 557, 1087, 1093, 1094, 1308, 1543, 1544, 2369

The records implementation note forbids numeric assertions on unconfirmed '购买耗时细节' (purchase-duration details) (2369). The body fixes the purchase at a random 60-300 s with 10-20 s payment segments (1093, 1543), and the tests require assertions on both (1087, 1308). What remains open is receipt timing, pause/interrupt and insufficient funds (1094, 1544).

Evidence:

- L2369: 尚未确认的心理状态、效果档位与购买耗时细节不编造数值断言，也不以保留旧代码代替确认。
- L1093: `purchase` 已确定为随机 1–5 分钟的活动，并占用地点／出行／购买名额；第三方支付段为尾部 10–20 秒
- L1308: 购买按已抽取的 60–300 秒推进，不被报告节律替换
- L1543: 正常支付时点、随机时长活动及其名额已经确定。

Fix: At 2369, replace 购买耗时细节 with the actual open items: 到店收货时点、暂停、中断与途中资金不足.

### 52. run_stop fires 'when the run stops', but it is compiled as a scheduled event and runs can stop early on failure

**Classification: wording**. Lines 1558-1560 fix the mechanism. run_stop is compiled by compile_schedule into pending_events with a due time, and it runs only when due via process_scheduled_events, with no added scheduler. It executes once at the end point (终点), which is the configured stop_time; per 1843 the run ends at the stop boundary. A failure stop (1128, 1834-1835) never reaches that due instant. Firing a pending row early from a failure path would need the extra execution path that 1558 rules out, and it would act as a degradation, which 1833 forbids. 运行停止时 at 1556 is a loose gloss for the stop_time instant.

*low · plausible · ambiguity* · lines 1125, 1128, 1556, 1558, 1559, 1834, 1835

run_stop is defined as firing 'when the run stops' (1556) and must execute once at the end point (1559). It is compiled into pending_events and therefore fires only when due (1558). The spec also uses 停止运行 (stop the run) for failure stops: six-round budget exhaustion (1128, 1835) and retry exhaustion leading to run_failed (1834). It does not say whether run_stop fires on a failed run, so a final bill or transfer on run_stop may or may not settle.

Evidence:

- L1556: | `run_stop` | 运行停止时 |
- L1559: 起止触发必须在起点和终点各执行一次，
- L1835: 六轮是引擎固定的同刻决策总上限，耗尽停止运行；
- L1834: 传输失败、决策无世界请求、画像抽取失败、反思或零点计划校验耗尽均记英文原因、`run_failed`、`failed` 和非零退出码。

Fix: Define run_stop either as the configured stop_time instant only (not firing on failed runs) or as any termination, and reconcile that with 'executed once at the end point'.

### 53. Six-round budget: one passage still assumes a configurable old budget that another deletes

**Classification: wording**. Lines 1835-1836 are explicit: six is a fixed engine total, max_same_moment_decisions is deleted, and no configuration may change the cap. Line 1131 only says the old phase restrictions survive and the old configurable budget cannot push past six, which is consistent with deleting it. Line 1984 sits in the run-config failure row, after the configurable retry budgets in 1822 and the bounded retries in 1833. There, 确认可配置预算 means those retry budgets. Nobody would build a configurable six-round cap from this text. (A separate, unraised question: how the six rounds split between the normal phase and the restricted phase, given the old code's limit+RESTRICTED_ROUNDS.)

*low · plausible · stale-reference* · lines 1128, 1131, 1829, 1835, 1836, 1837, 1984, 2013

Line 1131 says the configurable old budget cannot extend the limit beyond six rounds, which implies a config key that still exists and could be set lower. Lines 1835-1836 make six a fixed engine cap, delete max_same_moment_decisions and forbid any configuration from changing it. Test row 1984 tests that a reduced 'configurable budget' fails at that count without naming which budget.

Evidence:

- L1131: 既有普通轮与受限轮阶段的限制保留，但不能通过可配置旧预算延长为超过已确认的六轮容忍。
- L1835: 六轮是引擎固定的同刻决策总上限，耗尽停止运行；
- L1836: 删除旧 `max_same_moment_decisions` 配置和 `900` 秒跳过，不允许配置改变该上限或恢复跳过行为。
- L1984: 预算耗尽停止，缩小确认可配置预算在对应次数失败

Fix: Rewrite 1131 to say the six-round cap is a fixed constant with no configuration, and make 1984 name the retry budgets it means.

### 54. `plan_reminder_due` is said to be retained, but it is merged into `reminder_due` and rejected as an old name

**Classification: wording**. Lines 1377 and 1381 state it outright. The day-plan reminder mechanism (当日目标提醒本身仍存在 / 保留当日目标的计划提醒) is kept. Its wake-reason name merges into reminder_due, and 1441 rejects every merged old name. Line 1162 uses the old identifier for the mechanism. Its real point (it cannot end sleep) matches 1381. Snapshot and record field names are fixed by 1422 and 1441, so nobody would keep plan_reminder_due as a valid name.

*low · confirmed · stale-reference* · lines 1162, 1377, 1381, 1422, 1441

Line 1162 says the identifier plan_reminder_due is retained. The wake section merges that trigger name into reminder_due (1377), requires snapshot fields to use current trigger names (1422), and rejects all merged old names as unknown members (1441). Only the day-plan reminder mechanism survives (1377, 1381), not the identifier.

Evidence:

- L1162: 今日计划的 `plan_reminder_due` 保留，不能代替闹钟结束睡眠。
- L1377: | `plan_reminder_due`、`errand_reminder_due` | 在唤醒原因中合并为 `reminder_due`；当日目标提醒与事务提醒本身仍存在 |
- L1441: 所有删除或合并掉的旧触发名，包括 `discovery_result`，都按未知成员拒绝。

Fix: At 1162, say the day-plan reminder mechanism is kept and is recorded as `reminder_due`, without naming `plan_reminder_due`.

### 55. For --parent branch runs, 'first run start' and 'persona never had a decision' diverge as the sleep exception

**Classification: wording**. The static trigger table owns each trigger's condition: WAKE_TRIGGERS is the single place that keeps names and conditions (1334, 1421). Its session_open condition is 本人从未获得过决策 (1356). AgentState.pending_wake_reasons starts as [session_open], is saved with the checkpoint (1428), and is set during world setup (1463). The current code computes run_start as last_decision_time is None. A --parent, --checkpoint or --intervention child restores that state and does not re-run world setup, so a persona asleep at the branch point stays asleep. Lines 1336, 1343 and 1368 are prose summaries that coincide with the table for a fresh run.

*low · plausible · ambiguity* · lines 1336, 1343, 1356, 1368, 1404, 1428, 1516, 1930

The sleep-ending exception is phrased as the run's first start (1336, 1343, 1368). The trigger table phrases the same source as 'the persona has never had a decision' (1356). A --parent, --checkpoint or --intervention child run (1930) is a new run whose persona already had decisions, and branches read committed state (1516, 1428). If the checkpoint falls during sleep, one reading wakes the persona at branch start and the other keeps them asleep.

Evidence:

- L1336: 只有睡眠自己的计划结束，以及运行首次启动能结束睡眠；
- L1356: 本人的睡眠恰在本边界完成，或本人从未获得过决策；两者同时成立只记一次
- L1368: 运行首次决策的初始待处理原因只有 `session_open`。
- L1930: `--same-second-continuation`、`--parent`、`--checkpoint`、`--intervention` 等场景无关功能保持。

Fix: Use one definition ('the persona has never been given a decision') at 1336, 1343 and 1368, and state that resume, replay and branches inherit pending_wake_reasons and never re-emit session_open.

### 56. Category subscriptions at the initial run-start decision have no defined direct cause or anchor

**Classification: drift**. Implementation A treats the initial decision as a boundary: time_boundary category rules fire at start_time with anchor wake:{agent_id}:{start_time} and an invented cause (the run_start event, or none). Implementation B fires category subscriptions only at settled advance boundaries or same-moment rounds, so the same rule does not fire at run start and the callback evidence count is one lower.

*low · plausible · ambiguity* · lines 1356, 1368, 1428, 1528, 1581, 1582, 1892, 1893

Category subscriptions are evaluated at every boundary that offers a decision (1528, 1581). The first decision carries session_open, whose category is time_boundary (1356, 1368). Direct causes are defined only for time-advance or midnight decisions (body_settlement) and same-moment rounds (action_result) (1582), and anchors are defined only for those two cases (1892-1893). The initial decision fits neither.

Evidence:

- L1528: 可订阅的预排事件种类或静态触发表类别 | 预排事件写下事实时或提供决策的边界
- L1582: 时间推进或零点决策的直接原因是本边界 `body_settlement`；同刻再决策直接原因是本轮 `action_result`，
- L1892: | 推进边界的类别订阅 | `wake:{agent_id}:{current_time}` |
- L1368: 运行首次决策的初始待处理原因只有 `session_open`。

Fix: State whether category subscriptions are evaluated at the initial decision and, if so, give its direct cause and anchor.

### 57. Goal reminders that fall due during sleep are 'not consumed', but nothing says what happens to them after waking

**Classification: borderline**. The judges split: the implementer judge chose drift and the skeptic chose wording.

*low · plausible · ambiguity* · lines 1359, 1367, 1390, 1391, 1474

Line 1390 leaves both goal reminders and errand reminders that fall due during sleep unconsumed. After waking, only errands get an explicit catch-up delivery (1391, 1474). reminder_due fires when a reminder '到点' (reaches its due time) (1359), which can mean the edge at the due time or 'due and not yet consumed'. An unconsumed goal reminder might therefore be delivered at the first waking boundary or silently lapse.

Evidence:

- L1390: 睡眠中到点的目标提醒与事务提醒不消耗、不交付，也不把对应时刻作为提前叫醒的候选。
- L1391: 每个为清醒人物提供决策机会的边界，包括计划睡眠刚结束的边界，交付全部已到点未提醒的事务
- L1359: | 9 | `reminder_due` | 当日目标提醒或个人事务提醒到点；同边界两种来源同时成立只记一次 | `reminder_due` | 否 |

Fix: State whether a goal reminder that fell due during sleep is delivered (and raises reminder_due) at the first waking boundary, or lapses.

### 58. Whether hourly, daily and other whole-hour events fire exactly at start_time and stop_time is undecided

**Classification: drift**. Implementation A keeps the old filter for hourly and daily: an N-hour run fires an hourly rule N-1 times, skipping start_time and stop_time. Implementation B fires at every whole hour within the closed window: N+1 times, or N under a half-open window, so state deltas and transfers in the export differ.

*low · plausible · ambiguity* · lines 1552, 1553, 1559, 1560, 1625, 1707, 1818, 1843

start_time and stop_time are whole hours (1818), so the run edges are always due instants for hourly events and, at midnight, for daily events. Only run_start and run_stop are exempted from the old open-interval filter start < due < end (1559-1560). This implies the filter still drops edge instants for other keys, while 'every local whole hour' (1552) literally includes them. An hourly rule over an N-hour run could therefore fire N-1, N or N+1 times. The spec also does not say whether the stop boundary is settled.

Evidence:

- L1552: | `hourly` | 每个本地整点 |
- L1559: 起止触发必须在起点和终点各执行一次，
- L1560: 不能套用只接受 `start < due < end` 的旧过滤；提交前弹出到期项，恢复不重发。
- L1818: `start_time`、`stop_time` 为 TOML 原生带偏移日期时间，整点且停止晚于开始
- L1707: 小时、零点、按月、起止各按声明触发；恢复不重发

Fix: For each time key and scheduled event (weather, push and reminder hours, deliveries), state whether the window is closed or half-open at start and at stop, and how same-instant events are ordered.

### 59. `calendar_flag` is a subscribable event key but is never defined

**Classification: wording**. 保留的 calendar_flag (1576) refers to the existing ScheduledKind.CALENDAR_FLAG. compile._calendar_rows produces it from the calendar declaration that 1817 keeps, at local 00:00 of each flagged date, with scheduled_id calendar-{date}. Line 1891 says time and scheduled events use that scheduled_id as anchor, and 2033 lists calendar events as still having a producer. Its old wake mapping (message_arrival) is removed by 1373 and 1425, so it becomes silent. The spec relies on the existing definition here, as it does for other retained kinds. The missing test row is a coverage gap, not a meaning that could be read two ways.

*low · plausible · undefined-reference* · lines 1576, 1699, 1707, 1817, 1891, 2033

calendar_flag appears only at 1576, as a 'retained' subscribable event. The spec never gives its producer, when its fact is written, its scheduled_id (needed for the anchor at 1891), or its relationship to the run-config calendar marks (1817). The records list says only '日历' (2033), and the load and time-event tests do not cover it.

Evidence:

- L1576: 可订阅 `delivery_arrival`、普通 `app_push`、保留的 `calendar_flag` 与每日天气事件。
- L1817: | 运行日历 | 现有日历声明迁入；继续提供日历标记与排班核对，不保留场景日历副本 |
- L2033: `body_settlement`、`delivery_arrival`、日历、当日目标、个人事务、活动生命周期和初始资金证据继续按所属模块记录

Fix: Define calendar_flag (its producer from config calendar marks, write time, scheduled_id or anchor, and validity as an event key), or remove it from the list, and add it to the test rows.

### 60. Card version bump on a field change is mandatory in one place and left to separate confirmation in another

**Classification: bookkeeping**. The rule an implementer builds is the same from both lines. Line 1808 says bump when the actual field set changes, and not for a fingerprint-source change alone. Line 2329's rejection column says the same in substance: 不借旧号放行已改变的字段集合, and no bump for a fingerprint-source change. 另行确认 at 2329 only signals that the psychological and social field set is still undecided (Q43/Q45, 1805-1807, 1995). The mismatch is about status, not behaviour. Line 1808 names the mapping table and 2329 names the coverage file. That is an editorial gap on which versioned artifact the bump touches, and it should be harmonised.

*low · confirmed · open-item-mismatch* · lines 1808, 1995, 2329

The persona section requires bumping the card and mapping-table versions, and rejecting the old structure, whenever the actual field set changes (1808). The version table says that a card-structure bump for changed psychological or social fields is to be 'confirmed separately' (2329), and it names the coverage file instead of the mapping table. Neither open list tracks this bump policy.

Evidence:

- L1808: 实际字段集合改变时同步升卡片与映射表版本并拒旧结构；仅指纹取值来源改变不单独升号。
- L2329: 指纹来源变更本身不改变卡片 3 和覆盖文件 1；心理或社交字段改变时另行确认卡片结构升号

Fix: Make 2329 follow 1808 (bump whenever the field set changes, and name the mapping table), or track the bump policy as an open item and soften 1808.

### 61. Retry key names, defaults and ranges are left open in the body but tracked in no open-item list

**Classification: bookkeeping**. The body itself says the gap is open. L1829 says retry key names, defaults and ranges that are not yet specified must not be inferred from old constants. L1827-1828 keep the Q48 layout open, and L2733 forbids writing provisional defaults into frozen formats. L1984 limits the failure tests to budgets that are confirmed configurable. The named keys (transport_max_retries, max_react_turns, max_retries at L1821-1822) are clear. An implementer reading L1829 knows the two unnamed budgets are not decided, so they would ask rather than build something wrong. The only defect is that L1995-1996 and the Q48 row at L2796 do not list this item. That could mislead someone about freeze status or what to ask the owner, but it does not change what gets built.

*low · plausible · open-item-mismatch* · lines 1822, 1829, 1995, 1996, 2597, 2796

Line 1829 says the retry key names, defaults and ranges are not specified. The profile-extraction and bedtime-reflection budgets are unnamed (1822, 2597). The Q48 row of the final table (2796) and the persona section's open list (1995-1996) do not mention them, and they present retries as settled ('重试进入 config.toml').

Evidence:

- L1829: 未明确的重试键名、默认值与范围也不从旧代码常量推定为用户选择。
- L1822: | 重试预算 | `transport_max_retries`、`max_react_turns`、画像抽取和就寝反思重试预算由配置提供 |
- L2796: 模型身份、非密参数和重试进入 `config.toml`；凭据只引用环境变量名

Fix: Add 'retry key names, defaults and ranges (profile extraction, bedtime reflection)' to the 2796 row and the persona open list, or name the keys in the body.

### 62. Unknown-model handling is listed as open, but the body already fixes the load failure

**Classification: bookkeeping**. The load behaviour is settled. L1846 requires authoritative metadata, L1954 gives the rejection reason when it is unavailable, and L1833 forbids degradation. So a model without obtainable metadata is rejected with `model context metadata is unavailable`. L1847 and L1828 leave open only where the metadata comes from (and refuse an engine model table). That is presumably what '未知模型处理' in the Q48 row (L2796) means. The overlap only affects how the open item is worded and scoped. No implementer would build a different load path.

*low · plausible · open-item-mismatch* · lines 1846, 1847, 1954, 2796

The Q48 row lists '未知模型处理' (handling of an unknown model) as unconfirmed (2796). Load already requires authoritative context metadata (1846) and defines the rejection reason when it is unavailable (1954). Only the metadata source remains open (1847).

Evidence:

- L2796: TOML 分组、凭据文件和变量读取规则、真实上下文长度的取得及未知模型处理
- L1846: 完整加载入口核对声明至少 `262144`、权威元数据可得、实际模型上下文不小于声明。
- L1954: | 权威上下文不可得 | `model context metadata is unavailable` |

Fix: Reduce the Q48 item to the source of the context metadata, or mark 1846/1954 as provisional.

### 63. The persona/config section says export is version-bumped, but the records section keeps the export manifest at 4

**Classification: wording**. L1933 is a pointer: it hands version numbering for exports and manifests to the 「记录、导出与评估」 section. That section's normative version table is explicit: run manifest 5 (L2331), export manifest '4，不变' (L2332). L2008 repeats that the export manifest stays at 4, and L2338 fixes where each schema number is maintained. '统一升号' is a loose summary. Nobody reading the cited table would bump the export manifest.

*low · plausible · summary-mismatch* · lines 1933, 2008, 2331, 2332

Line 1933 says '导出与清单' (export and manifest) receive a unified version bump in 「记录、导出与评估」. That section keeps the export manifest at 4 unchanged (2008, 2332) and only shows the run manifest at 5 in the version table (2331).

Evidence:

- L1933: 导出与清单由「记录、导出与评估」统一升号；两份 TOML 各结构 `1`
- L2008: `EventType` 的 14 个成员和五项导出工件保持不变；导出清单结构仍为 4。
- L2332: | 导出清单 | 4，不变 | 仍为五项工件，由世界结构和规则身份拒绝旧导出 |

Fix: Change 1933 to say the run manifest is bumped to 5 in the records section and the export manifest stays at 4.

### 64. The handbook says `entertainment_preferences` is rejected, but the body never defines it or its rejection path

**Classification: wording**. The intended rule is clear. L1781-1782 list exactly four fields for LEGACY_TEMPLATE_FIELDS, and the code (profile/models.py) holds the same four. Any other top-level key falls under strict-model rejection of unknown keys, reported as `Persona file validation failed` (L1943, test row L1977). The handbook at L2589-2590 only says the separate entertainment_preferences config '继续拒绝' (stays rejected) and names no message. That matches the existing manual (docs/manual/personas.md:254) and 06-agent.md, where it is a deleted random-float field. The handbook groups it loosely with the template fields. Fixing that is editorial, because the body's enumerated list decides the path.

*low · plausible · undefined-reference* · lines 1781, 1782, 1943, 1945, 2589, 2590

entertainment_preferences appears only in the handbook (2590), grouped with the legacy template fields as 'still rejected'. The persona section defines a dedicated rejection only for the four legacy fields (1781-1782, 1945), so it is unclear whether this key gets the legacy-field message or the generic unknown-key failure (1943).

Evidence:

- L2589: 旧模板字段 `occupation`、`routine_preference`、`consumption_preference`、`risk_preference` 与独立
- L2590: `entertainment_preferences` 配置继续拒绝。

Fix: Add entertainment_preferences to the persona rejection rules with its reason, or remove it from the handbook.

### 65. Two different error messages for a retired activity kind in evaluation adapters

**Classification: borderline**. The judges split: the implementer judge chose wording and the skeptic chose drift.

*low · confirmed · contradiction* · lines 2090, 2091, 2293

Lines 2090-2091 say an event prefix with a retired activity kind is rejected in 评估适配 (evaluation adaptation), among other places, with 'Activity snapshot uses a retired activity kind'. Line 2293 says the non-content activity adapter still reports 'Activity type is outside the current closed set' for a retired kind. No precedence is given, so tests would assert different messages for the same input.

Evidence:

- L2090: 含退役活动种类的事件前缀在导出投影、导出认证、质量报告与评估适配中均被拒，错误为
- L2091: `Activity snapshot uses a retired activity kind`。
- L2293: 非内容活动适配遇到退役种类时仍报 `Activity type is outside the current closed set`。

Fix: State which check runs first and which message applies (e.g. the prefix check for retired kinds, the adapter message only for unknown non-retired types), or unify the messages.

### 66. It is unclear whether app_touch rows in app-usage.jsonl carry action and step fields

**Classification: drift**. One implementer emits app_touch as the shared fields plus occurred_at, and validation looks up action and step through source_event_id (following L2139). Another adds action and step (and perhaps keeps edge) as row keys so L2161 can compare them directly. Each one's closed-set test rejects the other's rows.

*low · plausible · ambiguity* · lines 2131, 2132, 2139, 2142, 2161

The app-usage section lists exact extra fields only for payment_notification (2142). For app_touch it says the action and step are traced from the source event (2139), which implies the row does not carry them. The invariant says validation matches the app_touch's app, action, step and person against the footprint event and rejects any key outside the closed set (2161), which implies the row does carry them. The row's field closure therefore depends on which statement is followed.

Evidence:

- L2132: `fact_kind`、`agent_id`、`activity_id`、`app_id`、`source_event_id`。
- L2139: 具体动作和步骤从来源事件追溯，购买支付 app 同时保留在订单事实中。
- L2161: 系统 `app_touch` 匹配真实足迹事件的 app、动作、步骤与人物，不能要求它附有虚构活动；字段闭集之外的键一律拒绝

Fix: List the exact app_touch row fields (with or without action, step and occurrence time) and align the invariant at 2161 with that list.

### 67. The implementation section still lists 'behaviour families' as evaluation-doc content after families were deleted

**Classification: wording**. The body plainly deletes every family construct: the family-based consistency rate (L2199), the activity_distribution family vectors (L2221), category-derived reports (L2217, L2250), and L2360 verifies '删除指标与家族'. The module-table row at L2742 only names topics that 07-evaluation.md must cover. Read with L2360, the intent is that the doc records the deletion of behaviour families. Nobody would rebuild families from a topic label, so fixing it is editorial.

*low · plausible · stale-reference* · lines 2199, 2221, 2360, 2742

The body deletes every activity-family construct: family-based habitual consistency (2199) and activity_distribution family vectors (2221), and the implementation verifies that families are deleted (2360). The module table still lists 行为家族 (behaviour families) as content of 07-evaluation.md (2742).

Evidence:

- L2742: | 评估 | `docs/design/07-evaluation.md`：指标删除、公开证据、行为家族与对比边界 |
- L2221: `activity_distribution` | 整组删除，含工作、娱乐、运动、家务等活动家族分钟向量
- L2360: 验证删除指标与家族、四种出行的公开映射

Fix: Replace 行为家族 at 2742 with '删除的活动家族与分类报表', or remove it.

### 68. Public evidence projects purchase from point facts, yet also says purchase is recorded as an activity interval

**Classification: borderline**. The judges split: the implementer judge chose wording and the skeptic chose drift.

*low · plausible · ambiguity* · lines 2280, 2282, 2283, 2291, 2299, 2300

The projection table gives purchase only point-fact projection from orders, payments, delivery_arrival and goods_received (2283). Travel and interact get interaction, object_type and object key (2280, 2282), but purchase does not. The prose says purchase is recorded with its drawn duration and actual activity interval, payment segments included (2291), and blind-review activity rows need start, end, status and location (2299). The purchase interval's projection fields are undefined.

Evidence:

- L2283: | 购买、到站与收货 | 继续由实际订单、付款、`delivery_arrival`、`goods_received` 投影；`goods_category` 取四种商品类别 |
- L2291: 购买按已抽取时长和实际活动区间记录，支付段包含在内；到店收货时点、暂停及中断规则仍待确认。
- L2299: 活动行删除 `companion` 与语义 `category`，使用 `action`、`start`、`end`、`status`、`location` 五键，

Fix: Add a projection row for the purchase activity interval (interaction, object_type, object key, status), or state that purchase remains point-fact evidence and remove the interval sentence at 2291.

## Angles that came back clean

The verifiers refuted every finding from the "body and psychological settlement while two slots run at once" angle. Error codes, version numbers, the money-account rules and fidelity to the owner answers produced only the issues listed above.
