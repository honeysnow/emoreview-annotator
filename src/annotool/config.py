"""Data-directory layout. Override the root with ANNOTOOL_DATA_DIR."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DataDirs:
    root: Path

    @property
    def raw(self) -> Path: return self.root / "raw"
    @property
    def imports(self) -> Path: return self.root / "imports"
    @property
    def exports(self) -> Path: return self.root / "exports"
    @property
    def reports(self) -> Path: return self.root / "reports"
    @property
    def db_path(self) -> Path: return self.root / "annotool.db"

    def ensure(self) -> "DataDirs":
        for d in (self.raw, self.imports, self.exports, self.reports):
            d.mkdir(parents=True, exist_ok=True)
        return self


def default_dirs() -> DataDirs:
    return DataDirs(Path(os.environ.get("ANNOTOOL_DATA_DIR", PROJECT_ROOT / "data")))
