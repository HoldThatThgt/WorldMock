# Source repair evidence staging

The raw source repair artifacts and incomplete diagnostic receipts are staged without changing their bytes.
Frozen source: `43e8d6dd3f2b66f74c19c90776bbef3b5c2f4237`.

The original Dataset report contains 466 distinct failed/error nodes: 453 call failures and 13 setup errors.
The later source-seam diagnostic at `36603196` is a separate population: five completed worker receipts cover
5451 nodes, with 5440 passed calls and 11 failed calls. The remaining worker has no completed receipt; standard
is incomplete and the full body never started. No standard summary was rewritten or manufactured.

The initial 13-file restore/source repair has real targeted evidence of 365 passed tests. The additional four-file
repair has real targeted evidence of 88 passed tests. Actual baseline Git sources, stored AST contracts, current
frozen sources, captured diffs, logs, and collection records were compared without rerunning tests. All declared
old assertions remain intact; the four additional repaired source files match their tested bytes at the frozen commit.

Authorization excludes the third-repository native phone consumer and five cross-platform floating value differences.
No production behavior, specification, formal data, tracked files, VM state, or publication was changed by this task.

macOS and Linux formal standard/full source results and current coverage remain **PENDING** until actual completed
full evidence is available. The parent owns final integration and publication. These artifacts are supporting evidence,
not a complete gate result. `manifest.json` lists every staged relative path, size, SHA-256 and raw-copy provenance.
