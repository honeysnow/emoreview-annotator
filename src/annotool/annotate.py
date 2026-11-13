"""Annotate-screen service layer: JSON <-> domain, payloads for the page and API."""
from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import models as m
from . import serialize as sz
from .codebook import load_codebook
from .store import load_annotation, raw_cells
from .validate import attention_items, has_errors, validate


class SpanIn(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    group: int = Field(default=0, ge=0)


class EmotionIn(BaseModel):
    tag: str = Field(max_length=60)
    cues: list[SpanIn] = []
    objects: list[SpanIn] = []
    actions: list[SpanIn] = []
    experiencers: list[SpanIn] = []


class AnnotationIn(BaseModel):
    polarity: list[str] = []
    arousal: int | None = Field(default=None, ge=1, le=5)
    source: str | None = None
    emotions: list[EmotionIn] = []
    acknowledged: list[str] = []  # fields whose import flags the annotator has checked


def to_domain(body: AnnotationIn, n_tokens: int) -> sz.Annotation:
    """Validate and normalise a PUT body. Raises ValueError with a user-facing message."""
    cb = load_codebook()
    pol = []
    for p in body.polarity:
        if p not in cb.polarities:
            raise ValueError(f"unknown polarity {p!r}")
        if p not in pol:
            pol.append(p)
    if "none" in pol:
        pol = ["none"]
    if body.source is not None and body.source not in cb.sources:
        raise ValueError(f"unknown source {body.source!r}")

    def spans(items: list[SpanIn]) -> list[sz.Span]:
        out = []
        for sp in items:
            if sp.end < sp.start or sp.end >= n_tokens:
                raise ValueError(f"span {sp.start}-{sp.end} is outside the sentence")
            out.append(sz.Span(sp.start, sp.end, sp.group))
        return out

    ann = sz.Annotation(polarity=pol, arousal=body.arousal, source=body.source)
    if pol == ["none"]:
        return sz.Annotation(polarity=pol)  # `none` is exclusive: everything else is dropped
    for e in body.emotions:
        emo = sz.Emotion(tag=e.tag.strip(), cues=spans(e.cues))
        if cb.is_section2(emo.tag):  # decision 4: other tags are locked
            emo.objects, emo.actions, emo.experiencers = spans(e.objects), spans(e.actions), spans(e.experiencers)
        ann.emotions.append(emo)
    return ann


def to_json(ann: sz.Annotation) -> dict:
    sp = lambda spans: [{"start": s.start, "end": s.end, "group": s.group} for s in spans]  # noqa: E731
    return {
        "polarity": ann.polarity, "arousal": ann.arousal, "source": ann.source,
        "emotions": [
            {"tag": e.tag, "cues": sp(e.cues), "objects": sp(e.objects),
             "actions": sp(e.actions), "experiencers": sp(e.experiencers)}
            for e in ann.emotions
        ],
    }


def sentence_state(s: Session, sentence: m.Sentence, annotator: m.Annotator) -> dict:
    """Everything the page needs to know about one sentence for one annotator."""
    row = (
        s.query(m.Annotation).filter_by(sentence_id=sentence.id, annotator_id=annotator.id).first()
    )
    ann = load_annotation(row) if row else sz.Annotation()
    attention = attention_items(row.flags_json or [], raw_cells(s, row.id)) if row else []
    return {
        "id": sentence.id, "sent_id": sentence.sent_id, "position": sentence.position,
        "text": sentence.text, "annotation": to_json(ann), **derived(sentence.text, ann, attention),
    }


def derived(text: str, ann: sz.Annotation, attention: list[dict]) -> dict:
    checks = validate(ann, attention)
    return {
        "status": ann.status,
        "preview": sz.serialize(text, ann),
        "checks": [c.dict() for c in checks],
        "has_errors": has_errors(checks),
        "attention": attention,
    }
