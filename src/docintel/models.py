"""Artifact records.

One record per thing the decomposer finds: a top-level file, an email, an
email body, an attachment, a zip member, an email nested inside an email.
Every record carries its lineage (parent id, container path, depth) from
day one, because reconstructing lineage later means reprocessing the corpus.

The census never stores document content. Names, hashes, counts, dates and
sender domains only. Subject lines and bodies stay out on purpose so census
outputs can be shared without leaking anything.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ArtifactKind = Literal["container", "document", "body"]


class ArtifactRecord(BaseModel):
    # ---- identity and lineage ------------------------------------------
    artifact_id: str
    parent_artifact_id: str | None = None
    root_source_uri: str = Field(description="Top-level file this artifact came from, relative to the corpus root.")
    container_path: str = Field(description="Human-readable path from the root file down to this artifact.")
    depth: int = Field(0, description="Container nesting depth. 0 is a top-level file.")
    email_depth: int = Field(0, description="Number of email containers on the path, including this one if it is an email.")
    category_hint: str | None = Field(None, description="Category taken from the corpus sub-folder, if any.")

    # ---- what it is ----------------------------------------------------
    name: str
    ext: str = ""
    detected_type: str = "unknown"
    mime_type: str = "application/octet-stream"
    kind: ArtifactKind = "document"
    size_bytes: int = 0
    content_sha256: str | None = None
    is_inline: bool | None = Field(None, description="Inline email part such as a signature image.")

    # ---- container summary (emails, zips) ------------------------------
    child_count: int | None = Field(None, description="Direct children: attachments for emails, members for zips.")
    inline_count: int | None = None
    email_date: datetime | None = None
    sender_domain: str | None = None
    message_id: str | None = None
    has_plain_body: bool | None = None
    has_html_body: bool | None = None

    # ---- pdf probe -----------------------------------------------------
    page_count: int | None = None
    text_chars_per_page: float | None = None
    is_scanned: bool | None = Field(None, description="No usable text layer on the sampled pages.")
    is_encrypted: bool | None = None
    has_acroform: bool | None = Field(None, description="PDF carries fillable form fields.")

    # ---- spreadsheet probe ---------------------------------------------
    sheet_count: int | None = None
    hidden_sheet_count: int | None = None
    max_rows: int | None = None
    max_cols: int | None = None
    nonempty_cells: int | None = None
    formula_count: int | None = None
    cross_sheet_formula_count: int | None = None
    external_link_count: int | None = None
    defined_name_count: int | None = None
    merged_range_count: int | None = None
    table_count: int | None = None
    pivot_cache_count: int | None = None
    has_vba: bool | None = None
    probe_truncated: bool | None = Field(None, description="Probe stopped early on a very large workbook.")

    # ---- delimited text probe ------------------------------------------
    row_count: int | None = None
    col_count: int | None = None
    delimiter: str | None = None
    encoding: str | None = None
    has_header: bool | None = None

    # ---- problems ------------------------------------------------------
    error: str | None = Field(None, description="Decomposition or probe problem. The record is still emitted.")

    def to_jsonl(self) -> str:
        return self.model_dump_json(exclude_none=True)
