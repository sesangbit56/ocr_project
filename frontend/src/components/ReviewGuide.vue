<template>
  <div class="review-guide">
    <div class="guide-header">
      <button class="back-button" @click="$emit('back')">&larr; 뒤로 가기</button>
      <h2>검토 페이지 사용법</h2>
    </div>

    <p class="intro">
      업로드한 PDF에서 자동으로 인식된 문제(텍스트, 수식, 보기 등)를 확인하고 수정하는 화면입니다.
      아래 내용을 참고하면 화면의 각 기능을 빠르게 익힐 수 있습니다.
    </p>

    <section class="guide-section">
      <h3>1. 문제 이동하기</h3>
      <ul>
        <li>화면 상단의 번호 탭을 클릭하면 해당 문제로 바로 이동합니다. 색상은 <b>흰색(미검토)</b>, <b>파란색(인식됨)</b>, <b>초록색(확인 완료)</b>을 의미합니다.</li>
        <li>화면 하단 가운데의 <b>&larr; Prev / Next &rarr;</b> 버튼으로 이전·다음 문제로 이동할 수 있습니다.</li>
        <li>텍스트 입력창에 커서가 있지 않을 때는 키보드의 <b>왼쪽·오른쪽 화살표 키</b>로도 문제를 이동할 수 있습니다.</li>
      </ul>
    </section>

    <section class="guide-section">
      <h3>2. 화면 구성 및 크기 조절</h3>
      <ul>
        <li>왼쪽은 원본 이미지, 오른쪽은 인식된 내용을 보여줍니다. 현재 보고 있는 문제 영역이 이미지에 자동으로 확대되어 표시됩니다.</li>
        <li>두 영역 사이의 <b>세로선을 마우스로 드래그</b>하면 이미지와 결과 영역의 크기 비율을 자유롭게 조절할 수 있습니다.</li>
        <li>"모든 인식된 영역 표시" 체크박스를 켜면 현재 문제에 포함된 모든 수식·이미지의 위치가 점선 박스로 표시됩니다. 수식은 <b>보라색</b>, 이미지는 <b>분홍색</b>이며, 신뢰도가 낮아 검토가 필요한 수식은 <b>주황색</b>으로 강조됩니다.</li>
      </ul>
    </section>

    <section class="guide-section">
      <h3>3. 항목 종류</h3>
      <ul>
        <li><span class="badge-sample text">text</span> 일반 텍스트(문제 지문 등)입니다.</li>
        <li><span class="badge-sample formula">formula</span> 수식입니다. 렌더링된 수식을 클릭하면 그 자리에서 바로 편집할 수 있습니다(원본 LaTeX 코드가 자동으로 함께 수정됩니다).</li>
        <li><span class="badge-sample choice">choice</span> ①②③④⑤ 같은 객관식 보기입니다. 인식된 여러 보기는 자동으로 하나의 그룹으로 묶입니다.</li>
        <li><span class="badge-sample image">image</span> 그래프나 그림처럼 텍스트로 인식할 수 없는 부분입니다. 별도로 다시 그리지 않고, 지정한 영역을 그대로 잘라내어 보여줍니다.</li>
        <li>각 항목 옆의 작은 입력창은 <b>라벨</b>입니다. ①, ㄱ, (가)처럼 항목을 구분하는 표시를 직접 입력할 수 있습니다.</li>
      </ul>
    </section>

    <section class="guide-section">
      <h3>4. 영역 다시 지정하기 (Adjust region)</h3>
      <ul>
        <li>수식이 잘못 인식되었거나 이미지를 다시 잘라야 할 때 사용합니다. 항목의 <b>"Adjust region"</b> 버튼을 클릭한 뒤, 왼쪽 이미지에서 올바른 영역을 마우스로 드래그하여 새로 지정하세요.</li>
        <li>수식 항목은 영역을 다시 지정하면 해당 부분만 다시 인식하여 내용을 갱신합니다. 인식에는 몇 초 정도 걸릴 수 있으며, 진행 중에는 "Saving..." 표시가 나타납니다.</li>
        <li>이미지 항목은 인식 과정 없이 지정한 영역을 그대로 잘라서 보여줍니다.</li>
      </ul>
    </section>

    <section class="guide-section">
      <h3>5. 항목 묶기 / 풀기 (그룹)</h3>
      <ul>
        <li>&lt;보기&gt; 상자나 (가)(나) 조건처럼 여러 항목을 하나로 묶어야 할 때 사용합니다.</li>
        <li>묶고 싶은 항목의 <b>체크박스</b>를 선택하고, 상단의 라벨 입력창에 이름(예: 보기)을 입력한 뒤 <b>"Group selected"</b> 버튼을 클릭하세요.</li>
        <li>묶인 그룹은 테두리가 있는 상자로 표시되며, 그룹 상단의 <b>"Ungroup"</b> 버튼으로 다시 풀 수 있습니다.</li>
        <li>객관식 보기(①②③④⑤)는 인식 시 자동으로 그룹이 만들어지므로 별도로 묶을 필요가 없습니다.</li>
      </ul>
    </section>

    <section class="guide-section">
      <h3>6. 항목 추가/삭제/순서 변경</h3>
      <ul>
        <li>여러 항목이 하나로 잘못 합쳐서 인식된 경우, <b>"+ Formula"</b> 또는 <b>"+ Text"</b> 버튼으로 빈 항목을 추가한 뒤 내용을 직접 채울 수 있습니다. 그래프나 그림처럼 자동으로 인식되지 않는 부분은 <b>"+ Image"</b> 버튼으로 추가하세요. 새로 추가한 수식·이미지 항목은 바로 영역 지정 모드로 전환되니, 이미지에서 원하는 부분을 드래그로 지정하면 됩니다.</li>
        <li>각 항목의 <b>Delete</b> 버튼으로 필요 없는 항목을 삭제할 수 있습니다.</li>
        <li>항목 왼쪽의 <b>▲ ▼</b> 버튼으로 순서를 위아래로 바꿀 수 있습니다.</li>
      </ul>
    </section>

    <section class="guide-section">
      <h3>7. 검토 완료하기</h3>
      <ul>
        <li>모든 문제의 확인이 끝나면 화면 오른쪽 아래의 <b>Confirm</b> 버튼을 눌러 저장하세요.</li>
        <li>확인이 완료된 문제는 상단 번호 탭이 초록색으로 바뀌고, 상태가 "confirmed"로 표시됩니다.</li>
      </ul>
    </section>
  </div>
</template>

<script setup>
defineEmits(['back'])
</script>

<style scoped>
.review-guide {
  max-width: 780px;
  margin: 0 auto;
}

.guide-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.guide-header h2 {
  margin: 0;
  font-size: 1.3rem;
}

.back-button {
  border: none;
  background: none;
  color: #2563eb;
  cursor: pointer;
  font-size: 0.95rem;
  padding: 0;
}

.intro {
  color: #4b5563;
  line-height: 1.6;
  margin-bottom: 1.5rem;
}

.guide-section {
  margin-bottom: 1.5rem;
  padding-bottom: 1.5rem;
  border-bottom: 1px solid #e5e7eb;
}

.guide-section:last-child {
  border-bottom: none;
}

.guide-section h3 {
  margin: 0 0 0.6rem;
  font-size: 1.05rem;
  color: #1f2937;
}

.guide-section ul {
  margin: 0;
  padding-left: 1.25rem;
  color: #374151;
  line-height: 1.7;
}

.guide-section li + li {
  margin-top: 0.4rem;
}

.badge-sample {
  display: inline-block;
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border-radius: 0.4rem;
  margin-right: 0.3rem;
}

.badge-sample.text {
  background: #e0e7ff;
  color: #3730a3;
}

.badge-sample.formula {
  background: #fef3c7;
  color: #92400e;
}

.badge-sample.choice {
  background: #d1fae5;
  color: #065f46;
}

.badge-sample.image {
  background: #fce7f3;
  color: #9d174d;
}
</style>
