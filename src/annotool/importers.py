"""Readers for raw and legacy-annotated .xlsx files (+ import report).

Input files are only ever opened read-only and never modified.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import openpyxl
from sqlalchemy.orm import Session

from . import models as m
from . import serialize as sz
from .store import save_annotation

RAW_HEADERS = ["DocumentID", "SentID", "Sentence"]
ANNOTATED_HEADERS = RAW_HEADERS + [sz.HEADERS[f] for f in sz.FIELDS]
_ANNOTATOR_PREFIX = re.compile(r"^([A-Z]{2,5})_(?=TRAIN)")
_SUFFIX = re.compile(r"_annotation(_\d+)?$")
_COPY = re.compile(r"__\d+$")  # duplicate-upload marker added by the app


class ImportError_(Exception):
    """Raised for unreadable / wrongly shaped files (message is user-facing)."""


@dataclass
class ImportReport:
    kind: str  # raw | annotated
    source_file: str
    batch: str = ""
    annotator: str | None = None
    rows_total: int = 0
    rows_imported: int = 0
    rows_flagged: int = 0
    skipped: str = ""  # reason, when nothing was imported
    corrections: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    outside_section2_rows: int = 0
    decisions_imported: int = 0  # typed Final values kept from a comparison sheet
    csv_path: str | None = None

    def summary(self) -> dict:
        return {
            "kind": self.kind, "source_file": self.source_file, "batch": self.batch,
            "annotator": self.annotator, "rows_total": self.rows_total,
            "rows_imported": self.rows_imported, "rows_flagged": self.rows_flagged,
            "auto_corrected_values": len(self.corrections),
            "flags": len(self.flags), "unmatched": len(self.unmatched),
            "outside_section2_rows": self.outside_section2_rows, "decisions_imported": self.decisions_imported,
            "skipped": self.skipped, "csv": self.csv_path,
        }

    def write_csv(self, path: Path) -> Path:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["type", "sent_id", "field", "original", "detail"])
            for c in self.corrections:
                w.writerow(["corrected", c["sent_id"], c["field"], c["original"], c["corrected"]])
            for fl in self.flags:
                w.writerow(["flagged", fl["sent_id"], fl["field"], fl["detail"], fl["reason"]])
            for sid in self.unmatched:
                w.writerow(["unmatched", sid, "", "", "SentID not in batch"])
        self.csv_path = str(path)
        return path


# ------------------------------------------------------------------------ helpers
def batch_name_from_file(path: Path) -> str:
    stem = _ANNOTATOR_PREFIX.sub("", _COPY.sub("", path.stem))
    return _SUFFIX.sub("", stem)


def annotator_from_file(path: Path) -> str | None:
    mt = _ANNOTATOR_PREFIX.match(_COPY.sub("", path.stem))
    return mt.group(1) if mt else None


def _read_rows(path: Path, required: list[str]):
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ImportError_(f"{path.name}: cannot open as .xlsx ({exc})") from exc
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if header is None:
        raise ImportError_(f"{path.name}: sheet is empty")
    names = [str(h).strip() if h is not None else "" for h in header]
    missing = [h for h in required if h not in names]
    if missing:
        raise ImportError_(f"{path.name}: missing column(s) {', '.join(missing)}")
    idx = {n: i for i, n in enumerate(names) if n}
    rows = []
    for r in it:
        r = tuple(r or ()) + (None,) * (len(names) - len(r or ()))  # read-only rows may be short
        if not r[idx["SentID"]]:
            continue  # trailing blank rows
        rows.append({n: r[i] for n, i in idx.items()})
    wb.close()
    return ws.title, rows


def get_annotator(s: Session, code: str) -> m.Annotator:
    a = s.query(m.Annotator).filter_by(code=code).first()
    if not a:
        a = m.Annotator(code=code, display_name=code)
        s.add(a)
        s.flush()
    return a


def _create_batch(s: Session, name, sheet, source, rows) -> m.Batch:
    b = m.Batch(name=name, kind="annotate", sheet_name=sheet, source_file=source)
    seen = set()
    for pos, r in enumerate(rows):
        sid = str(r["SentID"]).strip()
        if sid in seen:
            raise ImportError_(f"duplicate SentID {sid}")
        seen.add(sid)
        b.sentences.append(
            m.Sentence(
                document_id=str(r["DocumentID"]).strip(), sent_id=sid, position=pos,
                text=str(r["Sentence"]).strip(),
            )
        )
    s.add(b)
    s.flush()
    return b


# ---------------------------------------------------------------------------- raw
def import_raw(s: Session, path: Path) -> ImportReport:
    path = Path(path)
    rep = ImportReport("raw", path.name, batch=batch_name_from_file(path))
    sheet, rows = _read_rows(path, RAW_HEADERS)
    rep.rows_total = len(rows)
    if s.query(m.Batch).filter_by(name=rep.batch).first():
        rep.skipped = "batch already imported"
        return rep
    _create_batch(s, rep.batch, sheet, path.name, rows)
    rep.rows_imported = len(rows)
    s.commit()
    return rep


# ---------------------------------------------------------------------- annotated
def import_annotated(
    s: Session, path: Path, annotator_code: str | None = None, reports_dir: Path | None = None
) -> ImportReport:
    path = Path(path)
    code = annotator_code or annotator_from_file(path)
    if not code:
        raise ImportError_(f"{path.name}: cannot tell the annotator; name the file CODE_TRAIN_*.xlsx")
    rep = ImportReport("annotated", path.name, batch=batch_name_from_file(path), annotator=code)
    sheet, rows = _read_rows(path, ANNOTATED_HEADERS)
    rep.rows_total = len(rows)

    annotator = get_annotator(s, code)
    batch = s.query(m.Batch).filter_by(name=rep.batch).first()
    if batch is None:
        batch = _create_batch(s, rep.batch, sheet, path.name, rows)
    if (
        s.query(m.Annotation)
        .join(m.Sentence)
        .filter(m.Sentence.batch_id == batch.id, m.Annotation.annotator_id == annotator.id)
        .first()
    ):
        rep.skipped = f"{code} already has annotations in this batch"
        return rep

    by_sid = {x.sent_id: x for x in batch.sentences}
    used = {str(r["SentID"]).strip() for r in rows}  # SentIDs that appear in the file as-is
    for r in rows:
        sid = str(r["SentID"]).strip()
        sent = by_sid.get(sid)
        if sent is None:
            # Repair a corrupted SentID cell only when the sentence text identifies
            # exactly one not-yet-imported sentence of the same document.
            want = " ".join(str(r["Sentence"] or "").split())
            cands = [
                x for x in batch.sentences
                if x.document_id == str(r["DocumentID"]).strip() and x.sent_id not in used
                and " ".join(x.text.split()) == want
            ]
            if len(cands) == 1:
                sent = cands[0]
                rep.corrections.append({"sent_id": sent.sent_id, "field": "sent_id", "original": sid, "corrected": sent.sent_id})
                sid = sent.sent_id
        if sent is None:  # matched by SentID; never guess
            rep.unmatched.append(sid)
            continue
        cells = {f: r[sz.HEADERS[f]] for f in sz.FIELDS}
        res = sz.parse_annotation(sent.text, cells)
        file_text = " ".join(str(r["Sentence"] or "").split())
        if file_text != " ".join(sent.text.split()):  # annotator saw different text
            res.flags.append(sz.Flag("sentence", "sentence_text_differs", file_text))
        save_annotation(s, sent.id, annotator.id, res)
        rep.rows_imported += 1
        for c in res.corrections:
            rep.corrections.append({"sent_id": sid, "field": c.field, "original": c.original, "corrected": c.corrected})
        for f in res.flags:
            rep.flags.append({"sent_id": sid, "field": f.field, "reason": f.reason, "detail": f.detail})
        if res.flags:
            rep.rows_flagged += 1
        if any(f.reason == "outside_section2" for f in res.flags):
            rep.outside_section2_rows += 1
    s.commit()
    if reports_dir:
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rep.write_csv(reports_dir / f"import_report_{code}_{rep.batch}_{stamp}.csv")
    return rep


# ------------------------------------------------------------------- comparison
GROUP_FIELDS = sz.FIELDS  # 8 groups x 4 columns: A, B, Final, match


def import_comparison(s: Session, path: Path, reports_dir: Path | None = None) -> ImportReport:
    """Read a Comparison Sheet (plan §6.2): two annotators + typed Final values."""
    path = Path(path)
    try:
        wb = openpyxl.load_workbook(path, read_only=True)  # formulas stay as text
    except Exception as exc:  # noqa: BLE001
        raise ImportError_(f"{path.name}: cannot open as .xlsx ({exc})") from exc
    ws = wb.worksheets[0]
    rows = [tuple(r) + (None,) * max(0, 35 - len(r)) for r in ws.iter_rows(values_only=True)]
    wb.close()
    if len(rows) < 3 or [str(h or "").strip() for h in rows[1][:3]] != RAW_HEADERS:
        raise ImportError_(f"{path.name}: not a comparison sheet (row 2 must start with {', '.join(RAW_HEADERS)})")
    code_a, code_b = (str(rows[0][3] or "").strip(), str(rows[0][4] or "").strip())
    if not code_a or not code_b or code_a == code_b:
        raise ImportError_(f"{path.name}: row 1 must name the two annotators above the first field group")
    name = ws.title
    rep = ImportReport("comparison", path.name, batch=name, annotator=f"{code_a} × {code_b}")
    data = [r for r in rows[2:] if r[1]]
    rep.rows_total = len(data)
    if s.query(m.Batch).filter_by(name=name).first():
        rep.skipped = "batch already imported"
        return rep

    batch = m.Batch(name=name, kind="review", sheet_name=name, source_file=path.name)
    seen = set()
    for pos, r in enumerate(data):
        sid = str(r[1]).strip()
        if sid in seen:
            raise ImportError_(f"{path.name}: duplicate SentID {sid}")
        seen.add(sid)
        batch.sentences.append(m.Sentence(document_id=str(r[0]).strip(), sent_id=sid, position=pos, text=str(r[2]).strip()))
    s.add(batch)
    s.flush()
    ann = {code: get_annotator(s, code) for code in (code_a, code_b)}
    batch.annotators_json = [ann[code_a].id, ann[code_b].id]
    for r, sent in zip(data, batch.sentences):
        for who, code in ((0, code_a), (1, code_b)):
            cells = {f: r[3 + 4 * g + who] for g, f in enumerate(GROUP_FIELDS)}
            if all(v is None or str(v).strip() == "" for v in cells.values()):
                continue  # this annotator left the sentence blank
            res = sz.parse_annotation(sent.text, cells, plain_cues=True)
            save_annotation(s, sent.id, ann[code].id, res)
            rep.rows_imported += 1
            tag = f"{code}"
            for c in res.corrections:
                rep.corrections.append({"sent_id": sent.sent_id, "field": f"{tag}:{c.field}", "original": c.original, "corrected": c.corrected})
            for f in res.flags:
                rep.flags.append({"sent_id": sent.sent_id, "field": f"{tag}:{f.field}", "reason": f.reason, "detail": f.detail})
            if res.flags:
                rep.rows_flagged += 1
        for g, f in enumerate(GROUP_FIELDS):  # hand-typed Final values (formulas are recomputed, not imported)
            fin = r[3 + 4 * g + 2]
            if fin is not None and not str(fin).startswith("=") and str(fin).strip() != "":
                # a typed "-" means the reviewer resolved the field to no value
                text = "" if str(fin).strip() == "-" else (
                    str(int(fin)) if isinstance(fin, float) and fin == int(fin) else str(fin).strip())
                s.add(m.ReviewDecision(sentence_id=sent.id, field=f, final_value=text, resolved=True, reviewer="import"))
                rep.decisions_imported += 1
    s.commit()
    if reports_dir:
        reports_dir.mkdir(parents=True, exist_ok=True)
        rep.write_csv(reports_dir / f"import_report_{name}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    return rep
