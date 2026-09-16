# First steps

What happens now, in order, and what each step needs. Steps 1 and 2 run in
parallel; nothing else starts until both are done.

## Step 1: run the census on the sample (this repo, this week)

The tooling for this is built. What you do:

1. Put the sample under `samples/`, one sub-folder per category, original
   filenames, nothing cleaned or renamed. Leave emails and zips as they are;
   they are unpacked automatically. Anything you cannot categorise goes in
   `samples/unsorted/`.
2. Install and run:

       python3 -m venv .venv && . .venv/bin/activate
       pip install -e '.[dev]'
       docintel census samples/

3. Read `census/report.md`. Then read the **Errors** section again. Every
   error is a file the pipeline cannot see into yet, and the census is only
   as good as its coverage.
4. For any file that decomposed strangely, run `docintel tree <file>` and
   look at the lineage it printed.
5. Once Docling is installed on a machine with real PDFs, run
   `docintel bench-parse samples/` to get seconds per page for native and
   scanned documents. That number, times the page counts in the report, is
   the CPU budget for the parse tier.

**Exit criteria.** The report exists, the error list has been triaged (fixed,
or explained and accepted), and these numbers are known: format mix,
scanned ratio, duplicate ratio, page distribution, email nesting depth,
share of workbooks with cross-sheet formulas, and the funnel's LLM-candidate
share.

**What the report cannot tell you.** The sample is a convenience sample. It
is good for discovery and unreliable for accuracy or capacity estimates.
Whatever it shows, assume production has a longer tail: older scans, deeper
email chains, stranger workbooks.

## Step 2: answers only you can provide (in parallel)

Each of these changes the design, which is why they gate everything after
the census:

- **Security posture.** Air-gapped or not, and whether any content can leave
  the network. Decides whether hosted models are an option for the discovery
  pass or whether everything runs on local hardware from day one.
- **GPU availability.** One card is enough for discovery. If none is
  obtainable, the funnel has to be far more aggressive and the extraction
  model smaller; say so early.
- **SQL Server edition and version.** Partitioning and columnstore options
  depend on it.
- **How email arrives.** PST or mailbox exports versus individual MSG and
  EML files decides whether a mailbox exporter is part of decomposition.
- **What AP and AR actually are.** If they are ERP extracts they are a
  loading problem, not a document problem.
- **The remaining constraints** you mentioned.
- **Three or four auditors** willing to sit for the top-down interviews in
  step 4, with dates.

## Step 3: fix what the census exposed

Expected findings, in rough order of likelihood: password-protected PDFs and
zips, .xls and .xlsb workbooks (no formula visibility yet), PST files,
signed or encrypted email, odd encodings in CSV exports, emails saved as
.msg that are really .eml, inline images that are really pasted documents
(check the sizes of inline parts in the census), and protected files whose
password sits in the email body. Each gets a decision: handle it in decomposition,
route it to a manual queue, or accept the gap with a number attached.

## Step 4: schema discovery (Phase 1)

Only after steps 1 to 3:

1. Stratify the sample using `census/artifacts.jsonl` (category, format,
   sender domain, date, scanned or native) and pull 30 to 50 documents per
   stratum.
2. Build the open-extraction script: no schema, every fact as
   `(field_name, verbatim_label, value, type, unit, section_context,
   source_span)`, two or three passes per document, union kept.
3. Produce the field inventory and the clustering, then do the hand review.
4. Run the auditor interviews. Their questions are the fields.
5. Freeze schema v1 as Pydantic models and hand-label the gold set, with
   20% held out.

## What is deliberately not being built yet

- Any LLM call. The census is deterministic on purpose.
- The SQL Server layer. The DDL is drafted in `docs/design.md` and waits for
  schema v1.
- Full parsing (Docling) beyond the parse-time benchmark.
- The spreadsheet dependency graph. The census only counts cross-sheet
  formulas so the effort can be sized.
- Anything MCP.

What comes after these steps is laid out phase by phase in `docs/roadmap.md`.
