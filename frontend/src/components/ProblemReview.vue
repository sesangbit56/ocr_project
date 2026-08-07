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

        <div class="review-body">
          <div class="review-panel review-image">
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

          <div class="review-panel review-contents" ref="reviewContentsEl" @focusin="onContentFocusIn">
            <div v-if="currentProblem" class="problem-card">
              <div class="problem-card-sticky">
                <div class="problem-card-header">
                  <span class="problem-title">Problem {{ currentIndex + 1 }}</span>
                  <span class="status-tag" :class="currentProblem.status">{{ currentProblem.status }}</span>
                  <div class="problem-header-actions">
                    <button
                      class="reset-button"
                      :disabled="!currentProblem.has_initial_snapshot || revertingContent"
                      :title="currentProblem.has_initial_snapshot
                        ? 'Discard all edits and restore this problem\'s original OCR result (no re-recognition)'
                        : 'No initial OCR snapshot recorded for this problem yet'"
                      @click="revertToInitialSnapshot(currentProblem)"
                    >{{ revertingContent ? 'Reverting...' : 'Revert to initial OCR result' }}</button>
                    <button
                      class="reset-button"
                      :disabled="resettingContent || currentProblem.status === 'pending'"
                      title="Discard all edits and re-run OCR on this problem's original crop"
                      @click="resetToInitialRecognition(currentProblem)"
                    >{{ resettingContent || currentProblem.status === 'pending' ? 'Re-running...' : 'Re-run OCR' }}</button>
                  </div>
                </div>

                <div class="add-content-toolbar">
                  <span class="add-content-label">Add a block:</span>
                  <button class="add-button" :disabled="addingContent" @click="addContent('formula')">+ Formula</button>
                  <button class="add-button" :disabled="addingContent" @click="addContent('text')">+ Text</button>
                  <button class="add-button" :disabled="addingContent" @click="addContent('choice')">+ Choice</button>
                  <button class="add-button" :disabled="addingContent" @click="addContent('image')">+ Image</button>
                </div>

                <div v-if="topLevelContents.length > 0" class="group-toolbar">
                  <select v-model="groupLabelPreset" class="group-label-select">
                    <option v-for="preset in GROUP_LABEL_PRESETS" :key="preset" :value="preset">{{ preset }}</option>
                    <option value="other">Other</option>
                  </select>
                  <input
                    v-if="groupLabelPreset === 'other'"
                    v-model="groupLabelCustom"
                    type="text"
                    class="group-label-input"
                    placeholder="Group label (e.g. 보기)"
                  />
                  <button
                    class="group-button"
                    :disabled="selectedContentIds.size === 0 || !effectiveGroupLabel || grouping"
                    @click="groupSelected"
                  >
                    Group selected ({{ selectedContentIds.size }})
                  </button>
                </div>
              </div>

              <p v-if="currentProblem.contents.length === 0" class="info small">
                {{ currentProblem.status === 'pending' ? 'Recognizing...' : 'Not recognized yet.' }}
              </p>

              <template v-for="(item, index) in topLevelContents" :key="item.id">
                <template v-if="!isChoicesGroup(item)">
                  <div v-if="item.type === 'group'" class="content-group">
                    <div class="content-group-header">
                      <div class="group-order-buttons">
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
                      v-for="child in childrenOf(item.id)"
                      :key="child.id"
                      :content="child"
                      :reordering="reordering"
                      :flagged="isFlagged(child)"
                      :adjusting-content-id="adjustingContentId"
                      :submitting-region="submittingRegion"
                      :drag-over="dragOverId === child.id"
                      :changing-type="changingType"
                      show-line-break-toggle
                      @delete="deleteContent(currentProblem, child)"
                      @adjust-region="startAdjust(currentProblem, child)"
                      @type-change="onTypeChange(currentProblem, child, $event)"
                      @drag-start="onDragStart(child, $event)"
                      @drag-over="onDragOver(child)"
                      @drag-leave="onDragLeave(child)"
                      @drop="onDrop(child, childrenOf(item.id))"
                      @drag-end="onDragEnd"
                    />
                  </div>
                  <div
                    v-else
                    class="content-row-wrapper"
                    :class="{ selected: selectedContentIds.has(item.id) }"
                    @click="onRowClick(item.id, $event)"
                  >
                    <input
                      type="checkbox"
                      class="select-checkbox"
                      :checked="selectedContentIds.has(item.id)"
                      @change="toggleSelected(item.id)"
                    />
                    <ContentRow
                      :content="item"
                      :reordering="reordering"
                      :flagged="isFlagged(item)"
                      :adjusting-content-id="adjustingContentId"
                      :submitting-region="submittingRegion"
                      :drag-over="dragOverId === item.id"
                      :changing-type="changingType"
                      @delete="deleteContent(currentProblem, item)"
                      @adjust-region="startAdjust(currentProblem, item)"
                      @type-change="onTypeChange(currentProblem, item, $event)"
                      @drag-start="onDragStart(item, $event)"
                      @drag-over="onDragOver(item)"
                      @drag-leave="onDragLeave(item)"
                      @drop="onDrop(item, topLevelContents)"
                      @drag-end="onDragEnd"
                    />
                  </div>
                </template>
              </template>
            </div>

            <div v-if="choicesGroup" class="choices-block">
              <div class="choices-block-header">
                <span class="choices-block-label">{{ choicesGroup.label }}</span>
              </div>
              <div class="choices-grid">
                <ContentRow
                  v-for="child in sortedChoiceChildren"
                  :key="child.id"
                  :content="child"
                  compact
                  :sortable="false"
                  :flagged="isFlagged(child)"
                  :adjusting-content-id="adjustingContentId"
                  :submitting-region="submittingRegion"
                  :changing-type="changingType"
                  @delete="deleteContent(currentProblem, child)"
                  @adjust-region="startAdjust(currentProblem, child)"
                  @type-change="onTypeChange(currentProblem, child, $event)"
                />
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

          <div class="review-panel print-preview-box">
            <div class="print-preview-box-header">Preview</div>
            <ProblemPrintout v-if="currentProblem" :problem="currentProblem" />
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
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'
// Registers the <math-field> custom element used below - an experiment to
// let the LaTeX source be edited directly through its rendered form,
// instead of a raw-text textarea next to a read-only KaTeX preview.
import 'mathlive'
import ContentRow from './ContentRow.vue'
import ProblemPrintout from './ProblemPrintout.vue'

const props = defineProps({
  pageId: {
    type: String,
    required: true,
  },
})
const emit = defineEmits(['back', 'guide', 'switch-page', 'done'])

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
  groupLabelPreset.value = 'Choices'
  groupLabelCustom.value = ''
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

// Rendered as its own compact block below the problem card rather than
// inline with the rest of the content tree (see the template) - option
// rows read better as a small side-by-side grid than as full-width rows.
const choicesGroup = computed(() => topLevelContents.value.find(isChoicesGroup) || null)

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

// Choice tiles order themselves by their picked label (①-⑤) rather than by
// drag position - there's exactly one correct order for a set of answer
// options, so deriving it from the label removes a step instead of asking
// the reviewer to also drag each one into place after assigning it.
// Unlabeled/unrecognized labels sort after the labeled ones, stably.
const CHOICE_LABEL_ORDER = ['①', '②', '③', '④', '⑤']
const choiceLabelRank = (label) => {
  const i = CHOICE_LABEL_ORDER.indexOf(label)
  return i === -1 ? CHOICE_LABEL_ORDER.length : i
}
const sortedChoiceChildren = computed(() => {
  if (!choicesGroup.value) return []
  return childrenOf(choicesGroup.value.id).sort((a, b) => choiceLabelRank(a.label) - choiceLabelRank(b.label))
})

// content/label edits live only in local reactive state until Confirm is
// clicked (type is not - see onTypeChange). Several actions (reorder,
// delete, group, ungroup, polling) replace problem.contents wholesale with
// a fresh server response afterward, which would silently discard those
// edits on every other row unless carried forward here.
//
// Exception: content specifically (not label) defers to the server while a
// row is processing - it's a placeholder the reviewer can't have
// meaningfully hand-edited yet, and preserving it would overwrite a
// background OCR result with the stale placeholder the moment it finishes.
// Label has nothing to do with that job, so it's kept local either way -
// picking a choice's label while it's still recognizing shouldn't get
// silently reverted by the next poll.
const withLocalEdits = (problem, serverContents) => {
  const localById = new Map(problem.contents.map((c) => [c.id, c]))
  return serverContents.map((c) => {
    const local = localById.get(c.id)
    if (!local) return c
    return {
      ...c,
      content: local.processing ? c.content : local.content,
      label: local.label,
      display_mode: local.display_mode,
      line_break_before: local.line_break_before,
    }
  })
}

const commitOrder = async (problem, parentContentId, reordered) => {
  reordering.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/contents/order`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content_ids: reordered.map((c) => c.id),
        parent_content_id: parentContentId,
      }),
    })
    const updated = await response.json()
    problem.contents = withLocalEdits(problem, updated.contents)
  } finally {
    reordering.value = false
  }
}

const moveContent = async (problem, siblings, index, direction) => {
  const targetIndex = index + direction
  if (targetIndex < 0 || targetIndex >= siblings.length) return

  const reordered = siblings.slice()
  const [moved] = reordered.splice(index, 1)
  reordered.splice(targetIndex, 0, moved)

  await commitOrder(problem, siblings[index].parent_content_id || null, reordered)
}

// Drag-and-drop reordering for content rows (top-level items and a group's
// children each drag within their own sibling list - dragOverId/draggingId
// are global, but a drop only takes effect if the source id is actually
// found in the target's sibling array, which naturally keeps drags scoped
// to the list they started in).
const draggingId = ref(null)
const dragOverId = ref(null)
const reviewContentsEl = ref(null)

// The content list scrolls internally (max-height + overflow-y), so once a
// dragged row nears its top/bottom edge the list needs to auto-scroll -
// native HTML5 drag doesn't do this for inner scroll containers on its own,
// only for the page itself. Tracked via a plain (non-reactive) rAF loop
// rather than refs since it needs to run every frame regardless of Vue's
// reactivity, and is torn down on dragend so it never outlives the drag.
const AUTO_SCROLL_EDGE = 60
const AUTO_SCROLL_MAX_SPEED = 14
let autoScrollDelta = 0
let autoScrollRaf = null

const runAutoScroll = () => {
  if (!autoScrollDelta || !reviewContentsEl.value) {
    autoScrollRaf = null
    return
  }
  reviewContentsEl.value.scrollTop += autoScrollDelta
  autoScrollRaf = requestAnimationFrame(runAutoScroll)
}

// Listens on window rather than the list itself so the tracked position
// stays accurate even while the pointer passes over the image panel or a
// gap between rows - dragover still bubbles there regardless of whether
// anything called preventDefault on it.
const onWindowDragOver = (event) => {
  const el = reviewContentsEl.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const y = event.clientY
  // Clamped to 0 rather than gated on >= 0: overshooting past an edge
  // (easy to do while dragging fast, especially past the top edge which
  // sits right under the sticky header) should keep scrolling at max
  // speed, not cancel it the instant the cursor leaves the container.
  if (y < rect.top + AUTO_SCROLL_EDGE) {
    const dist = Math.max(y - rect.top, 0)
    autoScrollDelta = -AUTO_SCROLL_MAX_SPEED * (1 - dist / AUTO_SCROLL_EDGE)
  } else if (y > rect.bottom - AUTO_SCROLL_EDGE) {
    const dist = Math.max(rect.bottom - y, 0)
    autoScrollDelta = AUTO_SCROLL_MAX_SPEED * (1 - dist / AUTO_SCROLL_EDGE)
  } else {
    autoScrollDelta = 0
  }
  if (autoScrollDelta && !autoScrollRaf) {
    autoScrollRaf = requestAnimationFrame(runAutoScroll)
  }
}

const stopAutoScroll = () => {
  autoScrollDelta = 0
  if (autoScrollRaf) {
    cancelAnimationFrame(autoScrollRaf)
    autoScrollRaf = null
  }
  window.removeEventListener('dragover', onWindowDragOver)
}

const onDragStart = (item, event) => {
  draggingId.value = item.id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', item.id)
  const row = event.target.closest('.content-row')
  if (row) event.dataTransfer.setDragImage(row, 16, 16)
  window.addEventListener('dragover', onWindowDragOver)
}

const onDragOver = (item) => {
  if (!draggingId.value || draggingId.value === item.id) return
  dragOverId.value = item.id
}

const onDragLeave = (item) => {
  if (dragOverId.value === item.id) dragOverId.value = null
}

const onDrop = async (targetItem, siblings) => {
  const sourceId = draggingId.value
  dragOverId.value = null
  draggingId.value = null
  if (!sourceId || sourceId === targetItem.id) return

  const sourceIndex = siblings.findIndex((c) => c.id === sourceId)
  const targetIndex = siblings.findIndex((c) => c.id === targetItem.id)
  if (sourceIndex === -1 || targetIndex === -1) return

  const reordered = siblings.slice()
  const [moved] = reordered.splice(sourceIndex, 1)
  reordered.splice(targetIndex, 0, moved)

  await commitOrder(currentProblem.value, siblings[sourceIndex].parent_content_id || null, reordered)
}

const onDragEnd = () => {
  draggingId.value = null
  dragOverId.value = null
  stopAutoScroll()
}

const deleteContent = async (problem, content) => {
  const response = await fetch(`/api/problem_contents/${content.id}`, { method: 'DELETE' })
  const updated = await response.json()
  problem.contents = withLocalEdits(problem, updated.contents)
}

// Two distinct escape hatches for "I broke this problem's edits":
// - revertToInitialSnapshot restores the exact result of the first
//   successful recognition, replayed from a stored snapshot - no OCR model
//   call, so it's fast and always reproduces exactly what was there
//   originally, even if re-running OCR today would (for whatever reason)
//   come out slightly different.
// - resetToInitialRecognition actually re-runs OCR on the saved crop. Kept
//   as a separate, still-useful action (e.g. after adjusting a region) -
//   distinct from the revert button above, not a replacement for it.
// Both deliberately skip withLocalEdits: the whole point is to discard
// local edits, not merge them back in over the fresh/restored result.
const revertingContent = ref(false)
const revertToInitialSnapshot = async (problem) => {
  if (!problem || !problem.has_initial_snapshot || revertingContent.value) return
  const ok = window.confirm(
    "Discard all edits to this problem and revert it to its original OCR result?"
  )
  if (!ok) return
  revertingContent.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/revert`, { method: 'POST' })
    const updated = await response.json()
    problem.contents = updated.contents
    problem.status = updated.status
  } finally {
    revertingContent.value = false
  }
}

// Recognition now runs on the shared background OCR worker (see
// /recognize in app.py) instead of inline in this request - the model
// call can take minutes, and blocking on it here meant a reviewer who
// navigated to a different problem while waiting had no way to tell
// "problem X is still being re-recognized" from "stuck", since the
// disabled/"Re-running..." state was only ever local to this one
// function call. The POST now just queues the job and comes back with
// status: 'pending' right away; the button's own disabled state reads
// problem.status directly (see the template) so it stays correct across
// navigation, and the existing stillRecognizing/startPollingIfNeeded
// polling loop (already used for the initial per-page recognition pass)
// picks up problem.contents once the worker actually finishes.
const resettingContent = ref(false)
const resetToInitialRecognition = async (problem) => {
  if (!problem || resettingContent.value || problem.status === 'pending') return
  const ok = window.confirm(
    'Discard all edits to this problem and re-run OCR on its original crop?'
  )
  if (!ok) return
  resettingContent.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/recognize`, { method: 'POST' })
    const updated = await response.json()
    problem.status = updated.status
    startPollingIfNeeded()
  } finally {
    resettingContent.value = false
  }
}

// Switching a row to/from type="choice" has to move it in or out of the
// Choices group too (the compact choices block renders that group's
// children as-is, whatever their type), so unlike content/label edits this
// isn't a local-only change deferred to Confirm - it goes straight to the
// server, which owns the reparenting, and the response replaces
// problem.contents outright so the row's new group membership is reflected
// immediately.
const changingType = ref(false)

const onTypeChange = async (problem, content, newType) => {
  if (content.type === newType) return
  changingType.value = true
  try {
    const response = await fetch(`/api/problem_contents/${content.id}/type`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: newType }),
    })
    const updated = await response.json()
    problem.contents = withLocalEdits(problem, updated.contents)
  } finally {
    changingType.value = false
  }
}

// Manual grouping: boxed sections like <보기> or a (가)/(나) condition list
// have no detection signal in the current PDF-text-based pipeline (the box
// border is a vector line, invisible to it), so a reviewer marks one by
// selecting the rows that belong inside it.
const selectedContentIds = ref(new Set())
const GROUP_LABEL_PRESETS = ['Choices', '보기']
const groupLabelPreset = ref('Choices')
const groupLabelCustom = ref('')
const effectiveGroupLabel = computed(() =>
  groupLabelPreset.value === 'other' ? groupLabelCustom.value.trim() : groupLabelPreset.value
)
const grouping = ref(false)

const toggleSelected = (id) => {
  const next = new Set(selectedContentIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedContentIds.value = next
}

// Lets clicking anywhere on a row's background toggle its selection (the
// checkbox alone is a tiny target), while clicks meant to edit or act on
// the row's own controls - the label select, content textarea/math-field,
// adjust/delete/order buttons - pass through untouched.
const ROW_CLICK_IGNORE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'MATH-FIELD'])
const onRowClick = (id, event) => {
  if (ROW_CLICK_IGNORE_TAGS.has(event.target.tagName)) return
  if (event.target.closest('math-field') || event.target.closest('.drag-handle')) return
  toggleSelected(id)
}

const groupSelected = async () => {
  const problem = currentProblem.value
  const label = effectiveGroupLabel.value
  if (!problem || selectedContentIds.value.size === 0 || !label) return

  grouping.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/contents/group`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, content_ids: Array.from(selectedContentIds.value) }),
    })
    const updated = await response.json()
    problem.contents = withLocalEdits(problem, updated.contents)
    selectedContentIds.value = new Set()
    groupLabelPreset.value = 'Choices'
    groupLabelCustom.value = ''
  } finally {
    grouping.value = false
  }
}

const ungroupContent = async (group) => {
  const problem = currentProblem.value
  if (!problem) return
  const response = await fetch(`/api/problem_contents/${group.id}/group`, { method: 'DELETE' })
  const updated = await response.json()
  problem.contents = withLocalEdits(problem, updated.contents)
}

// Tracks which row the reviewer was last working in (by content field
// focus, not click - clicking a row background toggles its group-selection
// checkbox, which is a different concern), so a new block lands right
// where they're currently looking instead of always at the bottom of the
// list. Anchored to the row's top-level ancestor: a block inside a group
// (incl. the compact choices grid) can't itself be the insertion point,
// since new blocks are always created top-level - "after the group" is
// the closest equivalent.
const lastFocusedContentId = ref(null)
const onContentFocusIn = (event) => {
  if (!event.target.classList?.contains('row-content-field')) return
  const row = event.target.closest('.content-row')
  const id = row?.dataset.contentId
  if (!id) return
  const content = currentProblem.value?.contents.find((c) => c.id === id)
  if (!content) return
  lastFocusedContentId.value = content.parent_content_id || content.id
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
  // Only valid if it still resolves to a top-level row in this problem -
  // e.g. not stale from a different problem, and not a row that was since
  // deleted or absorbed into a group.
  const afterContentId = problem.contents.some(
    (c) => c.id === lastFocusedContentId.value && !c.parent_content_id
  )
    ? lastFocusedContentId.value
    : null

  addingContent.value = true
  try {
    const response = await fetch(`/api/problems/${problem.id}/contents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, after_content_id: afterContentId }),
    })
    const updated = await response.json()
    problem.contents = withLocalEdits(problem, updated.contents)
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
      problem.contents = withLocalEdits(problem, updated.contents)
    }
    // Formula/choice regions run OCR in the background (see /region) and
    // come back with processing: true - resume polling so the result gets
    // picked up once it's done, same as initial recognition.
    startPollingIfNeeded()
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
  () =>
    !!data.value &&
    data.value.problems.some((p) => p.status === 'pending' || p.contents.some((c) => c.processing))
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

// Set right before an auto-advance to the next page (see the pageId watch
// below) and consumed here - scrolling has to wait for the image to
// actually finish loading, not just for Vue to patch the DOM, since the
// image panel's height (and everything's position below the app header)
// isn't settled until then. Scrolling any earlier reliably undershoots,
// because the browser computes the target scroll offset from a layout
// that's about to shift once the image loads in.
const scrollToTopOnNextImageLoad = ref(false)

const onImageLoad = () => {
  updateScale()
  if (scrollToTopOnNextImageLoad.value) {
    scrollToTopOnNextImageLoad.value = false
    // updateScale's write to scale.value re-renders the image transform,
    // which still shifts the panel's height after this point - wait for
    // that re-render to flush before measuring where to scroll to.
    nextTick(() => {
      document.querySelector('.review-body')?.scrollIntoView({ block: 'start', behavior: 'auto' })
    })
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
      const fresh = await response.json()
      // Merged in per-problem via withLocalEdits rather than replacing
      // data.value outright - this poll now also runs while a reviewer is
      // actively editing other rows (background region-adjust OCR), not
      // just during the initial pre-review recognition pass, so a raw
      // replace would wipe out whatever they're mid-typing elsewhere.
      if (data.value) {
        const freshById = new Map(fresh.problems.map((p) => [p.id, p]))
        for (const problem of data.value.problems) {
          const freshProblem = freshById.get(problem.id)
          if (!freshProblem) continue
          problem.status = freshProblem.status
          problem.contents = withLocalEdits(problem, freshProblem.contents)
        }
      } else {
        data.value = fresh
      }
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
        contents: p.contents.map((c) => ({
          id: c.id,
          content: c.content,
          label: c.label,
          display_mode: c.display_mode,
          line_break_before: c.line_break_before,
        })),
      })),
    }
    const response = await fetch(`/api/pages/${props.pageId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    data.value = await response.json()
    justConfirmed.value = true
    await goToNextUnreviewedPage()
  } finally {
    saving.value = false
  }
}

// After confirming, jump straight to another page in the same document
// that still has something to review - mirrors PageViewer's "next
// incomplete page" behavior for the selection step. Only pages whose
// selection is already completed are candidates (an unselected page has no
// problems yet, so there's nothing to review there). Goes back to the
// document list when nothing else is left.
const goToNextUnreviewedPage = async () => {
  const documentId = data.value?.document_id
  if (!documentId) return
  const response = await fetch(`/api/documents/${documentId}`)
  const document = await response.json()
  const pages = document.pages || []
  const isCandidate = (p) => p.id !== props.pageId && p.status === 'completed' && !p.reviewed
  const currentIndex = pages.findIndex((p) => p.id === props.pageId)
  const nextPage =
    (currentIndex >= 0 ? pages.slice(currentIndex + 1).find(isCandidate) : undefined) ||
    pages.find(isCandidate)
  if (nextPage) {
    emit('switch-page', nextPage.id)
  } else {
    emit('done')
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
  stopAutoScroll()
})
// pageId only ever changes while this component stays mounted via the
// auto-advance-to-next-page flow in goToNextUnreviewedPage (switch-page is
// emitted nowhere else) - arm the post-image-load scroll (see
// scrollToTopOnNextImageLoad/onImageLoad above) so the app header/
// breadcrumb/page-picker chip row scroll out of view and the reviewer
// lands straight on the 3-panel review area, instead of having to
// manually scroll past the same header again for every page.
watch(() => props.pageId, () => {
  scrollToTopOnNextImageLoad.value = true
  fetchReview()
})
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
  gap: 0.75rem;
  /* Each .review-panel caps its own height at 68vh and scrolls
     internally, so without this the page's total height barely exceeds
     one viewport - nowhere near enough scroll room for the auto-advance
     scroll (see the pageId watch/onImageLoad below) to actually bring
     review-body's top up to the viewport's top and scroll the header out
     of view. Guaranteeing a full viewport of height here (panels stay
     capped at 68vh via max-height, which always wins over a stretched
     height) is what makes that scroll distance available at all. */
  min-height: 100vh;
}

/* Equal thirds - image | contents | preview, all visible at once. */
.review-panel {
  flex: 1 1 0;
  min-width: 0;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
}

.review-image {
  flex-grow: 1.4;
  display: flex;
  flex-direction: column;
  max-height: 68vh;
  overflow-y: auto;
}

.image-viewport {
  position: relative;
  /* Fills whatever's left under the toggle/adjust-banner, so the whole
     panel's total height matches review-contents/print-preview-box's own
     max-height: 68vh instead of adding a flat 68vh on top of them. */
  flex: 1;
  min-height: 0;
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

.choices-block {
  flex-shrink: 0;
  margin-top: 0.6rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  padding: 0.6rem 0.75rem;
}

.choices-block-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.choices-block-label {
  flex: 1;
  font-weight: 600;
  font-size: 0.85rem;
  color: #374151;
}

.choices-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

/* Bleeds out to the card's edges (cancelling the card's own padding) so the
   sticky background covers the full width when it's pinned above scrolled
   rows, instead of leaving the card's side padding transparent. */
.problem-card-sticky {
  position: sticky;
  top: 0;
  z-index: 5;
  background: white;
  margin: -0.75rem -0.75rem 0;
  padding: 0.75rem 0.75rem 0;
  border-radius: 0.5rem 0.5rem 0 0;
  border-bottom: 1px solid #e5e7eb;
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

.problem-header-actions {
  margin-left: auto;
  display: flex;
  gap: 0.4rem;
}

.reset-button {
  font-size: 0.75rem;
  padding: 0.2rem 0.5rem;
  border: 1px solid #fca5a5;
  border-radius: 0.35rem;
  background: white;
  color: #b91c1c;
  cursor: pointer;
}

.reset-button:disabled {
  opacity: 0.6;
  cursor: default;
}

.print-preview-box {
  flex-grow: 0.7;
  max-height: 68vh;
  overflow-y: auto;
  padding: 0.75rem 1rem;
}

.print-preview-box-header {
  font-weight: 600;
  font-size: 0.85rem;
  color: #374151;
  margin-bottom: 0.5rem;
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
}

.group-label-select {
  font-size: 0.85rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  cursor: pointer;
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
  padding: 0.2rem;
  border-radius: 0.4rem;
  cursor: pointer;
}

.content-row-wrapper:hover {
  background: #f3f4f6;
}

.content-row-wrapper.selected {
  background: #dbeafe;
}

.select-checkbox {
  margin-top: 0.9rem;
  flex-shrink: 0;
  width: 1.1rem;
  height: 1.1rem;
  cursor: pointer;
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
