"""CSV and TSV probe: encoding, delimiter, header guess, row and column counts."""

from __future__ import annotations

import csv
import io

from docintel.models import ArtifactRecord

MAX_ROWS = 5_000_000
_ENCODINGS = ("utf-8", "cp1252", "latin-1")

csv.field_size_limit(min(2**31 - 1, 64 * 1024 * 1024))


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    for enc in _ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def probe_delimited(record: ArtifactRecord, data: bytes) -> None:
    text, encoding = decode_text(data)
    record.encoding = encoding
    sample = text[:65536]
    default = "\t" if record.detected_type == "tsv" else ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = default
    record.delimiter = delimiter
    try:
        record.has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        record.has_header = None

    rows = cols = 0
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        rows += 1
        cols = max(cols, len(row))
        if rows >= MAX_ROWS:
            record.probe_truncated = True
            break
    record.row_count = rows
    record.col_count = cols
