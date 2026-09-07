"""Content-type detection from bytes, with the filename extension as a tiebreaker.

Pure Python on purpose: no libmagic, so it runs the same on every VM. The
rule is "trust the bytes, then the extension". Vendors send .msg files that
are really .eml, .xls files that are really HTML, and .csv files that are
really tab separated; the census has to see through all of that.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass

import olefile

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

IMAGE_MAGICS: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"BM", "image/bmp"),
    (b"RIFF", "image/webp"),
)

MIME_BY_TYPE = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    "xls": "application/vnd.ms-excel",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "msg": "application/vnd.ms-outlook",
    "eml": "message/rfc822",
    "zip": "application/zip",
    "7z": "application/x-7z-compressed",
    "rar": "application/vnd.rar",
    "gzip": "application/gzip",
    "rtf": "application/rtf",
    "html": "text/html",
    "xml": "application/xml",
    "json": "application/json",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "text": "text/plain",
    "ole": "application/x-ole-storage",
    "unknown": "application/octet-stream",
}

# Extension fallback when the bytes tell us nothing.
EXT_FALLBACK = {
    "pdf": "pdf", "xlsx": "xlsx", "xlsm": "xlsm", "xlsb": "xlsb", "xls": "xls",
    "docx": "docx", "doc": "doc", "pptx": "pptx", "ppt": "ppt", "msg": "msg",
    "eml": "eml", "zip": "zip", "7z": "7z", "rar": "rar", "gz": "gzip",
    "rtf": "rtf", "htm": "html", "html": "html", "xml": "xml", "json": "json",
    "csv": "csv", "tsv": "tsv", "txt": "text", "edi": "text", "x12": "text",
}

_EMAIL_HEADERS = {
    b"from", b"to", b"cc", b"subject", b"date", b"received", b"return-path",
    b"message-id", b"mime-version", b"content-type", b"delivered-to",
    b"x-mailer", b"reply-to", b"sender", b"thread-topic", b"thread-index",
}
_HEADER_RE = re.compile(rb"^([A-Za-z][A-Za-z0-9-]{0,60}):")


@dataclass(frozen=True)
class Sniffed:
    type: str
    mime: str


def extension_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def sniff(data: bytes, name: str) -> Sniffed:
    ext = extension_of(name)
    head = data[:8192]

    if head.startswith(b"%PDF"):
        return Sniffed("pdf", MIME_BY_TYPE["pdf"])
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return _sniff_zip(data, ext)
    if head.startswith(OLE_MAGIC):
        return _sniff_ole(data, ext)
    for magic, mime in IMAGE_MAGICS:
        if head.startswith(magic):
            if magic == b"RIFF" and head[8:12] != b"WEBP":
                break
            return Sniffed("image", mime)
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return Sniffed("7z", MIME_BY_TYPE["7z"])
    if head.startswith(b"Rar!\x1a\x07"):
        return Sniffed("rar", MIME_BY_TYPE["rar"])
    if head.startswith(b"\x1f\x8b"):
        return Sniffed("gzip", MIME_BY_TYPE["gzip"])
    if head.startswith(b"{\\rtf"):
        return Sniffed("rtf", MIME_BY_TYPE["rtf"])

    if ext == "eml" or looks_like_email(head):
        return Sniffed("eml", MIME_BY_TYPE["eml"])

    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if stripped.startswith((b"<!doctype html", b"<html")):
        return Sniffed("html", MIME_BY_TYPE["html"])
    if stripped.startswith(b"<?xml") or (ext == "xml" and stripped.startswith(b"<")):
        return Sniffed("xml", MIME_BY_TYPE["xml"])
    if ext == "json" and stripped[:1] in (b"{", b"["):
        return Sniffed("json", MIME_BY_TYPE["json"])

    if is_texty(head):
        if ext == "csv":
            return Sniffed("csv", MIME_BY_TYPE["csv"])
        if ext in ("tsv", "tab"):
            return Sniffed("tsv", MIME_BY_TYPE["tsv"])
        if ext in ("txt", "text", "dat", "") and looks_delimited(head):
            return Sniffed("csv", MIME_BY_TYPE["csv"])
        return Sniffed("text", MIME_BY_TYPE["text"])

    fallback = EXT_FALLBACK.get(ext, "unknown")
    return Sniffed(fallback, MIME_BY_TYPE.get(fallback, MIME_BY_TYPE["unknown"]))


def _sniff_zip(data: bytes, ext: str) -> Sniffed:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        # The decomposer records the failure when it tries to open it.
        return Sniffed("zip", MIME_BY_TYPE["zip"])
    if "xl/workbook.bin" in names:
        return Sniffed("xlsb", MIME_BY_TYPE["xlsb"])
    if "xl/workbook.xml" in names:
        if ext == "xlsm" or "xl/vbaProject.bin" in names:
            return Sniffed("xlsm", MIME_BY_TYPE["xlsm"])
        return Sniffed("xlsx", MIME_BY_TYPE["xlsx"])
    if any(n.startswith("word/") for n in names):
        return Sniffed("docx", MIME_BY_TYPE["docx"])
    if any(n.startswith("ppt/") for n in names):
        return Sniffed("pptx", MIME_BY_TYPE["pptx"])
    return Sniffed("zip", MIME_BY_TYPE["zip"])


def _sniff_ole(data: bytes, ext: str) -> Sniffed:
    try:
        ole = olefile.OleFileIO(data)
    except Exception:  # noqa: BLE001 - corrupt OLE, fall back to extension
        fallback = EXT_FALLBACK.get(ext, "ole")
        return Sniffed(fallback, MIME_BY_TYPE.get(fallback, MIME_BY_TYPE["ole"]))
    try:
        top = {entry[0] for entry in ole.listdir(streams=True, storages=True) if entry}
    finally:
        ole.close()
    if any(n.startswith("__substg1.0_") or n.startswith("__properties_version1.0") for n in top):
        return Sniffed("msg", MIME_BY_TYPE["msg"])
    if "Workbook" in top or "Book" in top:
        return Sniffed("xls", MIME_BY_TYPE["xls"])
    if "WordDocument" in top:
        return Sniffed("doc", MIME_BY_TYPE["doc"])
    if "PowerPoint Document" in top:
        return Sniffed("ppt", MIME_BY_TYPE["ppt"])
    return Sniffed("ole", MIME_BY_TYPE["ole"])


def looks_like_email(head: bytes) -> bool:
    """A header block at the top of the file, with at least two well-known headers."""
    lines = head.lstrip(b"\r\n").split(b"\n", 40)[:40]
    hits = 0
    for raw in lines:
        line = raw.rstrip(b"\r")
        if not line:
            break
        if line.startswith(b"From "):
            hits += 1
            continue
        if line[:1] in (b" ", b"\t"):
            continue  # folded continuation line
        match = _HEADER_RE.match(line)
        if not match:
            return False
        if match.group(1).lower() in _EMAIL_HEADERS:
            hits += 1
    return hits >= 2


def is_texty(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        pass
    control = sum(1 for b in head if b < 32 and b not in (9, 10, 13, 12))
    return control / len(head) < 0.02


def looks_delimited(head: bytes) -> bool:
    text = head.decode("utf-8", errors="replace")
    sample = "\n".join(text.splitlines()[:20])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return False
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    counts = [ln.count(dialect.delimiter) for ln in lines]
    return min(counts) >= 1 and max(counts) - min(counts) <= 2
