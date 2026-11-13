"""FastAPI app: Home page, import endpoints, rescan (M1/M2 scope)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import annotate as an
from . import exporters as ex
from . import importers as im
from . import review as rv
from . import models as m
from .config import DataDirs, default_dirs
from .codebook import load_codebook
from .db import init_db, make_engine
from .store import delete_batch, save_annotation

HERE = Path(__file__).parent
SYSTEM_ANNOTATOR = "FINAL"  # holds structured Final edits made on the Review screen (M5)
COOKIE = "annotator"


class DecisionIn(BaseModel):
    final_value: str | None = None
    resolved: bool = True
    note: str | None = None


class ReviewIn(BaseModel):
    batch: str
    a: str
    b: str


class SeedIn(BaseModel):
    source: str = "a"
    overwrite: bool = False


class CompExportIn(BaseModel):
    batch: str
    scope: str = "all"
    formulas: bool = True
    agreement_row: bool = True
    reviewer_note: bool = False
    cue_prefix: bool = True
    format: str = "xlsx"


class ExportIn(BaseModel):
    batch: str
    annotator: str
    scope: str = "all"
    document: str | None = None
    include_unannotated: bool = True
    cue_prefix: bool = True
    format: str = "xlsx"


# ------------------------------------------------------------------ home queries
def _status_pill(done: int, n: int) -> tuple[str, str]:
    if n and done >= n:
        return "Done", "done"
    return ("In progress", "prog") if done else ("Not started", "todo")


def home_context(s: Session, batch_name: str | None, annotator_code: str | None, view: str | None = None) -> dict:
    """One Annotate card per (batch, annotator who has work there), plus a card for the
    current user. Work stays tied to the annotator who did it."""
    batches = s.query(m.Batch).filter_by(kind="annotate").order_by(m.Batch.id).all()
    annotators = [a for a in s.query(m.Annotator).order_by(m.Annotator.id) if a.code != SYSTEM_ANNOTATOR]
    ann_by_code = {a.code: a for a in annotators}
    user = ann_by_code.get(annotator_code or "")

    def counts(batch: m.Batch, annotator: m.Annotator):
        n = s.query(func.count(m.Sentence.id)).filter_by(batch_id=batch.id).scalar()
        docs = s.query(func.count(func.distinct(m.Sentence.document_id))).filter_by(batch_id=batch.id).scalar()
        done = (
            s.query(func.count(m.Annotation.id)).join(m.Sentence)
            .filter(m.Sentence.batch_id == batch.id, m.Annotation.annotator_id == annotator.id,
                    m.Annotation.status != "todo").scalar()
        )
        return n, docs, done

    cache: dict[tuple[int, int], tuple] = {}

    def cnt(b: m.Batch, a: m.Annotator):
        if (b.id, a.id) not in cache:
            cache[(b.id, a.id)] = counts(b, a)
        return cache[(b.id, a.id)]

    cards = []
    for b in batches:
        owners = [a for a in annotators if cnt(b, a)[2]]
        if user and user not in owners:
            owners.append(user)
        if not owners and annotators:
            owners = [annotators[0]]
        chosen = (ann_by_code.get(view) if view and batch_name == b.name else None)
        if chosen not in owners:
            chosen = user if user in owners else owners[0]
        n, docs, done = cnt(b, chosen)
        last_edit = (  # resume where this annotator last edited; otherwise at the first open sentence
            s.query(m.Sentence).join(m.Annotation, m.Annotation.sentence_id == m.Sentence.id)
            .filter(m.Sentence.batch_id == b.id, m.Annotation.annotator_id == chosen.id,
                    m.Annotation.edited_at.isnot(None))
            .order_by(m.Annotation.edited_at.desc()).first()
        )
        resume = last_edit or (
            s.query(m.Sentence).outerjoin(
                m.Annotation,
                (m.Annotation.sentence_id == m.Sentence.id) & (m.Annotation.annotator_id == chosen.id))
            .filter(m.Sentence.batch_id == b.id,
                    (m.Annotation.id.is_(None)) | (m.Annotation.status == "todo"))
            .order_by(m.Sentence.position).first()
        ) or (b.sentences[0] if b.sentences else None)
        n_ann = s.query(func.count(m.Annotation.id)).join(m.Sentence).filter(m.Sentence.batch_id == b.id).scalar()
        cards.append({"batch": b, "title": b.name, "annotator": chosen.code, "n": n, "docs": docs,
                      "done": done, "pct": round(100 * done / n) if n else 0, "resume": resume,
                      "owners": [{"code": a.code, "done": cnt(b, a)[2]} for a in owners], "n_ann": n_ann})

    selected = next((c for c in cards if c["batch"].name == batch_name), None) \
        or next((c for c in cards if user and c["annotator"] == user.code), None) \
        or (cards[0] if cards else None)
    doc_rows = []
    if selected:
        a = ann_by_code[selected["annotator"]]
        rows = (
            s.query(m.Sentence.document_id, func.count(m.Sentence.id),
                    func.sum(func.iif(m.Annotation.status != "todo", 1, 0)), func.min(m.Sentence.position),
                    func.min(m.Sentence.sent_id))
            .outerjoin(m.Annotation, (m.Annotation.sentence_id == m.Sentence.id) & (m.Annotation.annotator_id == a.id))
            .filter(m.Sentence.batch_id == selected["batch"].id)
            .group_by(m.Sentence.document_id).order_by(func.min(m.Sentence.position)).all()
        )
        for doc, n, done, _, first_sid in rows:
            done = done or 0
            label, cls = _status_pill(done, n)
            doc_rows.append({"id": doc, "n": n, "done": done, "pct": round(100 * done / n),
                             "label": label, "cls": cls, "first_sent": first_sid})
    review_options = [{"batch": c["title"], "annotators": [o["code"] for o in c["owners"] if o["done"]]}
                      for c in cards if sum(1 for o in c["owners"] if o["done"]) >= 2]
    reviews = []
    for b in s.query(m.Batch).filter_by(kind="review").order_by(m.Batch.id):
        try:
            ra, rb = rv.review_annotators(s, b)
        except rv.ReviewError:
            continue
        rows = rv.load_rows(s, b)
        sm = rv.summary(rows)
        first = next((r for r in rows if r.status == "open"), rows[0] if rows else None)
        reviews.append({"batch": b, "a": ra.code, "b": rb.code, "docs": len({r.sentence.document_id for r in rows}),
                        "summary": sm, "open": sm["open"], "first": first.sentence.sent_id if first else None,
                        "n_dec": s.query(m.ReviewDecision).join(m.Sentence).filter(m.Sentence.batch_id == b.id).count()})
    return {"cards": cards, "reviews": reviews, "review_options": review_options, "selected": selected, "docs": doc_rows, "annotators": annotators,
            "current": user.code if user else (selected["annotator"] if selected else "")}


# --------------------------------------------------------------------------- app
def create_app(dirs: DataDirs | None = None) -> FastAPI:
    dirs = (dirs or default_dirs()).ensure()
    factory = init_db(make_engine(dirs.db_path))
    app = FastAPI(title="Emotion Annotation")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    app.state.dirs, app.state.session_factory = dirs, factory

    @app.exception_handler(StarletteHTTPException)
    async def friendly_errors(request: Request, exc: StarletteHTTPException):
        """API clients get JSON; people browsing get a readable page with a way back."""
        wants_html = "text/html" in request.headers.get("accept", "")
        if request.url.path.startswith("/api/") or not wants_html:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {"status": exc.status_code, "detail": exc.detail,
                                                                   "current": ""}, status_code=exc.status_code)

    def safe_copy(upload: UploadFile, folder: Path) -> Path:
        name = Path(upload.filename or "upload.xlsx").name
        if not name.lower().endswith(".xlsx"):
            raise HTTPException(400, f"{name}: only .xlsx files are supported")
        data = upload.file.read()
        stem = Path(name).stem
        for existing in sorted(folder.glob(f"{stem}*.xlsx")):  # same content already there: reuse it
            if re.fullmatch(re.escape(stem) + r"(__\d+)?", existing.stem) and existing.read_bytes() == data:
                return existing
        dest, i = folder / name, 1
        while dest.exists():  # never overwrite an existing input
            dest = folder / f"{stem}__{i}.xlsx"
            i += 1
        dest.write_bytes(data)
        return dest

    def run_import(kind: str, path: Path, annotator: str | None = None) -> dict:
        with factory() as s:
            if kind == "raw":
                rep = im.import_raw(s, path)
            elif kind == "comparison":
                rep = im.import_comparison(s, path, reports_dir=dirs.reports)
            else:
                rep = im.import_annotated(s, path, annotator or None, reports_dir=dirs.reports)
        return rep.summary()

    def remember(resp, code: str | None):
        if code and code != SYSTEM_ANNOTATOR:
            resp.set_cookie(COOKIE, code, max_age=365 * 86400, samesite="lax")
        return resp

    @app.get("/settings")
    def settings_page(request: Request):
        def listing(folder: Path):
            return sorted(p.name for p in folder.glob("*.xlsx") if not p.name.startswith("~$"))
        with factory() as s:
            people = []
            for a in s.query(m.Annotator).order_by(m.Annotator.id):
                if a.code == SYSTEM_ANNOTATOR:
                    continue
                n = s.query(func.count(m.Annotation.id)).filter(m.Annotation.annotator_id == a.id,
                                                                m.Annotation.status != "todo").scalar()
                people.append({"code": a.code, "n": n})
        reports = sorted(dirs.reports.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
        ctx = {"people": people, "current": request.cookies.get(COOKIE) or "", "dirs": dirs,
               "raw": listing(dirs.raw), "imports": listing(dirs.imports), "reports": [p.name for p in reports],
               "exports": sorted((p.name for p in dirs.exports.glob("*") if p.suffix in (".xlsx", ".csv")), reverse=True)[:8]}
        return templates.TemplateResponse(request, "settings.html", ctx)

    @app.get("/")
    def home(request: Request, batch: str | None = None, annotator: str | None = None, view: str | None = None):
        with factory() as s:
            ctx = home_context(s, batch, annotator or request.cookies.get(COOKIE), view)
        return remember(templates.TemplateResponse(request, "home.html", ctx), annotator)

    @app.post("/api/import/{kind}")
    def import_file(kind: str, file: UploadFile = File(...), annotator: str | None = Form(None)):
        if kind not in ("raw", "annotated", "comparison"):
            raise HTTPException(404, "unknown import kind")
        dest = safe_copy(file, dirs.raw if kind == "raw" else dirs.imports)
        try:
            return run_import(kind, dest, annotator)
        except im.ImportError_ as exc:
            dest.unlink(missing_ok=True)  # the copy we just made; originals untouched
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/rescan")
    def rescan():
        results, errors, notes = [], [], []
        for p in sorted(dirs.raw.glob("*.xlsx")):
            if p.name.startswith("~$"):
                continue
            try:
                results.append(run_import("raw", p))
            except im.ImportError_ as exc:
                errors.append(str(exc))
        for p in sorted(dirs.imports.glob("*.xlsx")):
            if p.name.startswith("~$"):
                continue
            try:
                results.append(run_import("comparison" if "comparison" in p.name.lower() else "annotated", p))
            except im.ImportError_ as exc:
                errors.append(str(exc))
        return {"reports": results, "errors": errors, "notes": notes}

    @app.get("/api/reports/{name}")
    def report(name: str):
        p = dirs.reports / Path(name).name
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, media_type="text/csv", filename=p.name)

    @app.get("/api/annotators")
    def annotators():
        with factory() as s:
            return [{"code": a.code, "display_name": a.display_name} for a in s.query(m.Annotator).order_by(m.Annotator.id) if a.code != SYSTEM_ANNOTATOR]

    @app.post("/api/annotators")
    def add_annotator(code: str = Form(...), display_name: str | None = Form(None)):
        code = code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,8}", code) or code == SYSTEM_ANNOTATOR:
            raise HTTPException(400, "annotator code must be 2-8 letters/digits (FINAL is reserved)")
        with factory() as s:
            a = im.get_annotator(s, code)
            if display_name:
                a.display_name = display_name
            s.commit()
            return {"code": a.code, "display_name": a.display_name}

    def pick_annotator(s: Session, batch: m.Batch, code: str | None, request: Request | None = None) -> m.Annotator:
        code = code or (request.cookies.get(COOKIE) if request else None)
        a = s.query(m.Annotator).filter_by(code=code).first() if code else None
        if a:
            return a
        a = (
            s.query(m.Annotator).join(m.Annotation).join(m.Sentence)
            .filter(m.Sentence.batch_id == batch.id).order_by(m.Annotator.id).first()
        )
        return a or s.query(m.Annotator).order_by(m.Annotator.id).first()

    # ------------------------------------------------------------------ review (M5)
    def review_row(s: Session, sentence_id: int):
        sent = s.get(m.Sentence, sentence_id)
        if not sent or sent.batch.kind != "review":
            raise HTTPException(404, "unknown review sentence")
        rows = rv.load_rows(s, sent.batch, only=sent.id)
        return sent, rows[0]

    @app.post("/api/reviews")
    def start_review(body: ReviewIn):
        """Combine two annotators' work on one batch into a review (the comparison sheet's rows)."""
        with factory() as s:
            try:
                b = rv.create_review(s, body.batch, body.a, body.b)
            except rv.ReviewError as exc:
                raise HTTPException(400, str(exc)) from exc
            s.commit()
            first = rv.load_rows(s, b)
            first = next((r for r in first if r.status == "open"), first[0])
            return {"batch": b.name, "url": f"/review/{b.name}/{first.sentence.sent_id}"}

    @app.delete("/api/batches/{name}")
    def remove_batch(name: str, confirm: str = ""):
        """Remove a batch and everything stored for it. Input files in data/ are never touched."""
        if confirm != name:
            raise HTTPException(400, "pass confirm=<batch name> to remove a batch")
        with factory() as s:
            b = s.query(m.Batch).filter_by(name=name).first()
            if not b:
                raise HTTPException(404)
            n = delete_batch(s, b)
            s.commit()
            return {"removed": name, **n}

    @app.get("/review/{batch}/{sent_id}")
    def review_page(request: Request, batch: str, sent_id: str):
        with factory() as s:
            b = s.query(m.Batch).filter_by(name=batch, kind="review").first()
            cur = b and s.query(m.Sentence).filter_by(batch_id=b.id, sent_id=sent_id).first()
            if not cur:
                raise HTTPException(404, "sentence not found")
            a, bb = rv.review_annotators(s, b)
            rows = rv.load_rows(s, b)
            user = request.cookies.get(COOKIE) or ""
            boot = {"batch": b.name, "a": a.code, "b": bb.code, "current": cur.id, "reviewer": user,
                    "rows": [r.payload() for r in rows], "summary": rv.summary(rows),
                    "labels": rv.LABELS, "fields": rv.FIELDS}
        return templates.TemplateResponse(request, "review.html", {"boot": boot, "batch": b, "current": user})

    @app.put("/api/review/{sentence_id}/{field}")
    def put_review(request: Request, sentence_id: int, field: str, body: DecisionIn):
        with factory() as s:
            sent, row = review_row(s, sentence_id)
            who = request.cookies.get(COOKIE)
            try:
                if field == rv.NOTE:
                    rv.set_note(s, sent.id, body.note or "", who)
                else:
                    rv.set_decision(s, row, field, body.final_value, body.resolved, who)
            except rv.ReviewError as exc:
                raise HTTPException(422, str(exc)) from exc
            s.commit()
            return review_row(s, sentence_id)[1].payload()

    @app.post("/api/review/{sentence_id}/use/{side}")
    def use_side(request: Request, sentence_id: int, side: str):
        if side not in ("a", "b"):
            raise HTTPException(404)
        with factory() as s:
            sent, row = review_row(s, sentence_id)
            for f in row.open_fields:
                rv.set_decision(s, row, f, (row.a if side == "a" else row.b)[f], True, request.cookies.get(COOKIE))
            s.commit()
            return review_row(s, sentence_id)[1].payload()

    @app.post("/api/review/{sentence_id}/final/seed")
    def seed_final(sentence_id: int, body: SeedIn):
        with factory() as s:
            sent, _ = review_row(s, sentence_id)
            a, b = rv.review_annotators(s, sent.batch)
            rv.seed_final(s, sent, a if body.source == "a" else b, body.overwrite)
            s.commit()
            return {"url": f"/annotate/{sent.batch.name}/{sent.sent_id}?annotator={rv.FINAL_CODE}&embed=1"}

    @app.get("/api/review/{sentence_id}/final")
    def final_strings(sentence_id: int):
        with factory() as s:
            sent, _ = review_row(s, sentence_id)
            return rv.final_annotation_strings(s, sent) or {}

    @app.get("/api/stats/agreement/{batch}")
    def agreement_stats(batch: str):
        with factory() as s:
            b = s.query(m.Batch).filter_by(name=batch, kind="review").first()
            if not b:
                raise HTTPException(404)
            sm = rv.summary(rv.load_rows(s, b))
            return {"agreement": sm["agreement"], "mean": sm["mean"], "rows": sm["rows"], "both": sm["both"]}

    @app.get("/annotate/{batch}/{sent_id}")
    def annotate_page(request: Request, batch: str, sent_id: str, annotator: str | None = None, embed: int = 0):
        with factory() as s:
            b = s.query(m.Batch).filter_by(name=batch).first()
            cur = b and s.query(m.Sentence).filter_by(batch_id=b.id, sent_id=sent_id).first()
            if not cur:
                raise HTTPException(404, "sentence not found")
            a = pick_annotator(s, b, annotator, request)
            doc = (
                s.query(m.Sentence).filter_by(batch_id=b.id, document_id=cur.document_id)
                .order_by(m.Sentence.position).all()
            )
            docs = [d for (d,) in s.query(m.Sentence.document_id).filter_by(batch_id=b.id)
                    .group_by(m.Sentence.document_id).order_by(func.min(m.Sentence.position)).all()]
            i = docs.index(cur.document_id)

            def edge(doc_id: str | None, last: bool) -> str | None:
                if doc_id is None:
                    return None
                q = s.query(m.Sentence).filter_by(batch_id=b.id, document_id=doc_id)
                x = q.order_by(m.Sentence.position.desc() if last else m.Sentence.position).first()
                return f"/annotate/{b.name}/{x.sent_id}?annotator={a.code}"

            cb = load_codebook()
            boot = {
                "batch": b.name, "annotator": a.code, "document": cur.document_id,
                "current": cur.id, "sentences": [an.sentence_state(s, x, a) for x in doc],
                "prev_doc": edge(docs[i - 1] if i else None, True),
                "next_doc": edge(docs[i + 1] if i + 1 < len(docs) else None, False),
                "codebook": {
                    "tags": [{"name": t, "definition": cb.definitions[t], "section2": cb.is_section2(t)} for t in cb.tags],
                    "polarities": list(cb.polarities), "sources": list(cb.sources),
                    "arousal": {str(k): v for k, v in cb.arousal.items()},
                },
                "annotators": [x.code for x in s.query(m.Annotator).order_by(m.Annotator.id)],
                "embed": bool(embed),
            }
            ctx = {"boot": boot, "batch": b, "doc_id": cur.document_id, "current": a.code, "embed": bool(embed)}
        return remember(templates.TemplateResponse(request, "annotate.html", ctx), annotator)

    @app.put("/api/annotations/{sentence_id}/{annotator}")
    def put_annotation(sentence_id: int, annotator: str, body: an.AnnotationIn):
        with factory() as s:
            sent = s.get(m.Sentence, sentence_id)
            a = s.query(m.Annotator).filter_by(code=annotator).first()
            if not sent or not a:
                raise HTTPException(404, "unknown sentence or annotator")
            try:
                ann = an.to_domain(body, len(sent.text.split(" ")))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            save_annotation(s, sent.id, a.id, ann, keep_raw=True, acknowledged=body.acknowledged)
            s.commit()
            return an.sentence_state(s, sent, a)

    def _opts(b: ExportIn) -> ex.AnnotationExport:
        return ex.AnnotationExport(b.batch, b.annotator, b.scope, b.document, b.include_unannotated, b.cue_prefix)

    @app.post("/api/export/annotations/preview")
    def export_preview(body: ExportIn):
        with factory() as s:
            try:
                d = ex.build_annotation_rows(s, _opts(body))
            except ex.ExportError as exc:
                raise HTTPException(400, str(exc)) from exc
        shown = [["" if v is None else v for v in r] for r in d.rows[:5]]
        return {"filename": d.filename, "sheet": d.sheet_name, "headers": ex.ANNOTATION_HEADERS,
                "rows": shown, "total": len(d.rows), "warnings": d.warnings}

    @app.post("/api/export/annotations")
    def export_file(body: ExportIn):
        with factory() as s:
            try:
                path, name, _ = ex.export_annotations(s, _opts(body), dirs.exports, body.format)
            except ex.ExportError as exc:
                raise HTTPException(400, str(exc)) from exc
        media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                 if body.format == "xlsx" else "text/csv")
        return FileResponse(path, media_type=media, filename=name)

    def _copts(b: CompExportIn) -> ex.ComparisonExport:
        return ex.ComparisonExport(b.batch, b.scope, b.formulas, b.agreement_row, b.reviewer_note, b.cue_prefix)

    @app.post("/api/export/comparison/preview")
    def comparison_preview(body: CompExportIn):
        with factory() as s:
            try:
                d = ex.build_comparison(s, _copts(body))
            except ex.ExportError as exc:
                raise HTTPException(400, str(exc)) from exc
        r1, r2 = ex.comparison_header_rows(d)
        shown = []
        for r in d.rows[:5]:
            cells = [r.doc, r.sent_id, r.text]
            for g in range(len(rv.FIELDS)):
                a, b, _, match = r.groups[g]
                cells += [a, b, r.finals_eval[g], match]
            if d.options.reviewer_note:
                cells.append(r.note)
            shown.append(["" if v is None else v for v in cells])
        return {"filename": d.filename, "sheet": d.sheet_name, "labels": ["" if v is None else v for v in r1],
                "headers": r2, "rows": shown, "total": len(d.rows), "all": d.n_all,
                "agreement": {rv.LABELS[f]: v for f, v in d.agreement.items()}}

    @app.post("/api/export/comparison")
    def export_comparison(body: CompExportIn):
        with factory() as s:
            try:
                path, name, _ = ex.export_comparison(s, _copts(body), dirs.exports, body.format)
            except ex.ExportError as exc:
                raise HTTPException(400, str(exc)) from exc
        media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                 if body.format == "xlsx" else "text/csv")
        return FileResponse(path, media_type=media, filename=name)

    @app.get("/export")
    def export_page(request: Request, batch: str | None = None, annotator: str | None = None,
                    document: str | None = None, type: str | None = None, review: str | None = None):
        with factory() as s:
            reviews = []
            for rb in s.query(m.Batch).filter_by(kind="review").order_by(m.Batch.id):
                try:
                    ra, rbb = rv.review_annotators(s, rb)
                except rv.ReviewError:
                    continue
                rows = rv.load_rows(s, rb)
                reviews.append({"name": rb.name, "a": ra.code, "b": rbb.code, "total": len(rows),
                                "diff": sum(1 for r in rows if r.diffs), "open": sum(1 for r in rows if r.status == "open")})
            b = (s.query(m.Batch).filter_by(name=batch, kind="annotate").first() if batch
                 else s.query(m.Batch).filter_by(kind="annotate").order_by(m.Batch.id).first())
            a = pick_annotator(s, b, annotator, request) if b else None
            docs = ([{"id": d, "n": n} for d, n in s.query(m.Sentence.document_id, func.count(m.Sentence.id))
                     .filter_by(batch_id=b.id).group_by(m.Sentence.document_id).order_by(func.min(m.Sentence.position))]
                    if b else [])
            boot = None
            if b or reviews:
                boot = {"batch": b.name if b else "", "annotator": a.code if a else "", "documents": docs,
                        "document": document if document in {d["id"] for d in docs} else (docs[0]["id"] if docs else ""),
                        "total": sum(d["n"] for d in docs), "reviews": reviews,
                        "review": review if review in {r["name"] for r in reviews} else (reviews[0]["name"] if reviews else ""),
                        "type": "comparison" if (type == "comparison" and reviews) or (not b and reviews) else "annotations"}
        cur = boot["annotator"] if boot else ""
        return remember(templates.TemplateResponse(request, "export.html", {"boot": boot, "current": cur}), annotator)

    @app.patch("/api/annotators/{code}")
    def rename_annotator(code: str, new_code: str = Form(...)):
        """Change the annotator code. It only affects export file names; data stays linked by id."""
        new = new_code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,8}", new) or new == SYSTEM_ANNOTATOR or code == SYSTEM_ANNOTATOR:
            raise HTTPException(400, "annotator code must be 2-8 letters/digits (FINAL is reserved)")
        with factory() as s:
            a = s.query(m.Annotator).filter_by(code=code).first()
            if not a:
                raise HTTPException(404)
            other = s.query(m.Annotator).filter_by(code=new).first() if new != a.code else None
            if other:
                if s.query(m.Annotation).filter_by(annotator_id=other.id).first():
                    raise HTTPException(400, f"{new} already has annotations; pick another code")
                s.delete(other)  # an unused (e.g. pre-seeded) code: free it up for this rename
                s.flush()
            a.code = new
            a.display_name = new
            s.commit()
            return {"code": new}

    return app
