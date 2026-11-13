"""Serializer round-trip over the real HZY file (plan §11.1)."""
import re
from collections import Counter

import openpyxl
import pytest

from annotool import importers as im
from annotool import models as m
from annotool import serialize as sz
from annotool.store import load_annotation
from conftest import HZY, RAW


def norm(v, field=None):
    """Whitespace / number normalisation allowed by the plan."""
    s = "" if v is None else str(v)
    if field == "cues":  # `tag:cue` and `tag: cue` are the same cue
        s = re.sub(r"(^|;;)\s*(\w+)\s*:\s*", r"\1 \2: ", s)
    if re.fullmatch(r"\d+\.0", s.strip()):
        s = s.strip()[:-2]
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s*(;;|&&|//)\s*", lambda x: f" {x.group(1)} " if x.group(1) != ";;" else ";; ", s)


@pytest.fixture
def imported(session):
    im.import_raw(session, RAW)
    rep = im.import_annotated(session, HZY)
    return session, rep


def legacy_cells():
    ws = openpyxl.load_workbook(HZY, read_only=True).worksheets[0]
    for r in ws.iter_rows(min_row=2, values_only=True):
        r = tuple(r) + (None,) * (11 - len(r))  # read-only rows may be short
        if r[1]:
            sid = r[1] if re.fullmatch(r"CS\d{6}", str(r[1])) else "CS000545"  # corrupted SentID, repaired on import
            yield sid, r[2], dict(zip(sz.FIELDS, r[3:11]))


def test_import_counts(imported):
    _, rep = imported
    assert rep.rows_total == 696
    assert rep.rows_imported == 696 and rep.unmatched == []
    assert {"sent_id": "CS000545", "field": "sent_id",
            "original": "i ordered a large , which was probably the best size for me .",
            "corrected": "CS000545"} in rep.corrections


def test_clean_rows_roundtrip_exactly(imported):
    """Every row with no flags and no corrections must serialize back to the
    original cells (after whitespace/3.0 normalisation) — via the database."""
    s, _ = imported
    originals = {sid: cells for sid, _, cells in legacy_cells()}
    ann = {
        a.sentence.sent_id: a
        for a in s.query(m.Annotation).all()
    }
    exact = failures = 0
    for sid, row in ann.items():
        if row.flags_json:
            continue
        got = sz.serialize(row.sentence.text, load_annotation(row))
        want = {f: norm(originals[sid][f], f) for f in sz.FIELDS}
        got = {f: norm(v, f) for f, v in got.items()}
        # corrections (typos / cue prefix) are recorded, not exact: checked below
        if got == want:
            exact += 1
        else:
            failures += 1
            # the only allowed difference is a recorded auto-correction
            assert any(
                c for c in _rows_with_corrections(s, sid)
            ), (sid, {f: (want[f], got[f]) for f in sz.FIELDS if want[f] != got[f]})
    print("exact", exact, "corrected-only", failures)
    assert (exact, failures) == (441, 11)  # 441 exact + 11 typo-corrected + 244 flagged = 696


def _rows_with_corrections(s, sid):
    cells = next(c for i, _, c in legacy_cells() if i == sid)
    text = s.query(m.Sentence).filter_by(sent_id=sid).first().text
    return sz.parse_annotation(text, cells).corrections


def test_serialize_is_idempotent_for_unflagged_rows(imported):
    """parse(serialize(x)) serializes to the same strings (fixed point)."""
    s, _ = imported
    for row in s.query(m.Annotation).all():
        if row.flags_json:
            continue
        text = row.sentence.text
        once = sz.serialize(text, load_annotation(row))
        again = sz.serialize(text, sz.parse_annotation(text, once).annotation)
        assert again == once, row.sentence.sent_id


def test_flagged_rows_are_explicit(imported):
    """Flagged rows are limited to known reasons; the totals are pinned so a
    parser change that silently flags/unflags rows fails here."""
    _, rep = imported
    reasons = Counter(f["reason"] for f in rep.flags)
    assert set(reasons) <= {
        "segment_count_mismatch", "span_not_found", "outside_section2", "group_mismatch",
        "unknown_value", "sentence_text_differs", "cue_tag_mismatch", "bad_arousal",
        "none_not_exclusive",
    }
    assert rep.rows_flagged == 244 and len(rep.corrections) == 30
    assert reasons["segment_count_mismatch"] == 551 and reasons["span_not_found"] == 94


def test_known_typos_are_corrected_and_reported(imported):
    _, rep = imported
    fixes = {(c["field"], c["original"], c["corrected"]) for c in rep.corrections}
    assert ("polarity", "posiitve", "positive") in fixes
    assert ("source", "sellf", "self") in fixes
    assert ("tag", "suprise", "surprise") in fixes
    assert ("source", "no other", "no one") in fixes


def test_outside_section2_kept_raw_not_exported(imported):
    s, rep = imported
    assert rep.outside_section2_rows > 0
    flagged = [f for f in rep.flags if f["reason"] == "outside_section2"]
    sid = flagged[0]["sent_id"]
    row = (
        s.query(m.Annotation).join(m.Sentence).filter(m.Sentence.sent_id == sid).first()
    )
    assert any(r.text for r in s.query(m.RawCell).filter_by(annotation_id=row.id))
    out = sz.serialize(row.sentence.text, load_annotation(row))
    for e in load_annotation(row).emotions:
        if not e.tag in ("happiness sadness anger surprise fear disgust disappointment hate".split()):
            assert e.objects == e.actions == e.experiencers == []
