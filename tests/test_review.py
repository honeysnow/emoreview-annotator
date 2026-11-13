import shutil

import openpyxl
import pytest
from fastapi.testclient import TestClient

from annotool import importers as im
from annotool import models as m
from annotool import review as rv
from annotool.app import create_app
from annotool.config import DataDirs
from annotool.db import init_db, make_engine
from conftest import FIXTURES

CMP = FIXTURES / "TRAIN_2_EmoReview40K_Comparison_Sheet_20250922.xlsx"
BATCH = "TRAIN_2_cell_phones_accessories"
# Reference agreement from the example workbook (plan §8)
REF = dict(polarity=79.3, arousal=76.5, tag=76.1, source=82.8, object=71.1, action=57.9, experiencer=79.3)


@pytest.fixture
def cmp_session(session):
    im.import_comparison(session, CMP)
    return session


@pytest.fixture
def rows(cmp_session):
    return rv.load_rows(cmp_session, cmp_session.query(m.Batch).filter_by(name=BATCH).first())


def test_import_shape(cmp_session):
    b = cmp_session.query(m.Batch).filter_by(name=BATCH).first()
    assert b.kind == "review" and len(b.sentences) == 599
    a, bb = rv.review_annotators(cmp_session, b)
    assert (a.code, bb.code) == ("OYC", "TJH")
    assert im.import_comparison(cmp_session, CMP).skipped == "batch already imported"
    with pytest.raises(im.ImportError_):
        im.import_comparison(cmp_session, FIXTURES / "HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx")


def test_agreement_regression_against_example_workbook(rows):
    ag = rv.agreement(rows)
    for f, want in REF.items():
        assert ag[f] == pytest.approx(want, abs=0.1), f
    # Decision 3 (cues carry the tag) lowers cue agreement a little vs the old sheet's 46.7.
    assert 44.0 <= ag["cues"] <= 46.7
    assert rv.summary(rows)["mean"] == pytest.approx(71, abs=1.5)


def test_reference_values_really_come_from_the_workbook():
    ws = openpyxl.load_workbook(CMP).worksheets[0]
    plain = [(r[15], r[16]) for r in ws.iter_rows(min_row=3, values_only=True) if r[1]]  # Emotion Cues A/B
    norm = lambda v: "" if v is None else str(v)  # noqa: E731
    assert 100 * sum(norm(a) == norm(b) for a, b in plain) / len(plain) == pytest.approx(46.7, abs=0.1)


def test_match_is_exact_and_blank_equals_blank(rows):
    r = next(x for x in rows if x.a["polarity"] == "" and x.b["polarity"] == "")
    assert r.match("polarity")
    r = rows[0]
    r.a["polarity"], r.b["polarity"] = "positive", "Positive"
    assert not r.match("polarity")  # case-sensitive
    r.b["polarity"] = "positive "
    assert not r.match("polarity")  # whitespace-sensitive


def test_plain_cues_get_tag_prefix(rows):
    r = next(x for x in rows if x.sentence.sent_id == "CP000723")  # "the cables are braided and seem durable ."
    assert r.a["cues"].startswith("happiness: ") and "durable" in r.a["cues"]


def test_imported_typed_finals_resolve_rows(rows, cmp_session):
    st = {s: sum(r.status == s for r in rows) for s in ("agreed", "resolved", "open")}
    assert sum(st.values()) == 599 and st["resolved"] > 0 and st["open"] > 0
    assert cmp_session.query(m.ReviewDecision).count() == 1060
    r = next(x for x in rows if x.status == "resolved")
    assert all(r.final(f) is not None for f in r.diffs)


def test_flags_blank_annotator_and_missing_cue(rows):
    blank = [r for r in rows if any("left this sentence unannotated" in f["msg"] for f in r.flags)]
    assert blank and all(any(r.a.values()) != any(r.b.values()) for r in blank)


@pytest.fixture
def client(tmp_path):
    dirs = DataDirs(tmp_path / "data").ensure()
    shutil.copy(CMP, dirs.imports)
    c = TestClient(create_app(dirs))
    res = c.post("/api/rescan").json()
    assert res["errors"] == [] and res["reports"][0]["kind"] == "comparison" and res["reports"][0]["rows_imported"] == 845
    return c


def open_row(c):
    page = c.get(f"/review/{BATCH}/CP000721").text
    import json, re
    boot = json.loads(re.search(r'id="boot">(.*?)</script>', page, re.S).group(1))
    return boot, next(r for r in boot["rows"] if r["status"] == "open" and r["open"] >= 1)


def test_review_page_and_home_card(client):
    boot, _ = open_row(client)
    assert boot["a"] == "OYC" and boot["b"] == "TJH" and len(boot["rows"]) == 599
    assert boot["summary"]["agreement"]["cues"] < 60
    home = client.get("/").text
    assert "OYC × TJH" in home and f"/review/{BATCH}/" in home
    assert client.get(f"/review/{BATCH}/NOPE").status_code == 404
    stats = client.get(f"/api/stats/agreement/{BATCH}").json()
    assert stats["agreement"]["polarity"] == pytest.approx(79.3, abs=0.1)


def test_use_a_use_b_and_no_free_text(client):
    _, row = open_row(client)
    f = next(k for k in row["final"] if row["final"][k] is None)
    ok = client.put(f"/api/review/{row['id']}/{f}", json={"final_value": row["b"][f], "resolved": True})
    assert ok.status_code == 200 and ok.json()["final"][f] == row["b"][f] and ok.json()["resolved"][f]
    bad = client.put(f"/api/review/{row['id']}/{f}", json={"final_value": "something I typed", "resolved": True})
    assert bad.status_code == 422                                         # decision 7: no free text
    assert client.put(f"/api/review/{row['id']}/nonsense", json={"final_value": "x"}).status_code == 422
    cleared = client.put(f"/api/review/{row['id']}/{f}", json={"resolved": False}).json()
    assert cleared["final"][f] is None and not cleared["resolved"][f]     # un-resolve


def test_use_a_for_all_open_fields(client):
    _, row = open_row(client)
    r = client.post(f"/api/review/{row['id']}/use/a").json()
    assert r["open"] == 0 and r["status"] == "resolved"
    assert all(r["final"][f] == r["a"][f] for f in r["final"] if r["a"][f] != r["b"][f] and row["final"][f] is None)
    assert client.post(f"/api/review/{row['id']}/use/c").status_code == 404


def test_note_roundtrip(client):
    _, row = open_row(client)
    r = client.put(f"/api/review/{row['id']}/_note", json={"note": "polarity is clear from 'great'"}).json()
    assert r["note"].startswith("polarity")
    assert client.put(f"/api/review/{row['id']}/_note", json={"note": ""}).json()["note"] == ""


def test_structured_final_edit_flow(client):
    _, row = open_row(client)
    f = "polarity"
    row = next(x for x in open_row(client)[0]["rows"] if x["status"] == "open" and x["a"][f] != x["b"][f])
    # Edit… seeds the structured FINAL annotation from A, opens Annotate (embedded) on it
    seed = client.post(f"/api/review/{row['id']}/final/seed", json={"source": "a"}).json()
    assert seed["url"].endswith("annotator=FINAL&embed=1")
    page = client.get(seed["url"])
    assert page.status_code == 200 and 'class="embed"' in page.text
    assert client.get(f"/api/review/{row['id']}/final").json()["polarity"] == row["a"][f]
    # the reviewer edits it with the normal Annotate API (structured, validated)
    sent_len = len(row["text"].split(" "))
    put = client.put(f"/api/annotations/{row['id']}/FINAL", json={
        "polarity": ["neutral"], "arousal": 3, "source": "self",
        "emotions": [{"tag": "doubt", "cues": [{"start": 0, "end": min(1, sent_len - 1)}]}]})
    assert put.status_code == 200
    final = client.get(f"/api/review/{row['id']}/final").json()
    assert final["polarity"] == "neutral"
    r = client.put(f"/api/review/{row['id']}/{f}", json={"final_value": final[f], "resolved": True})
    assert r.status_code == 200 and r.json()["final"][f] == "neutral"       # only structured output is accepted
    # FINAL never shows up as a selectable annotator
    assert "FINAL" not in [a["code"] for a in client.get("/api/annotators").json()]
    assert client.post("/api/annotators", data={"code": "FINAL"}).status_code == 400


def test_review_edits_survive_in_page(client):
    _, row = open_row(client)
    client.post(f"/api/review/{row['id']}/use/b")
    boot, _ = open_row(client)
    again = next(r for r in boot["rows"] if r["id"] == row["id"])
    assert again["open"] == 0


def test_migration_adds_column_to_old_database(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE batch (id INTEGER PRIMARY KEY, name VARCHAR, kind VARCHAR, sheet_name VARCHAR, source_file VARCHAR, created_at DATETIME)")
    init_db(engine)
    with engine.begin() as c:
        assert "annotators_json" in {r[1] for r in c.exec_driver_sql("PRAGMA table_info(batch)")}


def test_remove_review_batch_removes_decisions(client):
    from annotool import models as m
    r = client.delete(f"/api/batches/{BATCH}?confirm={BATCH}").json()
    assert r["decisions"] == 1060 and r["sentences"] == 599
    with client.app.state.session_factory() as s:
        assert s.query(m.ReviewDecision).count() == 0 and s.query(m.Annotation).count() == 0
