"""M7: resume point, grouping warning, friendly errors, Settings, migrations."""
import shutil

import pytest
from fastapi.testclient import TestClient

from annotool import models as m
from annotool import serialize as sz
from annotool.app import create_app
from annotool.config import DataDirs
from annotool.db import init_db, make_engine
from annotool.validate import validate
from conftest import HZY, RAW

BATCH = "TRAIN_1_clothing_shoes_and_jewelry"


@pytest.fixture
def client(tmp_path):
    dirs = DataDirs(tmp_path / "data").ensure()
    shutil.copy(RAW, dirs.raw); shutil.copy(HZY, dirs.imports)
    c = TestClient(create_app(dirs)); c.post("/api/rescan")
    return c, dirs


def sentence_id(c, sent_id):
    with c.app.state.session_factory() as s:
        return s.query(m.Sentence).filter_by(sent_id=sent_id).first().id


def test_continue_resumes_at_last_edited_sentence(client):
    c, _ = client
    html = c.get(f"/?view=HZY&batch={BATCH}").text
    assert "Continue · DCS0002, CS000033" in html          # nothing edited yet: first open sentence
    body = {"polarity": ["none"]}
    for sid in ("CS000005", "CS000012"):                   # edit two sentences; the later edit wins
        assert c.put(f"/api/annotations/{sentence_id(c, sid)}/HZY", json=body).status_code == 200
    html = c.get(f"/?view=HZY&batch={BATCH}").text
    assert "Continue · DCS0001, CS000012" in html or "Continue · DCS0002, CS000012" in html
    other = c.get(f"/?annotator=OYC&view=OYC&batch={BATCH}").text
    assert "Start · DCS0001, CS000001" in other            # OYC has not edited anything


def test_imports_do_not_count_as_edits(client):
    c, _ = client
    with c.app.state.session_factory() as s:
        assert s.query(m.Annotation).filter(m.Annotation.edited_at.isnot(None)).count() == 0


def test_actions_not_covering_every_object_warn():
    e = sz.Emotion("anger", cues=[sz.Span(0, 0)], objects=[sz.Span(1, 1, 0), sz.Span(3, 3, 1)], actions=[sz.Span(2, 2, 0)])
    ann = sz.Annotation(["negative"], 3, "self", [e])
    assert any(c.id == "groups" and c.level == "warn" for c in validate(ann))
    e.actions.append(sz.Span(4, 4, 1))
    assert not any(c.id == "groups" for c in validate(ann))


def test_group_choice_is_saved_and_exported(client):
    c, _ = client
    i = sentence_id(c, "CS000026")  # it looks nice , but it does not keep me warm ...  (it=0, it=5)
    spans = lambda *p: [{"start": a, "end": b, "group": g} for a, b, g in p]  # noqa: E731
    body = {"polarity": ["negative"], "arousal": 3, "source": "self", "emotions": [{
        "tag": "disappointment", "cues": spans((7, 7, 0)), "objects": spans((0, 0, 0), (5, 5, 1)),
        "actions": spans((1, 1, 1), (6, 6, 1), (7, 7, 0)), "experiencers": []}]}
    pv = c.put(f"/api/annotations/{i}/HZY", json=body).json()["preview"]
    assert pv["object"] == "it && it"
    assert pv["action"] == "not && looks // does"  # group 0 = "not"; group 1 = "looks // does"


def test_browser_gets_html_error_and_api_gets_json(client):
    c, _ = client
    page = c.get("/annotate/nope/CS1", headers={"accept": "text/html"})
    assert page.status_code == 404 and "Back to Tasks" in page.text and "<html" in page.text
    api = c.get("/annotate/nope/CS1")                      # no html accept header
    assert api.status_code == 404 and api.json()["detail"]
    assert c.get("/api/reports/missing.csv", headers={"accept": "text/html"}).json()["detail"] is not None


def test_settings_page_lists_folders_and_annotators(client):
    c, dirs = client
    html = c.get("/settings").text
    assert "Annotators" in html and "HZY" in html and "raw/" in html and "imports/" in html and "reports/" in html
    assert RAW.name in html and HZY.name in html
    assert "import_report_HZY" in html                      # the report written by the import
    assert "FINAL" not in html


def test_migration_adds_edited_at(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE annotation (id INTEGER PRIMARY KEY, sentence_id INTEGER, annotator_id INTEGER, status VARCHAR)")
    init_db(engine)
    with engine.begin() as c:
        assert "edited_at" in {r[1] for r in c.exec_driver_sql("PRAGMA table_info(annotation)")}
