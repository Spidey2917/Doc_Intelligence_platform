"""Light per-format probes used by the census.

A probe fills a few fields on an ArtifactRecord (page counts, scanned or
not, sheet and formula counts, delimiter). It never raises: failures land
in ``record.error`` so one bad file cannot stop a corpus run.
"""

from __future__ import annotations

from docintel.models import ArtifactRecord
from docintel.probe.delimited import probe_delimited
from docintel.probe.pdf import probe_pdf
from docintel.probe.spreadsheet import probe_xlsb, probe_xlsx

_PROBES = {
    "pdf": probe_pdf,
    "xlsx": probe_xlsx,
    "xlsm": probe_xlsx,
    "xlsb": probe_xlsb,
    "csv": probe_delimited,
    "tsv": probe_delimited,
}


def probe(record: ArtifactRecord, data: bytes) -> None:
    fn = _PROBES.get(record.detected_type)
    if fn is None:
        return
    try:
        fn(record, data)
    except Exception as exc:  # noqa: BLE001 - probes must never take down a run
        record.error = f"probe failed: {type(exc).__name__}: {exc}"[:500]


__all__ = ["probe", "probe_pdf", "probe_xlsx", "probe_xlsb", "probe_delimited"]
