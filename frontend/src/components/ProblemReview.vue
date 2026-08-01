<template>
  <div class="problem-review">
    <div class="review-header">
      <button class="back-button" @click="$emit('back')">&larr; Back to page</button>
      <h2 v-if="data">Review problems &mdash; Page {{ data.page_number }}</h2>
      <span v-if="allConfirmed" class="badge">All reviewed</span>
      <button class="guide-link" @click="$emit('guide')">사용법</button>
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

        <div class="review-body" ref="reviewBodyEl" :class="{ resizing: resizingPanels }">
          <div class="review-image" :style="{ flexBasis: imagePanelRatio * 100 + '%' }">
            <label class="formula-toggle">
              <input type="checkbox" v-model="showAllFormulas" />
              Show all recognized regions
            </label>
            <p v-if="adjustingContentId" class="adjust-banner">
              <template v-if="submittingRegion">Saving new region&hellip;</template>
              <template v-else>
                Drag a box around the correct region for this item.
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
                  :class="{ flagged: box.flagged, 'image-box': box.type === 'image' }"
                  :style="rectStyle(box)"
                ></div>
                <div v-if="draft" class="rect-box draft" :style="rectStyle(draft)"></div>
              </div>
            </div>
          </div>

          <div class="panel-resize-handle" @mousedown="startPanelResize"></div>

          <div class="review-contents">
            <div v-if="currentProblem" class="problem-card">
              <div class="problem-card-header">
                <span class="problem-title">Problem {{ currentIndex + 1 }}</span>
                <span class="status-tag" :class="currentProblem.status">{{ currentProblem.status }}</span>
              </div>

              <p v-if="currentProblem.contents.length === 0" class="info small">
                {{ currentProblem.status === 'pending' ? 'Recognizing...' : 'Not recognized yet.' }}
              </p>

              <div class="add-content-toolbar">
                <span class="add-content-label">Add a block:</span>
                <button class="add-button" :disabled="addingContent" @click="addContent('formula')">+ Formula</button>
                <button class="add-button" :disabled="addingContent" @click="addContent('text')">+ Text</button>
                <button class="add-button" :disabled="addingContent" @click="addContent('choice')">+ Choice</button>
                <button class="add-button" :disabled="addingContent" @click="addContent('image')">+ Image</button>
              </div>

              <div v-if="topLevelContents.length > 0" class="group-toolbar">
                <input
                  v-model="groupLabelInput"
                  type="text"
                  class="group-label-input"
                  placeholder="Group label (e.g. 보기)"
                />
                <button
                  class="group-button"
                  :disabled="selectedContentIds.size === 0 || !groupLabelInput.trim() || grouping"
                  @click="groupSelected"
                >
                  Group selected ({{ selectedContentIds.size }})
                </button>
              </div>

              <template v-for="(item, index) in topLevelContents" :key="item.id">
                <div v-if="item.type === 'group'" class="content-group">
                  <div class="content-group-header">
                    <div v-if="!isChoicesGroup(item)" class="group-order-buttons">
                      <button
                        class="order-button"
                        :disabled="index === 0 || reordering"
                        title="Move up"
                        @click="moveContent(currentProblem, topLevelContents, index, -1)"
                      >&#9650;</button>
                      <button
                        class="order-button"
                        :disabled="index >= topLevelOrderableCount - 1 || reordering"
                        title="Move down"
                        @click="moveContent(currentProblem, topLevelContents, index, 1)"
                      >&#9660;</button>
                    </div>
                    <span class="content-group-label">{{ item.label }}</span>
                    <button class="ungroup-button" @click="ungroupContent(item)">Ungroup</button>
                  </div>
                  <ContentRow
                    v-for="(child, cIndex) in childrenOf(item.id)"
                    :key="child.id"
                    :content="child"
                    :index="cIndex"
                    :sibling-count="childrenOf(item.id).length"
                    :reordering="reordering"
                    :flagged="isFlagged(child)"
                    :adjusting-content-id="adjustingContentId"
                    :submitting-region="submittingRegion"
                    @move="(direction) => moveContent(currentProblem, childrenOf(item.id), cIndex, direction)"
                    @delete="deleteContent(currentProblem, child)"
                    @adjust-region="startAdjust(currentProblem, child)"
                  />
                </div>
                <div v-else class="content-row-wrapper">
                  <input
                    type="checkbox"
                    class="select-checkbox"
                    :checked="selectedContentIds.has(item.id)"
                    @change="toggleSelected(item.id)"
                  />
                  <ContentRow
                    :content="item"
                    :index="index"
                    :sibling-count="topLevelOrderableCount"
                    :reordering="reordering"
                    :flagged="isFlagged(item)"
                    :adjusting-content-id="adjustingContentId"
                    :submitting-region="submittingRegion"
                    @move="(direction) => moveContent(currentProblem, topLevelContents, index, direction)"
                    @delete="deleteContent(currentProblem, item)"
                    @adjust-region="startAdjust(currentProblem, item)"
                  />
                </div>
              </template>
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
import ContentRow from './ContentRow.vue'

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
// draft box pointed at a problem the user can no longer see. Same for a
// pending group selection - it shouldn't carry over onto the next problem.
watch(currentIndex, () => {
  cancelAdjust()
  selectedContentIds.value = new Set()
  groupLabelInput.value = ''
})

// Content forms a two-level tree (top-level rows, plus a type="group" row's
// children) via parent_content_id, not true nesting in the API response -
// these derive that view from the flat problem.contents list.
const topLevelContents = computed(() => {
  const problem = currentProblem.value
  if (!problem) return []
  return problem.contents
    .filter((c) => !c.parent_content_id)
    .slice()
    .sort((a, b) => a.order_index - b.order_index)
})

// The auto-created multiple-choice group always sorts last (enforced
// server-side too - see _is_pinned_last), so it isn't part of the
// reorderable sequence: no move buttons of its own, and it's excluded from
// the "how many movable siblings are there" count other items' down-arrows
// use to know when they'd be trying to move past it.
const isChoicesGroup = (item) => item.type === 'group' && item.label === 'Choices'

const topLevelOrderableCount = computed(() => {
  const list = topLevelContents.value
  if (list.length > 0 && isChoicesGroup(list[list.length - 1])) {
    return list.length - 1
  }
  return list.length
})

const childrenOf = (groupId) => {
  const problem = currentProblem.value
  if (!problem) return []
  return problem.contents
    .filter((c) => c.parent_content_id === groupId)
    .slice()
    .sort((a, b) => a.order_index - b.order_index)
}

const moveContent = async (problem, siblings, index, direction) => {
  const targetIndex = index + direction
  if (targetIndex < 0 || targetIndex >= siblings.length) return

  const reordered = siblings.slice()
  const [moved] = reordered.splice(index, 1)
  reordered.splice(targetIndex, 0, moved)

  reordering.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/contents/order`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content_ids: reordered.map((c) => c.id),
        parent_content_id: siblings[index].parent_content_id || null,
      }),
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

// Manual grouping: boxed sections like <보기> or a (가)/(나) condition list
// have no detection signal in the current PDF-text-based pipeline (the box
// border is a vector line, invisible to it), so a reviewer marks one by
// selecting the rows that belong inside it.
const selectedContentIds = ref(new Set())
const groupLabelInput = ref('')
const grouping = ref(false)

const toggleSelected = (id) => {
  const next = new Set(selectedContentIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedContentIds.value = next
}

const groupSelected = async () => {
  const problem = currentProblem.value
  const label = groupLabelInput.value.trim()
  if (!problem || selectedContentIds.value.size === 0 || !label) return

  grouping.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/contents/group`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, content_ids: Array.from(selectedContentIds.value) }),
    })
    const updated = await response.json()
    problem.contents = updated.contents
    selectedContentIds.value = new Set()
    groupLabelInput.value = ''
  } finally {
    grouping.value = false
  }
}

const ungroupContent = async (group) => {
  const problem = currentProblem.value
  if (!problem) return
  const response = await fetch(`/api/problem_contents/${group.id}/group`, { method: 'DELETE' })
  const updated = await response.json()
  problem.contents = updated.contents
}

// Escape hatch for when clustering merges or splits content wrong (e.g.
// several multiple-choice options recognized as one formula): add an empty
// row and let the reviewer place it by hand. A new formula row goes
// straight into "Adjust region" draw mode since that's the only way to
// give it real content.
const addingContent = ref(false)

const addContent = async (type) => {
  const problem = currentProblem.value
  if (!problem || addingContent.value) return
  const previousIds = new Set(problem.contents.map((c) => c.id))

  addingContent.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/contents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type }),
    })
    const updated = await response.json()
    problem.contents = updated.contents
    if (type === 'formula' || type === 'image' || type === 'choice') {
      const created = updated.contents.find((c) => !previousIds.has(c.id))
      if (created) startAdjust(problem, created)
    }
  } finally {
    addingContent.value = false
  }
}

const showAllFormulas = ref(true)

const formulaBoxes = computed(() => {
  const problem = currentProblem.value
  if (!problem) return []
  const boxes = []
  for (const content of problem.contents) {
    if (content.type !== 'formula' && content.type !== 'image') continue
    const flagged = isFlagged(content)
    if (!showAllFormulas.value && !flagged) continue
    boxes.push({
      id: content.id,
      type: content.type,
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
    // Full problem, not just the one content row - adjusting an image's
    // region can delete other rows it now covers server-side (see the
    // /region endpoint), so the whole contents list needs replacing, not
    // just the one entry we started the request for.
    const problem = data.value.problems.find((p) => p.id === updated.id)
    if (problem) {
      problem.contents = updated.contents
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

// scale is naturalWidth/clientWidth - it depends on how wide the image is
// actually rendered, which changes whenever the image panel resizes (the
// draggable divider, or a window resize), not just on initial load. Every
// box overlay position derives from this, so letting it go stale after a
// resize is exactly what misaligns them from the real image.
const updateScale = () => {
  if (imgEl.value && imgEl.value.clientWidth) {
    scale.value = imgEl.value.naturalWidth / imgEl.value.clientWidth
  }
}

const onImageLoad = () => {
  updateScale()
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

// Draggable divider between the image and results panels. The image panel
// takes this fraction of review-body's width; review-contents fills the
// rest via flex:1. Resizing it changes image-viewport's size, which the
// ResizeObserver below already reacts to for the zoom/crop recalculation -
// no separate wiring needed for the image to keep fitting as you drag.
const reviewBodyEl = ref(null)
const imagePanelRatio = ref(0.66)
const resizingPanels = ref(false)
const PANEL_RATIO_MIN = 0.25
const PANEL_RATIO_MAX = 0.8

const onPanelResizeMove = (event) => {
  const rect = reviewBodyEl.value?.getBoundingClientRect()
  if (!rect || !rect.width) return
  const ratio = (event.clientX - rect.left) / rect.width
  imagePanelRatio.value = Math.min(Math.max(ratio, PANEL_RATIO_MIN), PANEL_RATIO_MAX)
}

const stopPanelResize = () => {
  resizingPanels.value = false
  window.removeEventListener('mousemove', onPanelResizeMove)
  window.removeEventListener('mouseup', stopPanelResize)
}

const startPanelResize = (event) => {
  event.preventDefault()
  resizingPanels.value = true
  window.addEventListener('mousemove', onPanelResizeMove)
  window.addEventListener('mouseup', stopPanelResize)
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
      updateScale()
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
        contents: p.contents.map((c) => ({ id: c.id, content: c.content, label: c.label })),
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
  window.removeEventListener('mousemove', onPanelResizeMove)
  window.removeEventListener('mouseup', stopPanelResize)
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

.guide-link {
  border: 1px solid #d1d5db;
  background: white;
  color: #374151;
  cursor: pointer;
  font-size: 0.85rem;
  padding: 0.3rem 0.65rem;
  border-radius: 0.4rem;
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
  align-items: stretch;
}

.review-body.resizing {
  user-select: none;
}

.review-image {
  flex: 0 0 auto;
  min-width: 200px;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
}

.panel-resize-handle {
  flex: 0 0 1.25rem;
  cursor: col-resize;
  position: relative;
}

.panel-resize-handle::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 4px;
  transform: translateX(-50%);
  border-radius: 999px;
  background: #e5e7eb;
}

.panel-resize-handle:hover::after,
.review-body.resizing .panel-resize-handle::after {
  background: #93c5fd;
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

.review-body.resizing .image-wrapper {
  transition: none;
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

.rect-box.formula-box.image-box {
  border: 2px dashed #db2777;
  background: rgba(219, 39, 119, 0.08);
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

.add-content-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
}

.add-content-label {
  font-size: 0.78rem;
  color: #6b7280;
}

.add-button {
  font-size: 0.8rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid #d1d5db;
  border-radius: 0.35rem;
  background: white;
  color: #374151;
  cursor: pointer;
}

.add-button:disabled {
  color: #9ca3af;
  cursor: not-allowed;
}

.group-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
  padding-bottom: 0.6rem;
  border-bottom: 1px dashed #e5e7eb;
}

.group-label-input {
  font-size: 0.85rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  width: 10rem;
}

.group-button {
  font-size: 0.8rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid #d1d5db;
  border-radius: 0.35rem;
  background: white;
  color: #374151;
  cursor: pointer;
}

.group-button:disabled {
  color: #9ca3af;
  cursor: not-allowed;
}

.content-row-wrapper {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
}

.select-checkbox {
  margin-top: 0.9rem;
  flex-shrink: 0;
}

.content-group {
  margin-bottom: 0.5rem;
  padding: 0.5rem;
  border: 1px dashed #a5b4fc;
  border-radius: 0.5rem;
  background: #eef2ff;
}

.content-group-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}

.content-group-label {
  flex: 1;
  font-weight: 600;
  font-size: 0.85rem;
  color: #3730a3;
}

.group-order-buttons {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  width: 1.6rem;
}

.group-order-buttons .order-button {
  font-size: 0.6rem;
  line-height: 1;
  padding: 0.15rem 0;
  border: 1px solid #c7d2fe;
  border-radius: 0.3rem;
  background: white;
  color: #3730a3;
  cursor: pointer;
}

.group-order-buttons .order-button:disabled {
  color: #c7d2fe;
  cursor: not-allowed;
}

.ungroup-button {
  font-size: 0.75rem;
  padding: 0.2rem 0.5rem;
  border: 1px solid #c7d2fe;
  border-radius: 0.35rem;
  background: white;
  color: #3730a3;
  cursor: pointer;
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
