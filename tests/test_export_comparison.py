"""Comparison Sheet exporter (plan §6.2), checked against the real example workbook."""
import io
import re
import shutil

import openpyxl
import pytest
from fastapi.testclient import TestClient

from annotool import exporters as ex
from annotool import importers as im
from annotool import models as m
from annotool import review as rv
from annotool.app import create_app
from annotool.config import DataDirs
from conftest import FIXTURES, HZY, RAW

CMP = FIXTURES / "TRAIN_2_EmoReview40K_Comparison_Sheet_20250922.xlsx"
BATCH = "TRAIN_2_cell_phones_accessories"
FIELDS = rv.FIELDS


@pytest.fixture
def imported(session):
    im.import_comparison(session, CMP)
    return session


def export(session, tmp_path, **kw):
    path, name, data = ex.export_comparison(session, ex.ComparisonExport(BATCH, **kw), tmp_path)
    return openpyxl.load_workbook(path), name, data, path


def evaluate(ws):
    """Tiny evaluator for the three formula shapes the sheet uses (no Excel/LibreOffice here)."""
    def val(a):
        return ws[a].value
    out = {}
    last = ws.max_row
    for row in ws.iter_rows(min_row=3):
        for c in row:
            v = c.value
            if not (isinstance(v, str) and v.startswith("=")):
                continue
            if mt := re.fullmatch(r'=IF\(EXACT\((\w+),(\w+)\),"-","X"\)', v):
                a, b = (val(g) for g in mt.groups())
                out[c.coordinate] = "-" if ("" if a is None else str(a)) == ("" if b is None else str(b)) else "X"
            elif mt := re.fullmatch(r'=IF\((\w+)=(\w+),IF\((\w+)="","-",(\w+)\),""\)', v):
                a, b = (val(mt.group(1)), val(mt.group(2)))
                same = ("" if a is None else str(a)).lower() == ("" if b is None else str(b)).lower()  # Excel `=` ignores case
                out[c.coordinate] = ("-" if b is None else b) if same else ""
            else:
                raise AssertionError(f"unexpected formula {v}")
    return out


def test_structure_matches_example_workbook(imported, tmp_path):
    wb, name, data, _ = export(imported, tmp_path, cue_prefix=False)
    ref = openpyxl.load_workbook(CMP)
    ws, rs = wb.worksheets[0], ref.worksheets[0]
    assert re.fullmatch(r"TRAIN_2_EmoReview40K_Comparison_Sheet_\d{8}\.xlsx", name)
    assert wb.sheetnames == ref.sheetnames == [BATCH, "Sheet1"]
    assert [c.value for c in wb["Sheet1"]["A"]] == [c.value for c in ref["Sheet1"]["A"]]      # the 30 tags
    assert (ws.max_row, ws.max_column) == (rs.max_row, rs.max_column) == (601, 35)
    rows = lambda w, r: [c.value for c in w[r]]  # noqa: E731
    assert rows(ws, 1) == rows(rs, 1)                       # annotator labels + agreement formulas, verbatim
    assert rows(ws, 2) == rows(rs, 2)                       # headers incl. "OYCxTJH"
    f_mismatch = 0
    for r in range(3, 602):                                   # every Match / Final formula, verbatim
        for g in range(8):
            assert ws.cell(r, 7 + 4 * g).value == rs.cell(r, 7 + 4 * g).value        # Match column
            ref_final = rs.cell(r, 6 + 4 * g).value
            if isinstance(ref_final, str) and ref_final.startswith("="):
                f_mismatch += ws.cell(r, 6 + 4 * g).value != ref_final
    assert f_mismatch == 0
    assert ws["G1"].value == '=COUNTIF(G3:G601,"-")/COUNTA(G3:G601)'
    assert ws["D3"].value == rs["D3"].value and ws["G3"].value == '=IF(EXACT(D3,E3),"-","X")'
    assert ws.freeze_panes == "D3" and (rs.sheet_view.pane.xSplit, rs.sheet_view.pane.ySplit) == (3, 2)
    dv = ws.data_validations.dataValidation[0]
    assert str(dv.sqref) == str(rs.data_validations.dataValidation[0].sqref) == "T3:U601"
    assert dv.formula1 == rs.data_validations.dataValidation[0].formula1
    assert 46 <= ws.column_dimensions["C"].width <= 48


def test_values_match_example_apart_from_intentional_differences(imported, tmp_path):
    """A, B and Final cells equal the example's, except the documented differences:
    non-Section-2 slots export `none`, cue `none` is blank, whitespace is trimmed."""
    wb, _, _, _ = export(imported, tmp_path, cue_prefix=False)
    ws, rs = wb.worksheets[0], openpyxl.load_workbook(CMP).worksheets[0]
    total = same = 0
    for r in range(3, 602):
        for j in range(4, 36):
            x, y = rs.cell(r, j).value, ws.cell(r, j).value
            total += 1
            same += str(x).strip() == str(y).strip()
    assert same / total > 0.985, (same, total)


def test_formulas_calculate_like_the_original(imported, tmp_path):
    wb, _, data, _ = export(imported, tmp_path)
    ws = wb.worksheets[0]
    calc = evaluate(ws)
    for i, r in enumerate(data.rows):
        for g in range(8):
            assert calc[ws.cell(3 + i, 3 + 4 * g + 4).coordinate] == r.groups[g][3]       # Match column
            cell = ws.cell(3 + i, 3 + 4 * g + 3)
            if cell.coordinate in calc:                                                    # Final formula
                assert (calc[cell.coordinate] or None) == r.finals_eval[g]
    # agreement formula == the review screen's percentage
    for g, f in enumerate(FIELDS):
        col = 3 + 4 * g + 4
        hits = sum(1 for k, v in calc.items() if re.fullmatch(rf"{openpyxl.utils.get_column_letter(col)}\d+", k) and v == "-")
        assert 100 * hits / 599 == pytest.approx(data.agreement[f])


def test_literal_mode_has_no_formulas(imported, tmp_path):
    wb, _, data, _ = export(imported, tmp_path, formulas=False)
    ws = wb.worksheets[0]
    assert not any(isinstance(c.value, str) and c.value.startswith("=") for row in ws.iter_rows() for c in row)
    assert ws["G3"].value in ("-", "X") and isinstance(ws["G1"].value, float)
    assert ws["G1"].value == pytest.approx(data.agreement["polarity"] / 100, abs=1e-5)


def test_options_agreement_row_note_column_and_prefix(imported, tmp_path):
    ws = export(imported, tmp_path, agreement_row=False)[0].worksheets[0]
    assert ws["G1"].value is None and ws["D1"].value == "OYC"       # labels stay, agreement cells go
    with imported.no_autoflush:
        d = imported.query(m.ReviewDecision).first()
        sent = imported.get(m.Sentence, d.sentence_id)
        imported.add(m.ReviewDecision(sentence_id=sent.id, field=rv.NOTE, note="because of 'great'", resolved=False))
        imported.commit()
    ws = export(imported, tmp_path, reviewer_note=True)[0].worksheets[0]
    assert ws.max_column == 36 and ws.cell(2, 36).value == "Reviewer note"
    assert "because of 'great'" in {ws.cell(r, 36).value for r in range(3, 602)}
    on = export(imported, tmp_path)[2].rows
    off = export(imported, tmp_path, cue_prefix=False)[2].rows
    r_on = next(r for r in on if r.sent_id == "CP000723")
    r_off = next(r for r in off if r.sent_id == "CP000723")
    cue = FIELDS.index("cues")
    assert ": " in r_on.groups[cue][0] and ": " not in r_off.groups[cue][0]


def test_scopes_and_scoped_agreement_row(imported, tmp_path):
    wb, _, data, _ = export(imported, tmp_path, scope="disagreements")
    ws = wb.worksheets[0]
    assert all(any(g[3] == "X" for g in r.groups) for r in data.rows) and 0 < len(data.rows) < 599
    assert ws.max_row == 2 + len(data.rows)
    assert isinstance(ws["G1"].value, float)                          # whole-batch %, not skewed by the filter
    unresolved = export(imported, tmp_path, scope="unresolved")[2].rows
    assert 0 < len(unresolved) <= len(data.rows)
    with pytest.raises(ex.ExportError):
        ex.build_comparison(imported, ex.ComparisonExport(BATCH, scope="weird"))
    with pytest.raises(ex.ExportError):
        ex.build_comparison(imported, ex.ComparisonExport("nope"))


def test_decisions_overwrite_formulas(client_factory):
    c, dirs = client_factory
    boot = rboot(c)
    row = next(r for r in boot["rows"] if r["status"] == "open")
    f = next(f for f in FIELDS if row["a"][f] != row["b"][f] and row["final"][f] is None)
    c.put(f"/api/review/{row['id']}/{f}", json={"final_value": row["a"][f], "resolved": True})
    r = c.post("/api/export/comparison", json={"batch": BATCH})
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).worksheets[0]
    n = 3 + [x["id"] for x in boot["rows"]].index(row["id"])
    col = lambda g: 3 + 4 * FIELDS.index(g) + 3  # noqa: E731
    assert ws.cell(n, col(f)).value == row["a"][f]                                  # a value, not a formula
    untouched = next(g for g in FIELDS if g != f and row["a"][g] == row["b"][g])
    assert str(ws.cell(n, col(untouched)).value).startswith("=IF(")                 # agreed field keeps its formula
    assert ws.cell(n, col(f) + 1).value.startswith("=IF(EXACT(")                    # match stays live


def test_resolved_to_nothing_exports_dash(imported, tmp_path):
    d = imported.query(m.ReviewDecision).filter(m.ReviewDecision.final_value == "").first()   # a typed "-" in the sheet
    sent = imported.get(m.Sentence, d.sentence_id)
    data = ex.build_comparison(imported, ex.ComparisonExport(BATCH))
    r = next(x for x in data.rows if x.sent_id == sent.sent_id)
    assert r.groups[FIELDS.index(d.field)][2] == "-"


def rboot(c):
    import json
    page = c.get(f"/review/{BATCH}/CP000721").text
    return json.loads(re.search(r'id="boot">(.*?)</script>', page, re.S).group(1))


@pytest.fixture
def client_factory(tmp_path):
    dirs = DataDirs(tmp_path / "data").ensure()
    shutil.copy(CMP, dirs.imports)
    c = TestClient(create_app(dirs))
    c.post("/api/rescan")
    return c, dirs


def test_roundtrip_export_then_import(imported, tmp_path, session_factory):
    """Exporting a review and importing the file again gives the same agreement and decisions."""
    before = rv.agreement(rv.load_rows(imported, imported.query(m.Batch).filter_by(name=BATCH).first()))
    n_dec = imported.query(m.ReviewDecision).count()
    *_, path = export(imported, tmp_path)
    with session_factory() as s2:
        rep = im.import_comparison(s2, path)
        assert rep.rows_total == 599 and rep.batch == BATCH
        after = rv.agreement(rv.load_rows(s2, s2.query(m.Batch).filter_by(name=BATCH).first()))
        assert s2.query(m.ReviewDecision).count() == n_dec
    for f in FIELDS:
        assert after[f] == pytest.approx(before[f], abs=0.2), f


def test_csv_export(imported, tmp_path):
    import csv
    path, name, data = ex.export_comparison(imported, ex.ComparisonExport(BATCH, scope="disagreements"), tmp_path, "csv")
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    assert name.endswith(".csv") and len(rows) == 2 + len(data.rows) and all(len(r) == 35 for r in rows)
    assert rows[0][3:7] == ["OYC", "TJH", "Final", f"{data.agreement['polarity']:.1f}%"]


def test_api_and_page(client_factory):
    c, dirs = client_factory
    body = {"batch": BATCH, "scope": "disagreements", "reviewer_note": True}
    pv = c.post("/api/export/comparison/preview", json=body).json()
    assert pv["all"] == 599 and 0 < pv["total"] < 599 and len(pv["rows"]) == 5 and len(pv["headers"]) == 36
    assert pv["labels"][3:6] == ["OYC", "TJH", "Final"] and set(pv["agreement"]) >= {"Polarity", "Emotion Cues"}
    r = c.post("/api/export/comparison", json=body)
    assert r.status_code == 200 and "Comparison_Sheet_" in r.headers["content-disposition"]
    assert len(list(dirs.exports.glob("*.xlsx"))) == 1
    assert c.post("/api/export/comparison", json={**body, "format": "csv"}).status_code == 200
    assert c.post("/api/export/comparison", json={**body, "batch": "nope"}).status_code == 400
    page = c.get(f"/export?type=comparison&review={BATCH}").text
    assert 'id="panel-cmp"' in page and "Comparison sheet" in page and "TRAIN_2" in page
    assert "Export comparison sheet" in c.get("/").text and "/export?type=comparison" in c.get(f"/review/{BATCH}/CP000721").text


def test_review_started_from_two_annotator_files_exports(tmp_path):
    dirs = DataDirs(tmp_path / "d").ensure()
    shutil.copy(RAW, dirs.raw); shutil.copy(HZY, dirs.imports)
    c = TestClient(create_app(dirs)); c.post("/api/rescan")
    body = {"polarity": ["positive"], "arousal": 3, "source": "self",
            "emotions": [{"tag": "happiness", "cues": [{"start": 0, "end": 0}]}]}
    with c.app.state.session_factory() as s:
        i = s.query(m.Sentence).filter_by(sent_id="CS000001").first().id
    c.put(f"/api/annotations/{i}/OYC", json=body)
    name = c.post("/api/reviews", json={"batch": "TRAIN_1_clothing_shoes_and_jewelry", "a": "HZY", "b": "OYC"}).json()["batch"]
    r = c.post("/api/export/comparison", json={"batch": name})
    assert r.status_code == 200
    assert "TRAIN_1_EmoReview40K_Comparison_Sheet_" in r.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["TRAIN_1_clothing_shoes_and_jewelry"[:31], "Sheet1"]    # Excel limit: 31 characters
    ws = wb.worksheets[0]
    assert (ws["D1"].value, ws["E1"].value) == ("HZY", "OYC") and ws.max_row == 2 + 696
    assert ws["E3"].value == "positive" and ws["D3"].value == "negative"             # CS000001: HZY vs OYC
