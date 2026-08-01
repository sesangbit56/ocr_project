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
from werkzeug.utils import secure_filename

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
    return {
        "id": str(document.id),
        "filename": document.filename,
        "total_pages": document.total_pages,
        "status": document.status,
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


def page_to_dict(page):
    return {
        "id": str(page.id),
        "page_number": page.page_number,
        "image_url": f"/api/files/{page.image_path}",
        "width": page.width,
        "height": page.height,
        "status": page.status,
    }


@app.route("/api/documents", methods=["GET"])
def list_documents():
    documents = Document.query.order_by(Document.created_at.desc()).all()
    return jsonify([document_to_dict(d) for d in documents])


@app.route("/api/documents", methods=["POST"])
def upload_document():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file is required"}), 400

    filename = secure_filename(file.filename)
    document = Document(filename=filename, file_path="", status="uploaded")
    db.session.add(document)
    db.session.flush()

    doc_dir = os.path.join(upload_dir, str(document.id))
    os.makedirs(doc_dir, exist_ok=True)

    pdf_path = os.path.join(doc_dir, "original.pdf")
    file.save(pdf_path)
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
    return jsonify(document_to_dict(document)), 201


@app.route("/api/documents/<uuid:document_id>", methods=["GET"])
def get_document(document_id):
    document = Document.query.get_or_404(document_id)
    pages = (
        Page.query.filter_by(document_id=document_id)
        .order_by(Page.page_number)
        .all()
    )
    data = document_to_dict(document)
    data["pages"] = [page_to_dict(p) for p in pages]
    return jsonify(data)


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
    }


def problem_to_dict(problem, contents):
    return {
        "id": str(problem.id),
        "order_index": problem.order_index,
        "status": problem.status,
        "bbox": {"x": problem.x, "y": problem.y, "w": problem.w, "h": problem.h},
        "contents": [problem_content_to_dict(c) for c in contents],
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
        results.append(
            {
                "type": seg["type"],
                "label": None,
                "content": "".join(seg["texts"]).strip(),
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

        digit_match = re.match(r"^\D*(\d.*)$", cur["content"].strip())
        if digit_match:
            # Marker and value already fused in one box (e.g. "@3" ~ "②3");
            # keep only the value, drop the unrecognizable marker prefix.
            result[i] = {**cur, "type": "choice", "label": None, "content": digit_match.group(1)}
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
            content = " ".join(t.strip() for _, t, c in reads if t.strip())
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


def clean_latex(latex):
    if not latex:
        return latex
    cleaned = latex
    for cmd in JUNK_LATEX_COMMANDS_WITH_ARG:
        cleaned = re.sub(re.escape(cmd) + r"\{[^{}]*\}", "", cleaned)
    for cmd in JUNK_LATEX_COMMANDS:
        cleaned = re.sub(re.escape(cmd) + r"\b", "", cleaned)
    cleaned = re.sub(r"\{\s*\}", "", cleaned)  # empty groups left behind
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


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
    return ProblemContent(
        problem_id=problem_id,
        parent_content_id=parent_content_id,
        order_index=order_index,
        type=segment["type"],
        label=segment.get("label"),
        content=segment["content"],
        bbox_x=segment["bbox_x"],
        bbox_y=segment["bbox_y"],
        bbox_w=segment["bbox_w"],
        bbox_h=segment["bbox_h"],
        confidence=segment["confidence"],
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

            for child_index, seg in enumerate(segments[i:run_end]):
                content = _content_from_segment(problem.id, seg, group.id, child_index)
                db.session.add(content)
                contents.append(content)
            i = run_end
            continue

        content = _content_from_segment(problem.id, segments[i], None, top_level_index)
        db.session.add(content)
        contents.append(content)
        top_level_index += 1
        i += 1

    problem.status = "recognized"
    return contents


@app.route("/api/problems/<uuid:problem_id>/recognize", methods=["POST"])
def recognize_problem(problem_id):
    problem = Problem.query.get_or_404(problem_id)
    if not problem.crop_path:
        return jsonify({"error": "problem has no cropped image; re-save problem selection first"}), 400

    page = Page.query.get(problem.page_id)
    document = Document.query.get(page.document_id)
    contents = recognize_and_save_problem(document, page, problem)
    db.session.commit()

    return jsonify([problem_content_to_dict(c) for c in contents])


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

    sibling_count = ProblemContent.query.filter_by(problem_id=problem_id, parent_content_id=parent_content_id).count()
    content = ProblemContent(
        problem_id=problem_id,
        parent_content_id=parent_content_id,
        order_index=sibling_count,
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


@app.route("/api/problem_contents/<uuid:content_id>/region", methods=["POST"])
def update_content_region(content_id):
    """Manually override a content block's region. For a formula or choice,
    re-runs LaTeX OCR on the new crop -- the safety net for cases automatic
    clustering can't get right (e.g. a symbol drawn as vector graphics with
    no extractable text, so there's nothing in the PDF's text layer to tell
    clustering where its bounding box even is). For an image, just saves
    the crop -- a diagram/graph isn't run through any recognition, it's
    shown as-is -- and deletes whatever other content rows the new region
    substantially covers: a diagram's own axis labels/coordinates otherwise
    tend to get picked up separately as stray text/formula fragments
    duplicating what the image crop already shows."""
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

    if content.type == "image":
        content.crop_path = crop_content_image(document, page, content, problem.x + x, problem.y + y, w, h)
    else:
        model = get_formula_ocr_model()
        img = crop_region_image(document, page, problem.x + x, problem.y + y, w, h)
        content.content = clean_latex(model.recognize_formula(img).strip())

    content.bbox_x = x
    content.bbox_y = y
    content.bbox_w = w
    content.bbox_h = h
    content.confidence = 1.0
    db.session.flush()

    if content.type == "image":
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

    remaining = ProblemContent.query.filter_by(problem_id=problem.id).order_by(ProblemContent.order_index).all()
    db.session.commit()
    return jsonify(problem_to_dict(problem, remaining))


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


@app.route("/api/problems/<uuid:problem_id>/contents/group", methods=["POST"])
def group_problem_contents(problem_id):
    """Bundle a set of top-level content rows under a new type="group" row
    (e.g. a <보기> box) with a chosen label. Not auto-detected -- the current
    pipeline reads only the PDF's text layer, which has no signal for a box
    border, so this is how a reviewer marks one by hand."""
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

    for index, row in enumerate(ordered_rows):
        row.parent_content_id = group.id
        row.order_index = index
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

    problem_id = group.problem_id
    _ungroup(group)
    db.session.delete(group)
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
