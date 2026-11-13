"""Structured annotation <-> the ';;' '&&' '//' export strings (plan §5).

Pure module: no database, no I/O. Spans are token ranges over
``sentence.text.split(" ")``.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .codebook import Codebook, load_codebook

# Field keys in the fixed export order, with their spreadsheet headers.
FIELDS = ["polarity", "arousal", "tag", "cues", "source", "object", "action", "experiencer"]
HEADERS = {
    "polarity": "Polarity",
    "arousal": "Arousal",
    "tag": "Emotion Tag",
    "cues": "Emotion Cues",
    "source": "Emotion Source",
    "object": "Emotion Cause Object",
    "action": "Emotion Cause Action",
    "experiencer": "Emotion Experiencer",
}
SEP = ";; "

# Known typos that difflib alone would not catch.
TYPOS = {
    "tag": {"angry": "anger", "fascinated": "fascination"},
    "source": {"no other": "no one"},
    "polarity": {},
}


# --------------------------------------------------------------------------- model
@dataclass
class Span:
    start: int
    end: int  # inclusive
    group: int = 0

    def text(self, tokens: list[str]) -> str:
        return " ".join(tokens[self.start : self.end + 1])


@dataclass
class Emotion:
    tag: str
    cues: list[Span] = field(default_factory=list)
    objects: list[Span] = field(default_factory=list)
    actions: list[Span] = field(default_factory=list)  # Span.group = owning object's group
    experiencers: list[Span] = field(default_factory=list)


@dataclass
class Annotation:
    polarity: list[str] = field(default_factory=list)
    arousal: int | None = None
    source: str | None = None
    emotions: list[Emotion] = field(default_factory=list)

    @property
    def status(self) -> str:
        return derive_status(self.polarity)


def derive_status(polarity: list[str]) -> str:
    """No polarity -> todo; `none` -> none; otherwise done (plan §4)."""
    if not polarity:
        return "todo"
    if polarity == ["none"]:
        return "none"
    return "done"


@dataclass
class Flag:
    field: str
    reason: str
    detail: str = ""


@dataclass
class Correction:
    field: str
    original: str
    corrected: str


@dataclass
class ParseResult:
    annotation: Annotation
    flags: list[Flag] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    raw: dict[str, str] = field(default_factory=dict)  # verbatim cell text for flagged fields


# ----------------------------------------------------------------------- serialize
def tokens_of(text: str) -> list[str]:
    return text.split(" ")


def _join(parts: list[str]) -> str:
    return SEP.join(parts)


def _spans_text(spans: list[Span], tokens: list[str], sep: str) -> str:
    return sep.join(s.text(tokens) for s in spans)


def _actions_text(actions: list[Span], tokens: list[str]) -> str:
    groups: dict[int, list[Span]] = {}
    for s in actions:
        groups.setdefault(s.group, []).append(s)
    return " && ".join(" // ".join(s.text(tokens) for s in groups[g]) for g in sorted(groups))


def serialize(
    sentence: str,
    ann: Annotation,
    *,
    cue_prefix: bool = True,
    codebook: Codebook | None = None,
) -> dict[str, str]:
    """The 8 export strings. Blank string means an empty cell."""
    cb = codebook or load_codebook()
    out = {f: "" for f in FIELDS}
    status = ann.status
    if status == "todo":
        return out
    if status == "none":
        out["polarity"] = "none"
        return out

    tokens = tokens_of(sentence)
    out["polarity"] = _join(ann.polarity)
    out["arousal"] = "" if ann.arousal is None else str(int(ann.arousal))
    out["source"] = ann.source or ""
    out["tag"] = _join([e.tag for e in ann.emotions])

    cues, objs, acts, exps = [], [], [], []
    for e in ann.emotions:
        c = _spans_text(e.cues, tokens, ", ")
        cues.append((f"{e.tag}: {c}" if c else f"{e.tag}:") if cue_prefix else c)
        if cb.is_section2(e.tag):
            objs.append(_spans_text(e.objects, tokens, " && ") or "none")
            acts.append(_actions_text(e.actions, tokens) or "none")
            exps.append(_spans_text(e.experiencers, tokens, " && ") or "none")
        else:  # decision 4: locked, exported as none
            objs.append("none")
            acts.append("none")
            exps.append("none")
    out["cues"] = _join(cues)
    out["object"] = _join(objs)
    out["action"] = _join(acts)
    out["experiencer"] = _join(exps)
    return out


# ----------------------------------------------------------------------------- parse
def _clean(value) -> str:
    return "" if value is None else re.sub(r"\s+", " ", str(value)).strip()


def _correct(value: str, allowed, typos: dict[str, str]) -> tuple[str | None, bool]:
    """Return (canonical value or None, was_changed). Case/whitespace-only
    differences count as a change only when the letters differ."""
    v = value.strip().lower()
    if v in allowed:
        return v, v != value
    if v in typos:
        return typos[v], True
    m = difflib.get_close_matches(v, list(allowed), n=1, cutoff=0.8)
    return (m[0], True) if m else (None, False)


def find_span(tokens: list[str], text: str) -> tuple[int, int] | None:
    """First whole-token occurrence of ``text`` in ``tokens``."""
    words = text.split()
    n = len(words)
    if not n:
        return None
    for i in range(len(tokens) - n + 1):
        if tokens[i : i + n] == words:
            return i, i + n - 1
    return None


_CUE_SPLIT = re.compile(r"(?<!\s),\s+")


def _segments(cell) -> list[str]:
    s = _clean(cell)
    return [p.strip() for p in s.split(";;")] if s else []


def parse_annotation(
    sentence: str, cells: dict, codebook: Codebook | None = None, *, plain_cues: bool = False
) -> ParseResult:
    """Parse the 8 legacy cells (keyed by FIELDS) into structure.

    Never guesses: anything that cannot be mapped exactly is left out of the
    structure, kept verbatim in ``raw`` and listed in ``flags``.

    ``plain_cues``: cues carry no ``tag:`` prefix (the old comparison sheet); each
    segment belongs to the emotion at the same position, without a correction entry.
    """
    cb = codebook or load_codebook()
    tokens = tokens_of(sentence)
    res = ParseResult(Annotation())
    ann = res.annotation

    def flag(f, reason, detail=""):
        res.flags.append(Flag(f, reason, detail))

    # polarity ---------------------------------------------------------------
    pol_raw = _clean(cells.get("polarity"))
    if pol_raw:
        for part in pol_raw.split(";;"):
            part = part.strip()
            val, changed = _correct(part, cb.polarities, TYPOS["polarity"])
            if val is None:
                flag("polarity", "unknown_value", part)
                res.raw["polarity"] = _clean_keep(cells.get("polarity"))
            else:
                if changed:
                    res.corrections.append(Correction("polarity", part, val))
                if val not in ann.polarity:
                    ann.polarity.append(val)
        if "none" in ann.polarity and len(ann.polarity) > 1:
            flag("polarity", "none_not_exclusive", pol_raw)
            ann.polarity = ["none"]

    # arousal ----------------------------------------------------------------
    ar = cells.get("arousal")
    if _clean(ar):
        try:
            f = float(ar)
            if f == int(f) and 1 <= int(f) <= 5:
                ann.arousal = int(f)
            else:
                raise ValueError
        except (TypeError, ValueError):
            flag("arousal", "bad_arousal", _clean(ar))
            res.raw["arousal"] = _clean(ar)

    # source -----------------------------------------------------------------
    src = _clean(cells.get("source"))
    if src:
        val, changed = _correct(src, cb.sources, TYPOS["source"])
        if val is None:
            flag("source", "unknown_value", src)
            res.raw["source"] = src
        else:
            if changed:
                res.corrections.append(Correction("source", src, val))
            ann.source = val

    # tags -------------------------------------------------------------------
    tag_segs = _segments(cells.get("tag"))
    for seg in tag_segs:
        val, changed = _correct(seg, cb.tags, TYPOS["tag"])
        if val is None:
            flag("tag", "unknown_value", seg)
            ann.emotions.append(Emotion(tag=seg))
        else:
            if changed:
                res.corrections.append(Correction("tag", seg, val))
            ann.emotions.append(Emotion(tag=val))
    n = len(ann.emotions)

    def aligned(fname) -> list[str] | None:
        """Per-emotion segments, or None (flagged, raw kept) on a count mismatch."""
        segs = _segments(cells.get(fname))
        if len(segs) != n:
            if segs or n:
                flag(fname, "segment_count_mismatch", f"{len(segs)} segments for {n} emotions")
                res.raw[fname] = _clean_keep(cells.get(fname))
            return None
        return segs

    def one(fname, item, group):
        """Span for one item, or None (and a flag) when not found."""
        pos = find_span(tokens, item)
        if pos is None:
            flag(fname, "span_not_found", item)
            res.raw[fname] = res.raw.get(fname) or _clean_keep(cells.get(fname))
            return None
        return Span(pos[0], pos[1], group)

    # cues -------------------------------------------------------------------
    segs = aligned("cues")
    if segs is not None:
        for emo, seg in zip(ann.emotions, segs):
            m = re.match(r"^([^:]*?)\s*:\s*(.*)$", seg)
            if m:
                ctag, body = m.group(1).strip(), m.group(2)
                cval, changed = _correct(ctag, cb.tags, TYPOS["tag"])
                if cval is None or cval != emo.tag:
                    flag("cues", "cue_tag_mismatch", f"{ctag!r} vs {emo.tag!r}")
                    res.raw["cues"] = res.raw.get("cues") or _clean_keep(cells.get("cues"))
                elif changed:
                    res.corrections.append(Correction("cues", ctag, cval))
            else:
                body = seg
                if not plain_cues:
                    res.corrections.append(Correction("cues", seg, f"{emo.tag}: {seg}"))
            body = body.strip()
            if body and body.lower() != "none":
                for item in _CUE_SPLIT.split(body):
                    sp = one("cues", item.strip(), 0)
                    if sp:
                        emo.cues.append(sp)

    # object / action / experiencer ------------------------------------------
    for fname in ("object", "action", "experiencer"):
        segs = aligned(fname)
        if segs is None:
            continue
        for emo, seg in zip(ann.emotions, segs):
            if seg.lower() == "none" or seg == "":
                continue
            if not cb.is_section2(emo.tag):
                flag(fname, "outside_section2", f"{emo.tag}: {seg}")
                res.raw[fname] = res.raw.get(fname) or _clean_keep(cells.get(fname))
                continue
            groups = [g.strip() for g in seg.split("&&")]
            if fname == "action":
                for gi, g in enumerate(groups):
                    for item in (i.strip() for i in g.split("//")):
                        sp = one(fname, item, gi)
                        if sp:
                            emo.actions.append(sp)
                if emo.objects and len(groups) != len(emo.objects):
                    flag(fname, "group_mismatch", f"{len(groups)} action groups for {len(emo.objects)} objects")
            else:
                for gi, item in enumerate(groups):
                    sp = one(fname, item, gi)
                    if sp:
                        (emo.objects if fname == "object" else emo.experiencers).append(sp)
    return res


def _clean_keep(value) -> str:
    """Verbatim cell text (only None -> '')."""
    return "" if value is None else str(value)
