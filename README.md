# Document Intelligence Platform (retail recovery audit)

Turns the pile of PDFs, spreadsheets, emails, and emails-inside-emails that a
retail recovery audit runs on into facts in SQL Server that can be queried,
so an auditor's question becomes a query instead of a week of tracing.

The design is pinned in [docs/design.md](docs/design.md). The plan for what
happens next is in [docs/first-steps.md](docs/first-steps.md).

## What exists today (Phase 0: census)

- `docintel census <folder>`: recursively unpacks every file (zip, .eml, .msg,
  nested emails, attachments), records lineage and content hashes, probes each
  document lightly, and writes a census report. No LLM, no database.
- `docintel tree <file>`: shows how one file decomposes.
- `docintel bench-parse <folder>`: times Docling parsing per page (optional extra).

The census report answers the sizing questions everything else depends on:
format mix, scanned ratio, duplicate rate, page distribution, email nesting
depth, spreadsheet complexity, and how much of the corpus would ever reach a
model.

## Install

    python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
    pip install -e '.[dev]'
    pytest

## Run the census

Put the sample corpus under `samples/`, one sub-folder per category (see
`samples/README.md`), then:

    docintel census samples/

Outputs land in `census/`: `report.md`, `summary.json`, and `artifacts.jsonl`
with one metadata-only line per artifact. Nothing under `samples/` or
`census/` is ever committed.

## Layout

    src/docintel/
      sniff.py        content-type detection from bytes
      decompose.py    recursive container unpacking with lineage
      probe/          light per-format probes (pdf, spreadsheet, delimited)
      census.py       aggregation and the markdown report
      cli.py          command line
      models.py       ArtifactRecord
    tests/            synthetic fixtures only, never client documents
    docs/             design decisions and the roadmap
