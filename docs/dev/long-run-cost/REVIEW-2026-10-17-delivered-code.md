# Independent review of the delivered code, 2026-10-17

> Basis: [the specification](SPEC-long-run-cost.md) and [the owner rulings](RULING-2026-10-15-owner.md). Reviewed head
> `a67a28f7`. The chapters are not rewritten; where this record and a chapter differ, this record describes the code.

## Scope

The commits after `d8286f61` (the end of chapter 05, chapter 06, the merges, the viewer calendar, the re-freeze and the
comparator corrections) had not been reviewed independently. Six reviewers each took one area: chapter 05, chapter 06,
the proof tools, the viewer, the owner rules over the whole branch, and integration between chapters. Every finding was
then given to three reviewers who tried to refute it; a finding stands only when at least two could not.

No finding touched the owner rules. Tool definitions, request models, the run-command parser and every dataset loader
are unchanged against `main`, and `life_prompt_sha256` and `life_tool_schema_sha256` are equal on both commits.

## Findings and corrections

| Finding | Effect | Correction |
| --- | --- | --- |
| A provider's `Retry-After` is never read. On the locked LiteLLM 1.89.2 Router path a `RateLimitError` carries a stand-in response without headers; the provider's headers are on `litellm_response_headers`. Chapter 05 assumed `error.response.headers`, and its unit tests built errors in a shape the Router never produces | A 429 waits by the backoff instead of `Retry-After`, and a `Retry-After` longer than the 12-hour park budget keeps re-sending for 12 hours instead of ending the call | `_retry_after_seconds` reads `litellm_response_headers`; the tests take their errors from the real Router against a local server, including 30 s and 50,000 s waits. The patch series and the locked AS2 source identity are regenerated |
| The plan_day call's transport attempts reach its evidence untested: the two tests chapter 05 names were missing | Dropping the attempts at either `_generate_day_plan` call site passed the whole suite | Both tests added, with a registered mutation for each call site |
| On days when daylight saving time skips local midnight (Beirut, Cairo), the viewer's activity calendar started the day an hour early | The previous day's last hour moved onto the next day's page, or became unreachable on the last day of a run | `midnight(day)` returns the first instant of the local date, found by bisection when midnight does not exist |

The complete-day baselines were re-frozen for the new AS2 identity. That re-freeze also restores the hash of the
repository's LF `.python-version` in their generation record; the previous freeze had recorded a CRLF checkout's hash.

## Not changed

A drop into the viewer needs `getAsFileSystemHandle`, which only Chromium browsers offer. Two of three reviewers judged
this the intended Chrome path that the owner checked, so it stays.
