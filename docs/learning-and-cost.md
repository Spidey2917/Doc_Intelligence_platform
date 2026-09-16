# Drift, self-learning and cost

The platform's core requirements, as stated by the project owner:

1. Look into the contents of a document and extract data from them.
2. Be independent of where content sits inside a file. Layouts drift,
   columns move, headers get renamed, contents change.
3. Be cost efficient. This is the major criterion at roughly a million
   files a month.
4. Be self-learning over time.
5. Learn from a sample corpus only, then extend to every incoming document
   of that category.
6. Accept any intake format: image, PDF, spreadsheet, email, embedded
   email, inline attachment.

`docs/design.md` meets these through mechanisms that are spread across the
pipeline. This document specifies them as one loop, because that is what
they are: every reviewed document makes the next one cheaper and more
accurate, and cost falls because the system learns.

## Requirement to mechanism

| Requirement | Mechanism | Where |
|---|---|---|
| Extract from content | Parse tier plus schema-driven extraction on the parsed content, with a verbatim check that every value exists in the source | Phase 2 |
| Location independence | Semantic field definitions; header resolution for tables; provenance records location but the extractor never assumes it | Phase 2 and 3 |
| Cost efficiency | Never pay twice (dedup, parse cache), never use a model where code suffices, learn a layout then stop paying for it (plan cache), escalate only on failure (cascade), right-size the model (fine-tuning) | Phase 2 onward, tracked as a KPI |
| Self-learning | Verification outcomes and review corrections feed the alias table, exemplar store, plan cache, gold set, thresholds and periodic fine-tunes, each gated by the eval harness | Phase 2 onward |
| Sample only, then generalise | Canonical vocabulary defined by meaning; novelty-based review sampling; expect a learning curve measured monthly rather than day-one generalisation | Phase 1 onward |
| Any intake format | Recursive decomposition (exists), OCR and VLM tiers for images, document-sized inline images treated as documents | Phase 0 and 2 |

## Location independence

**Prose, forms and letters.** Extraction is defined by what a field means,
not where it sits. The model reads parsed content and returns values with
source spans; provenance stores the page and box where a value was found,
but nothing downstream ever assumes the next document will put it there. A
layout change costs nothing except a cache miss.

**Tables, in spreadsheets, CSVs and PDFs.** Column position is never used.
Observed headers are resolved to canonical fields by a *header resolver*:

1. Normalise the header text (case, whitespace, punctuation, common
   abbreviations).
2. Exact hit in the alias table (`Vendor #`, `Supplier ID`, `VendorNo` all
   map to `vendor_id`).
3. Otherwise nearest alias by embedding similarity above a threshold.
4. Otherwise one model call proposing a mapping for the novel header,
   given the canonical vocabulary and a few sample values.
5. Verify by value shape: a column mapped to an identifier should hold
   identifier-like values, a date column should parse as dates, a
   percentage column should sit in a plausible range. A proposal that
   fails verification goes to review instead of the cache.
6. Cache the mapping keyed by normalised header and, where it matters,
   sender domain or vendor.

A mapping is learned once per new header string, not once per document.
That is the cost saver: a renamed column costs one small model call and one
verification, then nothing.

**Multi-row and merged headers, banners, footnotes.** Region detection in
the spreadsheet path finds the real header rows before resolution runs.

**Repeating groups.** Each row lands as an occurrence of a repeating group
under canonical field paths, so a table with reordered columns produces
identical facts.

## The extraction cascade

The cheapest extractor that passes verification wins. Escalation happens
only on failure.

| Tier | Extractor | Cost | When |
|---|---|---|---|
| 0 | Content-hash or near-duplicate hit | none | Same document seen before |
| 1 | Deterministic parser | CPU, negligible | Spreadsheets, CSV, EDI |
| 2 | Cached extraction plan for a known fingerprint | CPU plus at most a small verification call | Layout seen before |
| 3 | Small extraction model on parsed text | one model call | Everything else with a text layer |
| 4 | OCR then tier 3, or a vision model on page images | the expensive tier | Scanned pages, or OCR quality too poor |

**Verification gates** decide whether a tier's output is accepted:

- Every extracted string is located in the source text (normalised, fuzzy
  within a tight distance). Not found means unverified, never trusted.
- Type and shape checks from the vocabulary: dates parse, identifiers match
  their pattern, money is decimal with a currency.
- Cross-field consistency where it exists: line items sum to the total,
  effective date precedes expiry, percentages sit in range.
- Schema validation against the category's Pydantic model.

A tier-2 plan whose output fails verification escalates to tier 3 and the
plan is refreshed from the tier-3 result. A tier-3 result that fails goes
to tier 4 or to review. The escalation rate per tier is a tracked metric;
rising escalation is the first sign of drift.

**Section selection.** Long agreements do not go to the model whole. The
parse tier's headings and a small retrieval step pick the sections likely
to hold each field group, so a sixty-page agreement costs a few pages of
tokens. Provenance still points at the real page.

## Keeping cheap tiers honest: shadow sampling

Cost is minimised under a fixed quality target, never instead of one. The
cascade's acceptance criterion is the same semantic verification at every
tier, and on top of that a fraction of tier-1 and tier-2 outputs also run
through tier 3 in the background. The disagreement rate is the cheap
tier's true error rate for that vendor or fingerprint. The sampling rate
starts high for a new plan or alias and decays as agreement holds; it
rises again when a disagreement appears. A cheap tier whose error rate
exceeds the field's target is demoted for that vendor until it is
relearned. The rule the whole cascade obeys: caches accelerate, they
never decide.

Facts also carry the role of the document that asserted them (agreement,
statement, claim, invoice, email). Downstream reconciliation is a
comparison of what different documents say about the same entity and
period, which is what an audit is.

## The learning loop

Inputs:

- Verification outcomes on every document (pass, fail, escalated).
- Review corrections from Label Studio, landing as superseding facts
  tagged with a human extractor.
- Novelty signals: new fingerprints, new headers, new sender domains.

Outputs, each updated automatically but promoted only through a gate:

| Asset | Updated from | Gate |
|---|---|---|
| Alias table | Verified header proposals, corrections | Value-shape verification; conflicts go to review |
| Exemplar store | Verified extractions per category and vendor | Retrieval-augmented extraction picks nearest exemplars at inference; only verified or human-corrected ones qualify |
| Plan cache | First successful tier-3 extraction per fingerprint | Invalidated when its verification failure rate rises or a correction lands on a plan-extracted document |
| Gold set | Every human correction | Held-out split never used for anything but evaluation |
| Extraction model | Periodic fine-tune on accumulated verified labels | Must beat the current model on the held-out set, then canary by vendor or category before full rollout |
| Confidence thresholds | Review outcomes per field | Calibrated to a target error rate, so review load falls as accuracy rises |
| Classifier | New labelled documents | Same held-out gate |

Rollback is always supersession: a bad model version's facts are
superseded by re-extraction with the previous one; nothing is deleted.

**Active learning.** Review capacity is spent where it teaches the most:
novel fingerprints, novel headers, low-confidence fields, disagreement
between two cheap extractors, and a small random slice to keep the error
estimate honest. Not on documents the system already handles well.

## Drift detection

Two kinds of drift, two responses.

**Layout drift.** A vendor changes its invoice or statement format. The
fingerprint changes, the plan cache misses, tier 3 runs, a new plan is
learned. No one maintains anything. If the *same* fingerprint starts
failing verification, the plan is invalidated and relearned. Signals
tracked per vendor and category: fingerprint novelty rate, header novelty
rate, plan escalation rate, verification failure rate.

**Content and schema drift.** The terms themselves change shape: a new
allowance type, a field that used to be present disappears, a new document
kind appears in a category. Signals: field presence rates per vendor and
category month over month, and a monthly discovery-mode run (the Phase 1
open extraction, no schema) over a small sample. Frequent labels that are
not in the vocabulary become proposed fields for human approval. The EAV
layer already stores them either way, so nothing is lost while the
vocabulary catches up.

## Cost model

Principles, in priority order:

1. Never pay twice. Exact dedup by content hash, near-dedup by normalised
   text, parse output cached by hash.
2. Never use a model where code suffices. Structured formats, EDI, header
   resolution hits, cached plans.
3. Learn a layout, then stop paying for it. The plan cache is the largest
   single lever over time.
4. Escalate only on failure. The cascade keeps the average cost near the
   cheapest tier that works.
5. Send less to the model. Section selection, page-level OCR, JSON-only
   output, prefix caching of the schema prompt on the serving side.
6. Right-size the model. A small model fine-tuned on this domain's verified
   labels beats a large general model on accuracy and costs a fraction to
   run. This is where learning and cost meet.
7. Count human minutes. Review is a cost; calibrated thresholds and active
   learning minimise it.

**The KPI.** Cost per document, blended and per tier, reported by the eval
harness alongside accuracy from the first extraction run onward. Every
change to a prompt, model, threshold or cache is measured on both axes.
Expected shape over time: the tier-3 and tier-4 share falls as the plan
cache and alias table grow, the review share falls as thresholds
recalibrate, and the blended cost trends toward the deterministic tiers.

## Failure modes to design against

- **Confidently wrong learning.** Nothing self-updates without a gate.
  Models pass the held-out set, plans and aliases pass verification,
  rollouts canary first.
- **Cache poisoning.** A wrong plan applied to thousands of documents.
  Plans carry verification statistics and are invalidated on rising
  failure or on any correction; fingerprints include enough context
  (sender domain or vendor where available) that two vendors with similar
  headers do not share a plan.
- **Silent drift.** The signals above are dashboards and alerts, not
  logs nobody reads.
- **Over-review.** Thresholds are calibrated to a target error rate per
  field, and the always-review list from the auditor interviews stays
  small on purpose.
- **Inline images that are documents.** A statement screenshot pasted into
  an email body carries a Content-ID like a signature logo does. Image
  dimensions decide: document-sized inline images go through the OCR tier
  as documents. Small ones stay inline.
- **Passwords in the email.** Protected PDFs and zips often ship with the
  password in the body or a sibling email. A candidate-password step from
  the body text recovers many of them before they reach a manual queue.

## Where this lands in the roadmap

- Phase 2: verbatim and shape verification, header resolver with the alias
  table, exemplar store, section selection, cost per document in the eval
  harness.
- Phase 3: plan cache with invalidation, the cascade as the routing
  function, near-duplicate keys.
- Phase 4: corrections feeding every asset above, drift dashboards and
  alerts, active-learning review sampling, threshold calibration.
- Phase 5: the first fine-tuning cycle once verified labels reach the
  thousands, with canary rollout by vendor.
