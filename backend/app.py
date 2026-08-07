import json
import os
import queue
import re
import shutil
import threading
import time
import uuid

import fitz
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from models import Document, Job, Page, Problem, ProblemContent, ProblemTag, Review, db
from ocr import RENDER_DPI, clean_latex, crop_region_image, get_formula_ocr_model, recognize_and_save_problem

base_dir = os.path.abspath(os.path.dirname(__file__))
sqlite_path = os.path.join(base_dir, "data.sqlite")
upload_dir = os.path.join(base_dir, "uploads")
os.makedirs(upload_dir, exist_ok=True)

app = Flask(__name__, static_folder="../frontend/dist", static_url_path="/")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{sqlite_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

CORS(app, resources={r"/api/*": {"origins": "*"}})

db.init_app(app)

with app.app_context():
    db.create_all()


def document_to_dict(document):
    first_page = Page.query.filter_by(document_id=document.id).order_by(Page.page_number).first()
    return {
        "id": str(document.id),
        "filename": document.filename,
        "total_pages": document.total_pages,
        "status": document.status,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        # For the document list's gallery view - the document's own cover
        # page, not tied to review/selection state the way page_to_dict's
        # image_url is used elsewhere.
        "thumbnail_url": f"/api/files/{first_page.image_path}" if first_page and first_page.image_path else None,
    }


def page_to_dict(page):
    return {
        "id": str(page.id),
        "document_id": str(page.document_id),
        "page_number": page.page_number,
        "image_url": f"/api/files/{page.image_path}",
        "width": page.width,
        "height": page.height,
        "status": page.status,
    }


@app.route("/api/documents", methods=["GET"])
def list_documents():
    _scan_uploads_folder()
    documents = Document.query.order_by(Document.created_at.desc()).all()
    return jsonify([document_to_dict(d) for d in documents])


def _random_document_name(extension=".pdf"):
    return f"document_{uuid.uuid4().hex[:8]}{extension}"


def _safe_display_filename(raw_filename):
    """The display filename (Document.filename) is purely a label - the
    actual bytes on disk always live at a UUID-generated path
    (<document-id>/original.pdf), never at a path built from the user's
    filename, so there's no path-traversal/filesystem-safety reason to run
    it through secure_filename() the way the upload endpoint used to.
    That mattered because secure_filename strips non-ASCII characters
    entirely - it turns "수학문제.pdf" into just "pdf" - so doing that
    destroyed Korean titles outright rather than protecting anything.
    Korean (and other non-ASCII) text is preserved as-is here. The one
    fallback case is a name that's already corrupted before it reaches us
    (U+FFFD replacement characters, from a prior lossy decode somewhere
    upstream - e.g. a browser or filesystem using a different encoding than
    expected) - there's nothing meaningful left to preserve there, so a
    random name is used instead of persisting visible corruption. This
    can't catch subtler mojibake (valid-looking but wrong characters from a
    wrong-codec decode), only the unambiguous U+FFFD case."""
    name = os.path.basename(raw_filename or "").strip()
    if not name or "�" in name:
        return _random_document_name(os.path.splitext(name)[1] or ".pdf")
    return name


def _create_document_from_pdf(display_filename, save_pdf_fn):
    """Shared by web upload and uploads-folder scanning: creates the
    Document row + its own UUID storage folder, lets save_pdf_fn place the
    actual PDF bytes at the resulting path, then renders every page to a
    PNG and creates the matching Page rows. save_pdf_fn is a callback
    (rather than this function copying bytes itself) so each caller
    controls exactly how the bytes get there - a FileStorage.save() for a
    web upload, a shutil.move() for a file already sitting on disk."""
    document = Document(filename=display_filename, file_path="", status="uploaded")
    db.session.add(document)
    db.session.flush()

    doc_dir = os.path.join(upload_dir, str(document.id))
    os.makedirs(doc_dir, exist_ok=True)

    pdf_path = os.path.join(doc_dir, "original.pdf")
    save_pdf_fn(pdf_path)
    document.file_path = f"{document.id}/original.pdf"

    pdf = fitz.open(pdf_path)
    for i, pdf_page in enumerate(pdf, start=1):
        pixmap = pdf_page.get_pixmap(dpi=RENDER_DPI)
        image_name = f"page_{i:03d}.png"
        pixmap.save(os.path.join(doc_dir, image_name))
        page = Page(
            document_id=document.id,
            page_number=i,
            image_path=f"{document.id}/{image_name}",
            width=pixmap.width,
            height=pixmap.height,
            status="pending",
        )
        db.session.add(page)
    document.total_pages = pdf.page_count
    pdf.close()

    db.session.commit()
    return document


def _scan_uploads_folder():
    """Picks up PDF files placed directly into the uploads folder rather
    than through the web upload form (e.g. copied in via a file manager).
    Existing documents each live inside their own <uuid>/ subfolder, so any
    .pdf sitting loose at the uploads folder's top level is necessarily a
    new, not-yet-ingested file - run through the same ingestion as a web
    upload (and moved into its own subfolder in the process). Cheap to call
    on every document-list fetch: a directory listing plus, ordinarily,
    zero matching loose files."""
    ingested = []
    if not os.path.isdir(upload_dir):
        return ingested
    for name in os.listdir(upload_dir):
        full_path = os.path.join(upload_dir, name)
        if not os.path.isfile(full_path) or not name.lower().endswith(".pdf"):
            continue
        display_filename = _safe_display_filename(name)
        document = _create_document_from_pdf(display_filename, lambda pdf_path, _src=full_path: shutil.move(_src, pdf_path))
        ingested.append(document)
    return ingested


@app.route("/api/documents", methods=["POST"])
def upload_document():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file is required"}), 400

    display_filename = _safe_display_filename(file.filename)
    document = _create_document_from_pdf(display_filename, file.save)
    return jsonify(document_to_dict(document)), 201


def _pages_with_unconfirmed_problems(page_ids):
    """Page ids (from the given set) that still have at least one
    non-confirmed problem - the shared "does this page still need review"
    signal used by both a single document's page list and the global review
    queue. A page with no problems yet (still pending selection, or selected
    with nothing marked on it) is vacuously not in this set."""
    if not page_ids:
        return set()
    return {
        row[0]
        for row in db.session.query(Problem.page_id)
        .filter(Problem.page_id.in_(page_ids), Problem.status != "confirmed")
        .distinct()
    }


@app.route("/api/documents/<uuid:document_id>", methods=["GET"])
def get_document(document_id):
    document = Document.query.get_or_404(document_id)
    pages = (
        Page.query.filter_by(document_id=document_id)
        .order_by(Page.page_number)
        .all()
    )
    unconfirmed_page_ids = _pages_with_unconfirmed_problems([p.id for p in pages])

    data = document_to_dict(document)
    data["pages"] = [
        {**page_to_dict(p), "reviewed": p.id not in unconfirmed_page_ids} for p in pages
    ]
    return jsonify(data)


@app.route("/api/documents/<uuid:document_id>/print", methods=["GET"])
def get_document_print(document_id):
    """All of a document's problems, in reading order (page number, then
    position on the page), each with its full content tree - the data feed
    for the print/export view. Includes every problem regardless of review
    status; this is for checking how stored content renders, not a
    "confirmed only" final export (a filter can be added once the layout
    itself is validated)."""
    document = Document.query.get_or_404(document_id)
    rows = (
        db.session.query(Problem, Page.page_number)
        .join(Page, Problem.page_id == Page.id)
        .filter(Page.document_id == document_id)
        .order_by(Page.page_number, Problem.order_index)
        .all()
    )

    problem_ids = [p.id for p, _ in rows]
    contents_by_problem = {}
    if problem_ids:
        contents = (
            ProblemContent.query.filter(ProblemContent.problem_id.in_(problem_ids))
            .order_by(ProblemContent.order_index)
            .all()
        )
        for content in contents:
            contents_by_problem.setdefault(content.problem_id, []).append(content)

    problems = []
    for problem, page_number in rows:
        entry = problem_to_dict(problem, contents_by_problem.get(problem.id, []))
        entry["page_number"] = page_number
        problems.append(entry)

    return jsonify({"document": document_to_dict(document), "problems": problems})


@app.route("/api/queue/regions", methods=["GET"])
def get_region_queue():
    """Every page across all documents that still needs its problem regions
    marked, oldest document first - the backlog behind the Region Queue
    page, so a reviewer can work through pending pages without opening each
    document individually."""
    rows = (
        db.session.query(Page, Document)
        .join(Document, Page.document_id == Document.id)
        .filter(Page.status != "completed")
        .order_by(Document.created_at, Page.page_number)
        .all()
    )
    return jsonify([{**page_to_dict(p), "document_filename": d.filename} for p, d in rows])


@app.route("/api/queue/reviews", methods=["GET"])
def get_review_queue():
    """Every page across all documents whose regions are marked but still
    has at least one unconfirmed problem - the backlog behind the Review
    Queue page. Note: recognition only starts once *all* of a document's
    pages are marked complete (see save_page_problems), so a page can
    briefly appear here with its problems still unrecognized rather than
    ready to review - the same thing already happens in PageViewer's
    per-document sidebar, just surfaced globally here instead."""
    rows = (
        db.session.query(Page, Document)
        .join(Document, Page.document_id == Document.id)
        .filter(Page.status == "completed")
        .order_by(Document.created_at, Page.page_number)
        .all()
    )
    unconfirmed_page_ids = _pages_with_unconfirmed_problems([p.id for p, d in rows])
    return jsonify([
        {**page_to_dict(p), "document_filename": d.filename}
        for p, d in rows
        if p.id in unconfirmed_page_ids
    ])


def delete_problems(problem_ids):
    """Bulk-delete problems and everything that hangs off them. Uses plain
    SQL deletes (not ORM object deletion) so it doesn't depend on cascade
    configuration or on the rows being loaded into the session -- callers
    doing a bulk Problem delete elsewhere bypass ORM cascade entirely, which
    is exactly what orphaned problem_contents rows in the past."""
    if not problem_ids:
        return
    ProblemContent.query.filter(ProblemContent.problem_id.in_(problem_ids)).delete(synchronize_session=False)
    Review.query.filter(Review.problem_id.in_(problem_ids)).delete(synchronize_session=False)
    db.session.execute(ProblemTag.__table__.delete().where(ProblemTag.problem_id.in_(problem_ids)))
    Problem.query.filter(Problem.id.in_(problem_ids)).delete(synchronize_session=False)


@app.route("/api/documents/<uuid:document_id>", methods=["DELETE"])
def delete_document(document_id):
    document = Document.query.get_or_404(document_id)

    page_ids = [row[0] for row in Page.query.filter_by(document_id=document_id).with_entities(Page.id)]
    problem_ids = [row[0] for row in Problem.query.filter(Problem.page_id.in_(page_ids)).with_entities(Problem.id)]

    delete_problems(problem_ids)
    Page.query.filter_by(document_id=document_id).delete(synchronize_session=False)
    Job.query.filter_by(document_id=document_id).delete(synchronize_session=False)
    db.session.delete(document)
    db.session.commit()

    shutil.rmtree(os.path.join(upload_dir, str(document_id)), ignore_errors=True)

    return jsonify({"ok": True})


@app.route("/api/documents/<uuid:document_id>", methods=["PATCH"])
def rename_document(document_id):
    """Renames a document's display label only - the underlying file
    always lives at its own UUID path (see _create_document_from_pdf), so
    this is a pure DB update with no filesystem changes. The main use case
    is fixing an unhelpful name after an uploads-folder scan had to fall
    back to a random one (see _safe_display_filename), or just relabeling
    for easier browsing."""
    document = Document.query.get_or_404(document_id)
    data = request.get_json() or {}
    new_filename = (data.get("filename") or "").strip()
    if not new_filename:
        return jsonify({"error": "filename must not be empty"}), 400
    document.filename = new_filename
    db.session.commit()
    return jsonify(document_to_dict(document))


@app.route("/api/files/<path:filename>")
def get_file(filename):
    return send_from_directory(upload_dir, filename)


def crop_problem_image(document, page, problem):
    """Render a problem's crop straight from the original PDF (not the
    already-rasterized page PNG). Reopening a raster image via PyMuPDF gives
    it a "page" sized in points from the image's embedded DPI, so clipping
    with raw pixel numbers samples the wrong region entirely -- rendering
    from the source PDF with an explicit pixel<->point conversion avoids
    that and gives a sharper crop besides."""
    problems_dir = os.path.join(upload_dir, str(page.document_id), "problems")
    os.makedirs(problems_dir, exist_ok=True)
    crop_name = f"{problem.id}.png"
    crop_abs = os.path.join(problems_dir, crop_name)

    scale = RENDER_DPI / 72
    pdf_path = os.path.join(upload_dir, document.file_path)
    pdf = fitz.open(pdf_path)
    try:
        pdf_page = pdf[page.page_number - 1]
        rect = fitz.Rect(
            problem.x / scale,
            problem.y / scale,
            (problem.x + problem.w) / scale,
            (problem.y + problem.h) / scale,
        )
        pixmap = pdf_page.get_pixmap(clip=rect, dpi=RENDER_DPI)
        pixmap.save(crop_abs)
    finally:
        pdf.close()

    return f"{page.document_id}/problems/{crop_name}"


def crop_content_image(document, page, content, x, y, w, h):
    """Same idea as crop_problem_image, one level down: crop a single
    type="image" content block's region (e.g. a graph or diagram) straight
    from the source PDF and save it as its own file. x/y/w/h are absolute
    page-pixel coordinates (already offset by the problem's own position)."""
    contents_dir = os.path.join(upload_dir, str(page.document_id), "contents")
    os.makedirs(contents_dir, exist_ok=True)
    crop_name = f"{content.id}.png"
    crop_abs = os.path.join(contents_dir, crop_name)

    scale = RENDER_DPI / 72
    pdf_path = os.path.join(upload_dir, document.file_path)
    pdf = fitz.open(pdf_path)
    try:
        pdf_page = pdf[page.page_number - 1]
        rect = fitz.Rect(x / scale, y / scale, (x + w) / scale, (y + h) / scale)
        pixmap = pdf_page.get_pixmap(clip=rect, dpi=RENDER_DPI)
        pixmap.save(crop_abs)
    finally:
        pdf.close()

    return f"{page.document_id}/contents/{crop_name}"


@app.route("/api/pages/<uuid:page_id>/problems", methods=["POST"])
def save_page_problems(page_id):
    page = Page.query.get_or_404(page_id)
    document = Document.query.get(page.document_id)
    data = request.get_json() or {}
    rectangles = data.get("rectangles", [])

    old_problem_ids = [row[0] for row in Problem.query.filter_by(page_id=page_id).with_entities(Problem.id)]
    delete_problems(old_problem_ids)
    for index, rect in enumerate(rectangles):
        problem = Problem(
            page_id=page_id,
            order_index=index,
            x=int(rect["x"]),
            y=int(rect["y"]),
            w=int(rect["w"]),
            h=int(rect["h"]),
            status="pending",
        )
        db.session.add(problem)
        db.session.flush()
        problem.crop_path = crop_problem_image(document, page, problem)

    page.status = "completed"
    db.session.flush()

    remaining = Page.query.filter(
        Page.document_id == page.document_id, Page.status != "completed"
    ).count()
    if remaining == 0:
        document.status = "selection_completed"

    db.session.commit()

    # Recognition (including the LaTeX OCR pass) is slow -- tens of seconds
    # per page. Respond as soon as the selection itself is saved, and queue
    # recognition for THIS page rather than waiting for every page in the
    # document to be marked complete first - a reviewer working through a
    # long document gets each page's OCR results as soon as that page is
    # done, not all at once at the very end. document.status still tracks
    # whole-document completion (drives the page-gallery view and other
    # document-level UI), just decoupled from when recognition actually
    # runs. See _enqueue_ocr_task for why this is a queue rather than a
    # thread spawned per page.
    if rectangles:
        _enqueue_ocr_task(("page", page.id), {"kind": "page", "page_id": page.id})

    return jsonify({"page": page_to_dict(page), "document_status": document.status})


def problem_content_to_dict(content):
    return {
        "id": str(content.id),
        "parent_content_id": str(content.parent_content_id) if content.parent_content_id else None,
        "order_index": content.order_index,
        "type": content.type,
        "label": content.label,
        "content": content.content,
        # Cache-busted: the underlying file can be overwritten in place by a
        # later region adjustment while the path itself stays the same, and
        # an unchanged <img src> won't re-fetch on its own otherwise.
        "image_url": f"/api/files/{content.crop_path}?t={time.time()}" if content.crop_path else None,
        "bbox": {
            "x": content.bbox_x,
            "y": content.bbox_y,
            "w": content.bbox_w,
            "h": content.bbox_h,
        },
        "confidence": content.confidence,
        "processing": bool(content.processing),
        "display_mode": bool(content.display_mode),
        "line_break_before": bool(content.line_break_before),
    }


def problem_to_dict(problem, contents):
    return {
        "id": str(problem.id),
        "order_index": problem.order_index,
        "status": problem.status,
        "bbox": {"x": problem.x, "y": problem.y, "w": problem.w, "h": problem.h},
        "contents": [problem_content_to_dict(c) for c in contents],
        # Lets the frontend show/disable the "revert to initial OCR" button
        # without shipping the (potentially large) snapshot JSON itself on
        # every fetch - only /revert actually needs the full blob.
        "has_initial_snapshot": bool(problem.initial_content_snapshot),
    }


def page_review_to_dict(page):
    problems = Problem.query.filter_by(page_id=page.id).order_by(Problem.order_index).all()
    contents_by_problem = {}
    if problems:
        problem_ids = [p.id for p in problems]
        contents = (
            ProblemContent.query.filter(ProblemContent.problem_id.in_(problem_ids))
            .order_by(ProblemContent.order_index)
            .all()
        )
        for content in contents:
            contents_by_problem.setdefault(content.problem_id, []).append(content)

    data = page_to_dict(page)
    data["problems"] = [problem_to_dict(p, contents_by_problem.get(p.id, [])) for p in problems]
    return data


@app.route("/api/pages/<uuid:page_id>/review", methods=["GET"])
def get_page_review(page_id):
    page = Page.query.get_or_404(page_id)
    return jsonify(page_review_to_dict(page))


@app.route("/api/pages/<uuid:page_id>/review", methods=["POST"])
def confirm_page_review(page_id):
    page = Page.query.get_or_404(page_id)
    data = request.get_json() or {}

    for problem_data in data.get("problems", []):
        try:
            problem_uuid = uuid.UUID(str(problem_data["id"]))
        except (KeyError, ValueError, TypeError):
            continue
        problem = Problem.query.filter_by(id=problem_uuid, page_id=page_id).first()
        if not problem:
            continue

        for content_data in problem_data.get("contents", []):
            try:
                content_uuid = uuid.UUID(str(content_data["id"]))
            except (KeyError, ValueError, TypeError):
                continue
            content = ProblemContent.query.filter_by(id=content_uuid, problem_id=problem.id).first()
            if content is not None:
                content.content = content_data.get("content", content.content)
                content.label = content_data.get("label", content.label)
                if "display_mode" in content_data:
                    content.display_mode = bool(content_data["display_mode"])
                if "line_break_before" in content_data:
                    content.line_break_before = bool(content_data["line_break_before"])

        problem.status = "confirmed"

    db.session.commit()
    return jsonify(page_review_to_dict(page))


# All OCR model inference - page-level recognition after a region
# selection is completed, and per-block re-recognition from "Adjust
# region" - is funneled through a single sequential worker rather than
# each request spawning its own thread. The model calls are CPU-bound
# (pix2text/EasyOCR both run with gpu=False here), so running many at once
# doesn't parallelize the actual work, it just makes every one of them
# slower fighting over the same cores. A single worker also puts a hard,
# well-defined cap on how much is ever "in flight" at once (exactly one
# task): queuing up an unusually large batch - hundreds of pages completed
# in quick succession - degrades to "it takes a while, strictly in order"
# rather than to unbounded concurrent model calls competing for memory/CPU.
_ocr_task_queue = queue.Queue()
_ocr_queued_keys = set()
_ocr_queue_lock = threading.Lock()


def _enqueue_ocr_task(key, task):
    """key is a hashable dedup token, e.g. ("page", page_id) or
    ("region", content_id). Queuing the same task again while it's still
    waiting or already in flight is a no-op - re-marking the same page's
    selection, or re-adjusting the same block's region, before the first
    pass has even run would otherwise double the work for no benefit."""
    with _ocr_queue_lock:
        if key in _ocr_queued_keys:
            return
        _ocr_queued_keys.add(key)
    _ocr_task_queue.put((key, task))


def _run_page_recognition_task(page_id):
    page = Page.query.get(page_id)
    if page is None:
        return
    document = Document.query.get(page.document_id)
    problems = Problem.query.filter_by(page_id=page_id).all()
    for problem in problems:
        try:
            recognize_and_save_problem(document, page, problem)
            db.session.commit()
        except Exception:
            db.session.rollback()


def _run_problem_recognition_task(problem_id):
    """Companion to recognize_problem's "Re-run OCR" button: re-recognizes
    one already-existing problem from its saved crop. Same body as the
    per-problem step inside _run_page_recognition_task's loop, just for a
    single problem picked directly rather than every problem on a page."""
    problem = Problem.query.get(problem_id)
    if problem is None:
        return
    page = Page.query.get(problem.page_id)
    if page is None:
        return
    document = Document.query.get(page.document_id)
    try:
        recognize_and_save_problem(document, page, problem)
        db.session.commit()
    except Exception:
        db.session.rollback()


# Which task the worker is actually running right now (None while idle
# between queue.get() calls) - set/cleared only by the worker thread itself,
# but read from request-handling threads by get_ocr_status, hence the same
# _ocr_queue_lock protecting _ocr_queued_keys also guards this.
_ocr_current_task = None


def _ocr_worker_loop():
    global _ocr_current_task
    while True:
        key, task = _ocr_task_queue.get()
        with _ocr_queue_lock:
            _ocr_current_task = task
        started_at = time.time()
        print(f"[OCR] {task['kind']} task started: {task}")
        try:
            with app.app_context():
                try:
                    if task["kind"] == "page":
                        _run_page_recognition_task(task["page_id"])
                    elif task["kind"] == "problem":
                        _run_problem_recognition_task(task["problem_id"])
                    elif task["kind"] == "region":
                        _run_region_recognition_task(
                            task["content_id"],
                            task["document_id"],
                            task["page_id"],
                            task["x"],
                            task["y"],
                            task["w"],
                            task["h"],
                        )
                finally:
                    db.session.remove()
        except Exception:
            # Each task function already handles/commits its own failures -
            # this is a last-resort guard so a truly unexpected error (e.g.
            # the DB connection itself dropping) can't kill the worker
            # thread and silently stop everything queued behind it.
            pass
        finally:
            # 작업 하나가 실제로 몇 초 걸렸는지 로그에서 바로 보이도록 -
            # 이전에는 시작/종료 표시가 없어서, 인식 로그 뭉치 사이에서
            # 타임스탬프를 손으로 추적해야만 소요 시간을 알 수 있었다.
            elapsed = time.time() - started_at
            print(f"[OCR] {task['kind']} task finished in {elapsed:.1f}s: {task}")
            with _ocr_queue_lock:
                _ocr_queued_keys.discard(key)
                _ocr_current_task = None
            _ocr_task_queue.task_done()


def _start_ocr_worker():
    threading.Thread(target=_ocr_worker_loop, daemon=True).start()


def _describe_ocr_task(task):
    """Human-readable summary of a queued/in-flight OCR task for the status
    indicator (get_ocr_status) - which document/page it belongs to, so a
    reviewer watching the UI can tell OCR is actually moving rather than
    stuck, and roughly where. Deliberately looked up on demand here rather
    than stashed on the task dict when queued, since a page/document can be
    renamed or deleted while its task is still waiting."""
    page = Page.query.get(task.get("page_id"))
    document = Document.query.get(page.document_id) if page else None
    return {
        "kind": task.get("kind"),
        "document_filename": document.filename if document else None,
        "page_number": page.page_number if page else None,
    }


@app.route("/api/ocr/status", methods=["GET"])
def get_ocr_status():
    """Polled by the frontend's persistent OCR status indicator - what the
    single shared OCR worker (see _enqueue_ocr_task) is doing right now, if
    anything, and how many tasks are still waiting behind it."""
    with _ocr_queue_lock:
        current_task = _ocr_current_task
        queued = _ocr_task_queue.qsize()

    current = _describe_ocr_task(current_task) if current_task is not None else None
    return jsonify({"active": current_task is not None, "current": current, "queued": queued})


def _recover_ocr_queue_on_startup():
    """Runs once at process start, before the worker starts pulling tasks.
    Page-level recognition is resumable because Problem.status stays
    "pending" in the DB until OCR actually succeeds - so any pages left
    mid-batch by a previous run (a crash, or the dev server's own
    auto-reload) get automatically picked back up here instead of silently
    stalling until something else happens to re-trigger them. Per-block
    region re-recognition has no equivalent recovery: the crop coordinates
    for an in-flight "Adjust region" call only ever existed as in-memory
    task args, never persisted, so a stray processing=True left over from
    an interrupted run is just cleared here (the reviewer re-triggers that
    one block by hand) rather than guessed at."""
    with app.app_context():
        stray_page_ids = {
            row[0]
            for row in db.session.query(Problem.page_id).filter(Problem.status == "pending").distinct()
        }
        for page_id in stray_page_ids:
            _enqueue_ocr_task(("page", page_id), {"kind": "page", "page_id": page_id})

        ProblemContent.query.filter_by(processing=True).update({"processing": False})
        db.session.commit()


@app.route("/api/problems/<uuid:problem_id>/recognize", methods=["POST"])
def recognize_problem(problem_id):
    """Queues a full re-recognition of this problem on the shared OCR
    worker (see _enqueue_ocr_task) rather than running it inline - the
    model call can take minutes, and running it synchronously in the
    request left no way to tell "still working on this problem" from
    "stuck" once a reviewer navigated to a different problem before the
    request came back (the button's disabled state was local to that one
    request). Marks the problem "pending" immediately instead, so the
    existing polling machinery in ProblemReview (stillRecognizing) picks
    it up the same way it already does for the initial per-page
    recognition pass - correct regardless of which problem is on screen
    by the time it finishes. Discards any reviewer edits since the
    original recognition once the worker actually gets to it - the "reset
    to initial OCR" escape hatch. Safe to queue more than once: recognition
    has no randomness (greedy decoding, same crop image in, same content
    out), so it reproduces the original result rather than a fresh guess."""
    problem = Problem.query.get_or_404(problem_id)
    if not problem.crop_path:
        return jsonify({"error": "problem has no cropped image; re-save problem selection first"}), 400

    page = Page.query.get(problem.page_id)
    contents = ProblemContent.query.filter_by(problem_id=problem.id).order_by(ProblemContent.order_index).all()
    problem.status = "pending"
    db.session.commit()

    _enqueue_ocr_task(("problem", problem.id), {"kind": "problem", "problem_id": problem.id, "page_id": page.id})

    return jsonify(problem_to_dict(problem, contents))


@app.route("/api/problems/<uuid:problem_id>/revert", methods=["POST"])
def revert_problem_to_initial(problem_id):
    """Restores this problem's content rows to the exact result of its
    first successful recognition (see initial_content_snapshot) - unlike
    /recognize, this never calls the OCR model, it just replays the
    snapshot already on record. Fails if no snapshot exists yet (e.g. the
    problem has never been through recognize_and_save_problem), in which
    case /recognize is the only option."""
    problem = Problem.query.get_or_404(problem_id)
    if not problem.initial_content_snapshot:
        return jsonify({"error": "no initial OCR snapshot exists for this problem"}), 400

    snapshot = json.loads(problem.initial_content_snapshot)

    ProblemContent.query.filter_by(problem_id=problem.id).delete()
    db.session.flush()

    new_rows = []
    for entry in snapshot:
        row = ProblemContent(
            problem_id=problem.id,
            order_index=entry["order_index"],
            type=entry["type"],
            label=entry["label"],
            content=entry["content"],
            bbox_x=entry["bbox_x"],
            bbox_y=entry["bbox_y"],
            bbox_w=entry["bbox_w"],
            bbox_h=entry["bbox_h"],
            confidence=entry["confidence"],
            crop_path=entry["crop_path"],
            display_mode=entry["display_mode"],
        )
        db.session.add(row)
        new_rows.append(row)
    db.session.flush()  # need each row's id before wiring up parent_content_id below

    for row, entry in zip(new_rows, snapshot):
        if entry["parent_index"] is not None:
            row.parent_content_id = new_rows[entry["parent_index"]].id

    problem.status = "recognized"
    db.session.commit()

    return jsonify({"contents": [problem_content_to_dict(c) for c in new_rows], "status": problem.status})


@app.route("/api/problems/<uuid:problem_id>/contents", methods=["POST"])
def create_problem_content(problem_id):
    """Manually add an empty content row -- the escape hatch for when
    automatic clustering over- or under-merges a region (e.g. several
    multiple-choice options recognized as one formula): delete the bad row,
    add one row per option, and Adjust region each into place by hand."""
    problem = Problem.query.get_or_404(problem_id)
    data = request.get_json() or {}

    content_type = data.get("type")
    if content_type not in ("text", "formula", "choice", "image"):
        return jsonify({"error": "type must be 'text', 'formula', 'choice', or 'image'"}), 400

    raw_parent = data.get("parent_content_id")
    try:
        parent_content_id = uuid.UUID(str(raw_parent)) if raw_parent else None
    except (ValueError, TypeError, AttributeError):
        return jsonify({"error": "parent_content_id must be a UUID or null"}), 400

    if parent_content_id is not None:
        parent = ProblemContent.query.filter_by(id=parent_content_id, problem_id=problem_id, type="group").first()
        if parent is None:
            return jsonify({"error": "parent_content_id must reference a group in this problem"}), 400
    elif content_type == "choice":
        # "+ Choice" doesn't ask the caller for a parent - a manually added
        # choice belongs in the Choices group the same as any other choice,
        # so route it there automatically (creating the group if this is
        # the problem's first choice) rather than leaving it stranded as a
        # top-level row like set_content_type already avoids doing.
        parent_content_id = _get_or_create_choices_group(problem_id).id

    # Optional: place the new row right after a specific sibling (the
    # reviewer's last-focused row) instead of always appending at the end -
    # a fractional order_index sorts correctly between it and the next
    # sibling without having to shift everyone else's index by hand;
    # _renumber_siblings cleans it back up to a plain integer below.
    raw_after = data.get("after_content_id")
    order_index = None
    if raw_after:
        try:
            after_uuid = uuid.UUID(str(raw_after))
        except (ValueError, TypeError, AttributeError):
            return jsonify({"error": "after_content_id must be a UUID"}), 400
        after_content = ProblemContent.query.filter_by(
            id=after_uuid, problem_id=problem_id, parent_content_id=parent_content_id
        ).first()
        if after_content is not None:
            order_index = after_content.order_index + 0.5

    if order_index is None:
        order_index = ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=parent_content_id).count()

    content = ProblemContent(
        problem_id=problem_id,
        parent_content_id=parent_content_id,
        order_index=order_index,
        type=content_type,
        content="",
        bbox_x=0,
        bbox_y=0,
        bbox_w=min(60, problem.w),
        bbox_h=min(30, problem.h),
        confidence=1.0,
    )
    db.session.add(content)
    db.session.flush()
    # Keeps a new row from landing after the choices group -- it would
    # otherwise, since that group already counts toward sibling_count above.
    _renumber_siblings(problem_id, parent_content_id)

    remaining = ProblemContent.query.filter_by(problem_id=problem_id).order_by(ProblemContent.order_index).all()
    db.session.commit()
    return jsonify(problem_to_dict(problem, remaining)), 201


def _containment_ratio(outer, inner):
    """Fraction of inner's area that falls within outer. Both are dicts
    with x/y/w/h in the same (problem-relative) coordinate space."""
    ox0, oy0, ox1, oy1 = outer["x"], outer["y"], outer["x"] + outer["w"], outer["y"] + outer["h"]
    ix0, iy0, ix1, iy1 = inner["x"], inner["y"], inner["x"] + inner["w"], inner["y"] + inner["h"]
    overlap_w = max(0, min(ox1, ix1) - max(ox0, ix0))
    overlap_h = max(0, min(oy1, iy1) - max(oy0, iy0))
    inner_area = inner["w"] * inner["h"]
    return (overlap_w * overlap_h) / inner_area if inner_area > 0 else 0


# How much of another content block's area a new image region needs to
# cover before that block is treated as "this is what the image already
# shows" and removed, rather than a coincidental partial overlap.
IMAGE_OVERLAP_DELETE_THRESHOLD = 0.7


def _delete_image_overlaps(problem, content, x, y, w, h):
    """A new image region's own axis labels/coordinates otherwise tend to
    get picked up separately as stray text/formula fragments duplicating
    what the crop already shows -- remove whatever other content rows it
    substantially covers. Flushes but does not commit."""
    candidates = ProblemContent.query.filter(
        ProblemContent.problem_id == problem.id,
        ProblemContent.id != content.id,
        ProblemContent.type != "group",
    ).all()
    affected_parents = set()
    for other in candidates:
        if not other.bbox_w or not other.bbox_h:
            continue
        ratio = _containment_ratio(
            {"x": x, "y": y, "w": w, "h": h},
            {"x": other.bbox_x, "y": other.bbox_y, "w": other.bbox_w, "h": other.bbox_h},
        )
        if ratio >= IMAGE_OVERLAP_DELETE_THRESHOLD:
            affected_parents.add(other.parent_content_id)
            db.session.delete(other)
    db.session.flush()
    for parent_id in affected_parents:
        _renumber_siblings(problem.id, parent_id)


def _run_region_recognition_task(content_id, document_id, page_id, crop_x, crop_y, w, h):
    """Companion to update_content_region's formula/choice path: the model
    inference itself happens here, off the request thread, so the frontend
    isn't stuck waiting tens of seconds on it -- see the comment there. Runs
    on the shared OCR worker (see _enqueue_ocr_task), which already
    provides the app context / session teardown this needs."""
    try:
        document = Document.query.get(document_id)
        page = Page.query.get(page_id)
        model = get_formula_ocr_model()
        img = crop_region_image(document, page, crop_x, crop_y, w, h)
        recognized = clean_latex(model.recognize_formula(img).strip())
        content = ProblemContent.query.get(content_id)
        if content is not None:
            content.content = recognized
            content.confidence = 1.0
            content.processing = False
        db.session.commit()
    except Exception:
        db.session.rollback()
        content = ProblemContent.query.get(content_id)
        if content is not None:
            content.processing = False
            db.session.commit()


@app.route("/api/problem_contents/<uuid:content_id>/region", methods=["POST"])
def update_content_region(content_id):
    """Manually override a content block's region. For a formula or choice,
    re-runs LaTeX OCR on the new crop -- the safety net for cases automatic
    clustering can't get right (e.g. a symbol drawn as vector graphics with
    no extractable text, so there's nothing in the PDF's text layer to tell
    clustering where its bounding box even is). For an image, just saves
    the crop -- a diagram/graph isn't run through any recognition, it's
    shown as-is -- and deletes whatever other content rows the new region
    substantially covers.

    The OCR call is a slow model inference (tens of seconds is not unusual),
    so for formula/choice this responds as soon as the region itself is
    saved and runs recognition in a background thread -- same pattern as
    the initial per-page recognition. The frontend polls (via the existing
    "still recognizing" mechanism, extended to cover this) and picks up the
    result once processing flips back to false."""
    content = ProblemContent.query.get_or_404(content_id)
    if content.type not in ("formula", "choice", "image"):
        return jsonify({"error": "only formula, choice, or image content can have its region adjusted"}), 400

    problem = Problem.query.get(content.problem_id)
    page = Page.query.get(problem.page_id)
    document = Document.query.get(page.document_id)

    data = request.get_json() or {}
    try:
        x, y, w, h = int(data["x"]), int(data["y"]), int(data["w"]), int(data["h"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "x, y, w, h are required"}), 400
    if w <= 0 or h <= 0:
        return jsonify({"error": "w and h must be positive"}), 400

    content.bbox_x = x
    content.bbox_y = y
    content.bbox_w = w
    content.bbox_h = h

    if content.type == "image":
        content.crop_path = crop_content_image(document, page, content, problem.x + x, problem.y + y, w, h)
        content.confidence = 1.0
        db.session.flush()
        _delete_image_overlaps(problem, content, x, y, w, h)
        remaining = ProblemContent.query.filter_by(problem_id=problem.id).order_by(ProblemContent.order_index).all()
        db.session.commit()
        return jsonify(problem_to_dict(problem, remaining))

    content.processing = True
    remaining = ProblemContent.query.filter_by(problem_id=problem.id).order_by(ProblemContent.order_index).all()
    result = problem_to_dict(problem, remaining)
    # Captured before commit: db.session.commit() expires ORM instance
    # state by default, so reading these attributes afterward would
    # trigger implicit reload queries on a session about to be torn down
    # for this request - grab plain values now instead of relying on that.
    task = {
        "kind": "region",
        "content_id": content.id,
        "document_id": document.id,
        "page_id": page.id,
        "x": problem.x + x,
        "y": problem.y + y,
        "w": w,
        "h": h,
    }
    # Commit before queuing, not after -- the worker opens its own session
    # (see _ocr_worker_loop), which won't reliably see this row's
    # processing=True until this transaction lands.
    db.session.commit()
    _enqueue_ocr_task(("region", content.id), task)
    return jsonify(result)


def _is_pinned_last(content):
    """The auto-created multiple-choice group always sorts after everything
    else at its level -- a set of answer options reads as the end of the
    problem, not something that belongs wherever clustering happened to
    place it (or wherever a manual reorder of its siblings pushes it)."""
    return content.type == "group" and content.label == "Choices"


def _renumber_siblings(problem_id, parent_content_id):
    """Reassign order_index 0..n-1 among rows sharing the same parent (None
    for top-level), so structural changes never leave gaps or clashes. Pins
    any choices group to the end first (stable sort, so it's the only thing
    this can reorder -- everything else keeps its relative order)."""
    siblings = (
        ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=parent_content_id)
        .order_by(ProblemContent.order_index)
        .all()
    )
    siblings.sort(key=lambda c: 1 if _is_pinned_last(c) else 0)
    for index, c in enumerate(siblings):
        c.order_index = index
    return siblings


def _ungroup(group):
    """Reparent a group's children back to top level, appended after the
    problem's existing top-level rows, preserving their relative order.
    Does not delete the group row or commit -- caller controls both."""
    children = (
        ProblemContent.query.filter_by(parent_content_id=group.id)
        .order_by(ProblemContent.order_index)
        .all()
    )
    for child in children:
        child.parent_content_id = None
    db.session.flush()
    _renumber_siblings(group.problem_id, None)


# Numbering markers that conventionally start a new statement within a
# <보기> block: "(가)"/"(나)"/... (parenthesized Korean ordinals) and a bare
# jamo "ㄱ"/"ㄴ"/"ㄷ"/... immediately followed by "." or ")". Checked only
# when grouping under the literal "보기" label - meaningless as a
# line-break signal in any other kind of group (e.g. a "(가)/(나)"-style
# condition list is exactly what these mark, but a free-text "Other" group
# might legitimately contain unrelated parenthesized text).
BOGI_LINE_BREAK_RE = re.compile(r"^\((가|나|다|라|마|바|사)\)|^[ㄱㄴㄷㄹㅁㅂㅅ][.\)]")


def _looks_like_bogi_marker(content):
    return bool(content) and bool(BOGI_LINE_BREAK_RE.match(content.strip()))


@app.route("/api/problems/<uuid:problem_id>/contents/group", methods=["POST"])
def group_problem_contents(problem_id):
    """Bundle a set of top-level content rows under a new type="group" row
    (e.g. a <보기> box) with a chosen label. Not auto-detected -- the current
    pipeline reads only the PDF's text layer, which has no signal for a box
    border, so this is how a reviewer marks one by hand. Within a "보기"
    group specifically, rows whose content starts with a recognized
    numbering marker ("(가)", "ㄴ.", ...) are seeded with
    line_break_before=True so they print on their own line without the
    reviewer having to flag each one - anything the pattern misses is still
    manually toggleable per row from that point on."""
    problem = Problem.query.get_or_404(problem_id)
    data = request.get_json() or {}

    label = (data.get("label") or "").strip()
    if not label:
        return jsonify({"error": "label is required"}), 400

    try:
        content_ids = [uuid.UUID(str(cid)) for cid in data.get("content_ids", [])]
    except (ValueError, TypeError, AttributeError):
        return jsonify({"error": "content_ids must be a list of UUIDs"}), 400
    if not content_ids:
        return jsonify({"error": "content_ids must not be empty"}), 400

    rows = ProblemContent.query.filter(
        ProblemContent.id.in_(content_ids), ProblemContent.problem_id == problem_id
    ).all()
    if len(rows) != len(content_ids):
        return jsonify({"error": "content_ids must all belong to this problem"}), 400
    if any(r.parent_content_id is not None or r.type == "group" for r in rows):
        return jsonify({"error": "only top-level, non-group content can be grouped"}), 400

    ordered_rows = sorted(rows, key=lambda r: r.order_index)

    # Reuse a top-level group already carrying this exact label (e.g. a
    # second "Group selected" into "Choices") instead of creating a sibling
    # duplicate - two same-labelled groups would both claim to be *the*
    # choices group to the frontend, which only ever renders the first one
    # it finds, silently hiding the second's children.
    group = ProblemContent.query.filter_by(
        problem_id=problem_id, parent_content_id=None, type="group", label=label
    ).first()

    if group is not None:
        next_index = ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=group.id).count()
    else:
        top_level_count = ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=None).count()
        group = ProblemContent(
            problem_id=problem_id,
            parent_content_id=None,
            order_index=top_level_count,
            type="group",
            label=label,
        )
        db.session.add(group)
        db.session.flush()
        next_index = 0

    for offset, row in enumerate(ordered_rows):
        row.parent_content_id = group.id
        row.order_index = next_index + offset
        if label == "보기" and _looks_like_bogi_marker(row.content):
            row.line_break_before = True
    db.session.flush()
    _renumber_siblings(problem_id, None)

    remaining = ProblemContent.query.filter_by(problem_id=problem_id).order_by(ProblemContent.order_index).all()
    db.session.commit()
    return jsonify(problem_to_dict(problem, remaining))


@app.route("/api/problem_contents/<uuid:group_id>/group", methods=["DELETE"])
def ungroup_problem_content(group_id):
    group = ProblemContent.query.get_or_404(group_id)
    if group.type != "group":
        return jsonify({"error": "only group content can be ungrouped"}), 400
    if group.label == "Choices":
        return jsonify({"error": "the choices group cannot be ungrouped"}), 400

    problem_id = group.problem_id
    _ungroup(group)
    db.session.delete(group)
    db.session.flush()
    _renumber_siblings(problem_id, None)

    remaining = ProblemContent.query.filter_by(problem_id=problem_id).order_by(ProblemContent.order_index).all()
    db.session.commit()
    return jsonify(problem_to_dict(Problem.query.get(problem_id), remaining))


# "group" is deliberately excluded - it's a structural row managed only via
# the dedicated group/ungroup endpoints (reparenting children, etc.), not a
# content kind a reviewer should be able to flip a plain row into here.
EDITABLE_CONTENT_TYPES = {"text", "formula", "choice", "image"}


def _get_or_create_choices_group(problem_id):
    """The one Choices group a problem can have, creating it (appended after
    whatever's currently at the top level) if this is the first choice row
    to need it. Shared by set_content_type and create_problem_content, which
    both have to route a row into the group rather than leave it stranded
    as a top-level choice."""
    choices_group = ProblemContent.query.filter_by(
        problem_id=problem_id, parent_content_id=None, type="group", label="Choices"
    ).first()
    if choices_group is None:
        top_level_count = ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=None).count()
        choices_group = ProblemContent(
            problem_id=problem_id,
            parent_content_id=None,
            order_index=top_level_count,
            type="group",
            label="Choices",
        )
        db.session.add(choices_group)
        db.session.flush()
    return choices_group


@app.route("/api/problem_contents/<uuid:content_id>/type", methods=["POST"])
def set_content_type(content_id):
    """Changing a row to/from type="choice" also has to move it in or out of
    the "Choices" group, or the frontend's compact choices block (which
    renders that group's children as-is, regardless of type) and the plain
    content tree would disagree about where the row lives - a choice sitting
    outside the group, or a non-choice stranded inside it."""
    content = ProblemContent.query.get_or_404(content_id)
    if content.type == "group":
        return jsonify({"error": "cannot retype a group row"}), 400

    data = request.get_json() or {}
    new_type = data.get("type")
    if new_type not in EDITABLE_CONTENT_TYPES:
        return jsonify({"error": "invalid type"}), 400

    problem_id = content.problem_id
    old_parent_id = content.parent_content_id
    content.type = new_type
    db.session.flush()

    choices_group = ProblemContent.query.filter_by(
        problem_id=problem_id, parent_content_id=None, type="group", label="Choices"
    ).first()

    if new_type == "choice":
        if choices_group is None:
            choices_group = _get_or_create_choices_group(problem_id)
        if content.parent_content_id != choices_group.id:
            sibling_count = ProblemContent.query.filter_by(
                problem_id=problem_id, parent_content_id=choices_group.id
            ).count()
            content.parent_content_id = choices_group.id
            content.order_index = sibling_count
            db.session.flush()
            _renumber_siblings(problem_id, old_parent_id)
            _renumber_siblings(problem_id, choices_group.id)
    elif choices_group is not None and old_parent_id == choices_group.id:
        top_level_count = ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=None).count()
        content.parent_content_id = None
        content.order_index = top_level_count
        db.session.flush()
        _renumber_siblings(problem_id, choices_group.id)
        _renumber_siblings(problem_id, None)
        remaining_children = ProblemContent.query.filter_by(
            problem_id=problem_id, parent_content_id=choices_group.id
        ).count()
        if remaining_children == 0:
            db.session.delete(choices_group)
            db.session.flush()
            _renumber_siblings(problem_id, None)

    remaining = ProblemContent.query.filter_by(problem_id=problem_id).order_by(ProblemContent.order_index).all()
    db.session.commit()
    return jsonify(problem_to_dict(Problem.query.get(problem_id), remaining))


@app.route("/api/problem_contents/<uuid:content_id>", methods=["DELETE"])
def delete_problem_content(content_id):
    """Remove a content block outright -- the cleanup step for a stray
    leftover fragment that Adjust region can't get rid of on its own,
    since adjusting a region only ever changes the one block it's called
    on, never removes a sibling. Deleting a group reparents its children
    back to top level first, rather than orphaning them."""
    content = ProblemContent.query.get_or_404(content_id)
    problem_id = content.problem_id
    parent_content_id = content.parent_content_id

    if content.type == "group":
        _ungroup(content)

    db.session.delete(content)
    db.session.flush()
    _renumber_siblings(problem_id, parent_content_id)

    remaining = ProblemContent.query.filter_by(problem_id=problem_id).order_by(ProblemContent.order_index).all()
    db.session.commit()
    return jsonify(problem_to_dict(Problem.query.get(problem_id), remaining))


@app.route("/api/problems/<uuid:problem_id>/contents/order", methods=["POST"])
def reorder_problem_contents(problem_id):
    problem = Problem.query.get_or_404(problem_id)
    data = request.get_json() or {}

    try:
        ordered_ids = [uuid.UUID(str(cid)) for cid in data.get("content_ids", [])]
    except (ValueError, TypeError, AttributeError):
        return jsonify({"error": "content_ids must be a list of UUIDs"}), 400

    raw_parent = data.get("parent_content_id")
    try:
        parent_content_id = uuid.UUID(str(raw_parent)) if raw_parent else None
    except (ValueError, TypeError, AttributeError):
        return jsonify({"error": "parent_content_id must be a UUID or null"}), 400

    # Scoped to siblings sharing the same parent, not every content row in
    # the problem -- a group's children reorder independently of whatever
    # else is at the top level (and vice versa).
    contents = ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=parent_content_id).all()
    by_id = {c.id: c for c in contents}
    if set(by_id.keys()) != set(ordered_ids):
        return jsonify({"error": "content_ids must match this parent's existing contents exactly"}), 400

    for index, content_id in enumerate(ordered_ids):
        by_id[content_id].order_index = index
    db.session.flush()
    # Re-pins the choices group last even if the requested order tried to
    # move something past it -- see _is_pinned_last.
    _renumber_siblings(problem_id, parent_content_id)
    db.session.commit()

    contents_sorted = ProblemContent.query.filter_by(problem_id=problem_id).order_by(ProblemContent.order_index).all()
    return jsonify(problem_to_dict(problem, contents_sorted))


if __name__ == "__main__":
    # debug=True runs the app under Werkzeug's auto-reloader, which
    # actually launches this script twice - an outer watcher process, then
    # an inner one (marked by WERKZEUG_RUN_MAIN) that actually serves
    # requests. Starting the OCR worker/recovery scan in the watcher too
    # would mean two workers pulling from the same queue and two redundant
    # startup scans - only the real serving process should do this.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _recover_ocr_queue_on_startup()
        _start_ocr_worker()
    app.run(debug=True, threaded=True)
