from pathlib import Path

import pytest

from annotool.db import init_db, make_engine

FIXTURES = Path(__file__).parent / "fixtures"
RAW = FIXTURES / "TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx"
HZY = FIXTURES / "HZY_TRAIN_1_clothing_shoes_and_jewelry_annotation.xlsx"


@pytest.fixture
def session_factory(tmp_path):
    return init_db(make_engine(tmp_path / "t.db"))


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s
