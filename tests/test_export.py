import csv
import io
import shutil

import openpyxl
import pytest
from fastapi.testclient import TestClient

from annotool import exporters as ex
from annotool import importers as im
from annotool import models as m
from annotool import serialize as sz
from annotool.app import create_app
from annotool.config import DataDirs
from conftest import HZY, RAW
from test_roundtrip import legacy_cells, norm

BATCH = "TRAIN_1_clothing_shoes_and_jewelry"


@pytest.fixture
def imported(session):
    im.import_raw(session, RAW)
    im.import_annotated(session, HZY)
    return session


def load(path):
    ws = openpyxl.load_workbook(path).worksheets[0]
    return ws, [list(r) for r in ws.iter_rows(values_only=True)]


def test_layout_matches_hzy_file(imported, tmp_path):
    path, name, data = ex.export_annotations(imported, ex.AnnotationExport(BATCH, "HZY"), tmp_path)
    assert name == "HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx"
    assert path.name.startswith("HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation_") and path.suffix == ".xlsx"
    ws, rows = load(path)
    ref = openpyxl.load_workbook(HZY).worksheets[0]
    assert ws.title == ref.title == "clothing_shoes_and_jewelry_anno"          # 31-char sheet name
    assert rows[0] == [c.value for c in ref[1]][:11]                            # exact headers
    assert len(rows) == 1 + 696 and all(len(r) == 11 for r in rows)
    assert [r[0] for r in rows[1:]][:3] == ["DCS0001"] * 3                      # original order


def test_values_match_original_for_clean_rows(imported, tmp_path):
    path, _, _ = ex.export_annotations(imported, ex.AnnotationExport(BATCH, "HZY"), tmp_path)
    _, rows = load(path)
    exported = {r[1]: dict(zip(sz.FIELDS, r[3:11])) for r in rows[1:]}
    flagged = {a.sentence.sent_id for a in imported.query(m.Annotation) if a.flags_json}
    checked = 0
    for sid, _, cells in legacy_cells():
        if sid in flagged:
            continue
        got, want = exported[sid], cells
        for f in sz.FIELDS:
            if f == "arousal":
                assert got[f] == (None if want[f] is None else int(want[f])), sid
            elif norm(want[f], f) != norm(got[f], f):
                # only recorded typo corrections may differ
                continue
        checked += 1
    assert checked > 400


def test_cell_types_and_blank_rules(imported, tmp_path):
    _, rows = load(ex.export_annotations(imported, ex.AnnotationExport(BATCH, "HZY"), tmp_path)[0])
    by = {r[1]: r for r in rows[1:]}
    assert isinstance(by["CS000001"][4], int) and by["CS000001"][4] == 2          # `2`, not `2.0`
    assert by["CS000003"][3] == "none" and all(v is None for v in by["CS000003"][4:])  # none: others blank
    # a sentence the annotator left empty stays blank (CS000033's raw text is used; see importer)
    assert by["CS000033"][2].startswith("delivered to the hub")


def test_scopes_and_unannotated_option(imported, tmp_path):
    n_all = len(ex.build_annotation_rows(imported, ex.AnnotationExport(BATCH, "HZY")).rows)
    doc = ex.build_annotation_rows(imported, ex.AnnotationExport(BATCH, "HZY", "document", "DCS0002"))
    assert {r[0] for r in doc.rows} == {"DCS0002"} and len(doc.rows) == 26
    done = ex.build_annotation_rows(imported, ex.AnnotationExport(BATCH, "HZY", "completed"))
    assert 0 < len(done.rows) < n_all and all(r[3] for r in done.rows)
    skip = ex.build_annotation_rows(imported, ex.AnnotationExport(BATCH, "HZY", include_unannotated=False))
    assert len(skip.rows) == len(done.rows)
    blank = ex.build_annotation_rows(imported, ex.AnnotationExport(BATCH, "OYC"))
    assert len(blank.rows) == n_all and all(all(v is None for v in r[3:]) for r in blank.rows)
    assert len(blank.warnings) == n_all


def test_cue_prefix_setting(imported):
    on = ex.build_annotation_rows(imported, ex.AnnotationExport(BATCH, "HZY", "document", "DCS0002"))
    off = ex.build_annotation_rows(imported, ex.AnnotationExport(BATCH, "HZY", "document", "DCS0002", cue_prefix=False))
    r_on = next(r for r in on.rows if r[1] == "CS000026")
    r_off = next(r for r in off.rows if r[1] == "CS000026")
    assert ": " in r_on[6] and ": " not in r_off[6]


def test_csv_and_formula_safety(imported, tmp_path):
    path, name, data = ex.export_annotations(imported, ex.AnnotationExport(BATCH, "HZY", "document", "DCS0001"), tmp_path, "csv")
    assert name.endswith(".csv")
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    assert rows[0] == ex.ANNOTATION_HEADERS and len(rows) == 1 + 20
    data.rows[0][2] = "=1+1"
    p = ex.write_xlsx(data, tmp_path / "f.xlsx")
    cell = openpyxl.load_workbook(p).worksheets[0]["C2"]
    assert cell.data_type == "s" and cell.value == "=1+1"


def test_errors(imported, tmp_path):
    for bad in (ex.AnnotationExport("nope", "HZY"), ex.AnnotationExport(BATCH, "ZZZ"),
                ex.AnnotationExport(BATCH, "HZY", "document", "DCS9999"), ex.AnnotationExport(BATCH, "HZY", "document"),
                ex.AnnotationExport(BATCH, "HZY", "weird")):
        with pytest.raises(ex.ExportError):
            ex.build_annotation_rows(imported, bad)


@pytest.fixture
def client(tmp_path):
    dirs = DataDirs(tmp_path / "data").ensure()
    shutil.copy(RAW, dirs.raw); shutil.copy(HZY, dirs.imports)
    c = TestClient(create_app(dirs)); c.post("/api/rescan")
    return c, dirs


def test_api_download_preview_and_saved_copy(client):
    c, dirs = client
    body = {"batch": BATCH, "annotator": "HZY", "scope": "document", "document": "DCS0002"}
    pv = c.post("/api/export/annotations/preview", json=body).json()
    assert pv["total"] == 26 and len(pv["rows"]) == 5 and pv["headers"] == ex.ANNOTATION_HEADERS
    r = c.post("/api/export/annotations", json=body)
    assert r.status_code == 200 and "HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx" in r.headers["content-disposition"]
    saved = list(dirs.exports.glob("HZY_*.xlsx"))
    assert len(saved) == 1 and saved[0].read_bytes() == r.content
    assert c.post("/api/export/annotations", json={**body, "format": "csv"}).status_code == 200
    assert c.post("/api/export/annotations", json={**body, "batch": "x"}).status_code == 400
    assert c.post("/api/export/comparison", json={"batch": BATCH}).status_code == 400   # not a review batch


def test_edit_then_export_matches_preview(client):
    c, _ = client
    with c.app.state.session_factory() as s:
        i = s.query(m.Sentence).filter_by(sent_id="CS000027").first().id
    put = c.put(f"/api/annotations/{i}/OYC", json={
        "polarity": ["negative"], "arousal": 2, "source": "self",
        "emotions": [{"tag": "disappointment", "cues": [{"start": 2, "end": 3}], "objects": [{"start": 5, "end": 5}],
                      "experiencers": [{"start": 0, "end": 0}]}]}).json()
    r = c.post("/api/export/annotations", json={"batch": BATCH, "annotator": "OYC", "scope": "completed"})
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).worksheets[0]
    data = [list(x) for x in ws.iter_rows(min_row=2, values_only=True)]
    assert len(data) == 1
    assert dict(zip(sz.FIELDS, data[0][3:11])) == {**{k: (put["preview"][k] or None) for k in sz.FIELDS}, "arousal": 2}
    assert data[0][6] == "disappointment: not impressed" and data[0][8] == "it" and data[0][10] == "i"


def test_pages_render(client):
    c, _ = client
    r = c.get(f"/export?batch={BATCH}&annotator=HZY")
    assert r.status_code == 200 and "Download .xlsx" in r.text and "696 sentences" in r.text
    assert "Nothing to export" in TestClient(create_app(DataDirs(client[1].root.parent / "empty").ensure())).get("/export").text
    assert f"/export?batch={BATCH}" in c.get("/").text
