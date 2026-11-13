"""Drives static/annotate.js headlessly (Node + a tiny DOM shim) against real page data."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from annotool.app import create_app
from annotool.config import DataDirs
from conftest import HZY, RAW

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_annotate_js_interactions(tmp_path):
    dirs = DataDirs(tmp_path / "d").ensure()
    shutil.copy(RAW, dirs.raw)
    shutil.copy(HZY, dirs.imports)
    c = TestClient(create_app(dirs))
    c.post("/api/rescan")
    html = c.get("/annotate/TRAIN_1_clothing_shoes_and_jewelry/CS000026?annotator=HZY").text
    boot = tmp_path / "boot.json"
    boot.write_text(re.search(r'id="boot">(.*?)</script>', html, re.S).group(1))
    r = subprocess.run(["node", str(Path(__file__).parent / "ui" / "drive.js"), str(boot)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL OK" in r.stdout


def test_review_js_interactions(tmp_path):
    dirs = DataDirs(tmp_path / "d").ensure()
    shutil.copy(Path(__file__).parent / "fixtures" / "TRAIN_2_EmoReview40K_Comparison_Sheet_20250922.xlsx", dirs.imports)
    c = TestClient(create_app(dirs))
    c.post("/api/rescan")
    html = c.get("/review/TRAIN_2_cell_phones_accessories/CP000721").text
    boot = tmp_path / "rboot.json"
    boot.write_text(re.search(r'id="boot">(.*?)</script>', html, re.S).group(1))
    r = subprocess.run(["node", str(Path(__file__).parent / "ui" / "drive_review.js"), str(boot)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL OK" in r.stdout


@pytest.mark.parametrize("mode", ["annotations", "comparison"])
def test_export_js_both_modes(mode):
    r = subprocess.run(["node", str(Path(__file__).parent / "ui" / "drive_export.js"), mode], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL OK" in r.stdout
