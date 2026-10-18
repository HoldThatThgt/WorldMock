# Owner rulings on the long-run cost specification, 2026-10-15

> Basis: [the specification](SPEC-long-run-cost.md), chapter 09 "Not in this chapter", which listed three questions for
> the owner. The frozen chapters are not rewritten; where they say otherwise, these rulings apply.

| Question | Ruling | Effect |
| --- | --- | --- |
| HTTP 502 (`BadGatewayError`) as a transport failure that parks | No: a 502 ends the run | As chapter 05 and chapter 09 already specify; nothing changes |
| The CI job `complete-day-behaviour` (chapter 08, "Where it runs") | Dropped altogether | `.github/workflows/tests.yml` keeps main's jobs only. The masked complete-day comparison stays a local tool: chapter 09's gates still run it for every chapter, and a pull request that claims no behaviour change attaches its report. `docs/design/01-architecture.md`「工程验证」says so |
| When the main checkout's AS2 vendor copy is rebuilt | After this work is finished and merged | A merge follow-up, never from a worktree |
