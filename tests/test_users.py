"""Annotations stay tied to the annotator who made them; switching user never moves data."""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from annotool import importers as im
from annotool.app import create_app
from annotool.config import DataDirs
from conftest import HZY, RAW

BATCH = "TRAIN_1_clothing_shoes_and_jewelry"


@pytest.fixture
def client(tmp_path):
    dirs = DataDirs(tmp_path / "data").ensure()
    shutil.copy(RAW, dirs.raw); shutil.copy(HZY, dirs.imports)
    c = TestClient(create_app(dirs)); c.post("/api/rescan")
    return c, dirs


def test_one_card_per_batch_with_annotator_chips(client):
    c, _ = client
    html = c.get("/?annotator=OYC").text
    assert html.count('class="eyebrow">ANNOTATE') == 1                       # one card, not one per annotator
    assert "HZY · 694" in html and "OYC · 0" in html                          # both annotators' work stays separate
    assert 'data-code="OYC"' in html and "0 of 696 sentences" in html         # shows the current user's progress
    assert c.cookies.get("annotator") == "OYC"
    assert 'data-code="OYC"' in c.get("/").text                               # remembered without a param
    hzy = c.get(f"/?batch={BATCH}&view=HZY").text                             # viewing HZY's work
    assert "694 of 696 sentences" in hzy and c.cookies.get("annotator") == "OYC"


def test_viewing_a_card_does_not_switch_user(client):
    c, _ = client
    c.get("/?annotator=OYC")
    c.get(f"/?batch={BATCH}&view=HZY")
    assert c.cookies.get("annotator") == "OYC"


def test_rename_is_explicit_and_moves_data(client):
    c, _ = client
    assert c.patch("/api/annotators/HZY", data={"new_code": "ABC"}).json() == {"code": "ABC"}
    html = c.get("/?annotator=ABC").text
    assert "694 of 696 sentences" in html and "ABC · 694" in html and "HZY ·" not in html


def test_duplicate_upload_is_recognised(client):
    c, dirs = client
    for _ in range(2):
        with open(RAW, "rb") as f:
            assert c.post("/api/import/raw", files={"file": (RAW.name, f)}).status_code == 200
    assert len(list(dirs.raw.glob("*.xlsx"))) == 1                       # no TRAIN_1…__1.xlsx copy
    assert c.get("/").text.count('class="eyebrow">ANNOTATE') == 1


def test_copy_suffix_is_ignored_in_batch_names():
    assert im.batch_name_from_file(Path("TRAIN_1_clothing_shoes_and_jewelry_annotation__1.xlsx")) == BATCH
    assert im.annotator_from_file(Path("HZY_TRAIN_1_x_annotation__2.xlsx")) == "HZY"


def put_annotation(c, sent_id, code, body):
    from annotool import models as m
    with c.app.state.session_factory() as s:
        i = s.query(m.Sentence).filter_by(sent_id=sent_id).first().id
    assert c.put(f"/api/annotations/{i}/{code}", json=body).status_code == 200


def test_start_review_from_two_annotators(client):
    c, _ = client
    html = c.get("/").text
    assert "Start a review" not in html                                       # only HZY has work so far
    body = {"polarity": ["negative"], "arousal": 2, "source": "self",
            "emotions": [{"tag": "disappointment", "cues": [{"start": 0, "end": 0}]}]}
    put_annotation(c, "CS000001", "OYC", body)                                # second annotator
    put_annotation(c, "CS000002", "OYC", {**body, "polarity": ["positive"]})
    assert "Start a review" in c.get("/").text
    r = c.post("/api/reviews", json={"batch": BATCH, "a": "HZY", "b": "OYC"})
    assert r.status_code == 200
    name = r.json()["batch"]
    assert name == f"{BATCH}_review_HZY_OYC" and r.json()["url"].startswith(f"/review/{name}/")
    page = c.get(r.json()["url"])
    assert page.status_code == 200
    import json, re
    boot = json.loads(re.search(r'id="boot">(.*?)</script>', page.text, re.S).group(1))
    assert (boot["a"], boot["b"]) == ("HZY", "OYC") and len(boot["rows"]) == 696
    both = [x for x in boot["rows"] if x["a"]["polarity"] and x["b"]["polarity"]]
    assert len(both) == 2                                                      # only the sentences both annotated
    assert any(x["status"] == "open" for x in boot["rows"])
    # unchanged originals, idempotent, home card
    assert c.post("/api/reviews", json={"batch": BATCH, "a": "HZY", "b": "OYC"}).json()["batch"] == name
    assert "HZY × OYC" in c.get("/").text
    # HZY's legacy flags travelled with the copy (needs-attention text is kept)
    assert any("legacy cell" in f["msg"] for x in boot["rows"] for f in x["flags"])


def test_start_review_errors(client):
    c, _ = client
    for body in ({"batch": BATCH, "a": "HZY", "b": "HZY"}, {"batch": BATCH, "a": "HZY", "b": "OYC"},
                 {"batch": "nope", "a": "HZY", "b": "OYC"}, {"batch": BATCH, "a": "HZY", "b": "ZZZ"}):
        assert c.post("/api/reviews", json=body).status_code == 400


def test_remove_batch_needs_confirmation_and_cleans_everything(client):
    c, dirs = client
    from annotool import models as m
    assert c.delete(f"/api/batches/{BATCH}").status_code == 400
    assert c.delete(f"/api/batches/{BATCH}?confirm=wrong").status_code == 400
    r = c.delete(f"/api/batches/{BATCH}?confirm={BATCH}").json()
    assert r["sentences"] == 696 and r["annotations"] > 600
    with c.app.state.session_factory() as s:
        for t in (m.Batch, m.Sentence, m.Annotation, m.Emotion, m.Span, m.RawCell):
            assert s.query(t).count() == 0, t
    assert c.delete(f"/api/batches/{BATCH}?confirm={BATCH}").status_code == 404
    assert (dirs.raw / RAW.name).exists() and (dirs.imports / HZY.name).exists()   # input files untouched
