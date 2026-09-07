from docintel.sniff import sniff
from tests.helpers import PNG_1X1, make_csv, make_email, make_pdf, make_xlsx, make_zip


def test_pdf_by_magic_regardless_of_extension():
    assert sniff(make_pdf(), "whatever.bin").type == "pdf"


def test_xlsx_and_plain_zip_are_distinguished():
    assert sniff(make_xlsx(), "terms.xlsx").type == "xlsx"
    assert sniff(make_zip({"a.txt": b"hi"}), "bundle.zip").type == "zip"
    assert sniff(make_zip({"a.txt": b"hi"}), "bundle.dat").type == "zip"


def test_delimited_text():
    assert sniff(make_csv(), "data.csv").type == "csv"
    assert sniff(make_csv("\t"), "data.tsv").type == "tsv"
    # A .txt that is really delimited is promoted to csv; free text is not.
    assert sniff(make_csv(";"), "export.txt").type == "csv"
    assert sniff(b"Dear vendor,\n\nplease find the statement.\n", "note.txt").type == "text"


def test_email_by_content_even_when_named_msg():
    raw = make_email().as_bytes()
    assert sniff(raw, "forwarded.msg").type == "eml"
    assert sniff(raw, "no-extension").type == "eml"


def test_images_html_rtf_unknown():
    assert sniff(PNG_1X1, "logo.png").type == "image"
    assert sniff(b"<!DOCTYPE html><html><body>x</body></html>", "report.xls").type == "html"
    assert sniff(b"{\\rtf1\\ansi hello}", "body.rtf").type == "rtf"
    assert sniff(b"\x00\x01\x02\xff\xfe" * 50, "blob.bin").type == "unknown"
    assert sniff(b"\x00\x01\x02\xff\xfe" * 50, "old.xls").type == "xls"  # extension fallback


def test_rar_and_7z_are_recognised_but_not_unpacked():
    assert sniff(b"Rar!\x1a\x07\x00" + b"\x00" * 20, "a.rar").type == "rar"
    assert sniff(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 20, "a.7z").type == "7z"
