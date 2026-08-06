import json
import os
import re
import shutil
import threading
import time
import uuid

import fitz
import numpy as np
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from PIL import Image

from models import Document, Job, Page, Problem, ProblemContent, ProblemTag, Review, db

base_dir = os.path.abspath(os.path.dirname(__file__))
sqlite_path = os.path.join(base_dir, "data.sqlite")
upload_dir = os.path.join(base_dir, "uploads")
os.makedirs(upload_dir, exist_ok=True)

RENDER_DPI = 150

# Floor for the formula-clustering gap, expressed in points (a physical
# unit) rather than pixels, then converted at import time via RENDER_DPI.
# A bare pixel count would silently mean a different physical distance if
# RENDER_DPI ever changes; this way the floor scales automatically with it.
MIN_CLUSTER_GAP_PT = 7
MIN_CLUSTER_GAP = round(MIN_CLUSTER_GAP_PT * RENDER_DPI / 72)

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
    # for a whole document. Respond as soon as the selection itself is saved,
    # and run recognition in the background so the "Complete" button doesn't
    # sit there waiting on OCR. The frontend picks up the resulting status
    # changes by polling the review endpoint.
    if remaining == 0:
        threading.Thread(target=run_recognition_in_background, args=(document.id,), daemon=True).start()

    return jsonify({"page": page_to_dict(page), "document_status": document.status})


def run_recognition_in_background(document_id):
    with app.app_context():
        try:
            document = Document.query.get(document_id)
            if document is None:
                return
            rows = (
                db.session.query(Problem, Page)
                .join(Page, Problem.page_id == Page.id)
                .filter(Page.document_id == document.id)
                .all()
            )
            for problem, problem_page in rows:
                try:
                    recognize_and_save_problem(document, problem_page, problem)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        finally:
            db.session.remove()


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


# Classification runs per character (not per PDF span), since a single span
# can mix Hangul prose and a formula fragment with no space between them
# (e.g. "(x=1)에" is one font/size run). Hangul -> text. Letters, digits,
# math operators/brackets, Greek letters -> formula. Generic punctuation and
# whitespace are neutral: they don't start a new segment, they just extend
# whatever segment is already open, since a sentence-ending period or a
# decimal point shouldn't split a run on its own.
HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
FORMULA_CHAR_RE = re.compile(r"[A-Za-z0-9+\-=*/<>≤≥±√∞^_%|(){}\[\]Α-ω①-⑳]")
NEUTRAL_CHAR_RE = re.compile(r"[\s.,:;!?'\"·…‥]")

# A multiple-choice option ("① 1", "② -2x+3") starts with a circled numeral,
# which classify_char_type already buckets as a "formula" character since it
# sits in the same Unicode-range check as digits/operators. Peeling it off
# here keeps these out of the LaTeX-OCR clustering pass entirely -- the PDF
# text layer already extracts the plain value correctly, so there's nothing
# for pix2text to usefully add, only hallucination risk to invite.
CHOICE_LABEL_RE = re.compile(r"^([①-⑳])\s*(.*)$")

# A standard multiple-choice set always has exactly these 5 options -- used
# to pad out a detected choice group that's missing one or more (see
# recognize_and_save_problem) rather than leaving the reviewer to notice a
# gap and add it by hand.
STANDARD_CHOICE_LABELS = ["①", "②", "③", "④", "⑤"]


def classify_char_type(ch):
    if NEUTRAL_CHAR_RE.match(ch):
        return None
    if HANGUL_RE.match(ch):
        return "text"
    if FORMULA_CHAR_RE.match(ch):
        return "formula"
    # Anything else -- unrecognized scripts, mis-mapped glyphs from a font's
    # broken encoding (e.g. a "lim" subscript that extracts as Katakana
    # instead of Latin) -- defaults to formula rather than text. In this
    # domain almost everything that isn't Hangul prose is math-related, and
    # a garbled character defaulting to "text" splits a formula in two;
    # defaulting to "formula" keeps it joined to its neighbors instead.
    return "formula"


def collect_ordered_chars(pdf_page, clip):
    """Individual glyphs within clip, in reading order, each with its own
    bbox (via rawdict). A neutral space is inserted between lines so
    multi-line segments join with a space instead of running together."""
    chars = []
    for block in pdf_page.get_text("rawdict", clip=clip).get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    chars.append(ch)
            chars.append({"c": " ", "bbox": None})
    return chars


def merge_segments(chars, problem, scale):
    segments = []
    current = None
    for ch in chars:
        char_type = classify_char_type(ch["c"])
        bbox = ch.get("bbox")
        if char_type is None:
            if current is not None:
                current["texts"].append(ch["c"])
            continue
        if current is None or current["type"] != char_type:
            if current is not None:
                segments.append(current)
            current = {"type": char_type, "texts": [ch["c"]], "bbox": list(bbox) if bbox else None}
        else:
            current["texts"].append(ch["c"])
            if bbox:
                if current["bbox"] is None:
                    current["bbox"] = list(bbox)
                else:
                    current["bbox"][0] = min(current["bbox"][0], bbox[0])
                    current["bbox"][1] = min(current["bbox"][1], bbox[1])
                    current["bbox"][2] = max(current["bbox"][2], bbox[2])
                    current["bbox"][3] = max(current["bbox"][3], bbox[3])
    if current is not None:
        segments.append(current)

    results = []
    for seg in segments:
        if seg["bbox"] is None:
            continue
        x0, y0, x1, y1 = seg["bbox"]
        content = "".join(seg["texts"]).strip()
        # Formula content is provisional here -- apply_latex_to_formulas
        # replaces it with the LaTeX OCR result -- so only text segments'
        # spacing needs cleaning up at this point.
        if seg["type"] == "text":
            content = clean_text(content)
        results.append(
            {
                "type": seg["type"],
                "label": None,
                "content": content,
                "bbox_x": int(round(x0 * scale)) - problem.x,
                "bbox_y": int(round(y0 * scale)) - problem.y,
                "bbox_w": int(round((x1 - x0) * scale)),
                "bbox_h": int(round((y1 - y0) * scale)),
                "confidence": 1.0,
            }
        )
    return results


def extract_choice_labels(segments):
    """Peel a leading circled numeral off a formula or text segment into its
    own label, retyping it "choice". Checks both types because the two
    detection paths disagree on which one a circled numeral lands in: the
    PDF-text-layer char classifier buckets ①-⑳ as "formula" characters (so
    apply_latex_to_formulas's clustering, which only looks at type ==
    "formula", would otherwise sweep them into OCR), while pix2text's
    scanned-page layout analysis calls them "text" instead."""
    result = []
    for seg in segments:
        if seg["type"] in ("formula", "text"):
            match = CHOICE_LABEL_RE.match(seg["content"])
            if match:
                seg = {
                    **seg,
                    "type": "choice",
                    "label": match.group(1),
                    "content": match.group(2),
                }
        result.append(seg)
    return result


def _rows_overlap(a, b):
    """Two segments sit on the same visual row if their vertical spans
    overlap by more than half of the shorter one's height."""
    ay0, ay1 = a["bbox_y"], a["bbox_y"] + a["bbox_h"]
    by0, by1 = b["bbox_y"], b["bbox_y"] + b["bbox_h"]
    overlap = min(ay1, by1) - max(ay0, by0)
    return overlap > 0.5 * min(a["bbox_h"], b["bbox_h"])


def _merge_boxes(*segs):
    x0 = min(s["bbox_x"] for s in segs)
    y0 = min(s["bbox_y"] for s in segs)
    x1 = max(s["bbox_x"] + s["bbox_w"] for s in segs)
    y1 = max(s["bbox_y"] + s["bbox_h"] for s in segs)
    return {"bbox_x": x0, "bbox_y": y0, "bbox_w": x1 - x0, "bbox_h": y1 - y0}


def merge_empty_choice_markers(segments):
    """pix2text sometimes detects a circled-numeral marker as its own box,
    separate from its value (e.g. "③" alone, with "3√3" as an unrelated
    following formula box). extract_choice_labels already recognizes the
    marker itself (label="③", content="" since there's nothing else in that
    box) but leaves the value stranded as the next segment -- merge a
    content-less choice into whatever immediately follows it on the same
    row instead of leaving one empty and the other orphaned."""
    result = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg["type"] == "choice" and not seg["content"].strip() and i + 1 < len(segments):
            nxt = segments[i + 1]
            if _rows_overlap(seg, nxt):
                result.append({**nxt, "type": "choice", "label": seg["label"], **_merge_boxes(seg, nxt)})
                i += 2
                continue
        result.append(seg)
        i += 1
    return result


def relabel_choice_row(segments):
    """If a row already has at least one confirmed choice (a circled
    numeral OCR happened to read correctly), the rest of that row is almost
    certainly more choices whose marker OCR got wrong -- pix2text misreads
    ①/⑤ as "®" indiscriminately, for instance, so the same wrong glyph can't
    even be mapped back to which number it was. That's a safe row to act on
    specifically because we already have positive evidence it's a choice
    row, unlike guessing from a bare short-text-before-formula shape
    anywhere on the page. Absorbs those into choices too (self-contained
    marker+value in one box, like "@3", or a bare marker that needs the
    next segment as its value, like the empty "③" case above) and
    renumbers every choice on the row ①②③... by left-to-right position, so
    a garbled marker glyph never survives into the saved label."""
    anchors = [s for s in segments if s["type"] == "choice"]
    if not anchors:
        return segments
    anchor = anchors[0]

    result = list(segments)
    i = 0
    while i < len(result) - 1:
        cur, nxt = result[i], result[i + 1]
        if cur["type"] != "text" or len(cur["content"].strip()) > 3 or not _rows_overlap(cur, anchor):
            i += 1
            continue

        stripped = cur["content"].strip()
        digit_match = re.match(r"^\D*(\d.*)$", stripped)
        if digit_match:
            # Marker and value already fused in one box (e.g. "@3" ~ "②3");
            # keep only the value, drop the unrecognizable marker prefix.
            result[i] = {**cur, "type": "choice", "label": None, "content": digit_match.group(1)}
            i += 1
        elif HANGUL_RE.search(stripped):
            # This box's own marker glyph may be misread, but its content is
            # genuine Hangul (e.g. "ㄱ", a <보기>-item reference used as the
            # answer value itself, not a math expression) -- pix2text
            # doesn't hallucinate real Hangul out of a misread symbol, so
            # this is real content. Keep it as the choice's own value
            # instead of discarding it for whatever's next on the row.
            result[i] = {**cur, "type": "choice", "label": None, "content": stripped}
            i += 1
        elif _rows_overlap(cur, nxt):
            # Bare marker glyph with nothing else in its box; the value is
            # the next segment on the same row.
            result[i] = {**nxt, "type": "choice", "label": None, **_merge_boxes(cur, nxt)}
            del result[i + 1]
        else:
            i += 1

    ordered = sorted((i for i, s in enumerate(result) if s["type"] == "choice"), key=lambda i: result[i]["bbox_x"])
    for position, idx in enumerate(ordered):
        if position < 20:
            result[idx] = {**result[idx], "label": chr(0x2460 + position)}
    return result


def recognize_problem_regions(document, page, problem):
    """Split a problem's region into formula/text segments using the PDF's
    native text layer, classifying by Unicode range (Hangul vs Latin/math).
    Falls back to recognize_scanned_problem_region when the page has no
    extractable text layer at all (e.g. a scanned PDF, where every "page" is
    really just one embedded raster image)."""
    scale = RENDER_DPI / 72
    pdf_path = os.path.join(upload_dir, document.file_path)
    pdf = fitz.open(pdf_path)
    try:
        pdf_page = pdf[page.page_number - 1]
        clip = fitz.Rect(
            problem.x / scale,
            problem.y / scale,
            (problem.x + problem.w) / scale,
            (problem.y + problem.h) / scale,
        )
        chars = collect_ordered_chars(pdf_page, clip)
    finally:
        pdf.close()

    segments = merge_segments(chars, problem, scale)
    if not segments:
        return recognize_scanned_problem_region(problem)

    segments = extract_choice_labels(segments)
    return apply_latex_to_formulas(segments, document, page, problem)


_formula_ocr_model = None


def get_formula_ocr_model():
    """Lazily load the pix2text formula-recognition model (heavy: pulls in
    torch/onnxruntime and downloads weights on first use), cached as a
    module-level singleton so it's only paid for once per process."""
    global _formula_ocr_model
    if _formula_ocr_model is None:
        from pix2text import Pix2Text

        _formula_ocr_model = Pix2Text.from_config()
    return _formula_ocr_model


_text_ocr_reader = None


def get_text_ocr_reader():
    """Lazily load EasyOCR's Korean+English reader, cached the same way as
    the formula model. Downloads its own model weights on first use."""
    global _text_ocr_reader
    if _text_ocr_reader is None:
        import easyocr

        _text_ocr_reader = easyocr.Reader(["ko", "en"], gpu=False)
    return _text_ocr_reader


def recognize_scanned_problem_region(problem):
    """Recognize a problem's region straight from its already-cropped image,
    for when there's no PDF text layer to drive the usual char-classification
    pipeline (a scanned page -- every "page" is really one embedded raster
    image with zero extractable text).

    pix2text's combined text+formula analysis reads the page directly, so
    it doesn't need a text layer either, and it detects layout (where the
    text/formula regions are) reliably. Its formula recognition is likewise
    reliable. But its own text-line reading isn't tuned for Hangul -- it
    handles digits/Latin/symbols fine and mangles Korean words into
    unrelated-looking garbage. So layout and formula content come from
    pix2text; each detected *text* region is re-cropped and re-read with
    EasyOCR instead, which actually reads Korean correctly.

    Circled-numeral choice markers (①②③...) are a further wrinkle: they
    usually aren't in a general OCR model's character set at all, so
    pix2text tends to misread one as some unrelated symbol rather than
    dropping it -- and it's not even a consistent misreading (① and ⑤ can
    both come out as "®"), so it can't be corrected with a lookup table.
    merge_empty_choice_markers/relabel_choice_row recover this positionally
    instead: once at least one marker on a row was read correctly, the rest
    of that row is almost certainly more choices, so they get relabeled
    ①②③... by left-to-right position regardless of what OCR actually saw.
    The label field is still hand-editable per row as a fallback for
    whatever this doesn't catch (e.g. a row with no correct reads at all)."""
    img = Image.open(os.path.join(upload_dir, problem.crop_path)).convert("RGB")

    formula_model = get_formula_ocr_model()
    boxes = formula_model.recognize_text_formula(img, return_text=False)

    text_reader = None
    segments = []
    for box in boxes:
        position = box["position"]
        x0 = int(round(min(p[0] for p in position)))
        y0 = int(round(min(p[1] for p in position)))
        x1 = int(round(max(p[0] for p in position)))
        y1 = int(round(max(p[1] for p in position)))
        if x1 <= x0 or y1 <= y0:
            continue

        if box["type"] in ("isolated", "embedding"):
            content = clean_latex(str(box["text"]).strip())
            seg_type = "formula"
            confidence = float(box["score"])
        else:
            if text_reader is None:
                text_reader = get_text_ocr_reader()
            crop = img.crop((x0, y0, x1, y1))
            reads = text_reader.readtext(np.array(crop))
            content = clean_text(" ".join(t.strip() for _, t, c in reads if t.strip()))
            confidence = (sum(c for _, _, c in reads) / len(reads)) if reads else 0.0
            seg_type = "text"
            if not content.strip():
                # EasyOCR's character set doesn't include circled-numeral
                # choice markers (①②③...), so a box that's mostly/only one
                # comes back empty. pix2text's own guess for it, imperfect
                # as it can be, beats silently dropping the option entirely.
                content = str(box["text"]).strip()
                confidence = float(box["score"])

        if not content.strip():
            continue

        segments.append(
            {
                "type": seg_type,
                "label": None,
                "content": content,
                "bbox_x": x0,
                "bbox_y": y0,
                "bbox_w": x1 - x0,
                "bbox_h": y1 - y0,
                "confidence": round(confidence, 2),
            }
        )

    if not segments:
        return [
            {
                "type": "text",
                "label": None,
                "content": "",
                "bbox_x": 0,
                "bbox_y": 0,
                "bbox_w": problem.w,
                "bbox_h": problem.h,
                "confidence": None,
            },
        ]

    segments = extract_choice_labels(segments)
    segments = merge_empty_choice_markers(segments)
    return relabel_choice_row(segments)


# Tokens pix2text occasionally hallucinates into otherwise-correct output --
# obscure category-theory/model-theory symbols that essentially never
# belong in a K-12/high-school math problem (\models, \sharp, etc.).
# Stripped as a post-process rather than trying to prevent the model from
# emitting them in the first place.
JUNK_LATEX_COMMANDS = [r"\models", r"\bigstar", r"\boxplus", r"\nsubseteq", r"\boldmath", r"\sharp", r"\circ", r"\S", r"\Phi"]
JUNK_LATEX_COMMANDS_WITH_ARG = [r"\mathfrak"]

# A prime mark ("f'(x)") sometimes comes out of the OCR model as a
# superscript-of-a-superscript, e.g. h^{\,^{\prime}} instead of h^{\prime} -
# KaTeX renders the doubly-nested version with the tick floating unusually
# high and small above the letter, which at print size (~10.5pt) reads as a
# stray dot rather than a prime. Flattened to a single-level superscript,
# which renders as a normal prime mark in the usual position.
NESTED_PRIME_RE = re.compile(r"\^\{[^{}]*\^\{(\\prime+)\}[^{}]*\}")

# \stackrel{.}{X} draws a dot above X - a spurious OCR artifact, not
# intentional notation (confirmed against real output: neither "≐" over
# "=" nor a dot over an ordinary variable like an integral bound are
# actually wanted here), so X is unwrapped down to just itself in every
# case.
STRAY_DOT_STACKREL_RE = re.compile(r"\\stackrel\{\.\}\s*\{([^{}]*)\}")


def clean_latex(latex):
    if not latex:
        return latex
    cleaned = latex
    for cmd in JUNK_LATEX_COMMANDS_WITH_ARG:
        cleaned = re.sub(re.escape(cmd) + r"\{[^{}]*\}", "", cleaned)
    for cmd in JUNK_LATEX_COMMANDS:
        cleaned = re.sub(re.escape(cmd) + r"\b", "", cleaned)
    cleaned = NESTED_PRIME_RE.sub(r"^{\1}", cleaned)
    cleaned = STRAY_DOT_STACKREL_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"\{\s*\}", "", cleaned)  # empty groups left behind
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# Closing-side punctuation/brackets that shouldn't have a space before them,
# and opening-side ones that shouldn't have a space after -- covers both
# ASCII and the full-width forms Korean math PDFs commonly use.
_TEXT_CLOSING_PUNCT = r")\]}.,!?:;、。！？：；’”』」〉》"
_TEXT_OPENING_BRACKET = r"(\[{‘“『「〈《"
_TEXT_SPACE_BEFORE_CLOSING_RE = re.compile(r"\s+([" + re.escape(_TEXT_CLOSING_PUNCT) + r"])")
_TEXT_SPACE_AFTER_OPENING_RE = re.compile(r"([" + re.escape(_TEXT_OPENING_BRACKET) + r"])\s+")


def clean_text(text):
    """Normalizes spacing artifacts from the PDF text layer/OCR in a plain
    text segment: collapses any run of 2+ spaces to one, and drops a space
    that lands right before closing punctuation or right after an opening
    bracket (e.g. "값은 ." or "( x"), both of which read as visibly wrong
    even when every word itself came through correctly."""
    if not text:
        return text
    cleaned = re.sub(r"\s+", " ", text)
    cleaned = _TEXT_SPACE_BEFORE_CLOSING_RE.sub(r"\1", cleaned)
    cleaned = _TEXT_SPACE_AFTER_OPENING_RE.sub(r"\1", cleaned)
    return cleaned.strip()


# pix2text's rendering of a circled-numeral choice marker (①②③④⑤). Some
# PDFs' fonts don't expose those as real Unicode codepoints in the text
# layer (mapped instead to garbage like "@"/"®"/stray Katakana), which is
# why choice-splitting can't rely on extract_choice_labels alone for those
# documents -- but the OCR model recognizes the circled *shape* correctly
# regardless of the font, so this catches it after the fact instead.
CHOICE_MARKER_LATEX = r"\oplus"


def split_multi_choice_latex(latex):
    """If clustering merged several multiple-choice options into one OCR
    call (recognizable by 2+ \\oplus markers in the result), split it back
    into one piece per option. Returns None if the pattern doesn't apply,
    so the caller falls back to treating it as a single formula."""
    if latex.count(CHOICE_MARKER_LATEX) < 2:
        return None

    # Split on top-level '&' (cell) and '\\' (row) boundaries of the
    # aligned/array environment pix2text wraps multi-cell output in --
    # "top-level" meaning outside any {...} nesting, so a & or \\ inside a
    # \frac{a}{b} or similar doesn't get mistaken for a real separator.
    pieces = []
    current = []
    depth = 0
    i = 0
    while i < len(latex):
        ch = latex[i]
        if ch == "{":
            depth += 1
            current.append(ch)
            i += 1
        elif ch == "}":
            depth -= 1
            current.append(ch)
            i += 1
        elif depth == 0 and latex[i : i + 2] == "\\\\":
            pieces.append("".join(current))
            current = []
            i += 2
        elif depth == 0 and ch == "&":
            pieces.append("".join(current))
            current = []
            i += 1
        else:
            current.append(ch)
            i += 1
    pieces.append("".join(current))

    choices = []
    for piece in pieces:
        piece = piece.strip()
        if piece.startswith("{") and piece.endswith("}"):
            piece = piece[1:-1].strip()
        if CHOICE_MARKER_LATEX not in piece:
            continue
        cleaned = piece.replace(CHOICE_MARKER_LATEX, "", 1).strip()
        if cleaned:
            choices.append(cleaned)

    return choices if len(choices) >= 2 else None


def crop_region_image(document, page, x, y, w, h, pad=8, dpi=RENDER_DPI):
    """Render a page region (absolute pixel coords, top-left origin) straight
    from the original PDF into a PIL image, for feeding to the formula OCR
    model. Padding gives the model a little breathing room around tight
    text-derived bboxes."""
    scale = RENDER_DPI / 72
    pdf_path = os.path.join(upload_dir, document.file_path)
    pdf = fitz.open(pdf_path)
    try:
        pdf_page = pdf[page.page_number - 1]
        rect = fitz.Rect(
            (x - pad) / scale,
            (y - pad) / scale,
            (x + w + pad) / scale,
            (y + h + pad) / scale,
        )
        pixmap = pdf_page.get_pixmap(clip=rect, dpi=dpi)
    finally:
        pdf.close()
    mode = "RGBA" if pixmap.n >= 4 else "RGB"
    return Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)


def cluster_formula_segments(segments, min_gap=MIN_CLUSTER_GAP):
    """Group formula segments whose bboxes are spatially close (2D) into
    clusters, keyed by index into `segments`. One visual formula (e.g. a
    fraction's numerator and denominator) can land in the PDF's text stream
    non-contiguously -- interrupted by a trailing sentence fragment -- so
    grouping by bbox proximity instead of list order is what actually
    recovers "this is one formula" for cropping purposes. Uses union-find
    since a formula can have more than two disjoint pieces.

    The allowed gap between two fragments scales with the taller of the
    two (roughly one line-height), with min_gap as a floor for small
    fragments. A flat pixel threshold that works for compact text is too
    tight for anything with proportionally larger line spacing -- exactly
    what "the formula is bigger/taller" means in practice."""
    formula_idxs = [i for i, s in enumerate(segments) if s["type"] == "formula"]
    parent = {i: i for i in formula_idxs}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    def boxes_close(a, b):
        ax0, ay0 = a["bbox_x"], a["bbox_y"]
        ax1, ay1 = ax0 + a["bbox_w"], ay0 + a["bbox_h"]
        bx0, by0 = b["bbox_x"], b["bbox_y"]
        bx1, by1 = bx0 + b["bbox_w"], by0 + b["bbox_h"]
        gap = max(min_gap, a["bbox_h"], b["bbox_h"])
        return not (ax1 + gap < bx0 or bx1 + gap < ax0 or ay1 + gap < by0 or by1 + gap < ay0)

    for a in range(len(formula_idxs)):
        for b in range(a + 1, len(formula_idxs)):
            i, j = formula_idxs[a], formula_idxs[b]
            if boxes_close(segments[i], segments[j]):
                union(i, j)

    clusters = {}
    for i in formula_idxs:
        clusters.setdefault(find(i), []).append(i)
    return list(clusters.values())


def apply_latex_to_formulas(segments, document, page, problem):
    """Re-OCR each formula region (clustered by spatial proximity) into
    LaTeX using pix2text, replacing the plain-text content extracted from
    the PDF. Falls back to the plain-text content for a cluster if the OCR
    call itself fails, so one bad crop doesn't take down the whole result."""
    clusters = cluster_formula_segments(segments)
    if not clusters:
        return segments

    model = get_formula_ocr_model()

    replacements = {}
    consumed = set()
    for member_idxs in clusters:
        x0 = min(segments[i]["bbox_x"] for i in member_idxs)
        y0 = min(segments[i]["bbox_y"] for i in member_idxs)
        x1 = max(segments[i]["bbox_x"] + segments[i]["bbox_w"] for i in member_idxs)
        y1 = max(segments[i]["bbox_y"] + segments[i]["bbox_h"] for i in member_idxs)

        # How much of the cluster's bounding box is actually accounted for by
        # extracted-character ink vs bridged empty space. A single-member
        # cluster is 1.0 by construction. A multi-member cluster that had to
        # span a big gap between fragments (the case that made us build this
        # at all: a vector-drawn symbol with zero extractable text sitting
        # between two text-derived fragments) scores low -- that's a real
        # signal the crop region is a guess, worth flagging for review.
        union_area = (x1 - x0) * (y1 - y0)
        covered_area = sum(segments[i]["bbox_w"] * segments[i]["bbox_h"] for i in member_idxs)
        coverage = min(covered_area / union_area, 1.0) if union_area > 0 else 1.0

        anchor = min(member_idxs)
        try:
            img = crop_region_image(document, page, problem.x + x0, problem.y + y0, x1 - x0, y1 - y0)
            latex = clean_latex(model.recognize_formula(img).strip())
        except Exception:
            latex = segments[anchor]["content"]

        bbox = {"bbox_x": x0, "bbox_y": y0, "bbox_w": x1 - x0, "bbox_h": y1 - y0}
        choices = split_multi_choice_latex(latex)
        if choices:
            # All choices share this cluster's one bbox -- there's no way to
            # recover individual positions after the fact, only the combined
            # crop that went into OCR. Adjust region still works per-row to
            # fix that up by hand afterward.
            replacements[anchor] = [
                {
                    "type": "choice",
                    "label": chr(0x2460 + i) if i < 20 else None,
                    "content": choice_latex,
                    "confidence": round(coverage, 2),
                    **bbox,
                }
                for i, choice_latex in enumerate(choices)
            ]
        else:
            replacements[anchor] = [
                {
                    "type": "formula",
                    "label": None,
                    "content": latex,
                    "confidence": round(coverage, 2),
                    **bbox,
                }
            ]
        consumed.update(member_idxs)

    result = []
    for i, seg in enumerate(segments):
        if i in replacements:
            result.extend(replacements[i])
        elif i in consumed:
            continue
        else:
            result.append(seg)
    return result


def _content_from_segment(problem_id, segment, parent_content_id, order_index):
    # Every segment passes through here regardless of which detection path
    # produced it (PDF text layer, scanned-page OCR, choice-label peeling,
    # relabel_choice_row's recovery cases, ...) -- the one guaranteed place
    # to enforce "no leading/trailing whitespace on a stored chunk" without
    # auditing every upstream function for it individually.
    content = (segment["content"] or "").strip()
    return ProblemContent(
        problem_id=problem_id,
        parent_content_id=parent_content_id,
        order_index=order_index,
        type=segment["type"],
        label=segment.get("label"),
        content=content,
        bbox_x=segment["bbox_x"],
        bbox_y=segment["bbox_y"],
        bbox_w=segment["bbox_w"],
        bbox_h=segment["bbox_h"],
        confidence=segment["confidence"],
        # Formulas default to inline (unchecked "Full line") regardless of
        # length/complexity - a reviewer opts a specific formula into its
        # own full-width line by hand rather than it being pre-selected.
        display_mode=False,
    )


def recognize_and_save_problem(document, page, problem):
    """Run recognition for one problem and replace its problem_contents rows.
    Does not commit; caller controls the transaction."""
    segments = recognize_problem_regions(document, page, problem)

    ProblemContent.query.filter_by(problem_id=problem.id).delete()
    contents = []
    top_level_index = 0
    i = 0
    while i < len(segments):
        run_end = i
        if segments[i]["type"] == "choice":
            while run_end < len(segments) and segments[run_end]["type"] == "choice":
                run_end += 1

        # A run of 2+ consecutive choices reads as one multiple-choice set,
        # not N unrelated rows -- bundle them into a group automatically,
        # the same way a reviewer would do by hand with "Group selected".
        # Applies regardless of which path found the choices (extract_
        # choice_labels peeling them upfront, or split_multi_choice_latex
        # splitting a merged OCR result apart after the fact). A lone
        # choice isn't a "set" of anything, so it's left ungrouped.
        if run_end - i >= 2:
            group = ProblemContent(
                problem_id=problem.id,
                parent_content_id=None,
                order_index=top_level_index,
                type="group",
                label="Choices",
            )
            db.session.add(group)
            db.session.flush()  # need group.id before children can reference it
            contents.append(group)
            top_level_index += 1

            child_segments = segments[i:run_end]
            for child_index, seg in enumerate(child_segments):
                content = _content_from_segment(problem.id, seg, group.id, child_index)
                db.session.add(content)
                contents.append(content)

            # A multiple-choice set always has exactly 5 options -- if OCR
            # only picked up some of them (label misreads and low-confidence
            # single-jamo values like ㄱ/ㄴ/ㄷ are common failure modes here),
            # fill in the rest as empty placeholders so the reviewer starts
            # from a complete ①-⑤ set instead of having to notice a gap and
            # add the missing one by hand.
            detected_labels = {seg.get("label") for seg in child_segments}
            next_index = len(child_segments)
            for label in STANDARD_CHOICE_LABELS:
                if label in detected_labels:
                    continue
                placeholder = ProblemContent(
                    problem_id=problem.id,
                    parent_content_id=group.id,
                    order_index=next_index,
                    type="choice",
                    label=label,
                    content="",
                    bbox_x=0,
                    bbox_y=0,
                    bbox_w=min(60, problem.w),
                    bbox_h=min(30, problem.h),
                    confidence=None,
                )
                db.session.add(placeholder)
                contents.append(placeholder)
                next_index += 1

            i = run_end
            continue

        content = _content_from_segment(problem.id, segments[i], None, top_level_index)
        db.session.add(content)
        contents.append(content)
        top_level_index += 1
        i += 1

    problem.status = "recognized"

    # Snapshot the very first successful recognition (only) so a reviewer
    # can later restore to it directly, without re-running OCR - see
    # revert_problem_to_initial. Needs each row's id resolved (for the
    # parent/child index mapping below), hence the flush before reading it.
    if not problem.initial_content_snapshot:
        db.session.flush()
        problem.initial_content_snapshot = json.dumps(_snapshot_problem_contents(contents))

    return contents


def _snapshot_problem_contents(contents):
    """Serializes content rows for initial_content_snapshot. Parent/child
    links are encoded as indices into this same list, not DB ids - a
    restore creates fresh rows with fresh ids, so the original ids
    wouldn't resolve to anything by then."""
    index_by_id = {c.id: i for i, c in enumerate(contents)}
    return [
        {
            "order_index": c.order_index,
            "type": c.type,
            "label": c.label,
            "content": c.content,
            "parent_index": index_by_id.get(c.parent_content_id),
            "bbox_x": c.bbox_x,
            "bbox_y": c.bbox_y,
            "bbox_w": c.bbox_w,
            "bbox_h": c.bbox_h,
            "confidence": c.confidence,
            "crop_path": c.crop_path,
            "display_mode": bool(c.display_mode),
        }
        for c in contents
    ]


@app.route("/api/problems/<uuid:problem_id>/recognize", methods=["POST"])
def recognize_problem(problem_id):
    """Re-runs OCR from the problem's saved crop and replaces its content
    rows outright, discarding any reviewer edits since the original
    recognition - the "reset to initial OCR" escape hatch. Safe to call more
    than once: recognition has no randomness (greedy decoding, same crop
    image in, same content out), so this reproduces the original result
    rather than a fresh guess."""
    problem = Problem.query.get_or_404(problem_id)
    if not problem.crop_path:
        return jsonify({"error": "problem has no cropped image; re-save problem selection first"}), 400

    page = Page.query.get(problem.page_id)
    document = Document.query.get(page.document_id)
    contents = recognize_and_save_problem(document, page, problem)
    db.session.commit()

    return jsonify({"contents": [problem_content_to_dict(c) for c in contents], "status": problem.status})


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


def run_region_ocr_in_background(content_id, document_id, page_id, crop_x, crop_y, w, h):
    """Companion to update_content_region's formula/choice path: the model
    inference itself happens here, off the request thread, so the frontend
    isn't stuck waiting tens of seconds on it -- see the comment there."""
    with app.app_context():
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
        finally:
            db.session.remove()


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
    thread_args = (content.id, document.id, page.id, problem.x + x, problem.y + y, w, h)
    # Commit before starting the thread, not after -- it opens its own
    # session (see run_region_ocr_in_background's app_context), which won't
    # reliably see this row's processing=True until this transaction lands.
    db.session.commit()
    threading.Thread(target=run_region_ocr_in_background, args=thread_args, daemon=True).start()
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
    app.run(debug=True, threaded=True)
