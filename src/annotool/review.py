"""Review logic (plan §8): compare two annotators' exported strings, track decisions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, selectinload

from . import models as m
from . import serialize as sz
from .store import load_annotation, raw_cells
from .validate import REASON_TEXT

FIELDS = sz.FIELDS
LABELS = sz.HEADERS
NOTE = "_note"
FINAL_CODE = "FINAL"  # annotator that holds structured Final edits (Edit… on the Review screen)


class ReviewError(Exception):
    pass


@dataclass
class Row:
    sentence: m.Sentence
    a: dict[str, str]
    b: dict[str, str]
    cues_a: list[list[int]]
    cues_b: list[list[int]]
    decisions: dict[str, m.ReviewDecision] = field(default_factory=dict)
    note: str = ""
    flags: list[dict] = field(default_factory=list)

    def match(self, f: str) -> bool:
        return self.a[f] == self.b[f]  # exact: case and whitespace sensitive, blank == blank

    @property
    def diffs(self) -> list[str]:
        return [f for f in FIELDS if not self.match(f)]

    def resolved(self, f: str) -> bool:
        d = self.decisions.get(f)
        return bool(d and d.resolved)

    @property
    def open_fields(self) -> list[str]:
        return [f for f in self.diffs if not self.resolved(f)]

    @property
    def status(self) -> str:
        if not self.diffs:
            return "agreed"
        return "open" if self.open_fields else "resolved"

    def final(self, f: str) -> str | None:
        d = self.decisions.get(f)
        if d and d.resolved:
            return d.final_value or ""
        return self.a[f] if self.match(f) else None

    def payload(self) -> dict:
        return {
            "id": self.sentence.id, "sent_id": self.sentence.sent_id, "doc": self.sentence.document_id,
            "text": self.sentence.text, "a": self.a, "b": self.b, "cues_a": self.cues_a, "cues_b": self.cues_b,
            "final": {f: self.final(f) for f in FIELDS}, "resolved": {f: self.resolved(f) for f in FIELDS},
            "status": self.status, "diff": len(self.diffs), "open": len(self.open_fields),
            "note": self.note, "flags": self.flags,
        }


def review_annotators(s: Session, batch: m.Batch) -> tuple[m.Annotator, m.Annotator]:
    if batch.kind != "review" or not batch.annotators_json or len(batch.annotators_json) != 2:
        raise ReviewError(f"{batch.name} is not a review batch")
    a, b = (s.get(m.Annotator, i) for i in batch.annotators_json)
    return a, b


def _flags(who: list[tuple[str, sz.Annotation | None, m.Annotation | None, dict]]) -> list[dict]:
    out: list[dict] = []
    blank = [code for code, ann, _, _ in who if ann is None or ann.status == "todo"]
    if len(blank) == 1:
        out.append({"level": "warn", "msg": f"{blank[0]} left this sentence unannotated."})
    for code, ann, row, raw in who:
        if ann is None:
            continue
        for e in ann.emotions:
            if e.tag and not e.cues:
                out.append({"level": "warn", "msg": f"{code} tagged “{e.tag}” but gave no emotion cue."})
        for fl in (row.flags_json if row else []) or []:
            msg = f"{code}: {fl['field']} — {REASON_TEXT.get(fl['reason'], fl['reason'])}"
            if fl.get("detail"):
                msg += f" (“{fl['detail']}”)"
            if raw.get(fl["field"]):
                msg += f"; legacy cell: {raw[fl['field']]}"
            out.append({"level": "warn", "msg": msg})
    return out


# Import flags whose legacy text is shown verbatim in the review (what the annotator really wrote).
# `outside_section2` is excluded on purpose: decision 4 exports those emotions' slots as `none`.
VERBATIM_REASONS = {"span_not_found", "segment_count_mismatch", "group_mismatch", "cue_tag_mismatch",
                    "unknown_value", "bad_arousal"}


def _legacy_value(f: str, raw: str, ann: sz.Annotation) -> str:
    """Verbatim legacy cell, whitespace-normalised; plain cues get their `tag:` prefix (decision 3)."""
    text = " ".join(raw.split())
    if f == "cues" and text:
        segs = [x.strip() for x in text.split(";;")]
        if len(segs) == len(ann.emotions):
            segs = [x if re.match(r"^[^:]*\s*:", x) else f"{e.tag}: {x}" for e, x in zip(ann.emotions, segs)]
        text = sz.SEP.join(segs)
    return text


def load_rows(s: Session, batch: m.Batch, only: int | None = None) -> list[Row]:
    """All rows of a review batch (or just the sentence with db id `only`)."""
    a, b = review_annotators(s, batch)
    anns = {
        (x.sentence_id, x.annotator_id): x
        for x in s.query(m.Annotation).join(m.Sentence)
        .filter(m.Sentence.batch_id == batch.id, m.Annotation.annotator_id.in_([a.id, b.id]),
                (m.Annotation.sentence_id == only) if only else True)
        .options(selectinload(m.Annotation.emotions).selectinload(m.Emotion.spans))
    }
    decs: dict[int, dict[str, m.ReviewDecision]] = {}
    for d in (s.query(m.ReviewDecision).join(m.Sentence)
              .filter(m.Sentence.batch_id == batch.id, (m.ReviewDecision.sentence_id == only) if only else True)):
        decs.setdefault(d.sentence_id, {})[d.field] = d
    rows = []
    for sent in batch.sentences:
        if only and sent.id != only:
            continue
        vals, structs = [], []
        for who in (a, b):
            row = anns.get((sent.id, who.id))
            ann = load_annotation(row) if row else None
            v = sz.serialize(sent.text, ann) if ann else {f: "" for f in FIELDS}
            raw = raw_cells(s, row.id) if row and row.flags_json else {}
            for fl in (row.flags_json if row else []) or []:
                if fl["reason"] in VERBATIM_REASONS and fl["field"] in raw:
                    v[fl["field"]] = _legacy_value(fl["field"], raw[fl["field"]], ann)
            vals.append(v)
            structs.append((who.code, ann, row, raw))
        d = decs.get(sent.id, {})

        def cue_spans(ann):
            return [[c.start, c.end] for e in (ann.emotions if ann else []) for c in e.cues]

        r = Row(sent, vals[0], vals[1], cue_spans(structs[0][1]), cue_spans(structs[1][1]),
                {k: v for k, v in d.items() if k != NOTE}, (d[NOTE].note or "") if NOTE in d else "")
        r.flags = _flags(structs)
        for f in r.diffs:  # differs only by case / whitespace
            if r.a[f] and r.b[f] and r.a[f].strip().lower() == r.b[f].strip().lower():
                r.flags.append({"level": "warn", "msg": f"{LABELS[f]} differs only by case or spacing."})
        rows.append(r)
    return rows


_PREFIX = re.compile(r"(^|;;\s*)[^:;]*:\s*")


def strip_cue_prefix(v: str) -> str:
    return _PREFIX.sub(lambda mt: mt.group(1), v)


def agreement(rows: list[Row], strip_cue_prefix: bool = False) -> dict[str, float]:
    """Percent of rows whose A and B strings match, per field (= COUNTIF("-")/COUNTA in the sheet).
    `strip_cue_prefix` compares cues without their `tag:` prefix (to check against the old sheet)."""
    out = {}
    for f in FIELDS:
        if strip_cue_prefix and f == "cues":
            hits = sum(strip_cue_prefix(r.a[f]) == strip_cue_prefix(r.b[f]) for r in rows)
        else:
            hits = sum(r.match(f) for r in rows)
        out[f] = 100 * hits / len(rows) if rows else 0.0
    return out


def summary(rows: list[Row]) -> dict:
    ag = agreement(rows)
    both = sum(1 for r in rows if any(r.a.values()) and any(r.b.values()))
    low = min(ag, key=ag.get) if ag else None
    return {"rows": len(rows), "both": both, "mean": sum(ag.values()) / len(ag) if ag else 0.0,
            "lowest_field": LABELS[low] if low else "", "lowest": ag[low] if low else 0.0,
            "agreement": ag, "open": sum(1 for r in rows if r.status == "open")}


# ------------------------------------------------------------------- decisions
def final_annotation_strings(s: Session, sentence: m.Sentence) -> dict[str, str] | None:
    fin = s.query(m.Annotator).filter_by(code=FINAL_CODE).first()
    row = fin and s.query(m.Annotation).filter_by(sentence_id=sentence.id, annotator_id=fin.id).first()
    return sz.serialize(sentence.text, load_annotation(row)) if row else None


def set_decision(s: Session, row: Row, fieldname: str, final_value: str | None, resolved: bool,
                 reviewer: str | None = None) -> None:
    """No free text (decision 7): a Final value must equal A's, B's, or the structured
    Final annotation's string for that field."""
    if fieldname not in FIELDS:
        raise ReviewError(f"unknown field {fieldname!r}")
    if resolved:
        allowed = {row.a[fieldname], row.b[fieldname]}
        edited = final_annotation_strings(s, row.sentence)
        if edited:
            allowed.add(edited[fieldname])
        if final_value is None or final_value not in allowed:
            raise ReviewError("Final must be A, B, or a value built with the structured editor")
    d = s.query(m.ReviewDecision).filter_by(sentence_id=row.sentence.id, field=fieldname).first()
    if not resolved:
        if d:
            s.delete(d)
        return
    if not d:
        d = m.ReviewDecision(sentence_id=row.sentence.id, field=fieldname)
        s.add(d)
    d.final_value, d.resolved, d.reviewer, d.updated_at = final_value, True, reviewer, datetime.now()


def set_note(s: Session, sentence_id: int, note: str, reviewer: str | None = None) -> None:
    d = s.query(m.ReviewDecision).filter_by(sentence_id=sentence_id, field=NOTE).first()
    if not note.strip():
        if d:
            s.delete(d)
        return
    if not d:
        d = m.ReviewDecision(sentence_id=sentence_id, field=NOTE, resolved=False)
        s.add(d)
    d.note, d.reviewer, d.updated_at = note, reviewer, datetime.now()


def seed_final(s: Session, sentence: m.Sentence, source: m.Annotator, overwrite: bool = False) -> m.Annotation:
    """Start the structured Final annotation from A's or B's structure (never from free text)."""
    from .store import save_annotation  # local import: store imports models only
    fin = s.query(m.Annotator).filter_by(code=FINAL_CODE).first()
    if not fin:
        fin = m.Annotator(code=FINAL_CODE, display_name="Final")
        s.add(fin)
        s.flush()
    row = s.query(m.Annotation).filter_by(sentence_id=sentence.id, annotator_id=fin.id).first()
    if row and not overwrite:
        return row
    src = s.query(m.Annotation).filter_by(sentence_id=sentence.id, annotator_id=source.id).first()
    return save_annotation(s, sentence.id, fin.id, load_annotation(src) if src else sz.Annotation(), keep_raw=False)


def create_review(s: Session, batch_name: str, code_a: str, code_b: str) -> m.Batch:
    """Snapshot two annotators' work on an annotate batch as a review batch (idempotent)."""
    src = s.query(m.Batch).filter_by(name=batch_name, kind="annotate").first()
    if not src:
        raise ReviewError(f"unknown annotate batch {batch_name!r}")
    if code_a == code_b:
        raise ReviewError("pick two different annotators")
    people = []
    for code in (code_a, code_b):
        a = s.query(m.Annotator).filter_by(code=code).first()
        has = a and s.query(m.Annotation).join(m.Sentence).filter(
            m.Sentence.batch_id == src.id, m.Annotation.annotator_id == a.id, m.Annotation.status != "todo").first()
        if not has:
            raise ReviewError(f"{code} has no annotations in {batch_name}")
        people.append(a)
    name = f"{src.name}_review_{code_a}_{code_b}"
    existing = s.query(m.Batch).filter_by(name=name).first()
    if existing:
        return existing
    from .store import save_annotation
    rb = m.Batch(name=name, kind="review", sheet_name=src.sheet_name, source_file=src.source_file,
                 annotators_json=[people[0].id, people[1].id])
    for sent in src.sentences:
        rb.sentences.append(m.Sentence(document_id=sent.document_id, sent_id=sent.sent_id,
                                       position=sent.position, text=sent.text))
    s.add(rb)
    s.flush()
    new_by_sid = {x.sent_id: x for x in rb.sentences}
    for who in people:
        for old in (s.query(m.Annotation).join(m.Sentence)
                    .filter(m.Sentence.batch_id == src.id, m.Annotation.annotator_id == who.id,
                            m.Annotation.status != "todo")
                    .options(selectinload(m.Annotation.emotions).selectinload(m.Emotion.spans))):
            row = save_annotation(s, new_by_sid[old.sentence.sent_id].id, who.id, load_annotation(old))
            row.flags_json = list(old.flags_json or [])  # keep the import flags and legacy text
            for f, text in raw_cells(s, old.id).items():
                s.add(m.RawCell(annotation_id=row.id, field=f, text=text))
    return rb
