"""Validation rules for the Annotate "Checks" panel (plan §7).

level: ok | warn | error | na.  `error` = hard rule (Save & next asks for
confirmation; autosave is never blocked).  `warn` = soft rule.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .codebook import Codebook, load_codebook
from .serialize import Annotation

REASON_TEXT = {
    "segment_count_mismatch": "segment count did not match the emotions",
    "span_not_found": "text is not in the sentence",
    "outside_section2": "value on an emotion outside Section 2 (kept, not exported)",
    "group_mismatch": "action groups do not match the objects",
    "unknown_value": "unrecognised value",
    "sentence_text_differs": "annotated sentence differs from the raw file (raw text is used)",
    "cue_tag_mismatch": "cue tag differs from the emotion tag",
    "bad_arousal": "arousal is not an integer 1-5",
    "none_not_exclusive": "'none' was combined with other polarities",
}


@dataclass
class Check:
    id: str
    level: str
    msg: str

    def dict(self) -> dict:
        return asdict(self)


def attention_items(flags: list[dict], raw: dict[str, str]) -> list[dict]:
    """Import flags still awaiting an annotator's check, with the verbatim legacy text."""
    return [
        {
            "field": f["field"], "reason": f["reason"], "detail": f.get("detail", ""),
            "text": REASON_TEXT.get(f["reason"], f["reason"]), "raw": raw.get(f["field"], ""),
        }
        for f in flags
    ]


def validate(ann: Annotation, attention: list[dict] | None = None, cb: Codebook | None = None) -> list[Check]:
    cb = cb or load_codebook()
    out: list[Check] = []
    st = ann.status
    na = st == "todo"

    out.append(Check("polarity", "ok" if ann.polarity else "error",
                     "Polarity set" if ann.polarity else "Polarity missing"))
    if st == "none":
        out.append(Check("none", "ok", "Other facets skipped for “none”"))
    else:
        out.append(Check("arousal", "na" if na else ("ok" if ann.arousal else "error"),
                         "Arousal set" if ann.arousal else "Arousal missing"))
        out.append(Check("source", "na" if na else ("ok" if ann.source else "error"),
                         "Source set" if ann.source else "Source missing"))
        has_emo = bool(ann.emotions)
        out.append(Check("emotions", "na" if na else ("ok" if has_emo else "error"),
                         f"{len(ann.emotions)} emotion(s)" if has_emo else "At least one emotion is required"))
        if not na:
            bad = [e for e in ann.emotions if not e.tag]
            sug = [e.tag for e in ann.emotions if e.tag and e.tag not in cb.tags]
            if bad:
                out.append(Check("tags", "error", "Every emotion needs a tag"))
            elif sug:
                out.append(Check("tags", "warn", "Suggested new tag, flagged for review: " + ", ".join(sug)))
            elif has_emo:
                out.append(Check("tags", "ok", "All tags are valid codebook tags"))
            nocue = [e.tag or "?" for e in ann.emotions if not e.cues]
            if has_emo:
                out.append(Check("cues", "error" if nocue else "ok",
                                 ("Missing cue for: " + ", ".join(nocue)) if nocue
                                 else "Each emotion has at least one cue"))
            tags = [e.tag for e in ann.emotions if e.tag]
            dup = sorted({t for t in tags if tags.count(t) > 1})
            if dup:
                out.append(Check("dup", "warn", "Tag used more than once: " + ", ".join(dup)))
            for e in ann.emotions:
                if cb.is_section2(e.tag) and not e.objects and not e.actions:
                    out.append(Check("none?", "warn",
                                     f"{e.tag}: cause object and action are empty; they export as “none”"))
            for e in ann.emotions:
                if cb.is_section2(e.tag) and len(e.objects) >= 2 and e.actions:
                    covered = len({a.group for a in e.actions})
                    if covered < len(e.objects):
                        out.append(Check("groups", "warn",
                                         f"{e.tag}: actions cover {covered} of {len(e.objects)} objects; "
                                         "the export lists only action groups that exist"))
            out.append(Check("align", "ok", f"Columns stay aligned ({len(ann.emotions)} emotion(s), separated by ;;)"))
    for a in attention or []:
        out.append(Check(f"import:{a['field']}", "warn",
                         f"Imported {a['field']}: {a['text']}" + (f" — “{a['detail']}”" if a["detail"] else "")))
    return out


def has_errors(checks: list[Check]) -> bool:
    return any(c.level == "error" for c in checks)
