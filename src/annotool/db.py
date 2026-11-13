"""Engine / session setup."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Annotator, Base

DEFAULT_ANNOTATORS = ["HZY", "OYC"]  # decision 5


def make_engine(db_path: Path | str):
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def _migrate(engine) -> None:
    """Tiny forward-only migrations for databases created by earlier milestones."""
    with engine.begin() as c:
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(batch)")}
        if cols and "annotators_json" not in cols:  # added in M5
            c.exec_driver_sql("ALTER TABLE batch ADD COLUMN annotators_json JSON")
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(annotation)")}
        if cols and "edited_at" not in cols:  # added in M7
            c.exec_driver_sql("ALTER TABLE annotation ADD COLUMN edited_at DATETIME")


def init_db(engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    _migrate(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        for code in DEFAULT_ANNOTATORS:
            if not s.query(Annotator).filter_by(code=code).first():
                s.add(Annotator(code=code, display_name=code))
        s.commit()
    return factory
