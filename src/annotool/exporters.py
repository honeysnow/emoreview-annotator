"""Annotation exporter (plan §6.1). The comparison exporter arrives in M6."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy.orm import Session

from . import models as m
from . import serialize as sz
from .store import load_annotation
from .validate import has_errors, validate

ANNOTATION_HEADERS = ["DocumentID", "SentID", "Sentence"] + [sz.HEADERS[f] for f in sz.FIELDS]


class ExportError(Exception):
    """User-facing export problem (unknown batch / annotator / document)."""


@dataclass
class AnnotationExport:
    batch: str
    annotator: str
    scope: str = "all"  # all | document | completed
    document: str | None = None
    include_unannotated: bool = True  # only matters for scope all/document
    cue_prefix: bool = True  # setting `cue_prefix_with_tag` (decision 3)


@dataclass
class ExportData:
    filename: str
    sheet_name: str
    rows: list[list]  # list of 11 cells; None = blank cell
    warnings: list[str]  # incomplete / failing rows, for "warn about incomplete rows"


def default_sheet_name(batch: m.Batch) -> str:
    """First 31 chars (Excel limit), e.g. `clothing_shoes_and_jewelry_anno`."""
    if batch.sheet_name:
        return batch.sheet_name[:31]
    stem = batch.name.split("_", 2)[2] if batch.name.count("_") >= 2 else batch.name
    return f"{stem}_annotation"[:31]


def build_annotation_rows(s: Session, opts: AnnotationExport) -> ExportData:
    batch = s.query(m.Batch).filter_by(name=opts.batch).first()
    ann = s.query(m.Annotator).filter_by(code=opts.annotator).first()
    if not batch:
        raise ExportError(f"unknown batch {opts.batch!r}")
    if not ann:
        raise ExportError(f"unknown annotator {opts.annotator!r}")
    if opts.scope not in ("all", "document", "completed"):
        raise ExportError(f"unknown scope {opts.scope!r}")
    if opts.scope == "document" and not opts.document:
        raise ExportError("scope 'document' needs a document id")

    by_sentence = {
        a.sentence_id: a
        for a in s.query(m.Annotation).join(m.Sentence)
        .filter(m.Sentence.batch_id == batch.id, m.Annotation.annotator_id == ann.id)
    }
    rows: list[list] = []
    warnings: list[str] = []
    for sent in batch.sentences:  # original order
        if opts.scope == "document" and sent.document_id != opts.document:
            continue
        row = by_sentence.get(sent.id)
        a = load_annotation(row) if row else sz.Annotation()
        if a.status == "todo" and (opts.scope == "completed" or not opts.include_unannotated):
            continue
        if a.status == "todo":
            warnings.append(f"{sent.sent_id}: not annotated yet")
        else:
            bad = [c.msg for c in validate(a) if c.level == "error"]
            if bad:
                warnings.append(f"{sent.sent_id}: " + "; ".join(bad))
        out = sz.serialize(sent.text, a, cue_prefix=opts.cue_prefix)
        cells: list = [sent.document_id, sent.sent_id, sent.text]
        for f in sz.FIELDS:
            v = out[f]
            cells.append(None if v == "" else (int(v) if f == "arousal" else v))  # `3`, not `3.0`
        rows.append(cells)
    if opts.scope == "document" and not rows and not any(x.document_id == opts.document for x in batch.sentences):
        raise ExportError(f"unknown document {opts.document!r}")
    return ExportData(f"{opts.annotator}_{batch.name}_annotation.xlsx", default_sheet_name(batch), rows, warnings)


def write_xlsx(data: ExportData, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = data.sheet_name
    ws.append(ANNOTATION_HEADERS)
    for r in data.rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, str):
                c.data_type = "s"  # never let text starting with '=' become a formula
    ws.freeze_panes = "D2"
    for col, w in zip("ABCDEFGHIJK", (11, 11, 60, 18, 8, 24, 50, 14, 28, 40, 18)):
        ws.column_dimensions[col].width = w
    wb.save(path)
    return path


def write_csv(data: ExportData, path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
        w = csv.writer(f)
        w.writerow(ANNOTATION_HEADERS)
        for r in data.rows:
            w.writerow(["" if v is None else v for v in r])
    return path


def export_annotations(s: Session, opts: AnnotationExport, exports_dir: Path, fmt: str = "xlsx") -> tuple[Path, str, ExportData]:
    """Write the file into data/exports/ (timestamped) and return (path, download name, data)."""
    if fmt not in ("xlsx", "csv"):
        raise ExportError(f"unknown format {fmt!r}")
    data = build_annotation_rows(s, opts)
    exports_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(data.filename).stem
    path = exports_dir / f"{stem}_{datetime.now():%Y%m%d_%H%M%S}.{fmt}"
    (write_xlsx if fmt == "xlsx" else write_csv)(data, path)
    return path, f"{stem}.{fmt}", data


# ============================================================ comparison sheet (M6)
import re  # noqa: E402

from openpyxl.styles import Alignment, Font  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from . import review as rv  # noqa: E402
from .codebook import load_codebook  # noqa: E402

COMPARISON_TAG = "EmoReview40K"
SOURCE_LIST = '"self, other person, no one"'  # same list the example sheet validates against


@dataclass
class ComparisonExport:
    batch: str
    scope: str = "all"  # all | disagreements | unresolved
    formulas: bool = True  # live =IF(EXACT(...)) cells, as in the original sheet
    agreement_row: bool = True  # row 1 agreement % above each field group
    reviewer_note: bool = False  # extra column with notes typed during review
    cue_prefix: bool = True  # setting `cue_prefix_with_tag` (decision 3); off = old plain cues


@dataclass
class CRow:
    doc: str
    sent_id: str
    text: str
    groups: list[tuple]  # per field: (a, b, final, match); final None => formula/blank
    finals_eval: list  # per field: what the Final cell shows once calculated
    note: str


@dataclass
class ComparisonData:
    filename: str
    sheet_name: str
    a: str
    b: str
    rows: list[CRow]
    agreement: dict[str, float]  # whole batch, 0-100
    n_all: int
    options: ComparisonExport


def _cell(field: str, v: str | None):
    if v is None or v == "":
        return None
    return int(v) if field == "arousal" and re.fullmatch(r"\d+", v) else v


def comparison_names(batch: m.Batch) -> tuple[str, str]:
    """(file stem without the date, sheet name), e.g. TRAIN_2_EmoReview40K_Comparison_Sheet and
    TRAIN_2_cell_phones_accessories (an imported sheet keeps its own sheet name)."""
    base = re.sub(r"_review_[A-Z0-9]+_[A-Z0-9]+$", "", batch.name)
    prefix = (re.match(r"^(TRAIN_\d+)", base) or re.match(r"^(.*)$", base)).group(1)
    sheet = base if "_review_" in batch.name else (batch.sheet_name or base)
    return f"{prefix}_{COMPARISON_TAG}_Comparison_Sheet", sheet[:31]


def build_comparison(s: Session, opts: ComparisonExport) -> ComparisonData:
    batch = s.query(m.Batch).filter_by(name=opts.batch, kind="review").first()
    if not batch:
        raise ExportError(f"unknown review batch {opts.batch!r}")
    if opts.scope not in ("all", "disagreements", "unresolved"):
        raise ExportError(f"unknown scope {opts.scope!r}")
    a, b = rv.review_annotators(s, batch)
    all_rows = rv.load_rows(s, batch)
    chosen = [r for r in all_rows if opts.scope == "all" or (opts.scope == "disagreements" and r.diffs)
              or (opts.scope == "unresolved" and r.status == "open")]
    strip = (lambda v: rv.strip_cue_prefix(v)) if not opts.cue_prefix else (lambda v: v)  # noqa: E731
    out: list[CRow] = []
    for r in chosen:
        groups, evals = [], []
        for f in rv.FIELDS:
            va, vb = (strip(x) if f == "cues" else x for x in (r.a[f], r.b[f]))
            d = r.decisions.get(f)
            if d and d.resolved:  # a decision overwrites the formula, like a hand-typed value in the sheet
                fv = d.final_value or ""
                fin = _cell(f, (strip(fv) if f == "cues" else fv) or "-")  # nothing chosen shows as "-"
                shown = fin
            else:
                fin = None
                shown = (_cell(f, va) if va == vb and va else ("-" if va == vb else None))
            groups.append((_cell(f, va), _cell(f, vb), fin, "-" if va == vb else "X"))
            evals.append(shown)
        out.append(CRow(r.sentence.document_id, r.sentence.sent_id, r.sentence.text, groups, evals, r.note))
    stem, sheet = comparison_names(batch)
    return ComparisonData(f"{stem}_{datetime.now():%Y%m%d}.xlsx", sheet, a.code, b.code, out,
                          rv.agreement(all_rows), len(all_rows), opts)


def comparison_header_rows(d: ComparisonData) -> tuple[list, list]:
    """(row 1 labels, row 2 headers) without formulas; used by CSV and the preview."""
    r1: list = [None, None, None]
    r2: list = ["DocumentID", "SentID", "Sentence"]
    for f in rv.FIELDS:
        r1 += [d.a, d.b, "Final", None]
        r2 += [sz.HEADERS[f]] * 3 + [f"{d.a}x{d.b}"]
    if d.options.reviewer_note:
        r1.append(None)
        r2.append("Reviewer note")
    return r1, r2


def write_comparison_xlsx(d: ComparisonData, path: Path) -> Path:
    o = d.options
    wb = Workbook()
    ws = wb.active
    ws.title = d.sheet_name
    r1, r2 = comparison_header_rows(d)
    first, last = 3, 2 + len(d.rows)
    ws.append(r1)
    ws.append(r2)
    for g, f in enumerate(rv.FIELDS):
        base = 4 + 4 * g  # 1-indexed column of A for this group
        a_c, b_c, fin_c, m_c = (get_column_letter(base + k) for k in range(4))
        if o.agreement_row:
            if o.formulas and o.scope == "all":  # same formula as the example sheet
                ws[f"{m_c}1"] = f'=COUNTIF({m_c}{first}:{m_c}{last},"-")/COUNTA({m_c}{first}:{m_c}{last})'
            else:  # a filtered export would skew the formula: write the whole-batch value instead
                ws[f"{m_c}1"] = round(d.agreement[f] / 100, 6)
            ws[f"{m_c}1"].number_format = "0%"
    for i, r in enumerate(d.rows):
        n = first + i
        ws.cell(n, 1, r.doc).data_type = "s"
        ws.cell(n, 2, r.sent_id).data_type = "s"
        ws.cell(n, 3, r.text).data_type = "s"
        for g, f in enumerate(rv.FIELDS):
            base = 4 + 4 * g
            a_c, b_c, fin_c, m_c = (get_column_letter(base + k) for k in range(4))
            a, b, fin, match = r.groups[g]
            for k, v in ((0, a), (1, b)):
                c = ws.cell(n, base + k, v)
                if isinstance(v, str):
                    c.data_type = "s"
            if fin is not None:
                c = ws.cell(n, base + 2, fin)
                if isinstance(fin, str):
                    c.data_type = "s"
            elif o.formulas:
                ws.cell(n, base + 2, f'=IF({a_c}{n}={b_c}{n},IF({b_c}{n}="","-",{b_c}{n}),"")')
            elif r.finals_eval[g] is not None:
                ws.cell(n, base + 2, r.finals_eval[g])
            ws.cell(n, base + 3, f'=IF(EXACT({a_c}{n},{b_c}{n}),"-","X")' if o.formulas else match)
        if o.reviewer_note and r.note:
            ws.cell(n, 4 + 32, r.note).data_type = "s"
    for row in ws.iter_rows(min_row=1, max_row=2):
        for c in row:
            c.font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=first, max_row=max(last, first)):
        for c in row:
            c.alignment = wrap
    widths = {1: 11.8, 2: 10.6, 3: 47.1}
    for col in range(1, 4 + 32 + (1 if o.reviewer_note else 0)):
        ws.column_dimensions[get_column_letter(col)].width = widths.get(col, 9 if (col - 4) % 4 == 3 else 16)
    if o.reviewer_note:
        ws.column_dimensions[get_column_letter(36)].width = 40
    ws.freeze_panes = "D3"
    if d.rows:  # Emotion Source: group 4, columns of A and B (T:U in the example)
        s_a = get_column_letter(4 + 4 * rv.FIELDS.index("source"))
        s_b = get_column_letter(4 + 4 * rv.FIELDS.index("source") + 1)
        dv = DataValidation(type="list", formula1=SOURCE_LIST, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{s_a}{first}:{s_b}{last}")
    tags = wb.create_sheet("Sheet1")
    for t in load_codebook().tags:
        tags.append([t])
    wb.save(path)
    return path


def write_comparison_csv(d: ComparisonData, path: Path) -> Path:
    r1, r2 = comparison_header_rows(d)
    if d.options.agreement_row:
        for g, f in enumerate(rv.FIELDS):
            r1[3 + 4 * g + 3] = f"{d.agreement[f]:.1f}%"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["" if v is None else v for v in r1])
        w.writerow(r2)
        for r in d.rows:
            cells: list = [r.doc, r.sent_id, r.text]
            for g in range(len(rv.FIELDS)):
                a, b, _, match = r.groups[g]
                cells += [a, b, r.finals_eval[g], match]
            if d.options.reviewer_note:
                cells.append(r.note)
            w.writerow(["" if v is None else v for v in cells])
    return path


def export_comparison(s: Session, opts: ComparisonExport, exports_dir: Path, fmt: str = "xlsx") -> tuple[Path, str, ComparisonData]:
    if fmt not in ("xlsx", "csv"):
        raise ExportError(f"unknown format {fmt!r}")
    d = build_comparison(s, opts)
    exports_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(d.filename).stem
    path = exports_dir / f"{stem}_{datetime.now():%H%M%S}.{fmt}"
    (write_comparison_xlsx if fmt == "xlsx" else write_comparison_csv)(d, path)
    return path, f"{stem}.{fmt}", d
