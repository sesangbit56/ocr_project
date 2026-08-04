# 수정사항 정리 (feature/bogi-block 브랜치)

`main`에 마지막으로 병합된 시점(교차 문서 큐, 인쇄/미리보기 시스템 커밋) 이후,
이 브랜치에서 진행한 수정사항 전체 정리.

---

## 1. 선지(Choices) 수식 렌더링 버그 수정

**문제**: 미리보기에서 선지에 들어간 `lim`, 분수 등이 본문 수식보다 작게(구식 텍스트
스타일로) 렌더링됨.

**원인**: `ProblemPrintout.vue`의 `renderContentHtml`에서 `formula` 타입은
`\displaystyle`을 붙이고 `forceDfracInCases`를 적용하는데, `choice` 타입은 이
처리를 빠뜨리고 그냥 렌더링하고 있었음.

**수정**: `choice` 타입도 `formula`와 동일하게 `\displaystyle` 접두 + `forceDfracInCases`
적용하도록 통일.

---

## 2. `cases`/`array` 블록(분수 크기·줄 간격) 수정

**문제 1**: 조건별 정의(`f(x) = { ... }` 형태) 안의 분수가 다른 항목보다 작게 보임.

**원인**: 이 앱의 OCR 파이프라인은 실제로 `\begin{cases}`가 아니라
`\left\{ \begin{array}{cl} ... \end{array} \right.` 형태로 값을 뽑아내는데,
기존 `forceDfracInCases`는 `\begin{cases}`만 확인하고 있었음. `array`/`cases`
환경은 내부적으로 자기 행의 수식 스타일을 강제로 압축(textstyle)시키는 LaTeX/KaTeX
특성이 있어 `\displaystyle`만으로는 해결되지 않음.

**수정**: `\begin{array}`도 함께 감지하도록 확장하고, 해당 블록 안의 `\frac`를
`\dfrac`으로 치환.

**문제 2**: 같은 블록의 위아래 줄 간격이 너무 좁아 보임.

**수정**: 같은 감지 조건에서 `\def\arraystretch{1.4}`를 함께 주입해 행간을 넓힘
(KaTeX는 `\def`는 지원하지만 `\newcommand`/`\renewcommand`는 지원하지 않음 —
직접 확인).

---

## 3. 인쇄용 한글 폰트 교체

- 기존 지정 폰트("신명중명조", "HY신명조")는 시스템에 설치돼 있지 않거나(전자),
  설치돼 있어도(후자, `HY신명조` → `H2MJSM.TTF`) Chromium이 실제로 로드하지
  못하는 상태였음 (GDI+는 정상 인식하지만 Chromium이 쓰는 DirectWrite 쪽에서는
  매칭 실패 — 오래된 트루타입 폰트의 name table 문제로 추정). 사용자가 다운로드한
  `.HFT` 파일은 애초에 한컴 자체 폰트 포맷(Han Unified Font File)이라 Windows/
  브라우저가 아예 읽을 수 없는 형식이었음.
- 사용자가 새로 설치한 `(한)신중명조.TTF`(진짜 TrueType, 사용자별 설치)는 정상
  작동 확인됨 (GDI+·Chromium 양쪽에서 실제로 다른 폭으로 렌더링되는 것 확인).
- `ProblemPrintout.vue`, `PrintPreview.vue`의 `font-family`를
  `'(한)신중명조', '신명중명조', 'HY신명조', Batang, serif`로 변경.

---

## 4. `<보기>` 그룹 전용 인쇄 스타일

- 그룹 라벨이 정확히 "보기"인 그룹은 기존의 사각 테두리 박스 대신, 실제 모의고사
  스타일(위아래 가로선 + 가운데에 `<보기>` 라벨이 선 위에 걸쳐 보이는 형태)로
  렌더링하도록 `ProblemPrintout.vue`에 `isBogiGroup` 분기 추가.
- `ProblemReview.vue`의 그룹 라벨 프리셋 드롭다운에 `보기`를 추가.
- 더 이상 필요 없어진 `Options` 프리셋은 제거 (보기 프리셋으로 대체).

---

## 5. 그룹 내부 콘텐츠가 조각마다 줄바꿈되던 문제 수정

**문제**: 일반 그룹(`보기` 포함)의 자식 항목들이 각자 `.group-row` div로 렌더링돼,
OCR이 쪼갠 조각(텍스트/수식) 하나하나가 강제로 줄바꿈됨. 문제 본문(그룹 밖)은
자연스럽게 한 문단으로 흐르는 것과 대조적이었음.

**수정**: 그룹 자식 렌더링을 문제 본문과 동일한 인라인 흐름 방식(`&nbsp;` 간격
삽입 + 수식 뒤 텍스트일 때 공백 생략)으로 변경. `content-group`, `bogi-box`
양쪽 모두 적용.

---

## 6. 그룹 내부 수동/자동 줄바꿈 기능 추가

**요청 배경**: 5번 수정으로 조각들이 한 줄로 흐르게 됐지만, `<보기>`처럼 원래
(가)/(나), ㄱ/ㄴ/ㄷ 같은 번호별로 줄바꿈이 필요한 경우가 있음. 자동 인식이 안
되는 스타일도 있으므로 수동 조정 수단이 필요.

**구현**:
- `ProblemContent`에 `line_break_before` 컬럼 추가 (DB 마이그레이션 완료).
- `ContentRow.vue`에 그룹 자식 항목에서만 보이는 "New line" 체크박스 추가 —
  체크 시 인쇄/미리보기에서 이 블록 앞에 강제 줄바꿈.
- `group_problem_contents`(그룹 묶기 API)에서, 라벨이 정확히 "보기"인 경우
  각 자식의 내용이 `(가)`/`(나)`/.../`(사)` 또는 `ㄱ.`/`ㄴ.`/... 같은 번호
  패턴으로 시작하면 `line_break_before`를 자동으로 `true`로 세팅
  (`BOGI_LINE_BREAK_RE`). 패턴을 못 맞추는 경우는 체크박스로 수동 보정.
- `ProblemPrintout.vue`에서 `line_break_before`가 true인 자식 앞에는 `&nbsp;`
  간격 대신 실제 줄바꿈(`<br>`) 삽입.

---

## 7. 문제 본문 ↔ 선지, 선지 행 간 간격 조정

- 문제 본문과 선지 사이 간격: `margin-top: 0.45em` → `3.4em` (본문
  `line-height: 1.7em` 기준 약 2줄 분량).
- 선지 행 간 세로 간격: `gap: 0.3em 0.8em` → `1em 0.8em` (세로 간격만 확대,
  가로는 유지).

---

## 8. 리뷰 중 편집 내용이 사라지는 버그 수정

**문제**: 리뷰 페이지에서 텍스트/라벨을 수정한 뒤 "Confirm"을 누르기 전에 새
블록을 추가(`+ Formula`/`+ Text`/`+ Choice`/`+ Image`)하면, 방금 수정한 값이
서버에 저장된 원래 값으로 되돌아감.

**원인**: 다른 콘텐츠 조작 함수(삭제, 그룹핑, 타입 변경 등)는 서버 응답을 받은 뒤
`withLocalEdits`로 로컬에만 있는 미저장 수정사항을 다시 얹어주는데, `addContent`
함수만 이 처리 없이 서버 응답으로 그대로 덮어쓰고 있었음.

**수정**: `addContent`도 `withLocalEdits`를 거치도록 통일.

---

## 9. "초기 OCR 결과로 되돌리기" 기능 추가

**요청 배경**: 리뷰 중 실수로 블록을 지우는 등 되돌리기 어려운 실수를 했을 때,
처음 OCR이 뽑아낸 결과로 바로 되돌릴 수 있는 버튼이 필요.

**중요한 구분**: "초기 OCR로 되돌리기"는 OCR을 다시 실행하라는 뜻이 아니라, 이미
나온 최초 결과값 그대로 복원하라는 의미. (OCR 재실행 기능 자체도 이후 리전 조정
등에 유용하므로 별도로 유지.)

**구현**:
- `Problem`에 `initial_content_snapshot` 컬럼 추가 (DB 마이그레이션 완료). 이
  문제에 대해 OCR 인식이 처음 성공한 시점의 콘텐츠 트리를 JSON으로 스냅샷 —
  이후 재인식/편집과 무관하게 최초 1회만 기록되고 덮어쓰이지 않음.
- 새 엔드포인트 `POST /api/problems/<id>/revert`: 저장된 스냅샷으로 콘텐츠를
  즉시 복원 (모델 재호출 없음, 빠름). 스냅샷이 없으면 에러.
- 기존 `POST /api/problems/<id>/recognize`(재인식, 실제 OCR 재호출)는 그대로
  유지.
- 리뷰 페이지 헤더에 버튼 두 개를 나란히 배치:
  - **"Revert to initial OCR result"** — 스냅샷 기반, 빠른 복원. 스냅샷이 없는
    문제는 비활성화 + 안내 툴팁.
  - **"Re-run OCR"** — 기존 재인식 버튼 (이름만 명확하게 변경).

---

## 10. OCR이 만들어내는 "글자 위 점" 아티팩트 두 종류 수정

**증상**: 특정 수식에서 글자 위에 원인 불명의 작은 점이 찍혀 보임.

**원인 1 — prime(도함수 `'`) 표기**: OCR이 `f'(x)`류 프라임 기호를
`h^{\prime}` 대신 `h^{\,^{\prime}}`(위첨자의 위첨자, 이중 중첩)로 뽑아내는
경우가 있음. KaTeX가 이걸 렌더링하면 프라임 틱이 비정상적으로 높고 작게 떠서
인쇄 크기(약 10.5pt)에서는 점처럼 보임.
→ `NESTED_PRIME_RE`로 이중 중첩 위첨자를 단일 위첨자로 평탄화.

**원인 2 — `\stackrel{.}{X}` 오사용**: OCR이 특정 글자/기호 위에 점을 찍는
`\stackrel{.}{X}` 구문을 뜬금없이 삽입하는 경우가 있음 (예: 적분 위끝의 `x`가
`\stackrel{.}{x}`로, `=`가 `\stackrel{.}{=}`로). 처음에는 `=` 위의 점(`≐`)은
의도된 "정의" 표기일 수 있다고 보고 보존하도록 했으나, 실제로는 그것도 아티팩트인
것으로 확인되어 **모든** `\stackrel{.}{X}`를 예외 없이 `X`로 평탄화하도록 수정.
→ `STRAY_DOT_STACKREL_RE`.

**백필**: 두 정규식 모두 코드 수정과 별개로, 기존 DB에 이미 저장돼 있던 값에도
동일한 치환을 1회 실행해 소급 적용함 (전체 `clean_latex()`를 재실행하는 대신
해당 정규식만 타겟팅 — 이전에 전체 재실행으로 무관한 행 3개를 잘못 건드렸던
사고 이후 채택한 방식).

---

## 참고: 발견됐지만 아직 손대지 않은 이슈

- 다른 문제의 수식에서 `a \stackrel{2}{\iota} = 0` 형태 발견 — "2"가 이상한
  문자(iota) 위에 얹혀 있는 모양으로, `a^2 = 0`이 잘못 인식된 것으로 추정되나
  확실하지 않아 별도 확인 필요.

---

## 변경 파일 목록

- `backend/app.py`
- `backend/models.py` (+ `data.sqlite`에 컬럼 2개 마이그레이션:
  `problems.initial_content_snapshot`, `problem_contents.line_break_before`)
- `frontend/src/components/ContentRow.vue`
- `frontend/src/components/ProblemPrintout.vue`
- `frontend/src/components/ProblemReview.vue`
