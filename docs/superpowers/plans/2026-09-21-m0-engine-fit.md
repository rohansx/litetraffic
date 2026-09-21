# M0 engine-fit implementation plan

**Goal:** make one bounded scenario executable through stock k6 and produce an honest JSON verdict from captured assertion events.

1. Add failing runner tests for a complete pass, a definite assertion failure, missing evidence, and the CLI exit codes.
2. Add one subprocess runner that freezes the run inputs, invokes k6 with native JSON metrics and redirected console output, validates `LT_EVENT` records, and writes `result.json`.
3. Replace the checkout placeholder with a small stateful payment journey driven by the manifest schedule.
4. Run the unit suite, then run the checkout bundle against a local known-good and deliberately wrong server using an official k6 binary.
5. Record measured engine-fit findings and pin only what the experiment proves.

No engine interface, hosted runner, fixture framework, report UI, or generic observer plugin is part of this slice.
