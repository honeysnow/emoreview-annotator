"""Persist / load structured annotations (ORM <-> serialize dataclasses)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from sqlalchemy.orm import Session

from . import models as m
from . import serialize as sz

SLOTS = {"cues": "cue", "objects": "obj", "actions": "act", "experiencers": "exp"}


def save_annotation(
    s: Session, sentence_id: int, annotator_id: int, result: sz.ParseResult | sz.Annotation,
    *, keep_raw: bool = False, acknowledged: list[str] | None = None,
) -> m.Annotation:
    """Create or replace the annotation for (sentence, annotator).

    keep_raw=True (used by the UI): keep the verbatim legacy cells and the
    import flags, minus flags on fields the annotator has `acknowledged`.
    """
    ann = result.annotation if isinstance(result, sz.ParseResult) else result
    flags = result.flags if isinstance(result, sz.ParseResult) else []
    raw = result.raw if isinstance(result, sz.ParseResult) else {}

    row = s.query(m.Annotation).filter_by(sentence_id=sentence_id, annotator_id=annotator_id).first()
    old_flags = list(row.flags_json or []) if row else []
    if row:
        row.emotions.clear()
        if not keep_raw:
            s.query(m.RawCell).filter_by(annotation_id=row.id).delete()
    else:
        row = m.Annotation(sentence_id=sentence_id, annotator_id=annotator_id)
        s.add(row)
    row.status = ann.status
    row.arousal = ann.arousal
    row.source = ann.source
    row.polarity_json = list(ann.polarity)
    if keep_raw:
        ack = set(acknowledged or [])
        row.flags_json = [f for f in old_flags if f["field"] not in ack]
    else:
        row.flags_json = [asdict(f) for f in flags]
    row.updated_at = datetime.now()
    if keep_raw:  # an edit made in the UI (imports and review snapshots leave this empty)
        row.edited_at = row.updated_at

    for i, emo in enumerate(ann.emotions):
        e = m.Emotion(ord=i, tag=emo.tag)
        for attr, slot in SLOTS.items():
            for j, sp in enumerate(getattr(emo, attr)):
                e.spans.append(
                    m.Span(slot=slot, ord=j, group_no=sp.group, start_tok=sp.start, end_tok=sp.end)
                )
        row.emotions.append(e)
    s.flush()
    for fname, text in raw.items():
        s.add(m.RawCell(annotation_id=row.id, field=fname, text=text))
    return row


def load_annotation(row: m.Annotation) -> sz.Annotation:
    ann = sz.Annotation(
        polarity=list(row.polarity_json or []), arousal=row.arousal, source=row.source
    )
    for e in row.emotions:
        emo = sz.Emotion(tag=e.tag)
        for sp in sorted(e.spans, key=lambda x: (x.slot, x.ord)):
            attr = next(a for a, slot in SLOTS.items() if slot == sp.slot)
            getattr(emo, attr).append(sz.Span(sp.start_tok, sp.end_tok, sp.group_no))
        ann.emotions.append(emo)
    return ann


def raw_cells(s: Session, annotation_id: int) -> dict[str, str]:
    return {r.field: r.text for r in s.query(m.RawCell).filter_by(annotation_id=annotation_id)}


def delete_batch(s: Session, batch: m.Batch) -> dict:
    """Delete a batch with its sentences, annotations (emotions, spans, raw cells) and review decisions."""
    sids = [i for (i,) in s.query(m.Sentence.id).filter_by(batch_id=batch.id)]
    n_ann = n_dec = 0
    if sids:
        for a in s.query(m.Annotation).filter(m.Annotation.sentence_id.in_(sids)):
            s.query(m.RawCell).filter_by(annotation_id=a.id).delete()
            s.delete(a)  # cascades emotions and spans
            n_ann += 1
        n_dec = s.query(m.ReviewDecision).filter(m.ReviewDecision.sentence_id.in_(sids)).delete(synchronize_session=False)
    s.flush()
    s.delete(batch)  # cascades sentences
    return {"annotations": n_ann, "decisions": n_dec, "sentences": len(sids)}
