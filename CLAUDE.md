# CLAUDE.md

Document intelligence platform for retail recovery audit. Read
`docs/design.md` before changing anything structural; it pins the decisions
and the reasons behind them. `docs/first-steps.md` says what is being built
now and what is deliberately deferred; `docs/roadmap.md` covers the phases
after that, and `docs/learning-and-cost.md` specifies the learning loop and
the cost model that run through all of them.

## Working in this repo

- Python 3.11+, `src/` layout, package `docintel`. Install with
  `pip install -e '.[dev]'`, test with `pytest`.
- The core is a plain Python library. The CLI and, later, the batch pipeline
  and the MCP server all call it. Do not let any of those shape the library.
- Decomposition and probes never raise on bad input. Record the problem in
  `ArtifactRecord.error` and keep going.
- Every artifact record carries lineage (`parent_artifact_id`,
  `container_path`, `depth`, `email_depth`) and a content hash. Never drop
  these fields; later phases depend on them.
- Census outputs contain metadata only: names, hashes, counts, dates, sender
  domains. No subjects, no bodies, no cell values, no extracted text.
- `samples/` and `census/` are gitignored. Never commit client documents or
  anything derived from their content. Tests use synthetic fixtures built in
  `tests/helpers.py`.
- Content type comes from bytes first, extension second (`sniff.py`).
  Vendors send mislabelled files constantly.

## Phase discipline

Phase 0 (now) is decomposition and census only. Do not add LLM calls, a
database layer, or parsing beyond what the census needs until the census has
been run on the real sample and the open questions in `docs/first-steps.md`
are answered.
