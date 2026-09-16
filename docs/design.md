# Design

Reference for every decision made so far and why. Update this when a
decision changes; do not let the code drift away from it silently.

## 0. The invariant

Layouts change per vendor, per retailer, per document kind and over time.
What a document is trying to say does not. Everything below serves that:

- **Meaning is the key.** Every fact lands under a canonical field defined
  by what it means, never by where it was found or which label it sat
  beside. Two documents that say the same thing produce the same fact.
- **Form is a cache.** Fingerprints, cached extraction plans and header
  aliases only accelerate a semantic extraction that already happened.
  They never decide what a value means, and their output passes the same
  verification as a fresh extraction. A cache that disagrees with meaning
  is discarded, never trusted.
- **Identity is intent.** A document's category is what it asserts: terms
  between parties, a claim, a statement of account, an order, a request
  for payment. Not its filename, sender or shape. An email can assert more
  than one thing.
- **Quality is fixed first, cost is minimised under it.** Per-field
  accuracy targets come from the auditors. The cascade picks the cheapest
  tier that meets them, and shadow sampling keeps the cheap tiers honest.
- **Patterns only verify, never locate.** Regular expressions and keyword
  matches appear in verification and normalisation (does this look like a
  date, an identifier, an amount), never to find a value or decide its
  meaning.
- **Downstream consumes meaning.** Facts are queried by canonical field,
  entity, role and period. Nothing downstream knows how any document was
  laid out.

## 1. Problem

Retail recovery auditing checks that the money that moved between a retailer
and its vendors matches the terms they agreed: allowances, rebates, payment
terms, freight, promotional funding. Recoverable amounts (duplicate payments,
missed deductions, unclaimed allowances) are found by tracing a transaction
back through emails, their attachments, nested emails, agreements and
statements, and forming a conclusion about what the terms were and whether
they were honoured.

Today that trace is manual. The platform automates everything up to the
conclusion.

## 2. Scope

**Primary.** Ingest every file type, unpack containers recursively, classify
each document, extract the facts an auditor looks for, and land them in SQL
Server with provenance. An audit question then becomes a query.

**Secondary (deferred).** The conclusion itself. Storage must make it
possible later, which is why join keys and lineage are captured now.

**Inputs.** PDF, XLSX, XLSB, CSV, MSG, EML, email bodies, attachments inside
emails, emails inside emails, EDI. Categories so far: Vendor Submission
Forms, Vendor Payment Agreements, Internal Claims, Statements, Invoices,
Purchase Orders, Accounts Payable, Accounts Receivable. The list is open.
Samples exist for the first four, which are the high-variance ones.

## 2a. Core requirements

Stated by the project owner and treated as the acceptance criteria for the
whole platform:

1. Extract data by looking into the contents of a document.
2. Be independent of where content sits inside the file. Layouts drift,
   columns move, headers get renamed.
3. Be cost efficient. The major criterion at a million files a month.
4. Be self-learning over time.
5. Learn from a sample corpus only, then extend to every incoming document
   of each category.
6. Accept any intake format: image, PDF, spreadsheet, email, embedded
   email, inline attachment.

How each is met, and the learning loop that ties them together, is in
`docs/learning-and-cost.md`.

## 3. Constraints

- No templates, ever. Layouts, sources and document types are open sets.
- No schema exists and cannot be written up front. Extraction must run while
  the schema is still moving.
- Spreadsheets carry data across sheets, with references sometimes in
  formulas and sometimes only in the author's head.
- Volume of roughly a million documents a month, on local VMs, CPU
  preferred.
- Lineage as a feature is deferred; the columns are not.
- Endgame is a model-agnostic tool library exposed over MCP, driven by an
  orchestrator agent.
- Open source preferred. More constraints are expected.

## 4. Architecture

Pipeline stages, each a plain function in the core library:

    decompose -> normalize -> classify -> extract -> link

**Funnel before any model.** Content-hash dedup after decomposition
(forwarded attachments collapse), deterministic parsers for XLSX, XLSB, CSV
and EDI, and a structure-fingerprint cache: hash the set of field labels and
table headers a document exposes after parsing and cache the extraction plan
against it. Recurring vendor formats take a fast path with nobody
maintaining templates; a changed layout changes the fingerprint and falls
back to full extraction.

**Three routing tiers.**

1. Deterministic parsers for structured formats and EDI.
2. Text layer plus schema-driven LLM extraction for native-text PDFs and
   email bodies.
3. Vision model on page images for scans and pages whose text layer is
   garbage.

**Library first, MCP last.** The core has clean function boundaries. The
batch pipeline calls it; the MCP server calls it; neither owns it. MCP tools
are narrow and typed (`get_artifact_text`, `extract_fields`, `query_terms`,
`get_provenance`) with parameterized queries, never raw SQL.

## 5. Storage: EAV landing, projections for reading

The schema is unknown and will move for a year, so the landing layer is one
row per extracted fact rather than one column per field. Adding an attribute
is an INSERT, not a migration. A schema change rewrites a projection and
never triggers re-extraction; at this volume re-extraction is the single most
expensive mistake available.

Design points that separate a workable EAV from the horror stories:

- Typed value columns (`value_text`, `value_number`, `value_date`), exactly
  one populated per row, so range queries use indexes.
- `occurrence_idx` plus dotted `field_path` for repeating groups
  (`line_item.quantity`, occurrence 0..39).
- `provenance` as JSON because its shape differs by format: page and bbox
  for PDF, sheet and cell for XLSX, character offsets for email bodies.
- `superseded_by` instead of UPDATE. Re-extraction inserts new facts and
  points the old ones at them. Append-only, no DELETE, full audit trail.
- Partitioned by `extracted_at` month; retention decided in month two.

Nobody queries the fact table directly. Per-category projections are pivoted
out of it and, once stable, materialized nightly. Those tables are what
auditors and the MCP tools read.

Draft DDL (Phase 2, not yet applied anywhere):

```sql
CREATE TABLE dbo.artifact (
    artifact_id         BIGINT IDENTITY PRIMARY KEY,
    content_sha256      BINARY(32)     NOT NULL,
    source_uri          NVARCHAR(1000),
    parent_artifact_id  BIGINT         NULL REFERENCES dbo.artifact(artifact_id),
    container_path      NVARCHAR(2000),
    mime_type           VARCHAR(200),
    doc_category        VARCHAR(64)    NULL,
    category_confidence DECIMAL(5,4)   NULL,
    page_count          INT,
    ingested_at         DATETIME2      NOT NULL
);

CREATE TABLE dbo.extraction_fact (
    fact_id        BIGINT IDENTITY,
    artifact_id    BIGINT         NOT NULL,
    field_path     VARCHAR(200)   NOT NULL,
    occurrence_idx INT            NOT NULL DEFAULT 0,
    value_text     NVARCHAR(4000) NULL,
    value_number   DECIMAL(28,8)  NULL,
    value_date     DATE           NULL,
    value_type     VARCHAR(20)    NOT NULL,
    confidence     DECIMAL(5,4)   NULL,
    provenance     NVARCHAR(MAX)  NULL,
    extractor      VARCHAR(100)   NOT NULL,
    model_version  VARCHAR(100)   NOT NULL,
    extracted_at   DATETIME2      NOT NULL,
    superseded_by  BIGINT         NULL,
    CONSTRAINT pk_extraction_fact PRIMARY KEY (fact_id)
);

CREATE CLUSTERED INDEX ix_fact_artifact
    ON dbo.extraction_fact (artifact_id, field_path, occurrence_idx);
CREATE NONCLUSTERED INDEX ix_fact_path_text
    ON dbo.extraction_fact (field_path, value_text) INCLUDE (artifact_id)
    WHERE superseded_by IS NULL;
CREATE NONCLUSTERED INDEX ix_fact_path_number
    ON dbo.extraction_fact (field_path, value_number) INCLUDE (artifact_id)
    WHERE superseded_by IS NULL;
```

The `ArtifactRecord` produced by the census today maps onto `dbo.artifact`
one to one, plus the probe fields.

## 6. Schema discovery

Bottom-up and top-down at the same time; the intersection is schema v1.

**Bottom-up.** Stratify the sample by category, format, source and era, and
take 30 to 50 documents per stratum, a few hundred in total. Run open
extraction with no schema, two or three passes per document, keeping the
union: every fact as `(field_name, verbatim_label, value, type, unit,
section_context, source_span)`. Flatten into a field inventory, embed
`proposed_name + verbatim_label`, cluster tightly, then review every cluster
by hand. Output: a canonical field vocabulary with definitions, types,
normalization rules, aliases and per-category frequency.

**Top-down.** Three or four auditors, three questions: which of these do you
use, what do you look for that is missing, which would you not trust a
machine on. The third answer sets confidence thresholds and the
always-review list.

**Promotion rule.** A field in more than 70% of a category is a typed core
column; 20 to 70% is a nullable typed column; under 20% stays in EAV.
Identifiers that cross categories (vendor id, PO number, invoice number,
agreement reference, claim number) are the join keys and get exact
normalization rules before anything else.

**Categories as intents.** During discovery the category list is
re-expressed as what each kind of document asserts, so classification
targets meaning. Emails are allowed several intents at once.

**Qualifiers and references.** A term is often conditional ("applies above
a volume threshold", "net of returns") or points elsewhere ("as per last
year's terms"). The vocabulary carries qualifier fields so conditions are
captured with the value, and reference fields that the linking phase
resolves, instead of dropping either.

**Gold set.** 30 to 50 hand-labelled documents per category against the v1
schema, 20% held out untouched, before the first production prompt is
written. Without it no change can be shown to help or hurt.

## 7. Spreadsheets

Three separate problems, three tools:

1. Explicit references are deterministic. Parse each workbook for values and
   again for formulas; build a cell- and range-level dependency graph across
   sheets from formula references, defined names, external links, table
   definitions and pivot cache sources.
2. Sheet structure needs detection: connected components over non-empty
   cells plus format-change boundaries to find table regions, multi-row
   headers, banners, footnotes and multiple tables per sheet.
3. Implicit references need propose-then-verify. Give the model compressed
   sheet summaries (name, regions, headers, inferred types, cardinality, a
   few sample rows), never the raw grid; let it propose cross-sheet
   relationships; verify each by actually joining and checking cardinality.
   Unverified proposals go to human review.

The model reasons about structure; pandas reads the values.

## 8. Capacity reasoning

Parsing on CPU fits: a couple of mid-sized VMs. LLM extraction on CPU does
not: dozens of VMs against two to four GPU cards for the same work. The
argument to the budget owner is cost, not speed. After dedup and structured
routing, the share of documents that need inference drops far enough that a
single GPU is the likely steady state. Every number is a guess until the
census has run on the real mix; `docintel bench-parse` measures the parse
cost per page on real hardware.

## 9. Tooling

- Parse: Docling (MIT, local, PDF/DOCX/PPTX/XLSX/HTML/images, layout-aware
  JSON, ships an MCP server). CPU tier.
- Extract: NuExtract 3 on vLLM (4B, Apache 2.0, template-driven JSON, fits
  one mid-range GPU). Verify on the model card that extracted text is
  guaranteed verbatim from the input; that guarantee is worth a lot when a
  vendor disputes a claim.
- Review queue: Label Studio.
- Avoid: Datalab lift (commercial self-hosting licence).

## 10. Non-negotiables

- Capture lineage and provenance columns at ingest from day one.
- Never let a schema change trigger re-extraction.
- Build the gold set before the first production prompt.
- Census outputs never contain document content.

## 11. Roadmap

| Phase | Weeks | Deliverable |
|---|---|---|
| Sample census | 1-2 | Format mix, scan rate, duplicate rate, nesting depth |
| Open extraction and inventory | 3-5 | Raw field inventory with frequencies |
| Canonical vocabulary | 6-7 | Vocabulary and field-by-category matrix |
| Schema v1 and gold set | 8-9 | Pydantic schemas, labelled documents, eval harness |
| EAV and first pipeline | 10-12 | Measured accuracy on the four sampled categories |
| Structured formats and funnel | 13-16 | Spreadsheet path, EDI, dedup, fingerprint cache |
| Review UI and scale-out | 17-20 | Sustained throughput on real hardware |
| Remaining categories and MCP | 21+ | Invoices, POs, AP, AR, then the MCP wrapper |

## 12. Open questions

- The remaining constraints not yet shared.
- Sample counts per category and their format mix.
- Whether AP and AR are documents or ERP extracts (assumed extracts).
- Whether a GPU is obtainable.
- Security posture: air-gapped or not; can anything leave the network.
- SQL Server edition and version (partitioning, columnstore).
- How email arrives: PST or mailbox exports, or individual MSG and EML files.
