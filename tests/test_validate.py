from annotool import serialize as sz
from annotool.validate import attention_items, has_errors, validate


def levels(ann, att=None):
    return {c.id: c.level for c in validate(ann, att)}


def emo(tag, cues=1, obj=0):
    e = sz.Emotion(tag, cues=[sz.Span(0, 0)] * cues)
    e.objects = [sz.Span(1, 1)] * obj
    return e


def done(*emotions, arousal=3, source="self"):
    return sz.Annotation(["positive"], arousal, source, list(emotions))


def test_todo_only_flags_polarity():
    lv = levels(sz.Annotation())
    assert lv["polarity"] == "error" and lv["arousal"] == "na" and has_errors(validate(sz.Annotation()))


def test_none_is_fine():
    assert not has_errors(validate(sz.Annotation(["none"])))


def test_hard_rules():
    assert not has_errors(validate(done(emo("happiness", obj=1))))
    assert levels(done(emo("happiness"), arousal=None))["arousal"] == "error"        # rule 2
    assert levels(done(emo("happiness"), source=None))["source"] == "error"          # rule 2
    assert levels(sz.Annotation(["positive"], 3, "self", []))["emotions"] == "error"  # rule 2
    assert levels(done(emo("happiness", cues=0)))["cues"] == "error"                 # rule 4
    assert levels(done(emo("")))["tags"] == "error"                                  # rule 3


def test_suggested_tag_is_warning_not_error():
    assert levels(done(emo("nostalgia")))["tags"] == "warn"


def test_soft_rules():
    lv = levels(done(emo("happiness"), emo("happiness")))
    assert lv["dup"] == "warn" and lv["none?"] == "warn"            # rules 5 and 6
    assert "none?" not in levels(done(emo("relaxed")))               # non-Section-2: slots locked, nothing to prompt


def test_import_flags_show_as_warnings():
    att = attention_items([{"field": "object", "reason": "segment_count_mismatch", "detail": ""}], {"object": "needle"})
    assert att[0]["raw"] == "needle"
    assert levels(done(emo("happiness", obj=1)), att)["import:object"] == "warn"
