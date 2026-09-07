# Roadmap after Phase 1

This picks up where `docs/first-steps.md` stops. It assumes the census has
run on the real sample, the open questions are answered, and schema
discovery has produced a canonical field vocabulary, schema v1 as Pydantic
models for the four sampled categories, and a hand-labelled gold set with a
held-out split.

Phases are sequential by default. The census can reorder them; see the
gates near the end.

## Phase 2: landing layer and the first extraction pipeline

Goal: facts from the whole sample in SQL Server, with measured field-level
precision and recall on the four sampled categories.

1. **Parse tier** (`docintel/parse.py`). Docling for PDF, DOCX and images:
   text, layout, tables and reading order, with page and bounding box on
   every element. OCR only for pages the census marked scanned; pick the
   OCR backend from the bench-parse numbers. Email bodies go through a
   deterministic HTML-to-text step that keeps character offsets and marks
   quoted-reply depth. Parse output is cached by content hash, so a
   document forwarded twenty times is parsed once.
2. **Classification** (`docintel/classify.py`). Assign `doc_category` with
   a confidence. Start with a small supervised classifier trained on the
   labelled sample over parsed text, fall back to a zero-shot model call
   below a confidence threshold, and send the rest to review. Emails are
   their own category; their attachments are classified separately.
3. **Extraction** (`docintel/extract.py`). Schema-driven, through one
   interface: an OpenAI-compatible endpoint returning JSON constrained to
   the category's Pydantic schema. NuExtract 3 on vLLM is the first target;
   the interface is what keeps the platform model-agnostic. Long documents
   are chunked on Docling section boundaries with page markers kept. Every
   extracted string is located back in the source text; a value that cannot
   be found verbatim is marked unverified and never lands as trusted. That
   check is the anti-hallucination guard an auditor can defend to a vendor.
4. **Normalization** (`docintel/normalize.py`). The vocabulary's rules as
   code: canonical identifier forms, ISO dates, decimal money with currency,
   percentages as fractions. Raw and normalized values are both kept.
5. **Landing** (`docintel/landing/`). The DDL from `docs/design.md` plus an
   `extraction_run` table for extractor and model versions. Bulk insert,
   append-only, supersede instead of update. Projections for the four
   categories as views first, materialized once they stop changing.
6. **Eval harness** (`docintel/eval/`). Per-field precision, recall and F1
   against the gold set, exact and normalized, per category. Results are
   committed per run so every prompt, schema or model change has a number.
   No extraction change merges without one.

Decisions gated on the open questions: which endpoint serves the model
(local GPU, CPU only, or hosted), which OCR backend, and which SQL Server
partitioning options exist.

Done when: eval numbers exist for the four categories, the full sample's
facts are queryable, and one real auditor question is answered end to end
by a projection query with provenance on every value.

## Phase 3: structured formats and the funnel

Goal: the deterministic tiers exist, and the cost per document per tier is
measured on the real mix.

1. **Spreadsheet path** (`docintel/spreadsheet/`). Formula dependency graph
   across sheets from openpyxl's formula tokenizer, defined names, external
   links and pivot sources. Region detection to find tables, header rows,
   banners and footnotes inside a sheet. Compressed sheet summaries for the
   model to propose cross-sheet relationships, each verified by joining with
   pandas and checking cardinality before it is accepted. Facts land with
   sheet and cell provenance. XLSB and XLS are converted to XLSX with
   LibreOffice headless, or read with pyxlsb2 and xlrd, whichever the census
   volume justifies.
2. **EDI parsers** (`docintel/edi/`). Deterministic mapping for the
   transaction sets actually seen. In retail the usual suspects are X12 810
   invoices, 812 credit and debit adjustments, 820 remittances and 850
   purchase orders, or their EDIFACT equivalents. Write parsers for those
   few, not a general EDI engine.
3. **Dedup at ingest.** A content-hash registry in SQL Server, plus a
   near-duplicate key from normalized text for re-saved PDFs and forwarded
   attachments whose bytes differ but whose content does not.
4. **Fingerprint cache.** After parsing, hash the normalized set of labels
   and table headers; cache which fields were found where; give a repeat
   fingerprint the fast path. A changed vendor layout changes the hash and
   falls back automatically.
5. **Routing** (`docintel/route.py`). The three tiers as one decision
   function, made per page for mixed PDFs.

Done when: the LLM share of the real corpus is measured rather than
estimated, spreadsheet facts carry cell provenance, and cost per document
is known per tier.

## Phase 4: review loop and scale-out

Goal: the pipeline runs unattended at production rate and corrections flow
back into the gold set.

1. **Review queue.** Label Studio, fed by unverified values, low-confidence
   fields and the always-review list from the auditor interviews.
   Corrections land as superseding facts tagged with a human extractor and
   are added to the gold set.
2. **Ingestion connectors** (`docintel/ingest/`). Watched folders, mailbox
   exports, and a PST reader if that is how email arrives. All idempotent
   by content hash.
3. **Workers and queues** (`docintel/workers/`). CPU workers for
   decomposition and parsing, a separate pool for model calls, retries with
   a dead-letter queue, and backpressure so a burst does not fall over.
4. **Observability.** Per-stage timing and error rates, model and prompt
   versions on every fact, a nightly eval on a rolling sample, and drift
   alerts when a vendor's field frequencies change.
5. **Throughput test** on the real hardware allocation, with the capacity
   plan rewritten from measured numbers.

Done when: sustained documents per hour is measured on target hardware, the
review loop closes, and the pipeline runs for a week without intervention.

## Phase 5: remaining categories, linking, and the MCP layer

Goal: an orchestrator agent can answer an auditor's question end to end
with provenance.

1. **Invoices and purchase orders.** The vocabulary already covers most of
   their fields, so this is mostly schema profiles and gold-set labelling.
2. **Accounts payable and receivable.** If they are ERP extracts, a loader
   into the same fact model. They become the transaction side that every
   document links to.
3. **Linking** (`docintel/link.py`). Resolve identifiers to entities:
   vendor, agreement, PO, invoice, claim, statement. Thread emails by
   Message-ID, In-Reply-To and References. Tie attachments to the
   transactions they discuss. This is where the lineage captured since
   Phase 0 finally gets used.
4. **MCP server** (`docintel/mcp/`). Narrow, typed tools over the library
   and the projections: get an artifact and its text, extract fields for a
   schema, query terms for a vendor and date, find related artifacts, fetch
   provenance for a fact, search documents. Parameterized queries only, row
   limits, and a read-only database role.
5. **First audit use cases as tools.** Duplicate payment candidates,
   allowances in an agreement never claimed, terms that differ between an
   agreement and a statement. These are the building blocks of the
   conclusion step that was deferred.

## Gates that reorder the phases

- **Spreadsheets dominate the census.** The spreadsheet path moves ahead of
  LLM extraction; deterministic facts are cheaper and the vocabulary needs
  them anyway.
- **High scanned ratio.** OCR backend selection becomes the first Phase 2
  task, and the bench-parse numbers decide the CPU budget.
- **Email arrives as PST.** The PST reader moves into Phase 1, because
  sampling from mailboxes is not representative without it.
- **No GPU.** Extraction shrinks to a smaller model with a longer latency
  budget, the funnel gets more aggressive, and Phase 3 moves ahead of the
  model work in Phase 2.
- **Nothing may leave the network.** The discovery pass in Phase 1 runs on
  local hardware too, which changes its schedule, not its method.

## How the library grows

    src/docintel/
      decompose.py  sniff.py  probe/  census.py                        Phase 0 (exists)
      parse.py  classify.py  extract.py  normalize.py  landing/  eval/  Phase 2
      spreadsheet/  edi/  route.py                                     Phase 3
      ingest/  workers/                                                Phase 4
      link.py  mcp/                                                    Phase 5

Each phase adds stages to the same library and commands to the same CLI
(`docintel parse`, `extract`, `eval`, `load`). Nothing gets rewritten; the
batch pipeline and the MCP server stay thin callers.

## Rules that hold throughout

- Every fact carries extractor, model version and provenance.
- A schema change is a projection change, never a re-extraction.
- No extraction change without an eval number.
- No document content in logs, reports or tickets.
- Model access goes through one interface so the model can be swapped.
