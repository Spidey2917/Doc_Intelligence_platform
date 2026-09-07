"""Recursive decomposition of files and containers into artifact records.

Containers handled: directories, zip archives, .eml, .msg, and emails
nested inside emails to any depth (bounded by MAX_DEPTH). Every record
carries lineage: parent id, container path, depth, email depth.

Rules:
- Never raise on bad input. A problem becomes ``record.error`` and the run
  continues. One corrupt attachment must not lose the other nine.
- Parents are yielded before their children.
- Nothing is written to disk; attachments are decomposed from memory.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import functools
import io
import logging
import mimetypes
import operator
import posixpath
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

import extract_msg
from extract_msg.enums import AttachmentType, ErrorBehavior

from docintel.hashing import sha256_hex, stable_id
from docintel.models import ArtifactRecord
from docintel.probe import probe
from docintel.sniff import EXT_FALLBACK, MIME_BY_TYPE, extension_of, sniff

log = logging.getLogger(__name__)
logging.getLogger("extract_msg").setLevel(logging.ERROR)

MAX_DEPTH = 16
MAX_ZIP_MEMBERS = 10_000
MAX_MEMBER_BYTES = 1 << 30
ARCHIVES_NOT_UNPACKED = {"7z", "rar", "gzip"}

# Every lenient flag extract_msg offers: a broken or unsupported attachment
# comes back as a typed placeholder we record, instead of an exception that
# would lose the whole email.
_MSG_ERROR_BEHAVIOR = functools.reduce(operator.or_, ErrorBehavior)


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def decompose_path(
    path: Path,
    *,
    corpus_root: Path | None = None,
    category_from_folder: bool = True,
) -> Iterator[ArtifactRecord]:
    """Decompose one file, or every file under a directory."""
    path = Path(path)
    if corpus_root is None:
        corpus_root = path if path.is_dir() else path.parent
    corpus_root = Path(corpus_root)

    if path.is_dir():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            yield from decompose_path(child, corpus_root=corpus_root, category_from_folder=category_from_folder)
        return

    rel = _relative(path, corpus_root)
    hint = category_hint_from(rel) if category_from_folder else None
    try:
        data = path.read_bytes()
    except OSError as exc:
        yield ArtifactRecord(
            artifact_id=stable_id(rel, path.name),
            root_source_uri=rel,
            container_path=path.name,
            name=path.name,
            ext=extension_of(path.name),
            category_hint=hint,
            error=f"unreadable: {exc}"[:300],
        )
        return
    yield from decompose_bytes(data, path.name, root_source_uri=rel, category_hint=hint)


def decompose_bytes(
    data: bytes,
    name: str,
    *,
    root_source_uri: str,
    category_hint: str | None = None,
    parent_id: str | None = None,
    container_path: str | None = None,
    depth: int = 0,
    email_depth: int = 0,
    is_inline: bool | None = None,
) -> Iterator[ArtifactRecord]:
    container_path = container_path or name
    sniffed = sniff(data, name)
    record = ArtifactRecord(
        artifact_id=stable_id(root_source_uri, container_path),
        parent_artifact_id=parent_id,
        root_source_uri=root_source_uri,
        container_path=container_path,
        depth=depth,
        email_depth=email_depth,
        category_hint=category_hint,
        name=name,
        ext=extension_of(name),
        detected_type=sniffed.type,
        mime_type=sniffed.mime,
        size_bytes=len(data),
        content_sha256=sha256_hex(data),
        is_inline=is_inline,
    )

    if depth > MAX_DEPTH:
        record.kind = "container" if sniffed.type in ("eml", "msg", "zip") else "document"
        record.error = f"container depth {MAX_DEPTH} exceeded; not unpacked"
        yield record
        return

    if sniffed.type == "eml":
        yield from _decompose_eml(data, record)
    elif sniffed.type == "msg":
        yield from _decompose_msg(data, record)
    elif sniffed.type == "zip":
        yield from _decompose_zip(data, record)
    elif sniffed.type in ARCHIVES_NOT_UNPACKED:
        record.kind = "container"
        record.error = f"{sniffed.type} archive not unpacked (format not supported yet)"
        yield record
    else:
        record.kind = "document"
        probe(record, data)
        yield record


# --------------------------------------------------------------------------
# Children of a container, materialised before any of them is yielded
# --------------------------------------------------------------------------

@dataclass
class _Child:
    label: str
    name: str
    data: bytes | None = None
    is_body: bool = False
    body_type: str = "text"
    is_inline: bool = False
    error: str | None = None


def _emit_children(parent: ArtifactRecord, children: list[_Child]) -> Iterator[ArtifactRecord]:
    for child in children:
        path = f"{parent.container_path} > {child.label}"
        if child.error is not None or child.data is None:
            yield _stub(parent, path, child.name, child.error or "empty payload", is_inline=child.is_inline)
            continue
        if child.is_body:
            yield ArtifactRecord(
                artifact_id=stable_id(parent.root_source_uri, path),
                parent_artifact_id=parent.artifact_id,
                root_source_uri=parent.root_source_uri,
                container_path=path,
                depth=parent.depth + 1,
                email_depth=parent.email_depth,
                category_hint=parent.category_hint,
                name=child.name,
                ext=extension_of(child.name),
                detected_type=child.body_type,
                mime_type=MIME_BY_TYPE.get(child.body_type, "text/plain"),
                kind="body",
                size_bytes=len(child.data),
                content_sha256=sha256_hex(child.data),
            )
            continue
        yield from decompose_bytes(
            child.data,
            child.name,
            root_source_uri=parent.root_source_uri,
            category_hint=parent.category_hint,
            parent_id=parent.artifact_id,
            container_path=path,
            depth=parent.depth + 1,
            email_depth=parent.email_depth,
            is_inline=child.is_inline,
        )


def _stub(parent: ArtifactRecord, path: str, name: str, error: str, *, is_inline: bool | None = None) -> ArtifactRecord:
    ext = extension_of(name)
    detected = EXT_FALLBACK.get(ext, "unknown")
    return ArtifactRecord(
        artifact_id=stable_id(parent.root_source_uri, path),
        parent_artifact_id=parent.artifact_id,
        root_source_uri=parent.root_source_uri,
        container_path=path,
        depth=parent.depth + 1,
        email_depth=parent.email_depth,
        category_hint=parent.category_hint,
        name=name,
        ext=ext,
        detected_type=detected,
        mime_type=MIME_BY_TYPE.get(detected, MIME_BY_TYPE["unknown"]),
        kind="document",
        is_inline=is_inline,
        error=error[:300],
    )


def _finish_container(record: ArtifactRecord, children: list[_Child]) -> None:
    record.child_count = sum(1 for c in children if not c.is_body and not c.is_inline)
    record.inline_count = sum(1 for c in children if c.is_inline)
    record.has_plain_body = any(c.is_body and c.body_type == "text" for c in children)
    record.has_html_body = any(c.is_body and c.body_type == "html" for c in children)


# --------------------------------------------------------------------------
# .eml
# --------------------------------------------------------------------------

def _walk_parts(message):
    if message.get_content_type() == "message/rfc822":
        yield message
        return
    if message.is_multipart():
        for part in message.iter_parts():
            yield from _walk_parts(part)
    else:
        yield message


def _decompose_eml(data: bytes, record: ArtifactRecord) -> Iterator[ArtifactRecord]:
    record.kind = "container"
    record.email_depth += 1
    try:
        message = email.message_from_bytes(data, policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001
        record.error = f"eml parse failed: {type(exc).__name__}: {exc}"[:300]
        yield record
        return

    record.email_date = _to_datetime(message.get("Date"))
    record.sender_domain = _domain(message.get("From"))
    record.message_id = _clean_header(message.get("Message-ID"))

    children: list[_Child] = []
    try:
        for index, part in enumerate(_walk_parts(message)):
            children.append(_eml_child(part, index))
    except Exception as exc:  # noqa: BLE001
        record.error = f"eml parts failed: {type(exc).__name__}: {exc}"[:300]

    _finish_container(record, children)
    yield record
    yield from _emit_children(record, children)


def _eml_child(part, index: int) -> _Child:
    ctype = part.get_content_type()
    disposition = part.get_content_disposition()
    filename = _clean_header(part.get_filename())
    content_id = part.get("Content-ID")

    if ctype == "message/rfc822":
        name = filename or f"embedded-{index}.eml"
        if not name.lower().endswith(".eml"):
            name += ".eml"
        payload = part.get_payload()
        inner = payload[0] if isinstance(payload, list) and payload else None
        if inner is None:
            return _Child(label=f"embedded[{index}]:{name}", name=name, error="empty message/rfc822 part")
        try:
            raw = inner.as_bytes()
        except Exception:  # noqa: BLE001 - fall back to a lossy re-serialisation
            try:
                raw = inner.as_string().encode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                return _Child(label=f"embedded[{index}]:{name}", name=name, error=f"embedded email serialise failed: {exc}")
        return _Child(label=f"embedded[{index}]:{name}", name=name, data=raw)

    try:
        payload = part.get_payload(decode=True)
    except Exception as exc:  # noqa: BLE001
        return _Child(label=f"attachment[{index}]:{filename or ctype}", name=filename or f"part-{index}", error=f"undecodable part: {exc}")
    if payload is None:
        payload = b""

    is_body = ctype in ("text/plain", "text/html") and disposition != "attachment" and not filename
    if is_body:
        body_type = "html" if ctype == "text/html" else "text"
        return _Child(label=f"body[{body_type}]", name=f"body.{'html' if body_type == 'html' else 'txt'}", data=bytes(payload), is_body=True, body_type=body_type)

    inline = bool(content_id) and (disposition == "inline" or ctype.startswith("image/"))
    name = filename or f"part-{index}{_guess_ext(ctype)}"
    return _Child(label=f"attachment[{index}]:{name}", name=name, data=bytes(payload), is_inline=inline)


# --------------------------------------------------------------------------
# .msg
# --------------------------------------------------------------------------

def _decompose_msg(data: bytes, record: ArtifactRecord) -> Iterator[ArtifactRecord]:
    record.kind = "container"
    record.email_depth += 1
    try:
        message = extract_msg.openMsg(data, errorBehavior=_MSG_ERROR_BEHAVIOR)
    except Exception as exc:  # noqa: BLE001
        record.error = f"msg open failed: {type(exc).__name__}: {exc}"[:300]
        yield record
        return

    children: list[_Child] = []
    try:
        record.email_date = _to_datetime(_safe_attr(message, "date"))
        record.sender_domain = _domain(_safe_attr(message, "sender"))
        record.message_id = _clean_header(_safe_attr(message, "messageId"))

        plain = _safe_attr(message, "body")
        if isinstance(plain, str) and plain.strip():
            children.append(_Child(label="body[text]", name="body.txt", data=plain.encode("utf-8", errors="replace"), is_body=True, body_type="text"))
        html = _safe_attr(message, "htmlBody")
        if isinstance(html, str):
            html = html.encode("utf-8", errors="replace")
        if isinstance(html, (bytes, bytearray)) and html.strip():
            children.append(_Child(label="body[html]", name="body.html", data=bytes(html), is_body=True, body_type="html"))

        attachments = _safe_attr(message, "attachments") or []
        for index, attachment in enumerate(attachments):
            children.append(_msg_child(attachment, index))
    except Exception as exc:  # noqa: BLE001
        record.error = f"msg read failed: {type(exc).__name__}: {exc}"[:300]
    finally:
        try:
            message.close()
        except Exception:  # noqa: BLE001
            pass

    _finish_container(record, children)
    yield record
    yield from _emit_children(record, children)


def _msg_child(attachment, index: int) -> _Child:
    atype = _safe_attr(attachment, "type")
    name = _first_text(
        _safe_attr(attachment, "longFilename"),
        _safe_attr(attachment, "shortFilename"),
        _safe_attr(attachment, "name"),
    ) or f"attachment-{index}"
    inline = bool(_safe_attr(attachment, "cid") or _safe_attr(attachment, "contentId") or _safe_attr(attachment, "hidden"))
    data = _safe_attr(attachment, "data")

    if atype == AttachmentType.MSG or (data is not None and hasattr(data, "exportBytes")):
        if not name.lower().endswith(".msg"):
            name += ".msg"
        try:
            raw = data.exportBytes()
        except Exception as exc:  # noqa: BLE001
            return _Child(label=f"embedded[{index}]:{name}", name=name, error=f"embedded msg export failed: {type(exc).__name__}: {exc}")
        return _Child(label=f"embedded[{index}]:{name}", name=name, data=bytes(raw))

    if isinstance(data, (bytes, bytearray)):
        return _Child(label=f"attachment[{index}]:{name}", name=name, data=bytes(data), is_inline=inline)

    type_name = getattr(atype, "name", str(atype))
    return _Child(label=f"attachment[{index}]:{name}", name=name, is_inline=inline, error=f"unsupported msg attachment type {type_name}")


# --------------------------------------------------------------------------
# zip
# --------------------------------------------------------------------------

def _decompose_zip(data: bytes, record: ArtifactRecord) -> Iterator[ArtifactRecord]:
    record.kind = "container"
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        record.error = f"zip open failed: {type(exc).__name__}: {exc}"[:300]
        yield record
        return

    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        record.child_count = len(members)
        if len(members) > MAX_ZIP_MEMBERS:
            record.error = f"zip has {len(members)} members; only the first {MAX_ZIP_MEMBERS} unpacked"
        yield record

        for index, info in enumerate(members[:MAX_ZIP_MEMBERS]):
            name = posixpath.basename(info.filename) or info.filename
            path = f"{record.container_path} > zip[{index}]:{info.filename}"
            if info.file_size > MAX_MEMBER_BYTES:
                yield _stub(record, path, name, f"zip member too large ({info.file_size} bytes); skipped")
                continue
            try:
                payload = archive.read(info)
            except Exception as exc:  # noqa: BLE001 - encrypted members raise RuntimeError
                yield _stub(record, path, name, f"zip member unreadable: {type(exc).__name__}: {exc}")
                continue
            yield from decompose_bytes(
                payload,
                name,
                root_source_uri=record.root_source_uri,
                category_hint=record.category_hint,
                parent_id=record.artifact_id,
                container_path=path,
                depth=record.depth + 1,
                email_depth=record.email_depth,
            )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def category_hint_from(relative_path: str) -> str | None:
    parts = PurePosixPath(relative_path).parts
    if len(parts) < 2:
        return None
    return parts[0].strip().lower().replace(" ", "_").replace("-", "_")


def _safe_attr(obj, name: str):
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 - extract_msg properties can raise on odd files
        return None


def _first_text(*values) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _clean_header(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _domain(value) -> str | None:
    if value is None:
        return None
    _, address = email.utils.parseaddr(str(value))
    if "@" not in address:
        return None
    return address.rsplit("@", 1)[1].lower().strip("> ") or None


def _to_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None


def _guess_ext(ctype: str) -> str:
    return mimetypes.guess_extension(ctype) or ""
