<template>
  <div class="problem-review">
    <div class="review-header">
      <button class="back-button" @click="$emit('back')">&larr; Back to page</button>
      <h2 v-if="data">Review problems &mdash; Page {{ data.page_number }}</h2>
      <span v-if="allConfirmed" class="badge">All reviewed</span>
    </div>

    <p v-if="loading" class="info">Loading...</p>
    <template v-else-if="data">
      <p v-if="data.problems.length === 0" class="info">No problems on this page.</p>

      <template v-else>
        <div class="slide-nav">
          <button
            v-for="(problem, i) in data.problems"
            :key="problem.id"
            class="slide-nav-item"
            :class="[problem.status, { active: i === currentIndex }]"
            @click="currentIndex = i"
          >
            {{ i + 1 }}
          </button>
        </div>

        <div class="review-body">
          <div class="review-image">
            <label class="formula-toggle">
              <input type="checkbox" v-model="showAllFormulas" />
              Show all formula regions
            </label>
            <p v-if="adjustingContentId" class="adjust-banner">
              <template v-if="submittingRegion">Saving new region&hellip;</template>
              <template v-else>
                Drag a box around the correct region for this formula.
                <button class="cancel-adjust-button" @click="cancelAdjust">Cancel</button>
              </template>
            </p>
            <div class="image-viewport" ref="imageViewportEl">
              <div
                class="image-wrapper"
                :class="{ adjusting: !!adjustingContentId }"
                :style="wrapperTransform"
                @mousedown="onAdjustMouseDown"
                @mousemove="onAdjustMouseMove"
                @mouseup="onAdjustMouseUp"
                @mouseleave="onAdjustMouseUp"
              >
                <img
                  ref="imgEl"
                  :src="data.image_url"
                  :alt="`Page ${data.page_number}`"
                  @load="onImageLoad"
                  draggable="false"
                  @dragstart.prevent
                />
                <div v-if="currentProblem" class="rect-box" :style="rectStyle(currentProblem.bbox)">
                  <span class="rect-label">{{ currentIndex + 1 }}</span>
                </div>
                <div
                  v-for="box in formulaBoxes"
                  :key="`formula-${box.id}`"
                  class="rect-box formula-box"
                  :class="{ flagged: box.flagged }"
                  :style="rectStyle(box)"
                ></div>
                <div v-if="draft" class="rect-box draft" :style="rectStyle(draft)"></div>
              </div>
            </div>
          </div>

          <div class="review-contents">
            <div v-if="currentProblem" class="problem-card">
              <div class="problem-card-header">
                <span class="problem-title">Problem {{ currentIndex + 1 }}</span>
                <span class="status-tag" :class="currentProblem.status">{{ currentProblem.status }}</span>
              </div>

              <p v-if="currentProblem.contents.length === 0" class="info small">
                {{ currentProblem.status === 'pending' ? 'Recognizing...' : 'Not recognized yet.' }}
              </p>

              <div
                v-for="(content, cIndex) in currentProblem.contents"
                :key="content.id"
                class="content-row"
                :class="{ 'needs-review': isFlagged(content) }"
              >
                <div class="content-row-main">
                  <div class="order-buttons">
                    <button
                      class="order-button"
                      :disabled="cIndex === 0 || reordering"
                      title="Move up"
                      @click="moveContent(currentProblem, cIndex, -1)"
                    >&#9650;</button>
                    <button
                      class="order-button"
                      :disabled="cIndex === currentProblem.contents.length - 1 || reordering"
                      title="Move down"
                      @click="moveContent(currentProblem, cIndex, 1)"
                    >&#9660;</button>
                  </div>
                  <span class="type-tag" :class="content.type">{{ content.type }}</span>
                  <textarea
                    v-if="content.type !== 'formula'"
                    v-model="content.content"
                    rows="2"
                    class="content-input"
                    :class="content.type"
                  ></textarea>
                  <math-field
                    v-else
                    class="math-field-input"
                    smart-fence
                    :value="content.content"
                    @input="content.content = $event.target.value"
                  ></math-field>
                </div>
                <div class="content-row-tools">
                  <span v-if="isFlagged(content)" class="review-flag">&#9888; needs review</span>
                  <button
                    v-if="content.type === 'formula'"
                    class="adjust-button"
                    :disabled="(adjustingContentId && adjustingContentId !== content.id) || (adjustingContentId === content.id && submittingRegion)"
                    @click="startAdjust(currentProblem, content)"
                  >
                    {{
                      adjustingContentId === content.id
                        ? (submittingRegion ? 'Saving...' : 'Drawing on image...')
                        : 'Adjust region'
                    }}
                  </button>
                  <button class="delete-button" @click="deleteContent(currentProblem, content)">Delete</button>
                </div>
              </div>
            </div>

            <div class="slide-controls">
              <button class="slide-button" :disabled="currentIndex === 0" @click="prevProblem">&larr; Prev</button>
              <span class="slide-counter">Problem {{ currentIndex + 1 }} of {{ data.problems.length }}</span>
              <button
                class="slide-button"
                :disabled="currentIndex === data.problems.length - 1"
                @click="nextProblem"
              >Next &rarr;</button>
            </div>
          </div>
        </div>
      </template>

      <div class="review-actions">
        <button class="confirm-button" :disabled="saving || data.problems.length === 0" @click="confirm">
          {{ saving ? 'Saving...' : 'Confirm' }}
        </button>
        <span v-if="justConfirmed" class="saved-note">Saved</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'
// Registers the <math-field> custom element used below - an experiment to
// let the LaTeX source be edited directly through its rendered form,
// instead of a raw-text textarea next to a read-only KaTeX preview.
import 'mathlive'

const props = defineProps({
  pageId: {
    type: String,
    required: true,
  },
})

const renderLatex = (latex) => {
  if (!latex || !latex.trim()) return ''
  try {
    return katex.renderToString(latex, { throwOnError: false, displayMode: true })
  } catch (err) {
    return `<span class="latex-error">${err.message}</span>`
  }
}

// A formula's confidence is a coverage ratio from clustering (1.0 = one
// clean segment or a manually-adjusted region; lower = the crop region had
// to bridge a gap between disconnected fragments, which is exactly what
// happens when a symbol like a square root has no extractable PDF text at
// all). Below this threshold it's worth a second look, not necessarily wrong.
const REVIEW_CONFIDENCE_THRESHOLD = 0.5

const isFlagged = (content) => {
  if (content.type !== 'formula') return false
  if (content.confidence !== null && content.confidence < REVIEW_CONFIDENCE_THRESHOLD) return true
  return renderLatex(content.content).includes('katex-error')
}

const data = ref(null)
const loading = ref(false)
const saving = ref(false)
const justConfirmed = ref(false)
const imgEl = ref(null)
const scale = ref(1)
const reordering = ref(false)
let pollTimer = null

const currentIndex = ref(0)

const currentProblem = computed(() => {
  if (!data.value || data.value.problems.length === 0) return null
  const idx = Math.min(currentIndex.value, data.value.problems.length - 1)
  return data.value.problems[idx]
})

const prevProblem = () => {
  if (currentIndex.value > 0) currentIndex.value -= 1
}

const nextProblem = () => {
  if (data.value && currentIndex.value < data.value.problems.length - 1) currentIndex.value += 1
}

// Left/right arrow keys move between problems, except while the user is
// typing in a textarea/input (there they should move the text cursor, not
// the slide) or holding a modifier (reserved for browser/OS shortcuts).
const onKeydown = (event) => {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  if (event.altKey || event.ctrlKey || event.metaKey) return
  const tag = event.target?.tagName
  if (tag === 'TEXTAREA' || tag === 'INPUT' || tag === 'SELECT' || event.target?.isContentEditable) return
  if (!data.value || data.value.problems.length === 0) return
  event.preventDefault()
  if (event.key === 'ArrowLeft') prevProblem()
  else nextProblem()
}

// Adjusting a region mid-drag only makes sense for the problem currently on
// screen, so switching slides cancels it rather than leaving a dangling
// draft box pointed at a problem the user can no longer see.
watch(currentIndex, () => {
  cancelAdjust()
})

const moveContent = async (problem, index, direction) => {
  const targetIndex = index + direction
  if (targetIndex < 0 || targetIndex >= problem.contents.length) return

  const reordered = problem.contents.slice()
  const [moved] = reordered.splice(index, 1)
  reordered.splice(targetIndex, 0, moved)

  reordering.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/contents/order`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content_ids: reordered.map((c) => c.id) }),
    })
    const updated = await response.json()
    problem.contents = updated.contents
  } finally {
    reordering.value = false
  }
}

const deleteContent = async (problem, content) => {
  const response = await fetch(`/api/problem_contents/${content.id}`, { method: 'DELETE' })
  const updated = await response.json()
  problem.contents = updated.contents
}

const showAllFormulas = ref(true)

const formulaBoxes = computed(() => {
  const problem = currentProblem.value
  if (!problem) return []
  const boxes = []
  for (const content of problem.contents) {
    if (content.type !== 'formula') continue
    const flagged = isFlagged(content)
    if (!showAllFormulas.value && !flagged) continue
    boxes.push({
      id: content.id,
      x: problem.bbox.x + content.bbox.x,
      y: problem.bbox.y + content.bbox.y,
      w: content.bbox.w,
      h: content.bbox.h,
      flagged,
    })
  }
  return boxes
})

// Manual region adjustment: the fallback for cases the automatic clustering
// can't get right (e.g. a vector-drawn symbol with no extractable text, so
// there's nothing to inform the crop region at all).
const adjustingContentId = ref(null)
const adjustingProblem = ref(null)
const draft = ref(null)
const drawing = ref(false)
const startPoint = ref(null)
// Recognition on the new crop can take a few seconds. Without this guard,
// a user who thinks the app is stuck can start a second drag while the
// first request is still in flight, firing two overlapping submissions
// for the same content whose responses can land out of order.
const submittingRegion = ref(false)

const startAdjust = (problem, content) => {
  adjustingContentId.value = content.id
  adjustingProblem.value = problem
  draft.value = null
  drawing.value = false
}

const cancelAdjust = () => {
  adjustingContentId.value = null
  adjustingProblem.value = null
  draft.value = null
  drawing.value = false
  startPoint.value = null
}

const pointFromEvent = (event) => {
  const bounds = imgEl.value.getBoundingClientRect()
  // bounds already reflects the current slide's zoom (it's the rendered,
  // post-transform box), so divide out zoomFactor before applying the
  // natural-vs-displayed scale to land back in natural image pixels.
  const zoom = zoomFactor.value || 1
  return {
    x: ((event.clientX - bounds.left) / zoom) * scale.value,
    y: ((event.clientY - bounds.top) / zoom) * scale.value,
  }
}

const onAdjustMouseDown = (event) => {
  if (!adjustingContentId.value || submittingRegion.value) return
  event.preventDefault()
  const point = pointFromEvent(event)
  drawing.value = true
  startPoint.value = point
  draft.value = { x: point.x, y: point.y, w: 0, h: 0 }
}

const onAdjustMouseMove = (event) => {
  if (!drawing.value || !startPoint.value) return
  const point = pointFromEvent(event)
  draft.value = {
    x: Math.min(startPoint.value.x, point.x),
    y: Math.min(startPoint.value.y, point.y),
    w: Math.abs(point.x - startPoint.value.x),
    h: Math.abs(point.y - startPoint.value.y),
  }
}

const onAdjustMouseUp = async () => {
  if (!drawing.value) return
  drawing.value = false
  if (!draft.value || draft.value.w < 5 || draft.value.h < 5) {
    draft.value = null
    return
  }
  await submitAdjustedRegion()
}

const submitAdjustedRegion = async () => {
  if (!adjustingContentId.value || !adjustingProblem.value || !draft.value) {
    cancelAdjust()
    return
  }
  const contentId = adjustingContentId.value
  const problemBbox = adjustingProblem.value.bbox
  const region = {
    x: Math.round(draft.value.x - problemBbox.x),
    y: Math.round(draft.value.y - problemBbox.y),
    w: Math.round(draft.value.w),
    h: Math.round(draft.value.h),
  }
  submittingRegion.value = true
  try {
    const response = await fetch(`/api/problem_contents/${contentId}/region`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(region),
    })
    const updated = await response.json()
    for (const problem of data.value.problems) {
      const idx = problem.contents.findIndex((c) => c.id === contentId)
      if (idx !== -1) {
        problem.contents[idx] = updated
        break
      }
    }
  } finally {
    submittingRegion.value = false
    cancelAdjust()
  }
}

const allConfirmed = computed(
  () =>
    !!data.value &&
    data.value.problems.length > 0 &&
    data.value.problems.every((p) => p.status === 'confirmed')
)

const stillRecognizing = computed(
  () => !!data.value && data.value.problems.some((p) => p.status === 'pending')
)

const onImageLoad = () => {
  if (imgEl.value) {
    scale.value = imgEl.value.naturalWidth / imgEl.value.clientWidth
  }
}

const rectStyle = (bbox) => {
  const displayScale = 1 / scale.value
  return {
    left: `${bbox.x * displayScale}px`,
    top: `${bbox.y * displayScale}px`,
    width: `${bbox.w * displayScale}px`,
    height: `${bbox.h * displayScale}px`,
  }
}

// Slide view crops/zooms the image to the current problem so its regions
// are large enough to drag precisely, instead of showing the whole page at
// a fixed 1:1 scale. imageViewportEl is the clipping window; its size is
// tracked via ResizeObserver since it depends on the flex layout.
const imageViewportEl = ref(null)
const viewportSize = ref({ width: 0, height: 0 })
let resizeObserver = null

watch(imageViewportEl, (el) => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (el) {
    resizeObserver = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (rect) viewportSize.value = { width: rect.width, height: rect.height }
    })
    resizeObserver.observe(el)
  }
})

const FOCUS_PADDING_RATIO = 0.18 // margin around the problem's bbox, as a fraction of its size
const MAX_ZOOM = 4 // cap so a tiny problem doesn't zoom in past useful image resolution

const slideView = computed(() => {
  const problem = currentProblem.value
  if (!problem || !viewportSize.value.width || !viewportSize.value.height) return null

  const displayScale = 1 / scale.value
  const padX = problem.bbox.w * FOCUS_PADDING_RATIO
  const padY = problem.bbox.h * FOCUS_PADDING_RATIO
  const focus = {
    x: (problem.bbox.x - padX) * displayScale,
    y: (problem.bbox.y - padY) * displayScale,
    w: (problem.bbox.w + padX * 2) * displayScale,
    h: (problem.bbox.h + padY * 2) * displayScale,
  }
  if (focus.w <= 0 || focus.h <= 0) return null

  const fitScale = Math.min(viewportSize.value.width / focus.w, viewportSize.value.height / focus.h)
  const s = Math.min(Math.max(fitScale, 1), MAX_ZOOM)
  const tx = viewportSize.value.width / 2 - s * (focus.x + focus.w / 2)
  const ty = viewportSize.value.height / 2 - s * (focus.y + focus.h / 2)
  return { s, tx, ty }
})

const zoomFactor = computed(() => slideView.value?.s ?? 1)

const wrapperTransform = computed(() => {
  const view = slideView.value
  if (!view) return {}
  return {
    transform: `matrix(${view.s}, 0, 0, ${view.s}, ${view.tx}, ${view.ty})`,
    transformOrigin: '0 0',
  }
})

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const startPollingIfNeeded = () => {
  stopPolling()
  if (stillRecognizing.value) {
    pollTimer = setInterval(async () => {
      const response = await fetch(`/api/pages/${props.pageId}/review`)
      data.value = await response.json()
      if (!stillRecognizing.value) stopPolling()
    }, 2000)
  }
}

const fetchReview = async () => {
  loading.value = true
  justConfirmed.value = false
  currentIndex.value = 0
  try {
    const response = await fetch(`/api/pages/${props.pageId}/review`)
    data.value = await response.json()
  } finally {
    loading.value = false
  }
  startPollingIfNeeded()
}

const confirm = async () => {
  if (!data.value) return
  saving.value = true
  try {
    const payload = {
      problems: data.value.problems.map((p) => ({
        id: p.id,
        contents: p.contents.map((c) => ({ id: c.id, content: c.content })),
      })),
    }
    const response = await fetch(`/api/pages/${props.pageId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    data.value = await response.json()
    justConfirmed.value = true
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  fetchReview()
  window.addEventListener('keydown', onKeydown)
})
onUnmounted(() => {
  stopPolling()
  if (resizeObserver) resizeObserver.disconnect()
  window.removeEventListener('keydown', onKeydown)
})
watch(() => props.pageId, fetchReview)
</script>

<style scoped>
.review-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.review-header h2 {
  margin: 0;
  font-size: 1.3rem;
  flex: 1;
}

.back-button {
  border: none;
  background: none;
  color: #2563eb;
  cursor: pointer;
  font-size: 0.95rem;
  padding: 0;
}

.badge {
  font-size: 0.85rem;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: #dcfce7;
  color: #166534;
}

.slide-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
}

.slide-nav-item {
  min-width: 2rem;
  height: 2rem;
  padding: 0 0.4rem;
  border: 1px solid #d1d5db;
  border-radius: 0.4rem;
  background: white;
  color: #374151;
  font-size: 0.85rem;
  cursor: pointer;
}

.slide-nav-item.recognized {
  border-color: #93c5fd;
  background: #eff6ff;
  color: #1e40af;
}

.slide-nav-item.confirmed {
  border-color: #86efac;
  background: #f0fdf4;
  color: #166534;
}

.slide-nav-item.active {
  border-color: #2563eb;
  background: #2563eb;
  color: white;
}

.review-body {
  display: flex;
  gap: 1.25rem;
  align-items: flex-start;
}

.review-image {
  flex: 1.9;
  min-width: 0;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
}

.image-viewport {
  position: relative;
  height: 68vh;
  overflow: hidden;
}

.image-wrapper {
  position: relative;
  transition: transform 0.25s ease;
}

.review-image img {
  display: block;
  width: 100%;
}

.rect-box {
  position: absolute;
  border: 2px solid #2563eb;
  background: rgba(37, 99, 235, 0.1);
  pointer-events: none;
  transition: background 0.15s, border-color 0.15s;
}

.rect-label {
  position: absolute;
  top: -0.05rem;
  left: -0.05rem;
  transform: translate(-50%, -50%);
  min-width: 1.2rem;
  height: 1.2rem;
  padding: 0 0.2rem;
  border-radius: 999px;
  background: #2563eb;
  color: white;
  font-size: 0.7rem;
  line-height: 1.2rem;
  text-align: center;
}

.rect-box.formula-box {
  border: 2px dashed #7c3aed;
  background: rgba(124, 58, 237, 0.08);
}

.rect-box.formula-box.flagged {
  border: 2px dashed #d97706;
  background: rgba(217, 119, 6, 0.12);
}

.rect-box.draft {
  border: 2px dashed #16a34a;
  background: rgba(22, 163, 74, 0.15);
}

.formula-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.85rem;
  color: #374151;
  border-bottom: 1px solid #e5e7eb;
  cursor: pointer;
}

.adjust-banner {
  margin: 0;
  padding: 0.5rem 0.75rem;
  background: #ecfdf5;
  color: #166534;
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  border-bottom: 1px solid #e5e7eb;
}

.cancel-adjust-button {
  border: none;
  background: none;
  color: #2563eb;
  cursor: pointer;
  font-size: 0.85rem;
  padding: 0;
}

.image-wrapper.adjusting {
  cursor: crosshair;
}

.review-contents {
  flex: 1;
  min-width: 0;
  max-height: 68vh;
  overflow-y: auto;
  padding-right: 0.25rem;
  display: flex;
  flex-direction: column;
}

.problem-card {
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  padding: 0.75rem;
}

.problem-card-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.5rem;
}

.problem-title {
  font-weight: 600;
  font-size: 0.9rem;
}

.status-tag {
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: #e5e7eb;
  color: #374151;
}

.status-tag.recognized {
  background: #dbeafe;
  color: #1e40af;
}

.status-tag.confirmed {
  background: #dcfce7;
  color: #166534;
}

.content-row {
  margin-bottom: 0.5rem;
  padding: 0.4rem;
  border-radius: 0.4rem;
  border: 1px solid transparent;
}

.content-row.needs-review {
  border-color: #fbbf24;
  background: #fffbeb;
}

.content-row-main {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: flex-start;
}

.content-row-tools {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.35rem;
  padding-left: calc(1.6rem + 4.2rem + 1rem);
}

.review-flag {
  font-size: 0.78rem;
  color: #92400e;
}

.adjust-button,
.delete-button {
  font-size: 0.78rem;
  padding: 0.25rem 0.55rem;
  border: 1px solid #d1d5db;
  border-radius: 0.35rem;
  background: white;
  color: #374151;
  cursor: pointer;
}

.adjust-button:disabled {
  color: #9ca3af;
  cursor: not-allowed;
}

.delete-button {
  color: #b91c1c;
  border-color: #fecaca;
}

.order-buttons {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  width: 1.6rem;
}

.order-button {
  font-size: 0.6rem;
  line-height: 1;
  padding: 0.15rem 0;
  border: 1px solid #d1d5db;
  border-radius: 0.3rem;
  background: white;
  color: #374151;
  cursor: pointer;
}

.order-button:disabled {
  color: #d1d5db;
  cursor: not-allowed;
}

.type-tag {
  flex-shrink: 0;
  width: 4.2rem;
  text-align: center;
  font-size: 0.75rem;
  padding: 0.3rem 0.3rem;
  border-radius: 0.4rem;
  background: #f3f4f6;
  color: #374151;
}

.type-tag.formula {
  background: #fef3c7;
  color: #92400e;
}

.type-tag.text {
  background: #e0e7ff;
  color: #3730a3;
}

.content-input {
  flex: 1;
  min-width: 0;
  font-family: inherit;
  font-size: 0.9rem;
  padding: 0.4rem 0.55rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  resize: vertical;
}

.math-field-input {
  flex: 1;
  min-width: 8rem;
  min-height: 6rem;
  padding: 0.6rem 0.8rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  background: white;
  font-size: 1.15rem;
}

.math-field-input:focus {
  border-color: #2563eb;
  outline: none;
}

.latex-error {
  color: #dc2626;
  font-size: 0.8rem;
}

.slide-controls {
  position: fixed;
  left: 50%;
  bottom: 1.25rem;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
  z-index: 20;
}

.slide-button {
  font-size: 0.85rem;
  padding: 0.35rem 0.75rem;
  border: 1px solid #d1d5db;
  border-radius: 0.4rem;
  background: white;
  color: #374151;
  cursor: pointer;
}

.slide-button:disabled {
  color: #d1d5db;
  cursor: not-allowed;
}

.slide-counter {
  font-size: 0.85rem;
  color: #6b7280;
}

.review-actions {
  margin-top: 1rem;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 1rem;
}

.confirm-button {
  padding: 0.6rem 1.1rem;
  border: none;
  border-radius: 0.5rem;
  background: #16a34a;
  color: white;
  cursor: pointer;
}

.confirm-button:disabled {
  background: #86efac;
  cursor: not-allowed;
}

.saved-note {
  color: #166534;
  font-size: 0.9rem;
}

.info {
  color: #6b7280;
}

.info.small {
  font-size: 0.85rem;
  margin: 0;
}
</style>
