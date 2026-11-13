import shutil

import pytest
from fastapi.testclient import TestClient

from annotool import models as m
from annotool.app import create_app
from annotool.config import DataDirs
from conftest import HZY, RAW


@pytest.fixture
def client(tmp_path):
    dirs = DataDirs(tmp_path / "data").ensure()
    shutil.copy(RAW, dirs.raw)
    c = TestClient(create_app(dirs))
    c.post("/api/rescan")  # imports the raw batch
    return c, dirs


@pytest.fixture
def loaded(client):
    c, dirs = client
    shutil.copy(HZY, dirs.imports)
    c.post("/api/rescan")
    return c


def sid(c, sent_id):
    with c.app.state.session_factory() as s:
        return s.query(m.Sentence).filter_by(sent_id=sent_id).first().id


BODY = {
    "polarity": ["negative", "positive"], "arousal": 3, "source": "self",
    "emotions": [
        {"tag": "disappointment", "cues": [{"start": 5, "end": 9}], "objects": [{"start": 0, "end": 0, "group": 0}],
         "actions": [{"start": 5, "end": 9, "group": 0}], "experiencers": [{"start": 8, "end": 8}]},
        {"tag": "relaxed", "cues": [{"start": 0, "end": 1}], "objects": [{"start": 2, "end": 2}]},  # locked slot, must be dropped
    ],
}


def test_annotate_page_renders_document(loaded):
    r = loaded.get("/annotate/TRAIN_1_clothing_shoes_and_jewelry/CS000026?annotator=HZY")
    assert r.status_code == 200 and "CS000026" in r.text and 'id="boot"' in r.text
    assert loaded.get("/annotate/TRAIN_1_clothing_shoes_and_jewelry/NOPE").status_code == 404


def test_put_saves_and_returns_server_preview(client):
    c, _ = client
    i = sid(c, "CS000026")  # "it looks nice , but it does not keep me warm in northern colorado ..."
    r = c.put(f"/api/annotations/{i}/HZY", json=BODY)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "done"
    assert d["preview"]["polarity"] == "negative;; positive" and d["preview"]["tag"] == "disappointment;; relaxed"
    assert d["preview"]["cues"] == "disappointment: it does not keep me;; relaxed: it looks"
    # decision 4: the non-Section-2 emotion's object was dropped and exports as none
    assert d["preview"]["object"] == "it;; none" and d["preview"]["action"].endswith(";; none")
    again = c.get("/annotate/TRAIN_1_clothing_shoes_and_jewelry/CS000026?annotator=HZY").text
    assert "disappointment" in again  # persisted


def test_put_replaces_and_none_is_exclusive(client):
    c, _ = client
    i = sid(c, "CS000026")
    c.put(f"/api/annotations/{i}/HZY", json=BODY)
    d = c.put(f"/api/annotations/{i}/HZY", json={"polarity": ["none", "positive"], "emotions": BODY["emotions"]}).json()
    assert d["status"] == "none" and d["preview"]["tag"] == "" and d["preview"]["polarity"] == "none"


@pytest.mark.parametrize("bad", [
    {"polarity": ["happy"]}, {"polarity": ["positive"], "source": "nobody"},
    {"polarity": ["positive"], "arousal": 9},
    {"polarity": ["positive"], "emotions": [{"tag": "anger", "cues": [{"start": 0, "end": 999}]}]},
    {"polarity": ["positive"], "emotions": [{"tag": "anger", "cues": [{"start": 3, "end": 1}]}]},
])
def test_put_rejects_bad_input(client, bad):
    c, _ = client
    assert c.put(f"/api/annotations/{sid(c, 'CS000026')}/HZY", json=bad).status_code == 422


def test_put_unknown_targets(client):
    c, _ = client
    assert c.put("/api/annotations/999999/HZY", json={}).status_code == 404
    assert c.put(f"/api/annotations/{sid(c, 'CS000026')}/ZZZ", json={}).status_code == 404


def test_import_flags_survive_edits_until_acknowledged(loaded):
    # CS000005 is imported with flagged (legacy) fields
    with loaded.app.state.session_factory() as s:
        row = (s.query(m.Annotation).join(m.Sentence)
               .filter(m.Sentence.sent_id == "CS000023").first())  # one shared object segment for 2 emotions
        assert row.flags_json
        raw_fields = {r.field for r in s.query(m.RawCell).filter_by(annotation_id=row.id)}
        sent_id = row.sentence_id
    d = loaded.put(f"/api/annotations/{sent_id}/HZY", json={"polarity": ["negative"], "arousal": 3, "source": "self",
                   "emotions": [{"tag": "doubt", "cues": [{"start": 0, "end": 1}]}]}).json()
    assert {a["field"] for a in d["attention"]} == {f["field"] for f in row.flags_json}  # untouched by an edit
    fields = sorted({a["field"] for a in d["attention"]})
    d = loaded.put(f"/api/annotations/{sent_id}/HZY", json={"polarity": ["negative"], "arousal": 3, "source": "self",
                   "emotions": [], "acknowledged": fields}).json()
    assert d["attention"] == []
    with loaded.app.state.session_factory() as s:  # verbatim legacy text is never deleted
        assert {r.field for r in s.query(m.RawCell).filter_by(annotation_id=row.id)} == raw_fields


def test_outside_section2_is_highlighted_for_checking(loaded):
    with loaded.app.state.session_factory() as s:
        row = None
        for a in s.query(m.Annotation).all():
            if any(f["reason"] == "outside_section2" for f in a.flags_json or []):
                row = a; break
        sent_id_, batch_ = row.sentence.sent_id, row.sentence.batch.name
    import json, re
    page = loaded.get(f"/annotate/{batch_}/{sent_id_}?annotator=HZY").text
    boot = json.loads(re.search(r'id="boot">(.*?)</script>', page, re.S).group(1))
    st = next(x for x in boot["sentences"] if x["sent_id"] == sent_id_)
    assert any(a["reason"] == "outside_section2" and a["raw"] for a in st["attention"])


def test_rename_annotator_keeps_data(loaded):
    i = sid(loaded, "CS000026")
    loaded.put(f"/api/annotations/{i}/HZY", json=BODY)
    assert loaded.patch("/api/annotators/HZY", data={"new_code": "abc"}).json() == {"code": "ABC"}
    page = loaded.get("/annotate/TRAIN_1_clothing_shoes_and_jewelry/CS000026?annotator=ABC").text
    assert "disappointment" in page and "Annotator: ABC" in page
    assert loaded.patch("/api/annotators/ABC", data={"new_code": "OYC"}).json() == {"code": "OYC"}  # unused seeded code is freed
    assert loaded.patch("/api/annotators/OYC", data={"new_code": "HZY"}).json() == {"code": "HZY"}
    other = loaded.post("/api/annotators", data={"code": "TJH"}).json()["code"]
    i2 = sid(loaded, "CS000027")
    assert loaded.put(f"/api/annotations/{i2}/TJH", json={"polarity": ["none"]}).status_code == 200
    assert loaded.patch("/api/annotators/HZY", data={"new_code": "TJH"}).status_code == 400     # has data
    assert loaded.patch("/api/annotators/HZY", data={"new_code": "ABC"}).json() == {"code": "ABC"}
    assert loaded.patch("/api/annotators/ABC", data={"new_code": "x"}).status_code == 400
