from annotool import serialize as sz

S = "i love it but it does not keep me warm ."
T = sz.tokens_of(S)


def sp(text, group=0):
    a, b = sz.find_span(T, text)
    return sz.Span(a, b, group)


def ann():
    return sz.Annotation(
        polarity=["negative", "positive"], arousal=3, source="self",
        emotions=[
            sz.Emotion("disappointment", cues=[sp("does not keep me warm")], objects=[sp("it", 0)],
                       actions=[sp("does not keep me warm")], experiencers=[sp("me")]),
            sz.Emotion("happiness", cues=[sp("love")], objects=[sp("it")], experiencers=[sp("i")]),
        ],
    )


def test_serialize_example_from_plan():
    out = sz.serialize(S, ann())
    assert out["polarity"] == "negative;; positive"
    assert out["arousal"] == "3"
    assert out["tag"] == "disappointment;; happiness"
    assert out["cues"] == "disappointment: does not keep me warm;; happiness: love"
    assert out["object"] == "it;; it"
    assert out["action"] == "does not keep me warm;; none"
    assert out["experiencer"] == "me;; i"


def test_cue_prefix_off():
    assert sz.serialize(S, ann(), cue_prefix=False)["cues"] == "does not keep me warm;; love"


def test_none_and_todo():
    assert sz.serialize(S, sz.Annotation(polarity=["none"])) == {**{f: "" for f in sz.FIELDS}, "polarity": "none"}
    assert all(v == "" for v in sz.serialize(S, sz.Annotation()).values())


def test_non_section2_emotion_exports_none():
    a = sz.Annotation(polarity=["positive"], arousal=2, source="self",
                      emotions=[sz.Emotion("relaxed", cues=[sp("love")], objects=[sp("it")])])
    out = sz.serialize(S, a)
    assert (out["object"], out["action"], out["experiencer"]) == ("none", "none", "none")


def test_multiple_objects_and_double_slash_only_for_one_object_two_actions():
    e = sz.Emotion("anger", cues=[sp("love")], objects=[sp("i", 0), sp("it", 1)],
                   actions=[sp("love", 0), sp("keep", 0), sp("warm", 1)])
    a = sz.Annotation(polarity=["negative"], arousal=4, source="self", emotions=[e])
    assert sz.serialize(S, a)["action"] == "love // keep && warm"
    assert sz.serialize(S, a)["object"] == "i && it"
    e.actions = [sp("love", 0)]
    assert "//" not in sz.serialize(S, a)["action"]  # a single action never gets //


def test_parse_basic_and_typos():
    cells = {"polarity": "posiitve", "arousal": 3.0, "tag": "suprise ", "cues": "suprise: love",
             "source": "sellf", "object": "it", "action": "keep", "experiencer": "i"}
    r = sz.parse_annotation(S, cells)
    assert r.annotation.polarity == ["positive"] and r.annotation.source == "self"
    assert r.annotation.emotions[0].tag == "surprise" and r.annotation.arousal == 3
    assert not r.flags
    assert {(c.field, c.corrected) for c in r.corrections} >= {("polarity", "positive"), ("source", "self"), ("tag", "surprise")}


def test_parse_flags_instead_of_guessing():
    base = {"polarity": "positive", "arousal": 3, "tag": "happiness;; anger", "source": "self"}
    r = sz.parse_annotation(S, {**base, "cues": "happiness: look nice;; anger: love",
                                "object": "it", "action": "none;; none", "experiencer": "none;; none"})
    reasons = {(f.field, f.reason) for f in r.flags}
    assert ("cues", "span_not_found") in reasons          # "look nice" is not in the sentence
    assert ("object", "segment_count_mismatch") in reasons  # 1 segment, 2 emotions: not guessed
    assert r.raw["object"] == "it" and r.annotation.emotions[0].objects == []


def test_parse_outside_section2_kept_raw():
    r = sz.parse_annotation(S, {"polarity": "positive", "arousal": 3, "tag": "love", "cues": "love: love",
                                "source": "self", "object": "it", "action": "none", "experiencer": "none"})
    assert [f.reason for f in r.flags] == ["outside_section2"]
    assert r.annotation.emotions[0].objects == [] and r.raw["object"] == "it"


def test_parse_none_polarity_and_blank():
    assert sz.parse_annotation(S, {"polarity": "none"}).annotation.status == "none"
    assert sz.parse_annotation(S, {}).annotation.status == "todo"


def test_find_span_is_whole_token():
    assert sz.find_span(T, "it") == (2, 2)      # not the "it" inside another word
    assert sz.find_span(T, "lov") is None
