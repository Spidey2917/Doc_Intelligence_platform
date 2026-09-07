"""Command line entry points.

    docintel census <corpus_dir>     census report over a folder of samples
    docintel tree <file>             print the decomposition tree of one file
    docintel bench-parse <dir>       time Docling parsing per page (optional extra)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from docintel import __version__
from docintel.census import run_census, write_outputs
from docintel.decompose import decompose_path

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Document intelligence platform tooling.")
console = Console(stderr=True)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_print_version, is_eager=True, help="Print the version and exit."),
) -> None:
    """Document intelligence platform tooling."""


@app.command()
def census(
    corpus: Path = typer.Argument(..., exists=True, help="Folder of sample files, one sub-folder per category."),
    out: Path = typer.Option(Path("census/artifacts.jsonl"), "--out", help="One JSON line per artifact (metadata only)."),
    report: Path = typer.Option(Path("census/report.md"), "--report", help="Markdown report."),
    summary: Path = typer.Option(Path("census/summary.json"), "--summary", help="Machine-readable summary."),
    category_from_folder: bool = typer.Option(True, help="Use the first-level sub-folder as the category hint."),
    title: str = typer.Option("Corpus census", help="Report title."),
) -> None:
    """Decompose everything under CORPUS and write the census report."""
    total = sum(1 for p in corpus.rglob("*") if p.is_file()) if corpus.is_dir() else 1
    started = time.perf_counter()
    with Progress(
        TextColumn("[bold]census"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("census", total=total)
        result = run_census(
            corpus,
            out_jsonl=out,
            category_from_folder=category_from_folder,
            on_root=lambda _uri: progress.advance(task),
        )
    write_outputs(result, report_path=report, summary_path=summary, title=title)
    elapsed = time.perf_counter() - started
    h = result["headline"]
    console.print(
        f"[green]done[/green] {h['top_level_files']:,} files -> {h['artifacts']:,} artifacts "
        f"({h['documents']:,} documents, {h['emails']:,} emails, {h['records_with_errors']:,} errors) in {elapsed:,.1f}s"
    )
    console.print(f"report:  {report}\nsummary: {summary}\nrecords: {out}")


@app.command()
def tree(
    file: Path = typer.Argument(..., exists=True, help="A single file (email, zip, pdf, workbook)."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON lines instead of a tree."),
) -> None:
    """Show how one file decomposes, with lineage and probe results."""
    for rec in decompose_path(file):
        if as_json:
            typer.echo(rec.to_jsonl())
            continue
        indent = "  " * rec.depth
        facts = []
        if rec.kind == "container":
            facts.append(f"children={rec.child_count}")
            if rec.inline_count:
                facts.append(f"inline={rec.inline_count}")
            if rec.email_date:
                facts.append(f"date={rec.email_date.date()}")
            if rec.sender_domain:
                facts.append(f"from={rec.sender_domain}")
        if rec.page_count is not None:
            facts.append(f"pages={rec.page_count}")
        if rec.is_scanned is not None:
            facts.append("scanned" if rec.is_scanned else "text")
        if rec.has_acroform:
            facts.append("form-fields")
        if rec.sheet_count is not None:
            facts.append(f"sheets={rec.sheet_count}")
        if rec.formula_count:
            facts.append(f"formulas={rec.formula_count}/{rec.cross_sheet_formula_count} cross")
        if rec.row_count is not None:
            facts.append(f"rows={rec.row_count}x{rec.col_count}")
        if rec.is_inline:
            facts.append("inline")
        line = f"{indent}{rec.kind:9} {rec.detected_type:7} {rec.size_bytes:>10,}  {rec.name}"
        if facts:
            line += "  [" + ", ".join(facts) + "]"
        line = escape(line)
        if rec.error:
            line += f"  [red]ERROR {escape(rec.error)}[/red]"
        console.print(line, highlight=False, markup=True)


@app.command("bench-parse")
def bench_parse(
    corpus: Path = typer.Argument(..., exists=True, help="Folder containing PDFs (top level or nested folders)."),
    limit: int = typer.Option(30, help="How many PDFs to time."),
    out: Path = typer.Option(Path("census/bench_parse.json"), "--out"),
) -> None:
    """Time Docling parsing per page, split by native-text and scanned PDFs.

    Needs the optional parse extra:  pip install -e '.[parse]'
    Not exercised by the test suite; run it on a real machine with real PDFs.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        console.print("[red]docling is not installed.[/red] Install with: pip install -e '.[parse]'")
        raise typer.Exit(2)

    from docintel.probe.pdf import probe_pdf
    from docintel.models import ArtifactRecord

    pdfs = sorted(p for p in corpus.rglob("*.pdf") if p.is_file())[:limit]
    if not pdfs:
        console.print("no PDFs found")
        raise typer.Exit(1)

    converter = DocumentConverter()
    rows = []
    for path in pdfs:
        data = path.read_bytes()
        rec = ArtifactRecord(artifact_id="bench", root_source_uri=str(path), container_path=path.name, name=path.name, detected_type="pdf")
        try:
            probe_pdf(rec, data)
        except Exception as exc:  # noqa: BLE001
            rec.error = str(exc)
        started = time.perf_counter()
        status = "ok"
        try:
            converter.convert(str(path))
        except Exception as exc:  # noqa: BLE001
            status = f"failed: {type(exc).__name__}"
        seconds = time.perf_counter() - started
        pages = rec.page_count or 1
        rows.append({"file": str(path), "pages": pages, "scanned": rec.is_scanned, "seconds": round(seconds, 2), "seconds_per_page": round(seconds / pages, 3), "status": status})
        console.print(f"{seconds:7.2f}s  {pages:4d}p  {'scanned' if rec.is_scanned else 'native ':7}  {path.name}  {status if status != 'ok' else ''}")

    def _avg(flag):
        subset = [r for r in rows if r["scanned"] is flag and r["status"] == "ok"]
        total_pages = sum(r["pages"] for r in subset)
        return round(sum(r["seconds"] for r in subset) / total_pages, 3) if total_pages else None

    summary = {"files": len(rows), "seconds_per_page_native": _avg(False), "seconds_per_page_scanned": _avg(True), "rows": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    console.print(f"native: {summary['seconds_per_page_native']} s/page   scanned: {summary['seconds_per_page_scanned']} s/page   -> {out}")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())
