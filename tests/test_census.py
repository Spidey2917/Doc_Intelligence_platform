import json
from pathlib import Path

from typer.testing import CliRunner

from docintel.census import percentile, render_markdown, run_census, write_outputs
from docintel.cli import app
from tests.helpers import make_csv, make_email, make_pdf, make_xlsx, make_zip


def _corpus(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    agreement = make_pdf(pages=4)
    (root / "vendor_payment_agreements").mkdir()
    (root / "vendor_payment_agreements" / "acme-2024.pdf").write_bytes(agreement)
    (root / "statements").mkdir()
    (root / "statements" / "q1.xlsx").write_bytes(make_xlsx())
    (root / "statements" / "copy-of-agreement.pdf").write_bytes(agreement)  # duplicate under another root
    (root / "internal_claims").mkdir()
    thread = make_email(attachments={"claim.pdf": make_pdf(pages=1, text=None), "detail.csv": make_csv(";")}, inline_png=True)
    (root / "internal_claims" / "thread.eml").write_bytes(thread.as_bytes())
    (root / "unsorted").mkdir()
    (root / "unsorted" / "broken.zip").write_bytes(b"PK\x03\x04not really a zip")
    (root / "unsorted" / "bundle.zip").write_bytes(make_zip({"agreement-again.pdf": agreement, "notes.txt": b"free text\n"}))
    return root


def test_percentile():
    assert percentile([], 0.5) is None
    assert percentile([5], 0.9) == 5
    assert percentile([1, 2, 3, 4], 0.5) == 2.5


def test_run_census_summary_and_jsonl(tmp_path: Path):
    corpus = _corpus(tmp_path / "corpus")
    out = tmp_path / "artifacts.jsonl"
    roots = []
    summary = run_census(corpus, out_jsonl=out, on_root=roots.append)

    h = summary["headline"]
    assert h["top_level_files"] == 6
    assert sorted(roots) == sorted({"internal_claims/thread.eml", "statements/copy-of-agreement.pdf", "statements/q1.xlsx", "unsorted/broken.zip", "unsorted/bundle.zip", "vendor_payment_agreements/acme-2024.pdf"})
    assert h["emails"] == 1 and h["zips"] == 2 and h["inline_parts"] == 1 and h["email_bodies"] == 2
    assert h["records_with_errors"] == 1
    assert summary["format_mix"]["pdf"] == 4  # agreement x3 + claim.pdf; the inline png is not a document
    assert summary["roots_by_category"] == {"statements": 2, "unsorted": 2, "vendor_payment_agreements": 1, "internal_claims": 1}
    assert summary["category_by_type"]["internal_claims"] == {"pdf": 1, "csv": 1}

    dup = summary["duplicates"]
    assert dup["documents"] == 7
    assert dup["duplicate_documents"] == 2
    assert dup["hashes_seen_under_multiple_roots"] == 1
    assert dup["top"][0]["count"] == 3 and dup["top"][0]["roots"] == 3

    assert summary["pdf"]["scanned"] == 1 and summary["pdf"]["pages_total"] == 13
    assert summary["spreadsheet"]["with_cross_sheet_formulas"] == 1
    assert summary["delimited"]["delimiters"] == {"semicolon": 1}
    assert summary["email"]["nesting_depth"] == {"1": 1}
    assert summary["email"]["top_sender_domains"] == {"retailer.example": 1}
    assert summary["errors"][0]["error"].startswith("zip open failed")
    assert summary["funnel"]["after_dedup"] == 5
    assert summary["funnel"]["structured_unique"] == 2  # q1.xlsx and detail.csv
    assert summary["funnel"]["llm_candidates"] == 3  # agreement, claim.pdf, notes.txt

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == h["artifacts"]
    parsed = [json.loads(line) for line in lines]
    assert all("artifact_id" in p and "container_path" in p for p in parsed)
    assert not any("subject" in p for p in parsed)  # no content, no subjects

    md = render_markdown(summary)
    for heading in ("## Headline", "## Format mix", "## Email", "## PDF", "## Spreadsheets", "## Duplicates", "## Funnel", "## Errors"):
        assert heading in md
    write_outputs(summary, report_path=tmp_path / "r" / "report.md", summary_path=tmp_path / "r" / "summary.json")
    assert (tmp_path / "r" / "report.md").exists() and json.loads((tmp_path / "r" / "summary.json").read_text())


def test_cli_census_and_tree(tmp_path: Path):
    corpus = _corpus(tmp_path / "corpus")
    runner = CliRunner()
    result = runner.invoke(app, ["census", str(corpus), "--out", str(tmp_path / "a.jsonl"), "--report", str(tmp_path / "report.md"), "--summary", str(tmp_path / "summary.json")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "report.md").read_text().startswith("# Corpus census")

    result = runner.invoke(app, ["tree", str(corpus / "internal_claims" / "thread.eml")])
    assert result.exit_code == 0, result.output
    assert "thread.eml" in result.output and "claim.pdf" in result.output

    result = runner.invoke(app, ["tree", "--json", str(corpus / "unsorted" / "bundle.zip")])
    assert result.exit_code == 0
    assert all(json.loads(line) for line in result.output.strip().splitlines())
