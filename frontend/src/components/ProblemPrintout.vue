<template>
  <div class="problem-printout">
    <span v-if="number" class="problem-number">{{ number }}.</span>
    <template v-for="(item, i) in nonChoicesTopLevel" :key="item.id">
      <!-- &nbsp; (not a plain space) - Vue's template whitespace-condensing
           strips a lone ASCII space text node, but leaves U+00A0 alone. -->
      <span v-if="i > 0 && !skipGapBefore(nonChoicesTopLevel[i - 1], item)">&nbsp;</span>
      <div v-if="isBogiGroup(item)" class="bogi-box">
        <span class="bogi-label">&lt;{{ item.label }}&gt;</span>
        <template v-for="(child, ci) in childrenOf(item.id)" :key="child.id">
          <br v-if="ci > 0 && child.line_break_before" />
          <span v-else-if="ci > 0 && !skipGapBefore(childrenOf(item.id)[ci - 1], child)">&nbsp;</span>
          <span v-if="child.label" class="row-label">{{ child.label }}.</span>
          <img v-if="child.type === 'image'" :src="child.image_url" class="inline-image" />
          <span v-else class="inline-content" v-html="renderContentHtml(child)"></span>
        </template>
      </div>
      <div v-else-if="item.type === 'group'" class="content-group">
        <div class="content-group-label">&lt;{{ item.label }}&gt;</div>
        <template v-for="(child, ci) in childrenOf(item.id)" :key="child.id">
          <br v-if="ci > 0 && child.line_break_before" />
          <span v-else-if="ci > 0 && !skipGapBefore(childrenOf(item.id)[ci - 1], child)">&nbsp;</span>
          <span v-if="child.label" class="row-label">{{ child.label }}.</span>
          <img v-if="child.type === 'image'" :src="child.image_url" class="inline-image" />
          <span v-else class="inline-content" v-html="renderContentHtml(child)"></span>
        </template>
      </div>
      <img v-else-if="item.type === 'image'" :src="item.image_url" class="inline-image" />
      <span v-else class="inline-content" v-html="renderContentHtml(item)"></span>
    </template>

    <div
      v-if="choicesGroup"
      class="choices-row"
      :style="{ gridTemplateColumns: `repeat(${choicesColumnCount}, 1fr)` }"
    >
      <span v-for="child in choiceChildren" :key="child.id" class="choice-item">
        <span class="choice-label">{{ child.label }}</span>
        <img v-if="child.type === 'image'" :src="child.image_url" class="inline-image" />
        <span v-else v-html="renderContentHtml(child)"></span>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const props = defineProps({
  problem: {
    type: Object,
    required: true,
  },
  // Optional problem number (e.g. sequential position in a document) shown
  // as "N." right before the problem's content. Omitted entirely when not
  // given, e.g. when previewing a single problem out of its own sequence.
  number: {
    type: [String, Number],
    default: null,
  },
})

// The stored content tree (flat rows + parent_content_id, same shape used
// throughout the review UI) is reconstructed here the same way
// ProblemReview does it, rather than adding a nested-tree endpoint.
const topLevelOf = computed(() =>
  props.problem.contents
    .filter((c) => !c.parent_content_id)
    .slice()
    .sort((a, b) => a.order_index - b.order_index)
)

const childrenOf = (groupId) =>
  props.problem.contents
    .filter((c) => c.parent_content_id === groupId)
    .slice()
    .sort((a, b) => a.order_index - b.order_index)

// Case-insensitive: some existing rows in the dev data predate the
// "Choices" (capitalized) label convention used elsewhere in the app.
const isChoicesGroup = (item) => item.type === 'group' && (item.label || '').toLowerCase() === 'choices'

// A <보기> block (statements the problem body refers to, e.g. "ㄱ. ... ㄴ. ...")
// gets its own real-exam-style layout - a horizontal rule above and below
// with the label centered on the top rule - rather than the generic
// full-border box used for other group labels ("Options", free text, ...).
const isBogiGroup = (item) => item.type === 'group' && (item.label || '').trim() === '보기'

const choicesGroup = computed(() => topLevelOf.value.find(isChoicesGroup) || null)

// Choices print in label order (①②③④⑤), not raw order_index - mirrors
// ProblemReview's sortedChoiceChildren. order_index only reflects where a
// choice was detected/inserted, which doesn't always match its label (a
// misdetected run can leave label ① sitting last in storage order); an
// unlabeled stray fragment sorts to the end instead of jumbling the
// visible sequence.
const CHOICE_LABEL_ORDER = ['①', '②', '③', '④', '⑤']
const choiceLabelRank = (label) => {
  const i = CHOICE_LABEL_ORDER.indexOf(label)
  return i === -1 ? CHOICE_LABEL_ORDER.length : i
}
const choiceChildren = computed(() => {
  if (!choicesGroup.value) return []
  return childrenOf(choicesGroup.value.id).sort(
    (a, b) => choiceLabelRank(a.label) - choiceLabelRank(b.label)
  )
})

// The choices group renders separately as its own row at the end of the
// problem (matching real exam layout - options aren't boxed), so it's
// excluded from the normal top-level flow here.
const nonChoicesTopLevel = computed(() => topLevelOf.value.filter((item) => !isChoicesGroup(item)))

// Korean particles/endings (의, 은, 는, 가, 이, ...) attach directly to the
// word before them with no space - including when that "word" is a
// formula, e.g. "f(x)의 값은?" not "f(x) 의 값은?". A text chunk right
// after a formula is almost always exactly this case, so the usual
// inter-chunk gap is skipped there specifically.
const skipGapBefore = (prevItem, item) => prevItem.type === 'formula' && item.type === 'text'

// Rough rendered-width estimate for a LaTeX string: command names
// (\sqrt, \frac, \left, ...) and braces are markup, not visible width, so
// counting raw source length overcounts short-but-decorated content like
// "9 \sqrt{3}" (10 source characters, ~3 visually) and would wrongly drop
// it out of a 3-column row.
const estimateVisualLength = (latex) =>
  (latex || '').replace(/\\[a-zA-Z]+/g, 'x').replace(/[{}\\]/g, '').length

// Exam convention: 5 short choices (numbers, radicals, ㄱ/ㄴ/ㄷ combos) sit
// 3-per-row; longer ones drop to 2-per-row; long expressions get one full
// row each. Thresholds calibrated against this app's real problem sets.
const CHOICE_LEN_FOR_3_COL = 6
const CHOICE_LEN_FOR_2_COL = 14
const choicesColumnCount = computed(() => {
  if (!choicesGroup.value) return 3
  const maxLen = Math.max(0, ...choiceChildren.value.map((c) => estimateVisualLength(c.content)))
  if (maxLen <= CHOICE_LEN_FOR_3_COL) return 3
  if (maxLen <= CHOICE_LEN_FOR_2_COL) return 2
  return 1
})

const renderMath = (latex, displayMode) => {
  if (!latex) return ''
  try {
    return katex.renderToString(latex, { throwOnError: false, displayMode })
  } catch (err) {
    return `<span class="latex-error">${err.message}</span>`
  }
}

// A cases/array environment (piecewise definitions, "함수 f(x) = { ... }")
// resets math style to compact "textstyle" for its own rows regardless of
// the outer displayMode - a real LaTeX/KaTeX quirk, not something
// \displaystyle on the whole formula fixes, since it doesn't reach inside
// the array. \dfrac forces that one fraction to full display size
// specifically, which is the standard fix - applied whenever a cases/array
// block is present, so e.g. "3/2" sitting next to "x^2"/"x+1" in a piecewise
// definition matches their size instead of rendering visibly smaller.
// Checks for \begin{array} too, not just \begin{cases}: this app's OCR
// pipeline emits piecewise definitions as raw \left\{ \begin{array}{cl}
// ... \end{array} \right. rather than the \cases shorthand, and \cases
// itself is defined in terms of \array under the hood - same quirk either way.
// \def\arraystretch{1.4} (KaTeX supports \def but not \newcommand/
// \renewcommand for it - confirmed empirically) loosens the row spacing a
// bit for the same rows, since the default array spacing reads as cramped
// next to normal text line-height.
const forceDfracInCases = (latex) => {
  if (!latex || !(latex.includes('\\begin{cases}') || latex.includes('\\begin{array}'))) return latex
  return '\\def\\arraystretch{1.4}' + latex.replace(/\\frac\b/g, '\\dfrac')
}

const escapeHtml = (text) => {
  const div = document.createElement('div')
  div.textContent = text || ''
  return div.innerHTML
}

// Text/formula/choice content items are rendered as inline spans (not
// individual blocks) so consecutive fragments flow together into one
// paragraph, approximating how they originally read on the page - the
// stored data has no explicit "same line vs. new line" signal to rebuild
// exact original line breaks from. A formula with display_mode set still
// breaks onto its own line despite the inline wrapper, since KaTeX's
// displayMode output is block-level on its own - display_mode is seeded
// from a complexity heuristic at recognition time but is reviewer-editable
// from then on (the "Full line" checkbox in ContentRow), so it's read
// directly here rather than recomputed.
//
// Unchecked ("inline") still gets full displaystyle-size scripts/
// fractions/limits, not KaTeX's shrunk inline "textstyle" - achieved by
// prepending \displaystyle to the source itself while keeping
// displayMode: false, rather than using KaTeX's displayMode option (which
// would also force it onto its own block line, not just resize it). The
// tradeoff: that one text line ends up visually taller than its neighbors
// to fit the full-size formula, since it's not isolated onto its own row.
const renderContentHtml = (content) => {
  if (content.type === 'text') return escapeHtml(content.content)
  if (content.type === 'formula') {
    const latex = forceDfracInCases(content.content)
    if (content.display_mode) return renderMath(latex, true)
    return renderMath(latex ? `\\displaystyle ${latex}` : latex, false)
  }
  if (content.type === 'choice') {
    const latex = forceDfracInCases(content.content)
    return renderMath(latex ? `\\displaystyle ${latex}` : latex, false)
  }
  return ''
}
</script>

<style scoped>
.problem-printout {
  font-family: '(한)신중명조', '신명중명조', 'HY신명조', Batang, serif;
  font-size: 10.5pt;
  line-height: 1.7;
  color: #111;
}

.problem-number {
  font-weight: 700;
  margin-right: 0.3em;
}

.inline-content :deep(.katex) {
  font-size: 1em;
}

.inline-content :deep(.katex-display) {
  margin: 0.5em 0;
  overflow-x: auto;
  overflow-y: hidden;
}

.inline-image {
  max-width: 100%;
  vertical-align: middle;
}

.content-group {
  border: 1px solid #333;
  padding: 0.5em 0.7em;
  margin: 0.4em 0;
  break-inside: avoid;
}

.content-group-label {
  text-align: center;
  font-weight: 600;
  margin-bottom: 0.35em;
}

.bogi-box {
  position: relative;
  border-top: 1.5px solid #111;
  border-bottom: 1.5px solid #111;
  padding: 0.9em 0.7em 0.6em;
  margin: 0.9em 0 0.6em;
  break-inside: avoid;
}

.bogi-label {
  position: absolute;
  top: 0;
  left: 50%;
  transform: translate(-50%, -50%);
  background: #fff;
  padding: 0 0.6em;
  font-weight: 600;
}

.group-row {
  margin: 0.2em 0;
}

.row-label {
  margin-right: 0.3em;
}

.choices-row {
  display: grid;
  /* Row gap widened for breathing room between choices; column gap
     unchanged. margin-top ~= 2 body-text lines (line-height 1.7em) so the
     choices read as a visually separate block from the problem statement. */
  gap: 1em 0.8em;
  margin-top: 3.4em;
}

.choice-item {
  display: flex;
  align-items: baseline;
  gap: 0.25em;
  min-width: 0;
}

.choice-label {
  font-weight: 500;
}

.latex-error {
  color: #dc2626;
  font-size: 0.85em;
}
</style>
