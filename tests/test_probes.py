from docintel.models import ArtifactRecord
from docintel.probe import probe
from tests.helpers import make_csv, make_pdf, make_xlsx


def _rec(detected_type: str, name: str = "x") -> ArtifactRecord:
    return ArtifactRecord(artifact_id="t", root_source_uri=name, container_path=name, name=name, detected_type=detected_type)


def test_pdf_text_layer():
    rec = _rec("pdf")
    probe(rec, make_pdf(pages=3))
    assert rec.error is None
    assert rec.page_count == 3
    assert rec.is_scanned is False
    assert rec.text_chars_per_page and rec.text_chars_per_page > 20
    assert rec.has_acroform is False
    assert rec.is_encrypted is False


def test_pdf_without_text_is_scanned():
    rec = _rec("pdf")
    probe(rec, make_pdf(pages=2, text=None))
    assert rec.page_count == 2
    assert rec.is_scanned is True


def test_pdf_form_fields_detected():
    rec = _rec("pdf")
    probe(rec, make_pdf(acroform=True))
    assert rec.has_acroform is True


def test_corrupt_pdf_records_error_instead_of_raising():
    rec = _rec("pdf")
    probe(rec, b"%PDF-1.4\nthis is not a pdf\n")
    assert rec.error and rec.error.startswith("probe failed")


def test_xlsx_structure():
    rec = _rec("xlsx")
    probe(rec, make_xlsx())
    assert rec.error is None
    assert rec.sheet_count == 3
    assert rec.hidden_sheet_count == 1
    assert rec.formula_count == 2
    assert rec.cross_sheet_formula_count == 1
    assert rec.defined_name_count == 1
    assert rec.merged_range_count == 1
    assert rec.external_link_count == 0
    assert rec.has_vba is False
    assert rec.nonempty_cells == 9
    assert rec.max_rows == 4


def test_csv_dialects_and_encodings():
    rec = _rec("csv")
    probe(rec, make_csv())
    assert (rec.delimiter, rec.encoding, rec.row_count, rec.col_count, rec.has_header) == (",", "utf-8", 6, 3, True)

    rec = _rec("csv")
    probe(rec, make_csv(";", encoding="cp1252"))
    assert rec.delimiter == ";"
    assert rec.encoding == "cp1252"

    rec = _rec("csv")
    probe(rec, b"\xef\xbb\xbf" + make_csv())
    assert rec.encoding == "utf-8-sig"

    rec = _rec("tsv")
    probe(rec, make_csv("\t"))
    assert rec.delimiter == "\t"
