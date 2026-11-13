# benchmark-runner

Helpers carried over from the per-installation benchmark harness that produced
the historical Scope and Swift results (run records, independent evaluation,
the live monitor and Zed navigation). The unified product imports
`records.py` from here for task-state and benchmark-record bookkeeping
(`src/dwarfstar/preflight.py`, `src/dwarfstar/task_infrastructure.py`).

The historical reports themselves are not in this repository. Their summaries
are indexed in `research/benchmark-index.json` (`dwarfstar bench-index`), and
`docs/BENCHMARKS.md` explains how to read them.

The unified product's own runtime validation lives in
`scripts/validate/production_validation.py`.
