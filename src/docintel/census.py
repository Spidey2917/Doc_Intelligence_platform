"""Corpus census: run the decomposer over a folder and summarise what came out.

The report answers the sizing questions every later phase depends on:
format mix, scanned ratio, duplicate rate, page distribution, email
nesting depth, spreadsheet complexity, and how many documents would
actually reach an LLM after the funnel.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from docintel.decompose import decompose_path
from docintel.models import ArtifactRecord

SPREADSHEET_TYPES = ("xlsx", "xlsm", "xlsb", "xls")
STRUCTURED_TYPES = ("xlsx", "xlsm", "xlsb", "xls", "csv", "tsv", "json", "xml")
EMAIL_TYPES = ("eml", "msg")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


def _dist(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(sum(values) / len(values), 2),
        "p50": percentile(values, 0.5),
        "p90": percentile(values, 0.9),
        "max": max(values),
    }


class CensusAggregator:
    def __init__(self) -> None:
        self.roots: set[str] = set()
        self.total = 0
        self.by_kind: Counter = Counter()
        self.by_type: Counter = Counter()
        self.by_category_type: dict[str, Counter] = defaultdict(Counter)
        self.roots_by_category: Counter = Counter()
        self.inline_documents = 0
        self.errors: Counter = Counter()
        self.error_examples: dict[str, list[str]] = defaultdict(list)
        self.unknown_ext: Counter = Counter()
        self.sizes: list[int] = []
        self.hash_counts: Counter = Counter()
        self.hash_roots: dict[str, set[str]] = defaultdict(set)
        self.hash_example: dict[str, str] = {}
        self.hash_type: dict[str, str] = {}

        self.emails = 0
        self.emails_by_type: Counter = Counter()
        self.email_depths: Counter = Counter()
        self.email_children: list[int] = []
        self.inline_total = 0
        self.plain_bodies = 0
        self.html_bodies = 0
        self.sender_domains: Counter = Counter()
        self.dates: list[datetime] = []
        self.zips = 0
        self.zip_children: list[int] = []
        self.archives_unsupported = 0

        self.pdf = 0
        self.pdf_pages: list[int] = []
        self.pdf_scanned = 0
        self.pdf_scanned_pages = 0
        self.pdf_probed = 0
        self.pdf_encrypted = 0
        self.pdf_acroform = 0

        self.ss = 0
        self.ss_by_type: Counter = Counter()
        self.ss_sheets: list[int] = []
        self.ss_rows: list[int] = []
        self.ss_formula_known = 0
        self.ss_with_formulas = 0
        self.ss_cross = 0
        self.ss_external = 0
        self.ss_hidden = 0
        self.ss_vba = 0
        self.ss_pivot = 0
        self.ss_merged = 0

        self.csv = 0
        self.csv_rows: list[int] = []
        self.delims: Counter = Counter()
        self.encodings: Counter = Counter()

    # ------------------------------------------------------------------
    def add(self, r: ArtifactRecord) -> None:
        self.total += 1
        self.by_kind[r.kind] += 1
        if r.depth == 0 and r.root_source_uri not in self.roots:
            self.roots.add(r.root_source_uri)
            self.roots_by_category[r.category_hint or "(none)"] += 1

        if r.error:
            key = r.error.split(":")[0][:80]
            self.errors[key] += 1
            if len(self.error_examples[key]) < 3:
                self.error_examples[key].append(f"{r.root_source_uri} :: {r.container_path}")

        if r.kind == "body":
            if r.detected_type == "html":
                self.html_bodies += 1
            else:
                self.plain_bodies += 1
            return

        if r.kind == "container":
            if r.detected_type in EMAIL_TYPES:
                self.emails += 1
                self.emails_by_type[r.detected_type] += 1
                self.email_depths[r.email_depth] += 1
                if r.child_count is not None:
                    self.email_children.append(r.child_count)
                self.inline_total += r.inline_count or 0
                if r.sender_domain:
                    self.sender_domains[r.sender_domain] += 1
                if r.email_date is not None:
                    self.dates.append(r.email_date)
            elif r.detected_type == "zip":
                self.zips += 1
                if r.child_count is not None:
                    self.zip_children.append(r.child_count)
            else:
                self.archives_unsupported += 1
            return

        # kind == document
        if r.is_inline:
            self.inline_documents += 1
            return
        self.by_type[r.detected_type] += 1
        self.by_category_type[r.category_hint or "(none)"][r.detected_type] += 1
        self.sizes.append(r.size_bytes)
        if r.detected_type == "unknown":
            self.unknown_ext[r.ext or "(no extension)"] += 1
        if r.content_sha256:
            self.hash_counts[r.content_sha256] += 1
            self.hash_roots[r.content_sha256].add(r.root_source_uri)
            self.hash_example.setdefault(r.content_sha256, r.name)
            self.hash_type.setdefault(r.content_sha256, r.detected_type)

        if r.detected_type == "pdf":
            self.pdf += 1
            if r.is_encrypted:
                self.pdf_encrypted += 1
            if r.page_count is not None:
                self.pdf_pages.append(r.page_count)
            if r.is_scanned is not None:
                self.pdf_probed += 1
                if r.is_scanned:
                    self.pdf_scanned += 1
                    self.pdf_scanned_pages += r.page_count or 0
            if r.has_acroform:
                self.pdf_acroform += 1
        elif r.detected_type in SPREADSHEET_TYPES:
            self.ss += 1
            self.ss_by_type[r.detected_type] += 1
            if r.sheet_count is not None:
                self.ss_sheets.append(r.sheet_count)
            if r.max_rows is not None:
                self.ss_rows.append(r.max_rows)
            if r.formula_count is not None:
                self.ss_formula_known += 1
                if r.formula_count > 0:
                    self.ss_with_formulas += 1
                if (r.cross_sheet_formula_count or 0) > 0:
                    self.ss_cross += 1
            if (r.external_link_count or 0) > 0:
                self.ss_external += 1
            if (r.hidden_sheet_count or 0) > 0:
                self.ss_hidden += 1
            if r.has_vba:
                self.ss_vba += 1
            if (r.pivot_cache_count or 0) > 0:
                self.ss_pivot += 1
            if (r.merged_range_count or 0) > 0:
                self.ss_merged += 1
        elif r.detected_type in ("csv", "tsv"):
            self.csv += 1
            if r.row_count is not None:
                self.csv_rows.append(r.row_count)
            if r.delimiter:
                self.delims[{",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(r.delimiter, r.delimiter)] += 1
            if r.encoding:
                self.encodings[r.encoding] += 1

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        documents = sum(self.by_type.values())
        unique = len(self.hash_counts)
        duplicates = documents - unique
        cross_root = sum(1 for h, roots in self.hash_roots.items() if len(roots) > 1)
        top_dupes = [
            {"count": count, "example": self.hash_example.get(h, ""), "roots": len(self.hash_roots[h]), "sha256": h[:12]}
            for h, count in self.hash_counts.most_common(10)
            if count > 1
        ]
        structured_unique = self.unique_of(STRUCTURED_TYPES)
        llm_candidates = max(unique - structured_unique, 0)

        return {
            "headline": {
                "top_level_files": len(self.roots),
                "artifacts": self.total,
                "documents": documents,
                "email_bodies": self.plain_bodies + self.html_bodies,
                "inline_parts": self.inline_documents,
                "emails": self.emails,
                "zips": self.zips,
                "archives_not_unpacked": self.archives_unsupported,
                "records_with_errors": sum(self.errors.values()),
            },
            "roots_by_category": dict(self.roots_by_category.most_common()),
            "format_mix": dict(self.by_type.most_common()),
            "category_by_type": {cat: dict(c.most_common()) for cat, c in sorted(self.by_category_type.items())},
            "email": {
                "count": self.emails,
                "by_type": dict(self.emails_by_type),
                "nesting_depth": {str(k): v for k, v in sorted(self.email_depths.items())},
                "nested_emails": sum(v for k, v in self.email_depths.items() if k >= 2),
                "attachments_per_email": _dist(self.email_children),
                "emails_without_attachments": sum(1 for c in self.email_children if c == 0),
                "inline_parts_total": self.inline_total,
                "plain_bodies": self.plain_bodies,
                "html_bodies": self.html_bodies,
                "date_min": min(self.dates).isoformat() if self.dates else None,
                "date_max": max(self.dates).isoformat() if self.dates else None,
                "distinct_sender_domains": len(self.sender_domains),
                "top_sender_domains": dict(self.sender_domains.most_common(15)),
            },
            "zip": {"count": self.zips, "members_per_zip": _dist(self.zip_children)},
            "pdf": {
                "count": self.pdf,
                "pages_total": sum(self.pdf_pages),
                "pages": _dist(self.pdf_pages),
                "probed": self.pdf_probed,
                "scanned": self.pdf_scanned,
                "scanned_ratio": round(self.pdf_scanned / self.pdf_probed, 3) if self.pdf_probed else None,
                "scanned_pages": self.pdf_scanned_pages,
                "encrypted": self.pdf_encrypted,
                "with_form_fields": self.pdf_acroform,
            },
            "spreadsheet": {
                "count": self.ss,
                "by_type": dict(self.ss_by_type),
                "sheets": _dist(self.ss_sheets),
                "max_rows": _dist(self.ss_rows),
                "formula_stats_known": self.ss_formula_known,
                "with_formulas": self.ss_with_formulas,
                "with_cross_sheet_formulas": self.ss_cross,
                "with_external_links": self.ss_external,
                "with_hidden_sheets": self.ss_hidden,
                "with_vba": self.ss_vba,
                "with_pivot_caches": self.ss_pivot,
                "with_merged_ranges": self.ss_merged,
            },
            "delimited": {
                "count": self.csv,
                "rows": _dist(self.csv_rows),
                "delimiters": dict(self.delims),
                "encodings": dict(self.encodings),
            },
            "duplicates": {
                "documents": documents,
                "unique_by_content": unique,
                "duplicate_documents": duplicates,
                "duplicate_ratio": round(duplicates / documents, 3) if documents else None,
                "hashes_seen_under_multiple_roots": cross_root,
                "top": top_dupes,
            },
            "sizes": {"total_bytes": sum(self.sizes), "bytes": _dist(self.sizes)},
            "errors": [
                {"error": key, "count": count, "examples": self.error_examples[key]}
                for key, count in self.errors.most_common()
            ],
            "unknown_types": dict(self.unknown_ext.most_common(20)),
            "funnel": {
                "documents": documents,
                "after_dedup": unique,
                "structured_unique": structured_unique,
                "llm_candidates": llm_candidates,
                "llm_candidate_ratio": round(llm_candidates / documents, 3) if documents else None,
            },
        }

    def unique_of(self, types: tuple[str, ...]) -> int:
        """Distinct contents whose first-seen type is one of ``types``."""
        return sum(1 for t in self.hash_type.values() if t in types)


def run_census(
    corpus: Path,
    *,
    out_jsonl: Path | None = None,
    category_from_folder: bool = True,
    on_root: Callable[[str], None] | None = None,
) -> dict:
    corpus = Path(corpus)
    agg = CensusAggregator()
    sink = open(out_jsonl, "w", encoding="utf-8") if out_jsonl else None
    try:
        for record in decompose_path(corpus, category_from_folder=category_from_folder):
            agg.add(record)
            if sink is not None:
                sink.write(record.to_jsonl() + "\n")
            if on_root is not None and record.depth == 0:
                on_root(record.root_source_uri)
    finally:
        if sink is not None:
            sink.close()
    return agg.summary()


# ----------------------------------------------------------------------
# Markdown report
# ----------------------------------------------------------------------

def _pct(part: int | float | None, whole: int | float | None) -> str:
    if not whole or part is None:
        return "-"
    return f"{100.0 * part / whole:.1f}%"


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.1f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_fmt(c) for c in row) + " |")
    return "\n".join(out)


def _dist_row(label: str, d: dict) -> list:
    return [label, d.get("n", 0), d.get("mean"), d.get("p50"), d.get("p90"), d.get("max")]


def render_markdown(s: dict, *, title: str = "Corpus census") -> str:
    h = s["headline"]
    parts: list[str] = [f"# {title}", ""]
    parts.append("Generated by `docintel census`. Counts only; no document content is stored in this report.")
    parts.append("")

    parts.append("## Headline")
    parts.append(_table(["Metric", "Value"], [
        ["Top-level files", h["top_level_files"]],
        ["Artifacts after decomposition", h["artifacts"]],
        ["Documents (leaf, non-inline)", h["documents"]],
        ["Email containers", h["emails"]],
        ["Email bodies", h["email_bodies"]],
        ["Inline parts (signature images etc.)", h["inline_parts"]],
        ["Zip containers", h["zips"]],
        ["Archives not unpacked (7z/rar/gz)", h["archives_not_unpacked"]],
        ["Records with errors", h["records_with_errors"]],
    ]))
    parts.append("")

    if s["roots_by_category"]:
        parts.append("## Top-level files by category folder")
        parts.append(_table(["Category", "Files"], [[k, v] for k, v in s["roots_by_category"].items()]))
        parts.append("")

    parts.append("## Format mix (documents)")
    docs = h["documents"]
    parts.append(_table(["Type", "Count", "Share"], [[t, c, _pct(c, docs)] for t, c in s["format_mix"].items()]))
    parts.append("")

    cats = s["category_by_type"]
    if len(cats) > 1 or (cats and "(none)" not in cats):
        types = list(s["format_mix"].keys())[:10]
        parts.append("## Category by document type")
        parts.append(_table(["Category", *types], [[cat, *[counts.get(t, 0) for t in types]] for cat, counts in cats.items()]))
        parts.append("")

    e = s["email"]
    if e["count"]:
        parts.append("## Email")
        parts.append(_table(["Metric", "Value"], [
            ["Email containers", e["count"]],
            ["By format", ", ".join(f"{k}: {v}" for k, v in e["by_type"].items())],
            ["Nesting depth histogram (depth: emails)", ", ".join(f"{k}: {v}" for k, v in e["nesting_depth"].items())],
            ["Emails nested inside other emails", e["nested_emails"]],
            ["Emails without attachments", e["emails_without_attachments"]],
            ["Inline parts total", e["inline_parts_total"]],
            ["Plain bodies / HTML bodies", f"{e['plain_bodies']} / {e['html_bodies']}"],
            ["Date range", f"{e['date_min'] or '-'} to {e['date_max'] or '-'}"],
            ["Distinct sender domains", e["distinct_sender_domains"]],
        ]))
        parts.append("")
        parts.append(_table(["Distribution", "n", "mean", "p50", "p90", "max"], [_dist_row("Attachments per email", e["attachments_per_email"])]))
        parts.append("")
        if e["top_sender_domains"]:
            parts.append(_table(["Sender domain", "Emails"], [[k, v] for k, v in e["top_sender_domains"].items()]))
            parts.append("")

    p = s["pdf"]
    if p["count"]:
        parts.append("## PDF")
        parts.append(_table(["Metric", "Value"], [
            ["PDF documents", p["count"]],
            ["Pages total", p["pages_total"]],
            ["Scanned (no text layer)", f"{p['scanned']} of {p['probed']} probed ({_pct(p['scanned'], p['probed'])})"],
            ["Scanned pages (need OCR)", p["scanned_pages"]],
            ["Encrypted", p["encrypted"]],
            ["With fillable form fields", p["with_form_fields"]],
        ]))
        parts.append("")
        parts.append(_table(["Distribution", "n", "mean", "p50", "p90", "max"], [_dist_row("Pages per PDF", p["pages"])]))
        parts.append("")

    ss = s["spreadsheet"]
    if ss["count"]:
        parts.append("## Spreadsheets")
        parts.append(_table(["Metric", "Value"], [
            ["Workbooks", ss["count"]],
            ["By format", ", ".join(f"{k}: {v}" for k, v in ss["by_type"].items())],
            ["With formulas", f"{ss['with_formulas']} of {ss['formula_stats_known']} with formula stats ({_pct(ss['with_formulas'], ss['formula_stats_known'])})"],
            ["With cross-sheet or external formulas", f"{ss['with_cross_sheet_formulas']} ({_pct(ss['with_cross_sheet_formulas'], ss['formula_stats_known'])})"],
            ["With external workbook links", ss["with_external_links"]],
            ["With hidden sheets", ss["with_hidden_sheets"]],
            ["With VBA macros", ss["with_vba"]],
            ["With pivot caches", ss["with_pivot_caches"]],
            ["With merged ranges (form-like layouts)", ss["with_merged_ranges"]],
        ]))
        parts.append("")
        parts.append(_table(["Distribution", "n", "mean", "p50", "p90", "max"], [
            _dist_row("Sheets per workbook", ss["sheets"]),
            _dist_row("Max rows per workbook", ss["max_rows"]),
        ]))
        if ss["by_type"].get("xlsb") or ss["by_type"].get("xls"):
            parts.append("")
            parts.append("Formula statistics are not available for .xlsb and .xls in this census; those workbooks count toward the format mix only.")
        parts.append("")

    d = s["delimited"]
    if d["count"]:
        parts.append("## Delimited text (CSV / TSV)")
        parts.append(_table(["Metric", "Value"], [
            ["Files", d["count"]],
            ["Delimiters", ", ".join(f"{k}: {v}" for k, v in d["delimiters"].items())],
            ["Encodings", ", ".join(f"{k}: {v}" for k, v in d["encodings"].items())],
        ]))
        parts.append("")
        parts.append(_table(["Distribution", "n", "mean", "p50", "p90", "max"], [_dist_row("Rows per file", d["rows"])]))
        parts.append("")

    dup = s["duplicates"]
    parts.append("## Duplicates (by content hash)")
    parts.append(_table(["Metric", "Value"], [
        ["Documents", dup["documents"]],
        ["Unique by content", dup["unique_by_content"]],
        ["Duplicate documents", f"{dup['duplicate_documents']} ({_pct(dup['duplicate_documents'], dup['documents'])})"],
        ["Contents seen under more than one top-level file", dup["hashes_seen_under_multiple_roots"]],
    ]))
    if dup["top"]:
        parts.append("")
        parts.append(_table(["Copies", "Top-level files", "Example name", "sha256"], [[t["count"], t["roots"], t["example"], t["sha256"]] for t in dup["top"]]))
    parts.append("")

    sz = s["sizes"]
    parts.append("## Sizes")
    parts.append(_table(["Metric", "Value"], [["Total document bytes", sz["total_bytes"]]]))
    parts.append(_table(["Distribution", "n", "mean", "p50", "p90", "max"], [_dist_row("Bytes per document", sz["bytes"])]))
    parts.append("")

    f = s["funnel"]
    parts.append("## Funnel (what would reach an LLM)")
    parts.append(_table(["Stage", "Documents"], [
        ["All documents", f["documents"]],
        ["After content-hash dedup", f["after_dedup"]],
        ["Minus structured formats (deterministic parsers)", f["structured_unique"]],
        ["LLM candidates", f"{f['llm_candidates']} ({_pct(f['llm_candidates'], f['documents'])} of all documents)"],
    ]))
    parts.append("")
    parts.append("Fingerprint caching of recurring layouts is not modelled here; it only lowers the last number further.")
    parts.append("")

    if s["errors"]:
        parts.append("## Errors")
        parts.append(_table(["Error", "Count", "Examples"], [[er["error"], er["count"], "<br>".join(er["examples"])] for er in s["errors"]]))
        parts.append("")

    if s["unknown_types"]:
        parts.append("## Unrecognised types by extension")
        parts.append(_table(["Extension", "Count"], [[k, v] for k, v in s["unknown_types"].items()]))
        parts.append("")

    return "\n".join(parts)


def write_outputs(summary: dict, *, report_path: Path, summary_path: Path, title: str = "Corpus census") -> None:
    report_path = Path(report_path)
    summary_path = Path(summary_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(summary, title=title), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
