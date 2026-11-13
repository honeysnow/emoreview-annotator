"""End-to-end walkthrough on a fresh, empty data folder (what a new user does), over HTTP only."""
import io
import json
import re

import openpyxl
from fastapi.testclient import TestClient

from annotool.app import create_app
from annotool.config import DataDirs
from conftest import FIXTURES, HZY, RAW

BATCH = "TRAIN_1_clothing_shoes_and_jewelry"


def boot_of(html):
    return json.loads(re.search(r'id="boot">(.*?)</script>', html, re.S).group(1))


def test_fresh_machine_walkthrough(tmp_path):
    c = TestClient(create_app(DataDirs(tmp_path / "brand-new").ensure()))

    # 1. empty app: friendly empty states, every page loads
    assert "No documents yet" in c.get("/").text
    for url in ("/settings", "/export"):
        assert c.get(url).status_code == 200
    assert "Nothing to export" in c.get("/export").text

    # 2. import a raw file and an existing annotated file with the file picker endpoints
    with open(RAW, "rb") as f:
        assert c.post("/api/import/raw", files={"file": (RAW.name, f)}).json()["rows_imported"] == 696
    with open(HZY, "rb") as f:
        rep = c.post("/api/import/annotated", files={"file": (HZY.name, f)}).json()
    assert rep["annotator"] == "HZY" and rep["rows_flagged"] == 244 and rep["csv"]
    assert "100 documents · 696 sentences" in c.get("/").text

    # 3. annotate as a second annotator: switch user, open a document, edit a sentence, see it saved
    assert "OYC · 0" in c.get("/?annotator=OYC").text
    page = c.get(f"/annotate/{BATCH}/CS000026?annotator=OYC")
    sent = next(s for s in boot_of(page.text)["sentences"] if s["sent_id"] == "CS000026")
    body = {"polarity": ["negative", "positive"], "arousal": 3, "source": "self", "emotions": [
        {"tag": "disappointment", "cues": [{"start": 5, "end": 9, "group": 0}], "objects": [{"start": 0, "end": 0, "group": 0}],
         "actions": [{"start": 5, "end": 9, "group": 0}], "experiencers": [], },
        {"tag": "happiness", "cues": [{"start": 1, "end": 2, "group": 0}], "objects": [], "actions": [], "experiencers": []}]}
    saved = c.put(f"/api/annotations/{sent['id']}/OYC", json=body).json()
    assert saved["preview"]["tag"] == "disappointment;; happiness" and not saved["has_errors"]

    # 4. start a review of HZY x OYC, resolve a field, add a note
    start = c.post("/api/reviews", json={"batch": BATCH, "a": "HZY", "b": "OYC"}).json()
    rv_name = start["batch"]
    rb = boot_of(c.get(start["url"]).text)
    row = next(r for r in rb["rows"] if r["sent_id"] == "CS000026")
    field = next(f for f in rb["fields"] if row["a"][f] != row["b"][f])
    ok = c.put(f"/api/review/{row['id']}/{field}", json={"final_value": row["b"][field], "resolved": True})
    assert ok.status_code == 200 and ok.json()["final"][field] == row["b"][field]
    assert c.put(f"/api/review/{row['id']}/_note", json={"note": "walkthrough"}).json()["note"] == "walkthrough"

    # 5. export everything, then re-open the files like a spreadsheet user would
    ann = c.post("/api/export/annotations", json={"batch": BATCH, "annotator": "OYC", "scope": "completed"})
    ws = openpyxl.load_workbook(io.BytesIO(ann.content)).worksheets[0]
    assert [r[1] for r in ws.iter_rows(min_row=2, values_only=True)] == ["CS000026"]
    cmp_ = c.post("/api/export/comparison", json={"batch": rv_name, "reviewer_note": True})
    wb = openpyxl.load_workbook(io.BytesIO(cmp_.content))
    ws = wb.worksheets[0]
    assert ws["D1"].value == "HZY" and ws["E1"].value == "OYC" and ws.max_row == 2 + 696
    n = 3 + [r["sent_id"] for r in rb["rows"]].index("CS000026")
    assert ws.cell(n, 36).value == "walkthrough"

    # 6. the example comparison sheet imports too, and its numbers match the plan
    with open(FIXTURES / "TRAIN_2_EmoReview40K_Comparison_Sheet_20250922.xlsx", "rb") as f:
        r = c.post("/api/import/comparison", files={"file": ("TRAIN_2_EmoReview40K_Comparison_Sheet_20250922.xlsx", f)}).json()
    assert r["rows_total"] == 599
    stats = c.get("/api/stats/agreement/TRAIN_2_cell_phones_accessories").json()
    assert abs(stats["agreement"]["polarity"] - 79.3) < 0.1

    # 7. housekeeping: the settings page shows everything that was written, remove the review
    html = c.get("/settings").text
    assert "import_report_HZY" in html and "OYC" in html
    assert c.delete(f"/api/batches/{rv_name}?confirm={rv_name}").status_code == 200
    assert rv_name not in c.get("/").text
