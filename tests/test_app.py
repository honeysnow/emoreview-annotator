import shutil

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from annotool.app import create_app
from annotool.config import DataDirs
from annotool import importers as im, models as m
from conftest import HZY, RAW


@pytest.fixture
def client(tmp_path):
    dirs = DataDirs(tmp_path / "data").ensure()
    return TestClient(create_app(dirs)), dirs


def test_empty_home(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "Your tasks" in r.text and "No documents yet" in r.text


def test_raw_import_then_home_lists_documents(client):
    c, dirs = client
    shutil.copy(RAW, dirs.raw)
    res = c.post("/api/rescan").json()
    assert res["errors"] == [] and res["reports"][0]["rows_imported"] == 696
    html = c.get("/").text
    assert "TRAIN_1_clothing_shoes_and_jewelry" in html
    assert "100 documents · 696 sentences" in html
    assert "DCS0001" in html and "DCS0100" in html and "0 of 696 sentences" in html


def test_rescan_is_idempotent_and_inputs_untouched(client):
    c, dirs = client
    shutil.copy(RAW, dirs.raw)
    before = (dirs.raw / RAW.name).read_bytes()
    c.post("/api/rescan")
    again = c.post("/api/rescan").json()
    assert again["reports"][0]["skipped"] == "batch already imported"
    assert (dirs.raw / RAW.name).read_bytes() == before


def test_annotated_import_shows_progress_and_report_csv(client):
    c, dirs = client
    shutil.copy(RAW, dirs.raw)
    shutil.copy(HZY, dirs.imports)
    res = c.post("/api/rescan").json()
    ann = next(r for r in res["reports"] if r["kind"] == "annotated")
    assert ann["annotator"] == "HZY" and ann["rows_imported"] == 696 and ann["csv"]
    html = c.get("/").text
    assert "695 of 696 sentences" not in html  # 'todo' rows are not counted as done
    assert "of 696 sentences" in html and "HZY" in html
    name = ann["csv"].rsplit("/", 1)[1]
    assert c.get(f"/api/reports/{name}").text.startswith("type,sent_id,field")


def test_upload_endpoints_and_errors(client):
    c, dirs = client
    with open(RAW, "rb") as f:
        r = c.post("/api/import/raw", files={"file": (RAW.name, f)})
    assert r.status_code == 200 and (dirs.raw / RAW.name).exists()
    r = c.post("/api/import/raw", files={"file": ("notes.txt", b"x")})
    assert r.status_code == 400
    wb = Workbook(); wb.active.append(["a", "b"]); wb.save(dirs.root / "bad.xlsx")
    with open(dirs.root / "bad.xlsx", "rb") as f:
        r = c.post("/api/import/raw", files={"file": ("bad.xlsx", f)})
    assert r.status_code == 400 and "missing column" in r.json()["detail"]
    assert not (dirs.raw / "bad.xlsx").exists()
    assert c.post("/api/import/comparison", files={"file": ("c.xlsx", b"x")}).status_code == 400   # not a workbook


def test_default_annotators_and_add(client):
    c, _ = client
    assert [a["code"] for a in c.get("/api/annotators").json()] == ["HZY", "OYC"]
    assert c.post("/api/annotators", data={"code": "tjh"}).json()["code"] == "TJH"
    assert c.post("/api/annotators", data={"code": "x"}).status_code == 400


def test_batch_and_annotator_naming():
    from pathlib import Path
    assert im.batch_name_from_file(Path("HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx")) == "TRAIN_1_clothing_shoes_and_jewelry"
    assert im.batch_name_from_file(Path("TRAIN_1_clothing_shoes_and_jewelry_annotation_2.xlsx")) == "TRAIN_1_clothing_shoes_and_jewelry"
    assert im.annotator_from_file(Path("HZY_TRAIN_1_x_annotation.xlsx")) == "HZY"
    assert im.annotator_from_file(Path("TRAIN_1_x_annotation.xlsx")) is None
