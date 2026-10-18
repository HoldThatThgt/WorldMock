# Long runs without losing realism: development specification

> Status: frozen, 2026-10-15. Changing anything here after freezing is a new development record; this specification is not
> rewritten.
> Basis: [the proposal](PROPOSAL-long-run-cost.md), whose "Decisions", "Awaiting the owner" and "Not proposed" tables bind
> every chapter. Where a chapter and the proposal differ, the chapter's "Goal and basis" says why.
> Code baseline: `main` at `0bd2de65883b022802a5e0aab501e93caeca87c7`. "Today" and line references mean that commit.
> Evidence level: code reading plus offline measurements on the 30-day run's data (`d27f2c5a`, issue #15). No model call.

## The owner's rules

The engine exists to make the persona act like a real human. Nothing in this specification may lower simulation quality.

- **What may change.** Every change leaves each model prompt, each decision input and each settled world fact
  byte-identical, or changes only the recorded form with no information lost.
- **Two deliberate exceptions.** Both move behaviour toward an uninterrupted, fault-free run:
  - a resumed or replayed run now sees activities in the order an uninterrupted run shows;
  - a provider stall longer than the whole-call deadline is re-sent instead of becoming a correction turn.
- **What stays.** The 16,000-token thinking budget, every wake, the 30-minute progress reports, pushes, memory
  consolidation, the bedtime reflection, plan_day and the full model evidence stay.
- **What needs confirmation.** No model tool parameter, run-command CLI option or environment dataset field is added,
  removed or reinterpreted. Those need the owner's confirmation and are listed in the proposal.

## Terms

| Term | Meaning |
| --- | --- |
| The run | The 30-day white-collar run on `d27f2c5a` (issue #15), whose data the measurements use |
| Committed world | The world state in the store's `world_state` row after the last successful transaction |
| Staged world | A candidate world built from a copy of the committed world during an advance, a decision round, plan registration or a counterfactual, committed or discarded as a whole |
| Version-advancing commit | A store commit whose state version is the committed version plus one |
| Evidence append | A store write of events that leaves the state and its version unchanged (observation, model exchange, memory update, action request, run failure) |
| Settled entry | An entry of `processed_actions` or `activities` in the committed world; never edited in place |
| Recorded form | How a fact is stored or exported, as opposed to the fact itself |
| Recorded-response run | A run of new code against the model answers of a recorded run, served from 127.0.0.1 (chapter 08) |
| Integration branch | `feat/long-run-cost`, checked out at `.claude/worktrees/lrc` |

## What the persona will notice

Nothing, in an uninterrupted run. Each model request, observation, settlement, memory update and export artifact of a run
that is not interrupted is byte-identical to today's, apart from:
- run identifiers;
- wall-clock fields;
- the recorded-form changes listed below.

| Situation | Today | After |
| --- | --- | --- |
| A network outage of a few minutes | The run fails after about 35 s and has to be resumed by hand; the resumed branch gets a new run id and redraws action-keyed random numbers | The run waits and re-sends the same request for up to 12 hours, then carries on as if nothing happened |
| A provider stall | A call can wait up to 10 minutes and return one token, which becomes a correction the persona then reads | Each attempt is cut after 240 s and the same request is re-sent |
| A resume after a kill | The tool layer may list two running activities in a different order from the live run | The same order as the live run |
| Day 30 of a long run | About 13 s of engine time per decision on main | About 2–3 s |
| Opening a 30-day run in the viewer | Impossible | About 20 s, from the export directory |

## Recorded-form changes

| What | Today | After | Chapter |
| --- | --- | --- | --- |
| `model_response.data.request` | A full copy of the request | `null`, as plan_day evidence already writes | 03 |
| Observation bindings and slow tables in the run database | Inline in every observation | sha256 references into a `parts` table; every read rehydrates; the export writes them inline as today | 03 |
| `processed_actions` in the world state | Every action ever processed | Only the origins of unfinished activities and the current second's results | 02 |
| `activities` in the world state | A JSON object, re-sorted by canonical JSON | An array in insertion order | 02 |
| Checkpoint row body | Canonical JSON text | zlib-compressed canonical JSON; `record_sha256` still over the uncompressed text | 02 |
| Workspace snapshot asset | tar | gzip-compressed tar with a fixed header timestamp | 02 |
| Run manifest and export manifest | No lineage | `lineage`, the ordered segments of a resumed chain | 05 |
| Model-exchange evidence | No transport attempts | `transport_attempts` in the envelope of response and failure events | 05 |
| `status.json` | status, phase, error type | adds `error_message`; a `heartbeat.json` beside it | 05 |
| quality.json | `model_usage.by_purpose` | `model_usage.by_call_class`, a finer split with the same totals; a lineage section | 05, 06 |

The export manifest structure goes from 7 to 8, the world state from 14 to 15, the run manifest from 6 to 7 and
`quality.json` from 8 to 9. `RULES_VERSION` stays at 6, and every dataset package loads unchanged. Details are in
chapter 09.

## Chapters

The owner reads this page. The chapters are for implementers. Each one has the same sections:
- goal and basis;
- behaviour;
- interfaces and files;
- design chapters;
- tests;
- files touched;
- what is not in it.

| Chapter | Content |
| --- | --- |
| [Store commit path](spec/01-store-commits.md) | Write-through cache, evidence appends, the engine-equals-committed check before every version step, single-encode checkpoints, structural sharing with its runtime guard |
| [World state and checkpoint storage](spec/02-state-and-checkpoints.md) | Processed actions kept only while readable, request identity scoped to the open round, ordered activities, compressed checkpoint rows and workspace snapshots, status reading checkpoint ids |
| [Recorded evidence stored once](spec/03-recorded-evidence.md) | The request stored once with an in-process digest check; observation parts by sha256 with one rehydrating decoder |
| [Whole-history paths and live-run safety](spec/04-history-paths.md) | Timelines from one read, streaming export and check, branch by SQL copy, indexed failure and counterfactual lookups, the experience window, plan inputs and storefront reads, the session opening read, short reads in status and tools, the busy timeout |
| [Network failures, restarts and run status](spec/05-network-and-restarts.md) | Parking, the whole-call deadline, frozen metadata on resume, picklable errors, heartbeat, the atomic-write retry, lineage, client closing, ops records |
| [Per-decision host costs and model cost reporting](spec/06-host-costs-and-model-cost.md) | Open activities once, availability once, memory copies, token count reuse, cost by call class, the off-peak guidance |
| [Viewer for long runs](spec/07-viewer.md) | The viewer opens an export: in-page index, byte-range bodies, identity from the manifest |
| [Proof that nothing the persona experiences changed](spec/08-proof-tools.md) | Recorded-response run, comparator, masked complete-day comparison, benchmark kit, backport verification |
| [Versions, design chapters and implementation](spec/09-implementation.md) | Version numbers, the design and manual changes, tracks and file ownership, test gates, re-freezing, merging |

## Not in this specification

Everything in the proposal's "Awaiting the owner" and "Not proposed" tables.

The dataset repository's follow-ups (issues #11 and #12) are separate. Only the check stage's return value (chapter 04)
needs a one-line change in its `tools/execution.py`, coordinated with the session working there.
