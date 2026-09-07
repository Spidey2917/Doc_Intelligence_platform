import glob
from pathlib import Path

import pytest

from docintel.decompose import MAX_DEPTH, decompose_bytes, decompose_path
from tests.helpers import make_csv, make_email, make_pdf, make_xlsx, make_zip


def _index(records):
    return {r.container_path: r for r in records}


def _build_nested_email():
    deepest = make_email(subject="original", html=False)
    middle = make_email(subject="FW: original", attachments={"data.csv": make_csv()}, nested=[(deepest, None)])
    outer = make_email(
        attachments={
            "invoice.pdf": make_pdf(pages=2),
            "terms.xlsx": make_xlsx(),
            "bundle.zip": make_zip({"scan.pdf": make_pdf(pages=1, text=None), "inner/data.csv": make_csv()}),
        },
        nested=[(middle, "FW original.eml")],
        inline_png=True,
    )
    return outer.as_bytes()


def test_nested_email_decomposes_with_lineage():
    records = list(decompose_bytes(_build_nested_email(), "thread.eml", root_source_uri="claims/thread.eml", category_hint="claims"))
    by_path = _index(records)

    outer = by_path["thread.eml"]
    assert outer.kind == "container" and outer.detected_type == "eml"
    assert (outer.depth, outer.email_depth) == (0, 1)
    assert outer.child_count == 4  # pdf, xlsx, zip, nested email; the inline logo is not counted
    assert outer.inline_count == 1
    assert outer.has_plain_body and outer.has_html_body
    assert outer.sender_domain == "retailer.example"
    assert outer.email_date is not None and outer.email_date.year == 2024
    assert outer.message_id and outer.message_id.startswith("<")

    bodies = [r for r in records if r.kind == "body" and r.parent_artifact_id == outer.artifact_id]
    assert sorted(r.detected_type for r in bodies) == ["html", "text"]
    assert all(r.depth == 1 and r.email_depth == 1 for r in bodies)

    pdf = next(r for r in records if r.name == "invoice.pdf")
    assert (pdf.kind, pdf.depth, pdf.email_depth, pdf.page_count, pdf.is_scanned) == ("document", 1, 1, 2, False)
    assert pdf.parent_artifact_id == outer.artifact_id
    assert pdf.category_hint == "claims"

    xlsx = next(r for r in records if r.name == "terms.xlsx")
    assert xlsx.sheet_count == 3 and xlsx.cross_sheet_formula_count == 1

    logo = next(r for r in records if r.name == "logo.png")
    assert logo.is_inline is True and logo.detected_type == "image"

    bundle = next(r for r in records if r.name == "bundle.zip")
    assert bundle.kind == "container" and bundle.child_count == 2
    scan = next(r for r in records if r.name == "scan.pdf")
    assert (scan.depth, scan.email_depth, scan.is_scanned) == (2, 1, True)
    assert scan.parent_artifact_id == bundle.artifact_id
    assert "zip[0]:scan.pdf" in scan.container_path
    zipped_csv = next(r for r in records if r.container_path.endswith("zip[1]:inner/data.csv"))
    assert zipped_csv.name == "data.csv" and zipped_csv.row_count == 6

    middle = next(r for r in records if r.name == "FW original.eml")
    assert (middle.kind, middle.depth, middle.email_depth) == ("container", 1, 2)
    assert middle.child_count == 2  # data.csv and the deepest email
    middle_csv = next(r for r in records if r.parent_artifact_id == middle.artifact_id and r.name == "data.csv")
    assert (middle_csv.depth, middle_csv.email_depth) == (2, 2)

    deepest = next(r for r in records if r.parent_artifact_id == middle.artifact_id and r.detected_type == "eml")
    assert deepest.name.startswith("embedded-") and deepest.name.endswith(".eml")
    assert (deepest.depth, deepest.email_depth, deepest.child_count) == (2, 3, 0)
    assert deepest.has_plain_body and not deepest.has_html_body


def test_ids_unique_parents_first_and_resolvable():
    records = list(decompose_bytes(_build_nested_email(), "thread.eml", root_source_uri="thread.eml"))
    ids = [r.artifact_id for r in records]
    assert len(ids) == len(set(ids))
    seen = set()
    for r in records:
        if r.parent_artifact_id is not None:
            assert r.parent_artifact_id in seen, f"child before parent: {r.container_path}"
        seen.add(r.artifact_id)
    assert all(r.content_sha256 for r in records)
    assert not any(r.error for r in records), [r.error for r in records if r.error]


def test_ids_are_stable_across_runs():
    a = [r.artifact_id for r in decompose_bytes(_build_nested_email(), "thread.eml", root_source_uri="x/thread.eml")]
    b = [r.artifact_id for r in decompose_bytes(_build_nested_email(), "thread.eml", root_source_uri="x/thread.eml")]
    assert a == b


def test_email_saved_with_msg_extension_but_eml_content():
    raw = make_email(attachments={"a.pdf": make_pdf()}).as_bytes()
    records = list(decompose_bytes(raw, "forwarded.msg", root_source_uri="forwarded.msg"))
    assert records[0].detected_type == "eml" and records[0].kind == "container"
    assert any(r.name == "a.pdf" for r in records)


def test_bad_attachment_does_not_lose_siblings():
    raw = make_email(attachments={"broken.pdf": b"%PDF-1.4\ngarbage", "good.csv": make_csv(), "broken.zip": b"PK\x03\x04junkjunkjunk"}).as_bytes()
    records = list(decompose_bytes(raw, "m.eml", root_source_uri="m.eml"))
    broken_pdf = next(r for r in records if r.name == "broken.pdf")
    broken_zip = next(r for r in records if r.name == "broken.zip")
    good = next(r for r in records if r.name == "good.csv")
    assert broken_pdf.error and broken_pdf.error.startswith("probe failed")
    assert broken_zip.error and broken_zip.error.startswith("zip open failed")
    assert good.error is None and good.row_count == 6


def test_depth_limit_stops_zip_bombs_gracefully():
    payload = make_pdf()
    for i in range(MAX_DEPTH + 3):
        payload = make_zip({f"level{i}.zip" if i else "doc.pdf": payload})
    records = list(decompose_bytes(payload, "deep.zip", root_source_uri="deep.zip"))
    assert any(r.error and "depth" in r.error for r in records)
    assert max(r.depth for r in records) == MAX_DEPTH + 1


def test_decompose_path_uses_folder_as_category(tmp_path: Path):
    (tmp_path / "Vendor Payment-Agreements").mkdir()
    (tmp_path / "Vendor Payment-Agreements" / "a.pdf").write_bytes(make_pdf())
    (tmp_path / "loose.csv").write_bytes(make_csv())
    records = list(decompose_path(tmp_path))
    by_name = {r.name: r for r in records}
    assert by_name["a.pdf"].category_hint == "vendor_payment_agreements"
    assert by_name["a.pdf"].root_source_uri == "Vendor Payment-Agreements/a.pdf"
    assert by_name["loose.csv"].category_hint is None
    assert all(r.depth == 0 for r in records)


@pytest.mark.parametrize("path", sorted(glob.glob("tests/fixtures/*.msg")))
def test_real_msg_fixture_if_present(path: str):
    """Outlook .msg cannot be synthesised without Outlook. Drop real (non-client)
    samples into tests/fixtures/ to exercise this path locally."""
    records = list(decompose_path(Path(path)))
    assert records[0].detected_type == "msg" and records[0].kind == "container"
    assert records[0].error is None, records[0].error
