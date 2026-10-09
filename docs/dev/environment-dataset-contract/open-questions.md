# Open questions for the environment-dataset-contract spec

These questions block the final spec in
[SPEC-environment-dataset-contract.md](SPEC-environment-dataset-contract.md). They are derived from
[the proposal](../PROPOSAL-environment-dataset-contract.md).

Each one arises because the proposal contradicts itself, leaves the point open, or says nothing about a detail the
implementation needs. Until you answer, the spec text that depends on a question carries a `【待裁决 Qn】`
placeholder and picks no option.

**How to answer.** Fill in the `Answer:` line under each question, for example `B`, `A, drink = −0.10`, or your own
text. A *suggested* option is only a hint where the proposal leans one way; it is not in the spec.

"Proposal Qn" means a closed chapter-8 item of the proposal. Plain "Qn" means the questions in this file.

---

## Part 1 — Package, persona and run configuration

### Q1 Do scenario files still exist?

The proposal contradicts itself here:

- D1's eight-file package has no scenario files.
- C10 and proposal Q15 keep scenarios and add a `persona_file` reference.
- `persona.toml` and `config.toml` are single files at the environment root. Between them they hold the persona, its
  initial state (U9), the seed and the start/stop times (U11).

Today one environment holds several scenarios. The fixtures have single-person, two-person and half-day runs, and
dataset scenario 0006 has five people sharing one world.

- **A — one environment is one run.** The root holds `catalog/`, `persona.toml` and `config.toml`, and `scenarios/` is
  deleted.
  - Per-person fields (home, workplace, work schedule, work contents, balance, body, car) go into `persona.toml`.
  - Run-level fields (timezone, seed, start/stop, decision cadence, retries, context window) go into `config.toml`.
  - Multi-person runs and several experiments per environment disappear.
- **B — one directory per experiment.** Each experiment has `scenarios/<id>/persona.toml` (one table per person) and
  `scenarios/<id>/config.toml`, and the catalog is shared.
  - Leftover run fields (accounts, payroll, mail, calendar) go into that `config.toml`.
  - Multi-person runs and N experiments per environment stay.
- **C — keep the root-level files exactly as written, and keep `scenarios/*.json` for everything else.** Every
  scenario in an environment then shares one persona file, one seed and one time window.

Follow-ups, only if several personas per run remain:

- **Q1.a** Rules with no persona attached (time triggers, bills): who are they evaluated for? *Suggested: once per
  persona.*
- **Q1.b** U9 puts stamina (体力), mood (情绪) and location (位置) in `persona.toml`.
  - Should the whole body table move per person?
  - Should a new initial-mood value be added? Today mood always starts at 0.
  - Is the start location the home, or a separate location?
- **Q1.c** Do name, the habits text and the rhythm windows (meal, work, home, bed) also move into `persona.toml`? The
  proposal says it is the only persona source.
- **Q1.d** If scenarios survive, does a person entry carry a `persona_file` reference (C10)?

Answer:

**A — one environment is one run.** The root holds `catalog/`, `persona.toml` and `config.toml`, and `scenarios/`
is deleted. Per-person fields (home, workplace, work schedule, work contents, balance, body, car) go into
`persona.toml`; run-level fields (timezone, seed, start/stop, decision cadence, retries, context window) go into
`config.toml`. Multi-person runs and several experiments per environment disappear.

Author: "Option one." Extended by the standing ruling that only one person is ever simulated, so a scenario has no
remaining role as an experiment container.

- **Q1.a** — Moot once Q1 is A: there is exactly one persona, so a rule with no persona attached is evaluated for
  that one persona.
- **Q1.b** — The whole body table moves into `persona.toml`; U9 already put initial balance, stamina, mood, location
  and car ownership there. Initial mood is a `persona.toml` field, and its value is the dataset's to set.
  **Recommended, not yet ruled:** the start location is the home.
- **Q1.c** — Yes. Name, habits text and the rhythm windows move into `persona.toml`; it is the only persona source
  (D11: `persona.toml` **replaces** the scenario `background_story`).
- **Q1.d** — Moot under A: there are no scenario files, so no `persona_file` reference exists.

### Q19 Retry and degradation (U12)

Which retry settings go into `config.toml`? Does "降级" (degradation) mean anything beyond retrying?

*Suggested:* transport retries, maximum model turns, and the profile-extraction and bedtime-reflection retry counts.
Running out of retries fails the run, with no other degradation.

Answer:

Retry keys in `config.toml`: `transport_max_retries`, `max_react_turns`, the profile-extraction retry count and
the bedtime-reflection retry count. **Model identity also moves into `config.toml`** — `provider`, `base_url`,
`requested_model`, `max_retries` — overriding X1's "partly adopted". Credentials never enter the file; it names the
environment variables only. The run manifest freezes the `config.toml` identity, and resume and replay reject any
difference, as today.

"Degradation" means nothing beyond retrying. Running out of retries fails the run; there is no other degradation.

Author: "These should be configured in config.toml too." U12 supplies the degradation rule: "simple, don't make it
complicated".

### Q26 Extra entries at the environment root

Today the loader ignores unknown folders at the root. For example, the dataset ships a `sources/` copy of every catalog
file next to the real one, and the two differ byte for byte. Proposal §1.2.16 conclusion 4 says each environment
should have one copy of each file, but never records your ruling.

- **A.** Reject anything at the root that is not part of the package.
- **B.** Keep ignoring extra folders.

Answer:

**A.** Reject anything at the root that is not part of the package, as a load-time invariant: "each environment
set holds one copy of each file" becomes enforced rather than merely documented.

Author: "Adding validation to make it an invariant — ok."

---

## Part 2 — Locations, map, weather, travel

### Q3 Locations with several instances

The two parts of the proposal describe instances differently:

- N1 and §1.2.5 say a location entry declares `count`. The engine places N instances with generated addresses, and the
  persona must write "名称-地址".
- The later §1.2.17 shows explicit `location_id` + `branch_label` entries, displayed as "7-11（张江路店）", with no
  count.

- **A — follow N1.** An entry has `{id, name, kind, count, app_ids, opens, is_home, accepts_parcels}`. The engine
  generates instance ids and fictional addresses ("区 + 路 + 号"). Every place, including count = 1, is shown and must
  be written as "名称-地址".
- **B — follow §1.2.17.** Instances are explicit, with no count and no generated address. The display stays
  "名称（branch_label）".
- **C — both.** An entry has `location_id`, an optional `branch_label` and `count` (default 1). Only entries with
  count > 1 expand into "名称-地址" instances.

Follow-up for A or C: must the home and workplace entries have count = 1?

Answer:

**B — follow §1.2.17.** Instances are explicit, each with `location_id` and an optional `branch_label`; there is
no `count` and no generated address, and the display stays "名称（branch_label）". §1.2.5's `count` model and its
generated "区 + 路 + 号" addresses are dropped. Same-named locations must carry distinct `branch_label` values,
because `display_name` is what the uniqueness check compares.

Author: "Option two — the system code is simpler to write." The follow-up is moot: no entry carries `count`.

### Q4 The map file

R1 folds the map seed into `config.toml`'s single seed, and U7/R12 has the engine place instances by that seed.
But C9 still lists `map.json` in the catalog.

- **A.** Keep `map.json` as a generated, published catalog file. `worldmock environment generate-map` builds it from
  `location.jsonl` and the `config.toml` seed, and `--check` compares bytes.
- **B.** Delete `map.json`. The engine builds the map at load from `location.jsonl` and the `config.toml` seed, and
  `map_sha256` hashes the generated map.

Answer:

**B.** Delete `map.json`. The engine builds the map at load from `location.jsonl` and the `config.toml` seed, and
`map_sha256` hashes the generated map.

Author: "Is system-maintained state something we have to write out as a JSON file? Can't it stay in memory?" — a
system-maintained artifact does not need to be a published file.

### Q5 Weather (U5)

The proposal says only that weather is random, seeded, and multiplies travel base speed.

Please decide:

- the weather states and their speed factors;
- their probabilities;
- how often weather is drawn;
- how callbacks subscribe to weather: a daily scheduled event they can listen to (§1.2.9 option a), or a world-state
  value (§1.2.9 option b);
- whether the persona sees the weather, for example an environment-layer line "今天天气：小雨". §1.2.16 warns that the
  model currently invents weather and feeds it back into memory.

*Suggested:*

- One draw per local day at midnight.
- Equal probability over 晴 1.0 / 多云 1.0 / 小雨 0.85 / 大雨 0.7 / 雪 0.6.
- The factor applies to all four travel modes.
- Callbacks subscribe through the daily scheduled event.

Answer:

One draw per local day at 00:00. Equal probability over clear 1.0 / cloudy 1.0 / light rain 0.85 / heavy rain
0.7 / snow 0.6. The factor applies to all four travel modes. Callbacks subscribe through a new daily scheduled
event, the same shape as `bill_due`, not through a state subscription. The persona sees the weather as one
environment-layer line, e.g. "今天天气：小雨".

Landing this requires changing the environment-layer constructor, its `prompt_schemas` schema and the system-layer
rule text **in the same change**, and the environment must be the only source of weather — the model currently
invents weather that is written into memory and fed back (§1.2.16 conclusion five).

(Recommended; confirmed.)

### Q6 Bus and taxi

Today walking is 1.2 m/s and free. Driving is 7 m/s, with a fare of `max(500, ceil(0.2 × metres))` fen paid to
`transport:service`.

- **A — engine constants for the new modes.** Please give the numbers.
  - *Suggested bus:* 5 m/s, flat 200 fen.
  - *Suggested taxi:* 7 m/s, `max(1400, ceil(0.3 × metres))` fen.
- **B — engine speeds only.** Every fare, including driving's, comes from a dataset callback (`transfer` effect), and
  the engine fare formula is deleted.

Also confirm:

- Bus and taxi need no car.
- Only `drive` deducts fuel days.

Follow-up **Q6.a**: for bus and taxi, what energy cost (MET) applies, what does the ride occupy, and may the persona
place remote orders during the ride? *Suggested — passenger profile:* MET as driving, only the travel slot is
occupied (the phone stays usable), and remote ordering is allowed, as when walking.

Answer:

**A — engine constants.**

| mode | speed | fare |
| --- | --- | --- |
| walk | 1.2 m/s | 0 |
| bus | 5 m/s | flat 200 fen |
| taxi | 7 m/s | `max(1400, ceil(0.3 × metres))` fen |
| drive | 7 m/s | `max(500, ceil(0.2 × metres))` fen |

Bus and taxi need no car. Only `drive` deducts fuel days.

- **Q6.a** — Passenger profile: MET as driving, only the travel slot is occupied (the phone stays usable), and
  remote ordering is allowed, as when walking.

(Recommended; confirmed.)

### Q13 Sleep target

The proposal says "sleep target = 地点（home）".

- **A.** Sleep only at your own home, and only while you are there.
- **B.** The target is your current location, so you can sleep anywhere.

Answer:

**A.** Sleep only at your own home, and only while you are there. The target is the location `home`, from the
author's own action table (`{"action": "sleep", "target": "家"}`).

### Q22 Opening hours (U3)

*Suggested:*

- `opens: ["HH:MM-HH:MM", …]` gives daily local time windows; a missing field means open all day.
- When a place is closed, three things are rejected with `LOCATION_CLOSED`: an in-store purchase, a remote order from
  that place, and `interact` at that place.
- Travelling to a closed place is still allowed.
- The tool layer shows the opening hours.

Accept or change.

Answer:

`opens` is a field on the location entry, and it covers **which days of the week the place opens and the hours
on each day, plus special closing times** (public holidays, the shop's regular closing day, and so on).

Suggested shape:

```json
{"weekly": {"mon": ["09:00-22:00"], "sun": []},
 "exceptions": [{"date": "2026-10-01", "closed": true}]}
```

A missing field means open all day. A day absent from `weekly` means closed that day. An `exceptions` entry
overrides the weekly entry for that date.

When a place is closed, three things are rejected with `LOCATION_CLOSED`: an in-store purchase, a remote order from
that place, and `interact` at that place. Travelling to a closed place is still allowed. The tool layer shows the
opening hours.

Author: "It must specify which days of the week the place opens and the hours — set per day — plus special closing
times (public holidays? the shop's regular closing day, and so on)." The exact field shape is a recommendation
still to confirm.

### Q27 Which locations need a merchant account

Today every store-kind location needs a `merchant:<id>` account. Proposal Q23 replaces this with "an explicit flag
for whether the location sells", but gives no rule.

- **A.** Add a `sells_goods` flag, default false. Loading rejects goods that would be sold at an unflagged location,
  and every flagged location needs a merchant account. *(suggested)*
- **B.** Same as A, and also reject flagged locations that sell nothing.
- **C.** Do not check the flag against what goods are sold at the location. Merchant accounts are still required for
  flagged locations at load.

Answer:

**No location needs a merchant account.** Merchant accounts are deleted entirely, so there is no `sells_goods`
flag to decide. A purchase moves money from the persona to the scenario anchor (`external:scenario`), which is
already the anchor of the closed money domain, so the conservation assertions are unchanged. The forced
`merchant:<location_id>` declaration in `admission.py` and the `merchant:` account-role check in the scenario both
go. Every transfer entry still carries a `cause` naming the goods, the store and the app, so the record of what was
bought stays complete.

Author: "I don't think we need merchant accounts. What is that account even for? We have no inventory any more." —
landed as "derive the counterparty; do not make the dataset declare accounts".

---

## Part 3 — Apps, goods and fulfilment

### Q2 Goods categories

The mental model has four fixed categories with one fixed value each. Proposal Q4's table instead maps 商品 to
leisure + daily + fuel, which have three different effects, and leaves "four values or six internal values" open.

- **A — exactly four values: `snack`, `meal`, `drink`, `goods`.**
  - `goods` has a single fixed effect. Pick one: entertainment need −0.10 (today's leisure) or mood +0.05 (today's
    daily).
  - Fuel becomes an ordinary `goods` item; its driving-days meaning lives only in the callback rule.
- **B — keep six internal values.** The four categories are only the grouping shown to the persona and the dataset.
  Fuel keeps an engine-side receipt effect (reset to cap, proposal Q19).

Also: what value should `drink` get? *Suggested: entertainment need −0.10, the same as leisure today.*

Answer:

**A — exactly four values** (`snack`, `meal`, `drink`, `goods`), with no compatibility layer.

| category | effect |
| --- | --- |
| snack | 200 kcal |
| meal | 600 kcal |
| drink | the psychological quantity |
| goods | the psychological quantity |

`entertainment_need` and `mood_valence` are unified into one psychological quantity, so the four categories carry
two effects: eating restores calories; drinking and buying move the psychological quantity. Fuel is an ordinary
`goods` item and keeps no engine-side receipt effect — its driving-days semantics live only in the callback rule.

Author: "Six becomes four. The system shouldn't keep that much compatibility around — it's useless"; "Let's just
unify entertainment need and mood." D6 keeps `drink` on the psychological side.

### Q7 How function apps are bound to actions

Q13 says every function app must be bound to at least one concrete action, and §1.2.8 describes an automatic trigger.
But §1.2.15 also puts an `app_pool` in the callback `footprint_chain`.

- **A — on the app row.** The row carries `role: content | function`, and function apps also carry `actions: [...]`.
  - When a bound action happens, the engine records the app automatically.
  - With several candidate apps, it makes a seeded random pick.
  - `footprint_chain` stays as a separate, additional callback effect.
- **B — role only.** The app row carries only `role`, and every function-app use comes from callback `footprint_chain`
  rules.

Also: should `purposes` and `payment_app_ids` be deleted from the app row? *Suggested: yes.*

Answer:

**A — on the app row.** The row carries `role: content | function`, and function apps also carry `actions: [...]`.
When a bound action happens the engine records the app automatically; with several candidates it makes a seeded
random pick. `footprint_chain` stays as a separate, additional callback effect.

Delete `purposes` and `payment_app_ids` — nothing uses them any more, and unused code is not kept. The old
three-way interlock (`is_shopping` == has store bindings == `payment_app_ids` non-empty) is rewritten against
`role`.

Author: "If it isn't used any more, delete it. Don't keep dead code."; and on the binding key, "the binding is to a
concrete action, not to an action type".

### Q14 Remote orders for location-side goods

This applies when the persona is not at a store that sells the item.

- **A.** The store is the one with the earliest expected arrival (as today; ties go to the lower store id). The ordering
  app is a seeded random pick among that store's apps.
- **B.** The store is a seeded random pick among the selling stores that have apps.

Also confirm:

- Keep 600 s preparation + 5 m/s + 500 fen per order.
- Delete `Location.shipping_seconds`, so location-side deliveries can no longer take more than a day.

Answer:

**A.** The store is the one with the earliest expected arrival (ties go to the lower store id); the ordering app
is a seeded random pick among that store's apps. Keep 600 s preparation + 5 m/s + 500 fen per order. Delete
`Location.shipping_seconds`.

Standing rule applied — author: "Delete anything unused." Cited author ruling on the store choice: "Option one".

### Q15 Courier delivery for app-side goods

Please decide:

- **Delivery time:** whole days {2, 3, 4, 5} with equal probability, or uniform seconds between 2 and 5 days?
- **Fee:** none, or 500 fen?
- **Ordering app:** confirm it is a seeded random pick among the goods' apps.
- **No parcel station:** should loading fail when app-side goods exist but no parcel-station location does?

Answer:

- **Delivery time:** whole days {2, 3, 4, 5} with equal probability. Whole days rather than uniform seconds, so
  arrival lands on a local-day boundary and the "2–5 天到站" preview, the station notice and the decision to go and
  collect all agree.
- **Fee:** drawn per merchant — free shipping (0) or 500 fen. The draw is keyed on (goods, ordering app) rather than
  on the order, because it is a merchant's policy and not a per-order accident; that also makes it stable across a
  preview.
- **Ordering app:** confirmed — a seeded random pick among the goods' apps.
- **No parcel station:** loading fails when app-side goods exist but no parcel-station location does; otherwise
  those goods could never be collected.

Author, on the fee: "A fee of 0 doesn't feel right. Some merchants ship free, some charge shipping. Writing 0 for
all of them isn't right — the system can randomise it." The rest recommended; confirmed.

### Q16 Picking up parcels with `interact` (proposal Q24)

*Suggested:*

- Interacting at an `accepts_parcels` location collects all of your ready parcels there in zero seconds.
- It also starts one content bound to that location's kind, picked by the normal recommendation order.
- If nothing is ready, the request is rejected with `PARCEL_NOT_READY`.

Alternative: pickup only, with no content and no activity.

Follow-up **Q16.a**: what about a parcel-accepting location whose kind also has other contents bound, for example a
convenience store that accepts parcels? *Suggested: interacting there always collects the parcels and also starts a
normally picked content.*

Answer:

Interacting at an `accepts_parcels` location collects all of your ready parcels there. The pickup content
occupies **5 minutes**, and that duration is the dataset's to declare, not a system constant. It also starts one
content bound to that location's kind, picked by the normal recommendation order. If nothing is ready, the request
is rejected with `PARCEL_NOT_READY`.

- **Q16.a** — A parcel-accepting location whose kind also has other contents bound (a convenience store that accepts
  parcels) always collects the parcels and also starts a normally picked content.

Author: "Zero seconds isn't right. Five minutes of content is better for the pickup, but that should be the
dataset's call, not the system's." Also, "one interact collects every parcel — no collecting A and then B".

### Q28 Which apps locations and goods may bind

These bindings decide remote ordering (locations) and courier delivery (goods).

- **A.** Content apps only, such as Meituan-style platforms; anything else is rejected at load. *(suggested)*
- **B.** Any app.
- **C.** Locations may bind content apps only; goods may bind any app.

Answer:

**A.** Content apps only, such as Meituan-style platforms; anything else is rejected at load.

### Q29 A content app with no contents

- **A.** It must have at least one content.
- **B.** It must have at least one content, or be bound by a location or goods item. This carries over today's
  "an app has a purpose or a binding" rule. *(suggested)*
- **C.** No rule at load; interacting with it is refused at runtime.

Answer:

**B.** A content app must have at least one content, or be bound by a location or goods item. A platform app's
browsable goods catalog is itself a content, so it satisfies the rule without a separate entry.

Author: "This kind of app shouldn't exist. A content app must have content"; "My understanding is that the catalog
is content too."

### Q31 Two purchase items in one request

The engine now picks the store, the ordering app and the payment app.

- **A.** Pick the store and ordering app per item, then merge items with the same store, app and fulfilment into one
  order. Draw the payment app once per order, so one payment means one transfer. *(suggested)*
- **B.** Draw everything per item and never merge.
- **C.** Allow only one purchase item per request.

Answer:

**A.** Pick the store and the ordering app per item, then merge items with the same store, app and fulfilment
into one order. Draw the payment app once per order, so one payment means one transfer.

### Q32 Who sells a courier order

App-side goods have no store, but every order needs a seller account and a route.

- **A.** The seller is a `merchant:<app_id>` account for each ordering app. There is no store and no route; the path is
  just the station. *(suggested)*
- **B.** A fixed account, for example the existing external scenario account.
- **C.** The dataset declares an origin location for each app.

Answer:

**No seller account exists.** Merchant accounts are deleted (see Q27), so a courier order has no seller account.
The money moves from the persona to the scenario anchor, and the transfer's `cause` names the goods and the
ordering app. There is no store and no route; the path is just the station.

Author: "Does the seller really need an account??"

### Q33 The arrival time shown before buying, versus the random draw

- **A.** Courier rows show only "2–5 天到站", and the exact day is drawn when the order is accepted. Remote orders keep
  their exact quote. *(suggested)*
- **B.** Key the random draw only on facts known when the persona looks, so the preview is exact.
- **C.** Show no arrival preview.

Answer:

**B.** Key the random draw only on facts known when the persona looks, so the preview is exact. Consequence: the
same goods ordered through the same app on the same local day arrive on the same day.

### Q34 Whether app-side goods appear in the tool layer

- **A.** Yes, with the price, the fee and a "2–5 天到站" note. *(suggested)*
- **B.** Never. The persona learns about them by browsing the platform's content and buys them by name.
- **C.** Only while the persona is interacting with one of their apps.

Answer:

**A**, in this form: app-side goods are shown while the persona interacts with the app — price, fee and expected
arrival — and buying one routes automatically to its matching fulfilment. They are not listed in the tool layer on
their own.

Author: "How else would you show it? While interacting with the app: the price, the fee and the estimated arrival —
and buying XX goods routes automatically to the matching fulfilment."

### Q35 The ordering app in the persona's purchase memory sentence ("经美团……")

The engine picks the ordering app now; the persona no longer chooses it.

- **A.** Keep it for remote orders and courier orders.
- **B.** Drop it from all persona-visible text.
- **C.** Keep it for courier orders only.

Answer:

**B.** Drop the ordering app from all persona-visible text; the memory sentence says "bought XX goods" and stops
there.

Author: "Just guide it to write 'bought XX goods'. Nobody in real life insists on buying something from one
particular app."

---

## Part 4 — Contents, actions and callbacks

### Q8 Registering periodic state (fuel)

*Suggested:*

- `callbacks.jsonl` declares each state as `{state, initial, cap, reset_on_goods}`. Receiving one of the listed goods
  resets the state to its cap.
- `fuel.py`, `AgentState.driving_days`, `AgentState.last_driving_date` and `FUEL_DRIVING_DAYS` are deleted. A generic
  per-person map of callback states replaces them.

Also decide:

- Is `initial` declared in `callbacks.jsonl` (the same for everyone) or in `persona.toml` (per person)?
- Do `owns_car` and `CAR_REQUIRED` stay? That is, does driving still require a car?

Answer:

`callbacks.jsonl` declares each state as `{state, initial, cap, reset_on_goods}`, and `initial` lives there, not
in `persona.toml`. Receiving one of the listed goods resets the state to its cap: the value (the cap, e.g. 7 days)
is the dataset's and the reset rule is the engine's.

All personas own a car by default, so `owns_car` and `CAR_REQUIRED` go; driving no longer requires the dataset to
declare a car. `fuel.py`, `AgentState.driving_days`, `AgentState.last_driving_date` and `FUEL_DRIVING_DAYS` are
deleted and replaced by a generic per-person map of callback states.

Author: "Put it in callbacks.jsonl. Every persona owns a car by default", combined with the earlier value/rule
split — "Shouldn't the dataset define this? What we define is the rule."

### Q9 Threshold reminders (left unresolved in C7)

Please decide:

- **Frequency:** while the state is below the threshold, push at every push hour (08:00–21:00), or once per local day?
- **Payload:** is the reminder text declared by the dataset, and which app shows it (an `app_id` named in the rule)?
- **Sleep:** if the persona is asleep at reminder time, drop the reminder, deliver it on waking, or send it the next
  day?
- **Wake:** does a reminder wake the persona and give a decision, or arrive silently in the progress layer?

Answer:

- **Frequency:** once per local day, at the first push hour after the state goes below the threshold — not at
  every push hour.
- **Payload:** the text and the showing app are declared by the rule. A new payload kind is needed:
  `AppPush.content_id` is `min_length=1` and a fuel reminder has no content, so it cannot reuse that shape.
- **Sleep:** the persona is not woken; the reminder is delivered at the first push hour after waking.
- **Wake:** it neither wakes the persona nor forces a decision; it arrives silently in the progress layer.

(Recommended; confirmed, under the standing rule that only a planned end may wake sleep.)

### Q10 Time triggers, bills and payroll

Please decide:

- Should time triggers get a monthly key (day of month + HH:MM), for monthly utility bills? The proposal lists only
  hourly, daily, run start and run stop.
- Should scenario `bills` and the `bill_due` scheduled event be deleted? The rule "unpaid is recorded, nothing is
  blocked" stays either way.
- Where does payroll live: as a run-config field, or as a callback (time trigger + transfer into the persona)?
- Which account receives bill money? *Suggested: an account id declared in the rule.*

Follow-up **Q10.a**: when exactly does each time key fire, and what scheduled event carries it? *Suggested:* "hourly"
means every local whole hour and "daily" means local 00:00, both carried by one new per-persona scheduled-event kind.

Answer:

Everything here is the dataset's to define, run as callback rules; the engine's job is to provide the keys that
can express it.

- Time triggers gain a monthly key (day of month + HH:MM); without it a monthly utility bill cannot be written.
- Scenario `bills` and the `bill_due` scheduled event are deleted. The rule "unpaid is recorded, nothing is blocked"
  stays.
- Payroll becomes a callback: a time trigger plus a `transfer` into the persona's account, not a run-config field.
- Bill money goes to an account id declared in the rule.

- **Q10.a** — "hourly" means every local whole hour and "daily" means local 00:00, both carried by one new
  per-persona scheduled-event kind.

Author: "I already said the dataset defines it, so aren't all your questions still the dataset's call?" The
engine-side key requirement is the consequence of that ruling. The utility bill is settled as plan (b) — the
dataset declares whether it exists and it runs through a callback.

### Q11 Mail and payroll, once `message_arrival` is deleted

`message_arrival` is not only for direct messages. It also wakes the persona when mail arrives and on payday. An
unread inbox re-triggers it at every waking decision point. The proposal deletes `message_arrival` together with
`send_message`.

- **A.** Delete mail too. Payday credits the account silently.
- **B.** Keep mail. Mail and payday wake the persona through a new `mail_arrival` trigger.
- **C.** Keep mail and payroll, but neither wakes the persona.

Answer:

Mail is not built. The system has no default mail app, only the payday wake-up would have justified it, and that
is the dataset's concern. So `message_arrival` and `mail_arrival` are both deleted, and payday credits the account
silently without waking the persona.

Author: "If it's only for the payday reminder, there's no need. That's the environment dataset's concern — our
system has no default mail app."

### Q12 The merged reminder trigger

Day-plan reminders can wake a sleeping persona; errand reminders cannot. §1.2.14 merges them into one trigger with a
single "can wake sleep" flag.

- **A.** The merged trigger wakes sleep.
- **B.** The merged trigger does not wake sleep, so day-plan reminders stop waking the persona.
- **C.** Do not merge these two, which leaves 13 triggers instead of 12.

Also confirm the trigger names `session_open` (wake_from_sleep + run_start), `reminder_due` and `duty_boundary`.

Follow-up **Q12.a**: if the merged trigger may wake the persona, does an errand reminder that falls due during sleep
also end the sleep? *Suggested: yes.*

Answer:

**B**, extended: the merged trigger does not wake sleep. Sleep is ended only by its own planned end (plus
`run_start`). Any reminder, boundary fallback or duty boundary must not wake sleep. This replaces
`SLEEP_ALLOWED_REASONS`, which shrinks to the sleep's own end plus `run_start`. `alarm_ring` goes with `set_alarm`;
`plan_reminder_due` stays, because a day-plan reminder is the persona's own plan.

Trigger names `session_open` (`wake_from_sleep` + `run_start`), `reminder_due` and `duty_boundary`: confirmed.

- **Q12.a** — Yes.

Author: "Sleep can only be ended by its own planned end time; no reminder, boundary fallback or arriving errand may
wake sleep." — agreed. Note the alarm no longer exists (D17 deleted `set_alarm`), so it is not a wake source.

### Q17 Where exercise is allowed

With free-text location kinds, the indoor/outdoor table goes away. C14 leaves its replacement open.

- **A.** Delete the engine rule. Content binding expresses where exercise happens: exercise content bound to a location
  kind can only be done there, and exercise content bound to an app can be done anywhere.
- **B.** Add a per-location `allows_exercise` flag.

Answer:

**A.** The engine rule is deleted; content binding expresses where exercise happens. `PLACE_KIND_ENVIRONMENTS` is
deleted with it. Content bound to a location kind can only be done there; content bound to an app can be done
anywhere.

### Q18 Assigned work

- **A.** Keep per-person `work_content_ids`. Assigned content is only for its assignee, only at that person's
  workplace, and the `WORK_*` load checks stay.
- **B.** Drop assignment. Any work content bound to the location kind is picked by the recommender, and the `WORK_*`
  checks and the `work_content_completed` event go.

Answer:

**B.** Assignment is dropped; any work content bound to the location kind is picked by the recommender, and the
`WORK_*` checks and the `work_content_completed` event go. This is also forced by Q1: `work_content_ids` lived on
the scenario, which no longer exists.

### Q20 The `way` filter of `query_world`

- **A.** Rename it to `action`, with the seven actions as values.
- **B.** Drop the filter.

Follow-up **Q20.a**: what about the `app` filter, now that rows no longer carry an app? *Suggested: delete it; apps are
found by name.*

Answer:

**A**, plus the `app` filter deleted. The `way` filter is renamed `action`, with the seven actions as values, and
the `app` filter goes: rows no longer carry an app, and apps are found by name.

### Q24 `discovery_result` has no producer

Nothing in the code ever produces `discovery_result`, the same as the two dead triggers the proposal deletes.

- **A.** Keep it, as the proposal lists it (12 triggers).
- **B.** Delete it too (11 triggers).

Answer:

**B.** Delete it. `discovery_result` has no producer, and it disappears with the `WakeReason` enum, which D20
removes entirely.

### Q25 Bedtime reflection after the session-open merge

Today `wake_from_sleep` marks the point where the bedtime-reflection memory is written; `run_start` does not. The
proposal merges the two into one "session open" trigger.

*Suggested:* key reflection to "a sleep activity ended at this decision point", which keeps behaviour unchanged.
Confirm or change.

Answer:

Key the reflection to "a sleep activity ended at this decision point", which keeps behaviour unchanged. The
reflection itself stays a neutral, third-person compaction run as its own model call — it does not adopt the
persona.

### Q36 Category of chat-topic content

U10 says "chat is handled like work", but the social category is deleted.

- **A.** `work_study`.
- **B.** The dataset picks any of the four categories. *(suggested)*
- **C.** A fixed other category.

Answer:

`category` is deleted outright, along with the `ActionCategory` enum. Its two live consumers find new homes on
the content entry:

- **Exertion:** the content carries a tier, not a category. The system fixes the coefficient per tier — idle 1.0,
  light 1.5, moderate 2.0, heavy 5.0 — and the dataset only picks the tier.
- **Push eligibility:** a `pushable: true` boolean on the content.

The push mechanism stays; only its test changes, from a category-plus-manner test to `pushable`.

For the chat-topic content: exertion = light, the same as work.

Author: "I feel category should just be deleted"; "MET can be an attribute directly: the system defines the tiers,
the dataset only picks the tier, and each tier's rate is fixed"; "Keep it, with a pushable boolean."

### Q37 What the engine knows about an `interact` target before a content is picked

One app can host contents of different categories. For example, the fixture's bilibili has both entertainment and
study contents.

- **A.** A load rule: every content in one app, or in one location kind, shares its category and manner.
- **B.** A read-only preview of the pick.
- **C.** No check before the pick. App and location rows show no category, and conflicts are caught when the content
  is actually picked, by the existing check. *(suggested)*

Answer:

**C.** No check before the pick. App and location rows show no category, and a conflict is caught when the content
is actually picked, by the existing check. The dataset designer is responsible for semantically sane bindings.

Author: "Reuse the existing system design; don't get tangled up with specific contents"; "We don't need to
over-design."

### Q38 The 30-minute progress fact (D10, C6)

D10 says a progress record is written every 1800 s ("已进行 X / 共 Y 分钟"). Today live progress shows only as
"还剩…".

- **A.** Write a progress-layer record at each report point, reading "进行到 X / Y 分钟" (X rounded down, Y rounded
  up), and use that phrase instead of "还剩…" everywhere. *(suggested)*
- **B.** Show it as a live phrase only, with no record.
- **C.** Write a record only when the activity is paused.

Answer:

**A.** Write a progress-layer record at each report point, reading "进行到 X / Y 分钟" (X rounded down, Y rounded
up), and use that phrase instead of "还剩…" everywhere. Both X and Y are engine-derived — X from
`ContentProgress.position_seconds`, Y from the content's `duration_seconds` — so the model never writes a duration;
the model only records progress.

### Q39 Social need, once nothing can relieve it

Social need grows +0.04 per hour while awake. Every relief source is deleted with the social subsystem, so the
"social" felt band would saturate after about 25 waking hours. Two other things lose their purpose:

- The day-goal intent 社交 could never advance.
- Five social parameters in the persona mapping table would have nothing to act on.

- **A.** Delete social need, its felt band, the 社交 intent and the five mapping parameters. *(suggested)*
- **B.** Keep social need and name a relief source.
- **C.** Keep it growing with no relief, and keep the intent.

Answer:

**B.** Keep social need and name a relief source: consuming chat-type content relieves it, declared by a
`state_delta` rule in `callbacks.jsonl`. Deleting the interaction is not deleting the social need. The five social
parameters in the persona mapping table move onto that rule.

Author: "Option two. We only deleted the interaction — not the social need. Those are two different things."

### Q42 A once-per-day state change on an action that settles no time

By default, a `once_per: local_day` state change applies to each local date the activity actually covered with
positive settled seconds. A `purchase`, or an `interact` that starts no activity, settles no seconds, so such a rule
would never apply.

- **A.** Reject such rules at load.
- **B.** Apply them once at acceptance, on the acceptance local date.
- **C.** Answer together with Q8.

Answer:

**B**, as amended: a `purchase` settles 5 minutes and a parcel pickup settles 5 minutes, so every action settles
time and a `once_per: local_day` rule always has a local date to apply to. The question dissolves.

Author: "Just make them all settle time. Purchase 5 minutes, parcel pickup 5 minutes."

---

## Part 5 — Recording, export and evaluation

### Q21 Evaluation after `wait` is removed

Please decide:

- `effective_autonomous_decision_rate`: delete it, or redefine it? If redefined, say how.
- The `decision_budget_exhausted` event count: what acceptable range should it have?
- Where does that range live? *Suggested: reported in the quality report next to the count, without gating.*

Answer:

- Six exhausted rounds **stop the run**. The 900 s skip is deleted, and no other degradation is added.
- `effective_autonomous_decision_rate` is deleted; it loses its basis when `wait` goes.
- `decision_budget_exhausted` stops being a report count and becomes a run failure, so there is no acceptable range
  to gate on.

Author: "Stop the run. I think this is unreliable — when something goes wrong, locate it first, and only think about
what to do once it truly can't be solved. There's no real evidence right now that we need this mechanism."

Measured before the change: **zero occurrences across every run under `runs/`**, because `wait` was always
acceptable. Removing `wait` makes this a deliberate behaviour change, and the first occurrences after removal are
signals to investigate rather than a state to tolerate.

### Q23 Dead module

Should the unused `life/retrieval.py` also be deleted? The proposal only says it "can be cleaned up together".

- **A.** Yes.
- **B.** No.

Answer:

**A.** Yes — delete `life/retrieval.py`, together with the same-family dead `LOCATION_KINDS` code.

Author: "If it should be deleted, delete it."

### Q30 Function-app records and callback footprints in the output

§1.2.13 only says the footprint is system-visible, is caused by the action, and that browse and payment records appear
in the export.

- **A.** Use one event kind for all of them, carrying app, action and step (browse, payment or navigation). *(suggested)*
  - Each footprint becomes one `app_touch` row in `app-usage.jsonl`.
  - The old `navigation_app_id` on travel activities is deleted.
  - The payment app stays on orders.
- **B.** Keep a generalised app field on travel and sleep activities, with start/end rows. Footprints go to
  `events.jsonl` only.
- **C.** Record footprints in `events.jsonl` only.

Follow-up **Q30.a**: evidence for other callback effects. What event kind and fields record a transfer as paid or
unpaid? Do state changes and reset-to-cap write any event? *Suggested: each writes one `environment_event` with a
closed field set.*

Answer:

**A.** Use one event kind for all of them, carrying app, action and step (browse, payment or navigation). Each
footprint becomes one `app_touch` row in `app-usage.jsonl`. The old `navigation_app_id` on travel activities is
deleted. The payment app stays on orders.

- **Q30.a** — Each callback effect writes one `environment_event` with a closed field set: a transfer records
  whether it was paid or unpaid, and a state change or a reset-to-cap writes one too.

### Q40 Evaluation families "rest" and "social"

Both lose every WorldMock source, while the comparison reference still has them.

- **A.** Delete both families; the social reference minutes move to the unreferenced bucket.
- **B.** Keep both as always-zero families.
- **C.** Delete only "rest".

Answer:

**A.** Delete both families; the social reference minutes move to the unreferenced bucket, and the report states
why. `wait` is gone, so "rest" has no WorldMock source; the interaction is gone, so "social" has none either.

### Q41 Public evidence value for taxi and bus trips

The blind comparison data (LabelKit) only has walking and transit.

- **A.** Publish `taxi` and `bus` as new values.
- **B.** Map bus to `transit` and taxi to `driving`. *(suggested)*
- **C.** Omit the travel mode for these trips.

Answer:

**B.** Map bus to `transit` and taxi to `driving`. The public vocabulary stays aligned with the blind comparison
data instead of inventing values it cannot produce; the internal records still distinguish the two modes.

---

## Part 6 — Defaults already applied (object to any you disagree with)

The proposal does not rule these details directly. Each follows an existing behaviour or a later ruling. They are
written into the spec as decided; any you reject becomes an open question.

| # | Detail | Default | Basis | Object? |
| --- | --- | --- | --- | --- |
| 1 | Test fixture | New-format environment in a new directory `tests/fixtures/dataset-environment/`; old fixture deleted | Proposal Q13/Q23 "new artificial environment, do not rewrite in place"; AGENTS.md delete obsolete | |
| 2 | BOM and line endings | JSONL, `persona.toml` and `config.toml` reject a UTF-8 BOM or any CR | Today's catalog rejects CR; the repo's JSONL reader rejects BOM | |
| 3 | Order recipient | `recipient_id` deleted | The recipient is always the buyer (proposal Q21, U10, D17) | |
| 4 | Drinks | Stay in persona food-preference coverage, leave calorie-based metrics | D6 | |
| 5 | Random source R7 | No random draw for in-store vs delivery | Clarification 3 and Q1c are later than §1.2.6 | |
| 6 | Content bound to a location kind with no locations | Rejected at load (`CONTENT_BINDING_DANGLING`) | Proposal Q26.3 "consistent with goods" | |
| 7 | Series | Episodes share container, category and manner | Today's same-action invariant | |
| 8 | Entertainment continuation | Only within the activity's category and manner | Today's same-action continuation | |
| 9 | `interact` activity name | "进行<app 或地点>（M月D日 HH:MM 起）" | Today's fallback name | |
| 10 | Persona coverage targets | Drop action names; keep content-app names and location names | D22 deletes actions | |
| 11 | App target names | Resolve like goods and locations (lossless fold; unlisted apps can still be named) | Today's goods/location rule | |
| 12 | Affordance target discovery | Removed for `interact` | D22 | |
| 13 | Exercise gate timing | If the gate stays (Q17), ineligible exercise contents are filtered before the pick | Today the gate runs before content selection | |
| 14 | Idle reconsideration interval | Deleted | Unreachable without `wait` (D24, D25) | |
| 15 | Wake category column | §1.2.12 grouping; "session open" counted as a time boundary | §1.2.14 reuses the six group names | |
| 16 | Viewer reminder label | Errand names shown only when errand events exist at that moment; missing-record hint dropped | Consequence of the reminder merge | |
| 17 | Fuel daily deduction | Once per local date actually driven, including across midnight | Today's semantics; C7 | |
| 18 | State triggers | `below` only; fire on crossing below, at most once per commit; no cascade | The proposal gives only `below` | |
| 19 | Subscribing to wake categories | `event` trigger keys may be the six category names | §1.2.14 | |
| 20 | `transfer` effect | Moves the persona's money to a declared account; unpaid is recorded and never blocks; payment steps move no money | Proposal Q17; today's bill settlement | |
| 21 | Visible hint | Fixed text "余额减少了 X 元" in the progress layer | §1.2.13 | |
| 22 | Reset to cap | At goods receipt | Today's fuel effect point; C7 | |
| 23 | State value range | Clamped between 0 and the cap | C8 "clamp at zero"; cap from the dataset (proposal Q19) | |
| 24 | Reminder and hint pairing | Reminders only on state changes, hints only on footprint chains; mismatch rejected (`CALLBACK_NOTIFY_MISMATCH`) | §1.2.15 | |
| 25 | Callback details | Zero delta rejected; empty steps rejected (`CALLBACK_FOOTPRINT_EMPTY`); step kinds `browse`, `payment`; pick `random`; rules on one trigger apply in `rule_id` order | §1.2.15 example values | |
| 26 | Fixture callback file | Empty; callback tests build their own catalogs | Keeps existing fixture runs unchanged | |
| 27 | Persona fingerprint | `background_story_sha256` keeps its name and card schema 3; its value becomes the `persona.toml` identity; coverage file unchanged | Proposal Q6 item 3 | |
| 28 | Profile commands | `profile compile` / `profile check` take a `persona.toml` path | Proposal Q6 item 2 | |
| 29 | Run input identity | The run manifest freezes the `config.toml` identity; resume and replay reject any difference | Today's "inputs are frozen" guarantee | |
| 30 | "Byte-identical replay" | The normalised signature the day-run baseline already uses | Today's baseline practice | |
| 31 | Evaluation seed | `evaluate` reads the seed from the evaluated run; the review sampling seed folds into the single seed | U8 | |
| 32 | Context assertion | Declared window ≥ 262144, and the model's real context ≥ the declared window | C10 item 3; Q7 "lower-bound declaration" | |
| 33 | Start/stop | Native TOML offset datetime; whole hour judged in its own offset | U11 | |
| 34 | Manual examples | No automatic check; examples marked as full or partial | AGENTS.md documentation-gate scope | |
| 35 | Baseline re-freeze | Existing two-candidate freeze procedure; only baselines that tests actually read | Today's procedure | |
| 36 | Intentional omissions | The resource-engine design states that no stock, no sell-out and no supply cap are intentional | U4 | |

---

## Part 7 — Follow-ups raised by your answers

Everything else from your answers is now being written into the spec. These seven follow-ups come from answers that
open a new decision; the spec carries a `【待裁决 Qn】` placeholder for each until you answer.

### Q43 The unified psychological quantity (from Q2)

You unified `entertainment_need` and `mood_valence`. Today they behave differently:

| | `entertainment_need` | `mood_valence` |
| --- | --- | --- |
| Range | 0 to 1 (higher is worse) | −1 to 1 |
| Over time | grows while awake | decays back to 0 (half-life 45 min, longer for high neuroticism) |
| Raised or lowered by | entertainment content −0.3/h; leisure goods −0.10 | pulses: goal progressed +0.05, service refused −0.05, each discomfort hour −0.1; exercise +0.1/h; daily goods +0.05 |
| Felt band shown to the persona | 不闷 / 有点想放松 / 很想放松 | 很烦 / 有点烦 / 平静 / 挺好 / 很开心 |

- **A — keep the need form.** One quantity from 0 to 1 that grows while awake. Drinks, goods and content relieve it,
  today's mood pulses become signed changes to it, decay is deleted, and there is one felt band. *(suggested)*
- **B — keep the valence form.** One quantity from −1 to 1 that decays to 0. Drinks, goods and content raise it, and
  the awake growth is deleted.

Also give:

- the name;
- the amount for `drink`;
- the amount for `goods` *(suggested: both relieve 0.10)*;
- which felt-band labels stay.

Answer:

### Q44 What replaces `category` for its other consumers (from Q36)

You named two consumers of `category`: exertion becomes a tier and push eligibility becomes `pushable`. Today
`category` also decides six more things:

- **(a) Chaining.** Which contents chain straight into the next one inside the same activity; today only
  entertainment does. *Suggested: contents with `pushable: true` chain.*
- **(b) Relief per hour while consuming.** Entertainment relieves need 0.3/h and exercise raises mood 0.1/h.
  *Suggested: `pushable` contents relieve the Q43 quantity at 0.3/h; no other content changes it.* Or the dataset
  declares it per content.
- **(c) Slots.** Which slots an activity occupies. Today two contents of the same category cannot run at the same
  time (separate entertainment, work_study, exercise and daily_care slots). *Suggested: content activities claim only
  `use_app` (when phone-continuous), so any two may run together unless both use the phone.*
- **(d) Day-plan goals.** Day-plan intents (工作, 学习, 运动, 娱乐, 休息, 家务, 晨间例程…) advance when an activity of
  the matching category runs. *Suggested: delete category-based advancement; meal goals still advance on food
  receipts, and the other goals stay reminders only.*
- **(e) Query and labels.** The `query_world` category filter and the tool-layer category labels for contents.
  *Suggested: delete both (goods keep their goods category).*
- **(f) Evaluation.** Public-evidence categories and behaviour families. *Suggested: derive them from the exertion
  tier, or tell me which mapping you want.*

Answer:

### Q45 How chat content relieves social need (from Q39)

Please decide:

- **(a) What the rule fires on.** Should it fire on `interact` with a filter listing the chat content ids, and
  relieve per settled hour of that content (today: 0.4/h) rather than once per occurrence? This needs a "per hour"
  form of `state_delta`. *(suggested)*
- **(b) What the rule can change.** Can a callback rule change the engine's own `social_need` (an engine body value)?
  Today rules only change dataset-registered states. *Suggested: yes, for a fixed list of engine values.*
- **(c) The five persona mapping parameters.**
  - *Suggested:* `social_need_growth_per_hour` stays a persona parameter, and `conversation_relief_per_hour`
    becomes the rule's value.
  - *Suggested:* `invitation_acceptance`, `reply_tendency` and `present_preference` are deleted, because there are
    no other people.

Answer:

### Q46 Purchases take 5 minutes (from Q42)

*Suggested:*

- A purchase becomes a 5-minute system activity. The 5 minutes is a system constant; the pickup content's 5 minutes
  stays dataset-declared, as in Q16.
- It occupies one slot, and remote and courier orders also occupy the phone (`use_app`).
- Payment, and in-store receipt, still happen at acceptance; the 5 minutes only passes time.
- It cannot be paused.

Confirm or change.

Answer:

### Q47 Which money accounts exist (from Q27, Q32, Q10)

Today the accounts are:

- the persona's account;
- `merchant:*`;
- `employer:*`;
- `transport:service`;
- the anchor `external:scenario`.

Q27 says counterparties are derived and the dataset declares no accounts, but Q10 says bill money goes to "an account
declared in the rule".

*Suggested:*

- Only two accounts exist: the persona's and the anchor.
- Purchases, fares and bills all pay the anchor, and payroll comes from it.
- The counterparty named in a rule is a label written into the transfer's `cause`, not a real account.

Answer:

### Q48 Model identity in `config.toml` (from Q19)

*Suggested:*

- `[model]` holds `provider`, `base_url`, `requested_model`, `max_retries` and `api_key_env`.
- The key is read from the variable that `api_key_env` names, inside `~/.config/worldmock/deepseek.env`. That stays
  the only credential file.
- The 256k check uses an engine table of known models' context lengths; an unknown model fails at load.

Confirm or change.

Answer:

### Q49 Four small confirmations

- **(a) Start location (Q1.b).** You marked "start location is the home" as not yet ruled. Confirm it is the home.
- **(b) Opening hours (Q22).** Confirm the `opens` shape: `{weekly: {mon…sun: ["HH:MM-HH:MM", …]}, exceptions: [{date,
  closed} | {date, hours: [...]}]}`. That is, can an exception give special hours as well as a closure?
- **(c) Sleep (Q12.a).** Under Q12 B only a sleep's own planned end (or `run_start`) ends sleep. I read your "Yes" on
  Q12.a as moot. Confirm.
- **(d) Weather (Q5).** "The environment is the only source of weather." *Suggested:* implement this as the
  environment-layer line plus a system-layer rule that weather comes only from the environment layer; there is no
  filtering of the model's own text.

Answer:
