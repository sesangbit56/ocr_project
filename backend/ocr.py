# OCR 인식 파이프라인: 페이지 위의 한 문제 영역을 구조화된 텍스트/수식/선택지
# 콘텐츠로 변환한다. 여기 있는 코드는 전부 순수 인식 로직(PDF 텍스트 레이어
# 분류, pix2text/EasyOCR 호출, LaTeX/텍스트 정리, 선택지 마커 복구)과 그 인식
# 결과를 ProblemContent 행으로 저장하는 진입점뿐이다. Flask 라우트와, 언제
# 인식을 실행할지 스케줄링하는 OCR 작업 큐로부터 분리해서, 인식 파이프라인만
# 따로 읽고/디버그하고/테스트할 수 있도록 app.py에서 떼어냈다.

import json
import os
import re
import shutil
import threading
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageDraw

from models import ProblemContent, db

base_dir = os.path.abspath(os.path.dirname(__file__))
upload_dir = os.path.join(base_dir, "uploads")

RENDER_DPI = 150

# 한글 문자 범위 - 선택지 값이 진짜 한글 내용인지 판단하는 데 쓴다
# (relabel_choice_row), 표 셀이 한글 위주인지 보고 EasyOCR로 보낼지
# 정할 때도 쓴다(_recognize_table_cell).
HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")

# 객관식 선택지("① 1", "② -2x+3")는 원문자(circled numeral)로 시작한다.
# 여기서 미리 떼어내면 LaTeX-OCR 클러스터링 단계에 아예 안 들어가게
# 된다 - PDF 텍스트 레이어가 이미 값을 정확히 뽑아내므로, pix2text가
# 여기 보탤 게 없고 오히려 환각(hallucination) 위험만 초래한다.
CHOICE_LABEL_RE = re.compile(r"^([①-⑳])\s*(.*)$")

# 표준 객관식 세트는 항상 정확히 이 5개 선택지로 구성된다 - 인식된 선택지
# 그룹에 일부가 빠져 있을 때(recognize_and_save_problem 참고) 검토자가
# 빈틈을 알아채고 손으로 추가하게 두는 대신, 여기서 바로 채워 넣는 데 쓴다.
STANDARD_CHOICE_LABELS = ["①", "②", "③", "④", "⑤"]


def extract_choice_labels(segments):
    # formula 또는 text 세그먼트 앞에 붙은 원문자 하나를 떼어내서 자기만의
    # label로 만들고, 타입을 "choice"로 바꾼다. formula/text 두 타입을 다
    # 검사하는 이유는, pix2text의 박스 분류("isolated"/"embedding" ->
    # formula, 그 외 -> text)가 원문자를 어느 쪽으로 볼지 상황에 따라
    # 갈리기 때문이다.
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
    # 두 세그먼트의 세로 범위가 둘 중 짧은 쪽 높이의 절반보다 많이
    # 겹치면 같은 시각적 행에 있는 것으로 본다.
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


def _reading_order(segments):
    # 세그먼트들을 위->아래, 왼쪽->오른쪽 읽기 순서로 정렬한다. pix2text의
    # 레이아웃 인식은 이 순서를 보장해주지 않는데, merge_empty_choice_markers/
    # relabel_choice_row는 리스트에서 인접한 항목이 같은 시각적 행에 있다고
    # 가정하고 동작한다 - 이 정렬이 없으면 마커 박스가 전혀 무관한 행의
    # 값과 잘못 짝지어질 수 있다(예: 선택지 하나가 문제 본문 자신의
    # 수식을 자기 값인 것처럼 흡수해버리는 경우).
    ordered = sorted(segments, key=lambda s: s["bbox_y"])
    rows = []
    for seg in ordered:
        if rows and _rows_overlap(rows[-1][-1], seg):
            rows[-1].append(seg)
        else:
            rows.append([seg])
    result = []
    for row in rows:
        result.extend(sorted(row, key=lambda s: s["bbox_x"]))
    return result


def merge_empty_choice_markers(segments):
    # pix2text는 가끔 원문자 마커를 그 값과 별개의 박스로 따로 검출한다
    # (예: "③"만 단독으로, "3√3"은 무관해 보이는 다음 formula 박스로).
    # extract_choice_labels는 마커 자체는 이미 인식하지만(label="③",
    # 그 박스 안에 다른 게 없으니 content=""), 값은 다음 세그먼트로
    # 따로 남겨둔 채로 둔다 - 내용이 없는 choice를 그대로 하나 남겨두고
    # 다른 하나를 고아로 만드는 대신, 바로 다음에 오는 같은 행의
    # 세그먼트와 합쳐준다.
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
    # 한 행에 이미 확정된 choice가 하나라도 있다면(원문자 OCR이 우연히
    # 제대로 읽힌 경우), 그 행의 나머지도 마커 인식이 잘못됐을 뿐 거의
    # 확실히 선택지다 - pix2text는 ①/⑤를 가리지 않고 둘 다 "®"로
    # 잘못 읽는 식이라, 같은 오독 글리프를 어느 숫자였는지로 되돌려
    # 매핑할 수조차 없다. 이건 손대도 안전한 행이다 - 이미 "이 행은
    # 선택지 행이다"라는 적극적인 증거를 하나 확보했기 때문이고,
    # 페이지 어디서든 "짧은 텍스트 다음에 수식이 온다" 같은 막연한
    # 모양만 보고 추측하는 것과는 다르다. 이런 것들도 choice로
    # 흡수한다(마커+값이 한 박스에 같이 있는 경우, 예: "@3", 또는 위
    # merge_empty_choice_markers의 빈 "③" 사례처럼 다음 세그먼트가
    # 값이 되어야 하는 맨 마커만 있는 경우). 그리고 행에 있는 모든
    # choice를 왼쪽->오른쪽 위치 기준으로 ①②③...으로 다시 번호를
    # 매겨서, 깨진 마커 글리프가 저장된 label까지 살아남는 일이 없게
    # 한다.
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
            # 마커와 값이 이미 한 박스에 붙어서 나온 경우(예: "@3" ~
            # "②3") - 값만 남기고 인식 불가능한 마커 접두부는 버린다.
            result[i] = {**cur, "type": "choice", "label": None, "content": digit_match.group(1)}
            i += 1
        elif HANGUL_RE.search(stripped):
            # 이 박스 자체의 마커 글리프는 잘못 읽혔을 수 있지만, 그
            # 내용물은 진짜 한글이다(예: "ㄱ" - <보기> 항목을 답 값
            # 자체로 참조하는 경우이지 수식이 아니다) - pix2text가 잘못
            # 읽은 기호에서 있지도 않은 한글을 환각으로 만들어내지는
            # 않으므로, 이건 진짜 내용이다. 행에서 다음에 오는 것을
            # 대신 쓰는 대신, 이걸 그대로 choice의 값으로 유지한다.
            result[i] = {**cur, "type": "choice", "label": None, "content": stripped}
            i += 1
        elif _rows_overlap(cur, nxt):
            # 박스 안에 다른 내용 없이 맨 마커 글리프만 있는 경우 -
            # 값은 같은 행의 다음 세그먼트다.
            result[i] = {**nxt, "type": "choice", "label": None, **_merge_boxes(cur, nxt)}
            del result[i + 1]
        else:
            i += 1

    ordered = sorted((i for i, s in enumerate(result) if s["type"] == "choice"), key=lambda i: result[i]["bbox_x"])
    for position, idx in enumerate(ordered):
        if position < 20:
            result[idx] = {**result[idx], "label": chr(0x2460 + position)}
    return result


def _get_quantized_mfr_dir():
    # MFR(수식->LaTeX) 모델의 INT8 양자화 사본을 준비해서 그 디렉터리
    # 경로를 반환한다. 이 파이프라인에서 가장 오래 걸리는 단계가 MFR인데
    # (박스 하나당 CPU에서 수 초), 실측해보니 같은 가중치를 정밀도만
    # 낮춘 것이라 출력은 사실상 그대로면서(실제 조판된 수식 10개로
    # 테스트 - 9개 완전 일치, 나머지 1개도 렌더링 결과가 같은 불필요한
    # 중괄호 차이뿐) CPU 추론 속도는 1.3~1.7배 빨라진다. 이미 다른
    # 프로세스가 만들어둔 캐시가 있으면 그걸 재사용하고, 없으면
    # pix2text가 원본 FP32 모델을 받아두는 자리(못 받아뒀으면 그
    # 다운로드까지) 그대로 이용해서 딱 한 번만 양자화한다.
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from pix2text.consts import MODEL_VERSION
    from pix2text.latex_ocr import AVAILABLE_MODELS
    from pix2text.utils import data_dir, prepare_model_files2

    model_root = Path(data_dir()) / MODEL_VERSION
    src_dir = model_root / "mfr-1.5-onnx"
    int8_dir = model_root / "mfr-1.5-onnx-int8"
    onnx_names = ["encoder_model.onnx", "decoder_model.onnx"]

    if int8_dir.is_dir() and all((int8_dir / name).is_file() for name in onnx_names):
        return str(int8_dir)

    if not (src_dir.is_dir() and list(src_dir.glob("**/[!.]*"))):
        model_info = AVAILABLE_MODELS.get_info("mfr-1.5", "onnx")
        prepare_model_files2(model_fp_or_dir=src_dir, remote_repo=model_info["hf_model_id"], file_or_dir="dir")

    int8_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        if item.is_file() and item.name not in onnx_names:
            shutil.copy(item, int8_dir / item.name)
    for name in onnx_names:
        quantize_dynamic(model_input=str(src_dir / name), model_output=str(int8_dir / name), weight_type=QuantType.QInt8)

    return str(int8_dir)


_formula_ocr_model = None
_formula_ocr_model_lock = threading.Lock()


def get_formula_ocr_model():
    # pix2text 모델을 지연 로딩한다(무거움: torch/onnxruntime을 끌고
    # 들어오고 최초 사용 시 가중치를 다운로드한다). 프로세스당 한 번만
    # 비용을 치르도록 모듈 수준 싱글턴으로 캐시해둔다. 이름은
    # "formula"지만 실제로는 pix2text의 범용 객체라, 수식 하나만 읽는
    # recognize_formula뿐 아니라 페이지 레이아웃 전체를 분석하는
    # recognize_page도 이 같은 객체에서 호출한다(analyze_problem_layout
    # 참고) - 모델을 새로 로드할 필요가 없으므로 그대로 재사용한다.
    # MFR(수식 인식) 서브모델만 INT8 양자화 사본으로 바꿔서 로드한다 -
    # 레이아웃/수식 위치 검출/표 구조는 원래 모델 그대로다.
    #
    # 잠금이 필요한 이유: 서버가 막 켜진 직후에는 백그라운드 OCR
    # 워커(밀린 작업을 이어서 처리)와 방금 들어온 요청이 동시에 이
    # 함수를 처음 호출할 수 있다. 실제로 그 경합 상황에서 torch가
    # "meta tensor" 오류를 내며 모델 로딩 자체가 깨지는 걸 확인했다 -
    # 잠금 없는 "None이면 로드" 검사만으로는 두 스레드가 동시에
    # Pix2Text.from_config()를 부르는 걸 못 막는다.
    global _formula_ocr_model
    if _formula_ocr_model is None:
        with _formula_ocr_model_lock:
            if _formula_ocr_model is None:
                from pix2text import Pix2Text

                mfr_dir = _get_quantized_mfr_dir()
                _formula_ocr_model = Pix2Text.from_config(
                    total_configs={"text_formula": {"formula": {"model_dir": mfr_dir}}}
                )
    return _formula_ocr_model


_text_ocr_reader = None
_text_ocr_reader_lock = threading.Lock()


def get_text_ocr_reader():
    # EasyOCR의 한국어+영어 리더를 지연 로딩한다. formula 모델과 같은
    # 이유로 같은 방식(잠금 포함)으로 캐시해둔다. 이것도 최초 사용 시
    # 자체 모델 가중치를 다운로드한다.
    global _text_ocr_reader
    if _text_ocr_reader is None:
        with _text_ocr_reader_lock:
            if _text_ocr_reader is None:
                import easyocr

                _text_ocr_reader = easyocr.Reader(["ko", "en"], gpu=False)
    return _text_ocr_reader


def analyze_problem_layout(problem):
    # 문제 크롭 이미지에 pix2text의 전체 페이지 레이아웃 분석기
    # (recognize_page)를 돌려서, 텍스트/수식/그림/표로 타입이 붙은
    # 하위 영역 목록을 얻는다. 이게 이제 인식의 첫 단계다.
    #
    # 예전에는 "이 문제 영역에 추출 가능한 PDF 텍스트 레이어가 있는가"로
    # 두 경로(recognize_problem_regions vs recognize_scanned_problem_region)
    # 중 하나를 골랐는데, 실제 문서로 검증해보니 이 구분 자체가 틀렸다:
    # 이 앱이 실제로 다루는 PDF 중에는 "텍스트 레이어가 있긴 한데, 그
    # 문자 값 자체가 이미 어떤 외부 도구가 이 스캔본 위에 미리 심어놓은
    # 부정확한 OCR 결과인" 경우가 있었다(실제 사례: 원문자 선택지
    # 마커 ①~⑤이 전부 "CD", "@", "(5)" 같은 엉뚱한 글자로 박혀있었고,
    # get_drawings()/get_images()로 확인해보니 페이지 자체가 벡터 도형
    # 하나 없이 통째로 JPEG 한 장이었다). 이런 문서는 "텍스트 레이어
    # 유무"만으로는 진짜 스캔 문서와 구분이 안 된다.
    #
    # 그래서 레이아웃은 텍스트 레이어 유무와 무관하게 항상 렌더링된
    # 픽셀에서 직접 뽑는다. PDF의 텍스트 레이어는 이제 아예 쓰지 않는다
    # - recognize_problem_regions가 여기서 나온 표/그림 영역만 구조화해서
    # 뽑고, 나머지 전부는 _recognize_pixel_region으로 문제 전체를
    # 통짜로 다시 읽는다(실측 비교 결과, 문자 단위 분류보다 이쪽이
    # 본문/선지 인식률이 뚜렷하게 높았다).
    #
    # ElementType.TITLE/ABANDONED/IGNORED/UNKNOWN(페이지 헤더, "5지선다형"
    # 같은 섹션 라벨, "확인 사항" 안내 박스, 페이지 번호 등 문제 내용이
    # 아닌 부속물)은 제외한다.
    from pix2text.page_elements import ElementType

    img = Image.open(os.path.join(upload_dir, problem.crop_path)).convert("RGB")
    model = get_formula_ocr_model()
    layout_page = model.recognize_page(img)

    ignored_types = {ElementType.TITLE, ElementType.ABANDONED, ElementType.IGNORED, ElementType.UNKNOWN}
    kind_by_type = {
        ElementType.FIGURE: "figure",
        ElementType.TABLE: "table",
        ElementType.FORMULA: "formula",
    }

    elements = [el for el in layout_page.elements if el.type not in ignored_types]
    elements.sort()  # Element.__lt__가 다열(column) 인식 읽기 순서를 이미 구현하고 있다

    regions = []
    for el in elements:
        x0, y0, x1, y1 = (int(round(v)) for v in el.box)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        # TEXT가 기본값 - 레이아웃 분석기가 놓친/애매한 타입도 일단
        # 평문 취급해서 문자 분류 파이프라인을 태우는 쪽이, 조용히
        # 버리는 것보다 안전하다.
        regions.append({"kind": kind_by_type.get(el.type, "text"), "x": x0, "y": y0, "w": w, "h": h, "element": el})
    return img, regions


def _recognize_pixel_region(img):
    # 크롭 이미지 하나를 PDF 텍스트 레이어를 전혀 참고하지 않고 픽셀만
    # 보고 읽는다 - pix2text(레이아웃 세부 줄 검출 + 수식)와
    # EasyOCR(한글) 조합. 텍스트 레이어가 아예 없을 때(진짜 스캔 문서)와,
    # 있어도 이 영역에서는 못 믿을 때(예: 원문자 선택지 마커가 죄다
    # 엉뚱한 글자로 박혀 있던 경우) 둘 다에 쓰이는 공용 leaf 함수다.
    #
    # pix2text 자체의 텍스트 줄 인식은 한글에 맞춰져 있지 않다 -
    # 숫자/영문/기호는 잘 처리하지만 한글 단어는 무관해 보이는
    # 문자열로 뭉개버린다. 그래서 수식 콘텐츠는 pix2text에서 가져오고,
    # 검출된 각 *텍스트* 영역은 다시 크롭해서 EasyOCR로 다시 읽는다 -
    # 이쪽이 한글을 실제로 제대로 읽는다.
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
                # EasyOCR의 문자 집합에는 원문자 선택지 마커(①②③...)가
                # 없어서, 박스 내용이 거의/전부 그것뿐이면 빈 값으로
                # 돌아온다. 이럴 땐 pix2text 자체의 (불완전할지언정)
                # 추측값이라도 쓰는 게, 선택지를 통째로 조용히
                # 버리는 것보다 낫다.
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
    return segments


def recognize_scanned_problem_region(problem):
    # analyze_problem_layout이 이 문제 영역에서 아무 것도 못 찾았을 때
    # 쓰는 마지막 안전망 - 문제 전체를 통째로 _recognize_pixel_region에
    # 넘긴다. 원문자 선택지 마커 오독은 여기서도
    # merge_empty_choice_markers/relabel_choice_row로 위치 기반 복구한다.
    img = Image.open(os.path.join(upload_dir, problem.crop_path)).convert("RGB")
    segments = _recognize_pixel_region(img)

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

    segments = _reading_order(segments)
    segments = extract_choice_labels(segments)
    segments = expand_choice_glob_segments(segments)
    segments = merge_empty_choice_markers(segments)
    return relabel_choice_row(segments)


def _extract_figure_region(x0, y0, w, h):
    # 그림/도형 영역은 OCR을 아예 하지 않는다 - 지금까지 검토자가 수동
    # "+ Image" 블록으로 만들던 것과 결과물은 동일하고, 자동으로
    # 생성된다는 점만 다르다. 실제 크롭 파일 저장은 이 시점엔 아직
    # ProblemContent 행의 id가 없어서 못 하고, recognize_and_save_problem이
    # 행을 만들고 flush한 뒤에 처리한다.
    return [{"type": "image", "label": None, "content": "", "bbox_x": x0, "bbox_y": y0, "bbox_w": w, "bbox_h": h, "confidence": 1.0}]


# 레이아웃 분석이 FIGURE로 분류한 영역이라도, 실제로는 선지 다섯 개가
# 가로로 빽빽하게 늘어선 줄("① 3/2 ② 5/2 ...")인 경우가 실측으로 두
# 건 확인됐다 - 진짜 도형(삼각형, 그래프 등)과 달리 선지 행은 항상
# 한 줄 높이 정도로 짧고, 문제 폭 대부분을 가로로 채운다. 처음엔
# "다시 스캔해서 텍스트/수식으로 덮이는 비율"로 판단하려 했는데,
# 실측해보니 오히려 거꾸로였다 - 진짜 도형도 삼각형/곡선의 잉크
# 전체를 감싸는 박스 하나로 인식돼서 커버리지가 더 높게 나왔다. 그래서
# 모양(짧고 넓적함)만으로 먼저 걸러낸다.
FIGURE_ROW_MAX_HEIGHT = 70
FIGURE_ROW_MIN_ASPECT = 4


def _figure_region_is_actually_text(problem_img, x0, y0, w, h):
    # None을 반환하면 진짜 그림이라는 뜻(호출한 쪽이 기존처럼 이미지
    # 블록으로 처리한다). 텍스트로 뒤집힌 경우엔 좌표를 문제 전체
    # 기준으로 되돌린 세그먼트 목록을 반환하고, 각 세그먼트에
    # "_from_figure_row" 표시를 남긴다 - 이 표시는 아래
    # _rescue_unclaimed_figure_rows에서, 원문자 마커가 엉뚱한 글자로
    # 읽혀서 relabel_choice_row가 선지로 인정해 줄 발판(anchor)조차
    # 못 찾은 경우를 구제하는 데 쓰고, 저장 직전에 지운다.
    if h > FIGURE_ROW_MAX_HEIGHT or w < FIGURE_ROW_MIN_ASPECT * h:
        return None

    # 세로로 여백을 좀 둘러서 다시 스캔한다 - 원본 높이(27px 사례로
    # 확인) 그대로 넘기면 검출 모델이 너무 얇다고 보고 아예 박스를
    # 하나도 못 찾는 경우가 실제로 있었다. 흰 여백을 둘러주면 같은
    # 줄이 정상적으로 검출된다.
    pad = 20
    crop = problem_img.crop((x0, y0, x0 + w, y0 + h))
    padded = Image.new("RGB", (w, h + pad * 2), "white")
    padded.paste(crop, (0, pad))

    segments = _recognize_pixel_region(padded)
    if not segments:
        return None
    for seg in segments:
        seg["bbox_x"] += x0
        seg["bbox_y"] += y0 - pad
        seg["_from_figure_row"] = True
    return segments


# 선택지 다섯 개가 한 줄에 촘촘히 붙어 있는 영역만 좁게 크롭해서 다시
# 읽으면(위 _figure_region_is_actually_text가 그림 오판을 뒤집은
# 직후처럼), pix2text가 이걸 낱개 박스로 안 쪼개고 "\oplus {3/2}
# \qquad\oplus {5/2} \qquad..." 식으로 수식 하나에 다 욱여넣는 걸
# 실측으로 확인했다(원문자 마커는 뜻과 무관한 \oplus 등으로 오독됨).
# \qquad로 확실히 갈라져 있고 조각마다 순수 명령어 마커로 시작하는
# 모양일 때만 각 조각을 choice 세그먼트로 되돌린다 - 마커 글리프
# 자체는 못 믿으므로 라벨은 비워두고, choice 타입으로 바로 만들어두면
# relabel_choice_row가 x좌표 순으로 ①②③④⑤를 다시 매겨준다.
_CHOICE_GLOB_ITEM_RE = re.compile(r"^\\[a-zA-Z]+\s*(?:\\[,;:]|\\\s)*\s*(.*)$", re.DOTALL)


def expand_choice_glob_segments(segments):
    result = []
    for seg in segments:
        if seg["type"] != "formula":
            result.append(seg)
            continue
        pieces = [p.strip() for p in seg["content"].split(r"\qquad") if p.strip()]
        if len(pieces) < 3:
            result.append(seg)
            continue
        items = [_CHOICE_GLOB_ITEM_RE.match(p) for p in pieces]
        if not all(items):
            result.append(seg)
            continue
        slot_w = seg["bbox_w"] / len(pieces)
        for idx, m in enumerate(items):
            result.append(
                {
                    "type": "choice",
                    "label": None,
                    "content": clean_latex(m.group(1)),
                    "bbox_x": seg["bbox_x"] + round(idx * slot_w),
                    "bbox_y": seg["bbox_y"],
                    "bbox_w": round(slot_w),
                    "bbox_h": seg["bbox_h"],
                    "confidence": seg["confidence"],
                }
            )
    return result


def _rescue_unclaimed_figure_rows(segments):
    # relabel_choice_row는 "이미 제대로 읽힌 choice가 문제 어딘가에
    # 하나라도 있다"는 발판(anchor)이 있어야 나머지를 선지로 인정해
    # 준다. 그런데 그림으로 오판됐다가 되돌아온 선지 행은 원문자
    # 마커가 "0", "@" 같은 전혀 무관한 글자로 읽히는 경우가 흔해서,
    # 문제 전체에 그 발판이 하나도 없을 수 있다(실측 사례: "0 117",
    # "121", "125", ... - 마커를 알아볼 방법이 없다). 하지만 이
    # 조각들은 애초에 "그림으로 오판될 만큼 짧고 넓적한 영역" 하나에서만
    # 나왔다는 맥락 자체가 이미 선지 행이라는 강한 증거이므로, 여기까지
    # 와서도 여전히 choice로 인정받지 못한 _from_figure_row 조각이
    # 2개 이상 연달아 있으면 마커 인식 여부와 무관하게 그대로 선지로
    # 확정한다.
    result = list(segments)
    i = 0
    while i < len(result):
        if not result[i].get("_from_figure_row") or result[i]["type"] == "choice":
            i += 1
            continue
        j = i
        while j < len(result) and result[j].get("_from_figure_row") and result[j]["type"] != "choice":
            j += 1
        run = sorted(result[i:j], key=lambda s: s["bbox_x"])
        if len(run) >= 2:
            for position, seg in enumerate(run):
                stripped = seg["content"].strip()
                digit_match = re.match(r"^\D*(\d.*)$", stripped)
                seg["type"] = "choice"
                seg["content"] = digit_match.group(1) if digit_match else stripped
                seg["label"] = chr(0x2460 + position) if position < 20 else None
        result[i:j] = run
        i = j
    return result


def _recognize_table_cell(img, fallback_text):
    # 표 셀 하나의 (아주 작은) 크롭을 읽는다. 처음에는 이걸
    # _recognize_pixel_region(레이아웃 검출부터 다시 하는 무거운 경로)에
    # 맡겼는데, 실제 표로 테스트해보니 셀 테두리가 크롭 가장자리에
    # 살짝 걸리는 것만으로도 모델이 "\hline", "\fbox{b}"처럼 표 구문
    # 자체를 수식 안에 끼워 넣는 환각을 자주 일으켰다 - 레이아웃부터
    # 다시 찾으려는 모델에게 셀 하나짜리 맥락은 너무 빈약했던 것.
    # 그래서 셀은 "이 안에 뭐가 있는지 레이아웃부터 다시 찾아라"가
    # 아니라 "이 안의 값 하나를 읽어라"로 직접 지시한다: pix2text의
    # 표 인식이 준 자체 셀 텍스트(부정확할 수 있음, 그래도 한글이
    # 섞여 있는지 정도는 판단 재료가 된다)에 한글이 있으면 EasyOCR로,
    # 아니면 (표 셀 값은 거의 항상 숫자/기호/분수이므로)
    # recognize_formula로 곧장 읽는다.
    fallback_text = (fallback_text or "").strip()
    if HANGUL_RE.search(fallback_text):
        text_reader = get_text_ocr_reader()
        reads = text_reader.readtext(np.array(img))
        content = clean_text(" ".join(t.strip() for _, t, c in reads if t.strip()))
        if content:
            return "text", content
        return "text", clean_text(fallback_text)

    model = get_formula_ocr_model()
    try:
        latex = clean_latex(model.recognize_formula(img).strip())
    except Exception:
        latex = ""
    if latex:
        return "formula", latex
    return "text", clean_text(fallback_text)


def _extract_table_region(problem_img, element, x0, y0, w, h):
    # pix2text의 표 레이아웃 인식이 준 셀별 bbox/행렬 번호(el.meta['cells'])를
    # 이용해 셀 하나하나를 다시 크롭하고 _recognize_table_cell로 다시
    # 읽는다 - pix2text 자체의 표-텍스트 변환(마크다운) 결과는 구조
    # (몇 행 몇 열, 어느 셀이 어느 값)는 정확했지만 한글은 뭉개지고
    # 분수는 분수 기호가 사라지는 등 셀 *내용* 품질은 이 앱의 다른
    # 곳과 같은 이유로 못 미더워서, 구조만 가져오고 내용은 재인식한다.
    #
    # 반환값이 None이면 셀 구조를 못 뽑은 경우(레이아웃 인식이 TABLE로는
    # 잡았지만 표 파서 자체가 실패한 경우) - 호출한 쪽이 이미지 블록으로
    # 대체 처리한다.
    cells_meta = (element.meta or {}).get("cells") or []
    flat_cells = cells_meta[0] if cells_meta else []
    if not flat_cells:
        return None

    max_row = max((max(c["row_nums"]) for c in flat_cells), default=-1)
    max_col = max((max(c["column_nums"]) for c in flat_cells), default=-1)
    if max_row < 0 or max_col < 0:
        return None

    grid = [[None for _ in range(max_col + 1)] for _ in range(max_row + 1)]
    for cell in flat_cells:
        cbx0, cby0, cbx1, cby1 = (int(round(v)) for v in cell["bbox"])
        # 셀 경계선 몇 픽셀을 안쪽으로 깎아낸다 - 표 격자선이 크롭
        # 가장자리에 살짝 걸리기만 해도 recognize_formula가 그 선을
        # "\fbox{...}"(테두리로 감싼 수식)로 잘못 해석하는 걸 실제로
        # 확인했다.
        trim_x = min(4, (cbx1 - cbx0) // 4)
        trim_y = min(4, (cby1 - cby0) // 4)
        cbx0, cby0 = cbx0 + trim_x, cby0 + trim_y
        cbx1, cby1 = cbx1 - trim_x, cby1 - trim_y
        cw, ch = cbx1 - cbx0, cby1 - cby0
        fallback_text = cell.get("cell text", "")
        if cw > 0 and ch > 0:
            crop = problem_img.crop((x0 + cbx0, y0 + cby0, x0 + cbx0 + cw, y0 + cby0 + ch))
            cell_type, cell_content = _recognize_table_cell(crop, fallback_text)
        else:
            cell_type, cell_content = "text", clean_text(fallback_text)
        for r in cell["row_nums"]:
            for c in cell["column_nums"]:
                if 0 <= r <= max_row and 0 <= c <= max_col:
                    grid[r][c] = {"type": cell_type, "content": cell_content}

    grid = [[cell or {"type": "text", "content": ""} for cell in row] for row in grid]
    table_json = json.dumps({"rows": max_row + 1, "cols": max_col + 1, "cells": grid}, ensure_ascii=False)
    return [{"type": "table", "label": None, "content": table_json, "bbox_x": x0, "bbox_y": y0, "bbox_w": w, "bbox_h": h, "confidence": 1.0}]


def _mask_boxes(img, boxes, pad=6):
    # 표/그림으로 이미 구조화해서 뽑아낸 영역을, 뒤이어 나머지 전체를
    # 훑는 통짜 픽셀 스캔에서 흰색으로 덮어 가려버린다 - 안 가리면 표
    # 셀 값이나 그림 안 라벨이 스캔 결과에도 또 나와서 내용이 중복된다.
    if not boxes:
        return img
    masked = img.copy()
    draw = ImageDraw.Draw(masked)
    for x0, y0, w, h in boxes:
        left = max(0, x0 - pad)
        top = max(0, y0 - pad)
        right = min(img.width, x0 + w + pad)
        bottom = min(img.height, y0 + h + pad)
        draw.rectangle([left, top, right, bottom], fill="white")
    return masked


def recognize_problem_regions(document, page, problem):
    # 표/그림만 레이아웃 분석으로 구조화해서 뽑아내고, 나머지 전부
    # (본문, 수식, 선지)는 문제 전체를 한 덩어리로 픽셀 기반 통짜
    # 인식한다(recognize_scanned_problem_region과 같은 방식) - PDF
    # 텍스트 레이어를 문자 단위로 분류해서 조각내고 다시 클러스터링해
    # 이어붙이던 이전 경로는 걷어냈다.
    #
    # 실제 문제로 두 방식을 나란히 비교해본 결과에 따른 결정이다: 선지
    # 5개 중 1개만 건지고 나머지가 빈 값이 되던 문제가, 통짜 인식에서는
    # 5개 다 정확히 나왔고, 본문 문장도 훨씬 덜 깨졌다. 문자 단위
    # 분류→클러스터링 경로는 PDF에 심어진 텍스트 레이어 자체가
    # 부정확한 문서(이 앱이 실제로 다루는 대다수 문서가 그렇다 -
    # analyze_problem_layout의 주석 참고)에서 오류가 겹겹이 쌓이는
    # 구조였는데, pix2text 자체 레이아웃 인식 + EasyOCR 조합은 텍스트
    # 레이어를 아예 참고하지 않아 그 문제를 원천적으로 비껴간다.
    # 표는 반대다 - 통짜 인식은 표의 행/열 구조를 전혀 모르므로
    # (셀 값이 그냥 죽 나열되어 나옴) 표는 레이아웃 분석으로 구조를
    # 잡는 게 필수다.
    problem_img, regions = analyze_problem_layout(problem)

    segments = []
    text_like_segments = []
    mask_boxes = []
    for region in regions:
        kind, x0, y0, w, h = region["kind"], region["x"], region["y"], region["w"], region["h"]
        if kind == "figure":
            reclassified = _figure_region_is_actually_text(problem_img, x0, y0, w, h)
            if reclassified is not None:
                text_like_segments.extend(reclassified)
            else:
                segments.extend(_extract_figure_region(x0, y0, w, h))
            mask_boxes.append((x0, y0, w, h))
        elif kind == "table":
            table_segment = _extract_table_region(problem_img, region["element"], x0, y0, w, h)
            segments.extend(table_segment if table_segment else _extract_figure_region(x0, y0, w, h))
            mask_boxes.append((x0, y0, w, h))

    scan_img = _mask_boxes(problem_img, mask_boxes)
    text_like_segments.extend(_recognize_pixel_region(scan_img))
    if text_like_segments:
        text_like_segments = _reading_order(text_like_segments)
        text_like_segments = extract_choice_labels(text_like_segments)
        text_like_segments = expand_choice_glob_segments(text_like_segments)
        text_like_segments = merge_empty_choice_markers(text_like_segments)
        text_like_segments = relabel_choice_row(text_like_segments)
        text_like_segments = _rescue_unclaimed_figure_rows(text_like_segments)
        for seg in text_like_segments:
            seg.pop("_from_figure_row", None)
    segments.extend(text_like_segments)

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

    # 단순히 (bbox_y, bbox_x)로만 정렬하면 안 된다 - 지수/분수처럼 키가
    # 큰 수식 조각은 같은 줄에 있어도 위쪽 y가 옆의 평범한 텍스트보다
    # 작게(즉 "더 위"로) 잡히는 경우가 흔해서, 그 정렬 하나만으로 같은
    # 줄 안의 왼쪽->오른쪽 순서가 실제로 뒤섞이는 걸 확인했다.
    # _reading_order는 먼저 세로로 겹치는 조각끼리 "같은 줄"로 묶은
    # 다음 그 안에서만 x로 정렬하므로, 줄 높이가 들쭉날쭉해도 순서가
    # 안 깨진다.
    return _reading_order(segments)


# pix2text가 그 외에는 멀쩡한 결과물 안에 가끔 환각으로 끼워 넣는
# 토큰들 - 고등학교 이하 수준 수학 문제에는 사실상 등장할 일이 없는,
# 범주론/모델이론 계열의 낯선 기호들(\models, \sharp 등). 모델이 애초에
# 이런 걸 뱉지 않도록 막으려 하는 대신, 후처리로 제거한다.
JUNK_LATEX_COMMANDS = [r"\models", r"\bigstar", r"\boxplus", r"\nsubseteq", r"\boldmath", r"\sharp", r"\circ", r"\S", r"\Phi"]
JUNK_LATEX_COMMANDS_WITH_ARG = [r"\mathfrak"]

# 프라임 기호("f'(x)")가 OCR 모델에서 위첨자의 위첨자로 나오는 경우가
# 있다(예: h^{\prime} 대신 h^{\,^{\prime}}) - KaTeX가 이렇게 이중으로
# 중첩된 형태를 렌더링하면 프라임 틱이 비정상적으로 높고 작게 떠서,
# 인쇄 크기(~10.5pt)에서는 프라임이 아니라 낯선 점처럼 보인다. 이걸
# 단일 위첨자로 평탄화하면, 원래 자리에 정상적인 프라임 모양으로
# 렌더링된다.
NESTED_PRIME_RE = re.compile(r"\^\{[^{}]*\^\{(\\prime+)\}[^{}]*\}")

# \stackrel{.}{X}는 X 위에 점을 그린다 - 의도된 표기가 아니라 OCR이
# 만들어낸 아티팩트다(실제 출력을 대조 확인해본 결과, "=" 위의 "≐"도,
# 적분 위끝 같은 평범한 변수 위의 점도 둘 다 원하는 표기가 아님을
# 확인했다). 그래서 예외 없이 모든 경우에 X를 그 자체로 풀어낸다.
STRAY_DOT_STACKREL_RE = re.compile(r"\\stackrel\{\.\}\s*\{([^{}]*)\}")

# 등호(=)의 위나 아래에 뭔가를 얹어서 그리는 \stackrel도 같은 부류의
# 아티팩트다 - 등호 자체가 진짜 내용이고 다른 한쪽 인자는 잡음이므로,
# "="가 어느 위치(위/아래)에 있든 등호 하나만 남긴다.
STRAY_EQUALS_STACKREL_RE = re.compile(r"\\stackrel\{[^{}]*\}\s*\{\s*=\s*\}|\\stackrel\{\s*=\s*\}\s*\{[^{}]*\}")

# 이 앱이 다루는 문제 지문에는 볼드체로 강조된 부분이 원래 없다 - OCR이
# 가끔 이유 없이 특정 글자/기호를 \mathbf 등으로 잘못 인식한다(실제
# 출력에서 \mathbf{\Sigma}, \mathbf{\alpha} 같은 경우를 확인했다).
# 명령어와 중괄호만 벗겨내고 안의 내용은 그대로 남긴다.
BOLD_WRAPPER_RE = re.compile(r"\\(?:mathbf|boldsymbol|textbf|bold)\{([^{}]*)\}")

# {\bf ...} 처럼 중괄호 없이 그 뒤 전체에 볼드를 적용하는 옛 스타일
# 선언형 명령 - 명령어 토큰만 지우면 나머지 내용과 감싸는 중괄호는
# 그대로 남아 볼드만 해제된다.
BOLD_DECLARATION_RE = re.compile(r"\\bf\s*")


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
    cleaned = STRAY_EQUALS_STACKREL_RE.sub("=", cleaned)
    # 볼드 래퍼는 중첩될 수 없다는 보장이 없으니(예: \mathbf{\mathbf{X}}는
    # 실제로 본 적 없지만), 더 이상 안 걸릴 때까지 반복해서 완전히
    # 벗겨낸다.
    while True:
        stripped = BOLD_WRAPPER_RE.sub(r"\1", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
    cleaned = BOLD_DECLARATION_RE.sub("", cleaned)
    cleaned = re.sub(r"\{\s*\}", "", cleaned)  # 위 치환 후 남은 빈 그룹 제거
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# 앞에 공백이 오면 안 되는 닫는 쪽 문장부호/괄호와, 뒤에 공백이 오면
# 안 되는 여는 쪽 문장부호/괄호 - ASCII와, 한국 수학 문제 PDF에서 흔히
# 쓰이는 전각(full-width) 형태를 모두 포함한다.
_TEXT_CLOSING_PUNCT = r")\]}.,!?:;、。！？：；’”』」〉》"
_TEXT_OPENING_BRACKET = r"(\[{‘“『「〈《"
_TEXT_SPACE_BEFORE_CLOSING_RE = re.compile(r"\s+([" + re.escape(_TEXT_CLOSING_PUNCT) + r"])")
_TEXT_SPACE_AFTER_OPENING_RE = re.compile(r"([" + re.escape(_TEXT_OPENING_BRACKET) + r"])\s+")


def clean_text(text):
    # 일반 text 세그먼트에서 PDF 텍스트 레이어/OCR이 남긴 공백
    # 아티팩트를 정리한다: 공백이 2개 이상 연속되면 하나로 줄이고,
    # 닫는 문장부호 바로 앞이나 여는 괄호 바로 뒤에 붙은 공백을
    # 제거한다(예: "값은 ." 또는 "( x") - 단어 자체는 다 제대로
    # 읽혔어도 이런 건 눈에 띄게 틀려 보인다.
    if not text:
        return text
    cleaned = re.sub(r"\s+", " ", text)
    cleaned = _TEXT_SPACE_BEFORE_CLOSING_RE.sub(r"\1", cleaned)
    cleaned = _TEXT_SPACE_AFTER_OPENING_RE.sub(r"\1", cleaned)
    return cleaned.strip()


def crop_region_image(document, page, x, y, w, h, pad=8, dpi=RENDER_DPI):
    # 페이지의 한 영역(절대 픽셀 좌표, 좌상단 기준)을 원본 PDF에서 바로
    # 렌더링해 PIL 이미지로 만든다 - 수식 OCR 모델에 넣기 위함이다.
    # 패딩을 주는 이유는, 텍스트로부터 유도된 빡빡한 bbox 주변에 모델이
    # 숨 쉴 공간을 조금 더 주기 위해서다.
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


def _content_from_segment(problem_id, segment, parent_content_id, order_index):
    # 어떤 검출 경로(PDF 텍스트 레이어, 스캔 페이지 OCR, 선택지 라벨
    # 떼어내기, relabel_choice_row의 복구 케이스, ...)로 만들어졌든
    # 모든 세그먼트가 결국 여기를 거쳐간다 - "저장되는 조각에는
    # 앞뒤 공백이 없어야 한다"는 규칙을 강제할 수 있는, 유일하게
    # 보장된 지점이다. 이걸 위해 상위 함수들을 하나하나 감사할
    # 필요가 없다.
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
        # formula는 길이나 복잡도와 무관하게 기본으로 인라인(체크
        # 안 된 "Full line")으로 시작한다 - 특정 수식을 한 줄 전체로
        # 뺄지는 미리 선택해두는 대신, 검토자가 직접 그렇게 고르도록
        # 한다.
        display_mode=False,
    )


def _save_figure_crop(document, page, content, x, y, w, h):
    # app.py의 crop_content_image와 사실상 동일한 로직이다(수동으로
    # 그림 블록의 영역을 조정할 때 쓰는 것과 같은 크롭 방식) - 다만
    # 자동 검출된 FIGURE 영역을 저장하는 이 경로는 ocr.py 안에서 끝나야
    # app.py를 다시 임포트하는 순환 참조가 생기지 않으므로 따로 둔다.
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


def recognize_and_save_problem(document, page, problem):
    # 문제 하나에 대해 인식을 실행하고 그 problem_contents 행들을
    # 통째로 교체한다. 커밋은 하지 않으며, 트랜잭션 제어는 호출한
    # 쪽의 몫이다.
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

        # 연속된 choice가 2개 이상이면 무관한 행 N개가 아니라 하나의
        # 객관식 세트로 본다 - 검토자가 "Group selected"로 손수 묶는
        # 것과 똑같은 방식으로 자동으로 묶어준다. choice가 하나뿐이면
        # 그건 무엇의 "세트"도 아니므로 묶지 않고 그대로 둔다.
        if run_end - i >= 2:
            group = ProblemContent(
                problem_id=problem.id,
                parent_content_id=None,
                order_index=top_level_index,
                type="group",
                label="Choices",
            )
            db.session.add(group)
            db.session.flush()  # 자식이 참조할 수 있으려면 먼저 group.id가 필요하다
            contents.append(group)
            top_level_index += 1

            child_segments = segments[i:run_end]
            for child_index, seg in enumerate(child_segments):
                content = _content_from_segment(problem.id, seg, group.id, child_index)
                db.session.add(content)
                contents.append(content)

            # 객관식 세트는 항상 정확히 5개의 선택지를 갖는다 - OCR이
            # 일부만 잡아냈다면(라벨 오독이나, ㄱ/ㄴ/ㄷ 같은 낮은
            # 신뢰도의 단일 자모 값이 흔한 실패 사례다), 검토자가
            # 빈틈을 알아채고 손으로 추가하게 두는 대신, 나머지를
            # 빈 placeholder로 채워서 검토자가 처음부터 완전한
            # ①-⑤ 세트로 시작하게 한다.
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
        if content.type == "image":
            # 자동 검출된 그림(FIGURE) 영역 - _extract_figure_region은
            # bbox만 넘겨줄 뿐 크롭 파일은 아직 없다. 파일명이 content.id
            # 기반이라 먼저 flush로 id를 확정한 뒤에 크롭해서 저장한다.
            db.session.flush()
            content.crop_path = _save_figure_crop(
                document, page, content, problem.x + content.bbox_x, problem.y + content.bbox_y, content.bbox_w, content.bbox_h
            )
        contents.append(content)
        top_level_index += 1
        i += 1

    problem.status = "recognized"

    # 최초로 인식에 성공한 시점(단 한 번만)의 스냅샷을 남겨서,
    # 검토자가 나중에 OCR을 다시 돌리지 않고도 바로 이 상태로
    # 복원할 수 있게 한다 - revert_problem_to_initial 참고. 아래의
    # 부모/자식 인덱스 매핑을 위해 각 행의 id가 확정돼 있어야
    # 하므로, 읽기 전에 flush한다.
    if not problem.initial_content_snapshot:
        db.session.flush()
        problem.initial_content_snapshot = json.dumps(_snapshot_problem_contents(contents))

    return contents


def _snapshot_problem_contents(contents):
    # initial_content_snapshot에 넣기 위해 콘텐츠 행들을 직렬화한다.
    # 부모/자식 관계는 DB id가 아니라 이 리스트 안에서의 인덱스로
    # 인코딩한다 - 복원할 때는 새 id를 가진 새 행들이 만들어지므로,
    # 그 시점에는 원래 id가 아무것도 가리키지 못하게 되기 때문이다.
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
