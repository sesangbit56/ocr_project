<template>
  <div class="page-viewer">
    <div class="viewer-header">
      <button class="back-button" @click="$emit('back')">&larr; Back to list</button>
      <h2>{{ doc?.filename }}</h2>
      <span v-if="doc?.status === 'selection_completed'" class="badge">Selection completed</span>
    </div>

    <div v-if="doc" class="viewer-body">
      <aside class="page-nav">
        <button
          v-for="page in doc.pages"
          :key="page.id"
          class="page-nav-item"
          :class="{ active: page.id === currentPage?.id, completed: page.status === 'completed' }"
          @click="goToPage(page)"
        >
          Page {{ page.page_number }}
          <span v-if="page.status === 'completed'" class="check">&#10003;</span>
        </button>
      </aside>

      <section v-if="currentPage" class="page-content">
        <template v-if="currentPage.status !== 'completed'">
          <div
            class="image-wrapper"
            @mousedown="startDraw"
            @mousemove="onDraw"
            @mouseup="endDraw"
            @mouseleave="cancelDraw"
          >
            <img
              ref="imgEl"
              :src="currentPage.image_url"
              class="page-image"
              draggable="false"
              @load="onImageLoad"
              @dragstart.prevent
            />
            <div
              v-for="(rect, index) in rectangles"
              :key="index"
              class="rect-box"
              :style="rectStyle(rect)"
            >
              <button class="rect-delete" @click.stop="removeRect(index)">&times;</button>
            </div>
            <div v-if="draft" class="rect-box draft" :style="rectStyle(draft)"></div>
          </div>

          <div class="page-actions">
            <p>{{ rectangles.length }} problem(s) marked</p>
            <button class="complete-button" :disabled="submitting" @click="complete">
              {{ submitting ? 'Saving...' : 'Complete' }}
            </button>
          </div>
        </template>

        <div v-else class="page-actions">
          <p class="info">This page's selections are already completed.</p>
          <button class="review-button" @click="$emit('review', currentPage.id)">
            Review problems
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'

const props = defineProps({
  documentId: {
    type: String,
    required: true,
  },
})

const emit = defineEmits(['back', 'changed', 'review'])

const doc = ref(null)
const currentPageId = ref(null)
const rectangles = ref([])
const draft = ref(null)
const startPoint = ref(null)
const drawing = ref(false)
const submitting = ref(false)
const imgEl = ref(null)
const scale = ref(1)

const currentPage = computed(
  () => doc.value?.pages.find((p) => p.id === currentPageId.value) || null
)

const fetchDocument = async () => {
  const response = await fetch(`/api/documents/${props.documentId}`)
  doc.value = await response.json()
  if (!currentPageId.value && doc.value.pages.length > 0) {
    currentPageId.value = doc.value.pages[0].id
  }
}

const goToPage = (page) => {
  currentPageId.value = page.id
  rectangles.value = []
  draft.value = null
}

const onImageLoad = () => {
  if (imgEl.value) {
    scale.value = imgEl.value.naturalWidth / imgEl.value.clientWidth
  }
}

const pointFromEvent = (event) => {
  const bounds = imgEl.value.getBoundingClientRect()
  return {
    x: (event.clientX - bounds.left) * scale.value,
    y: (event.clientY - bounds.top) * scale.value,
  }
}

const startDraw = (event) => {
  if (!currentPage.value || currentPage.value.status === 'completed') return
  event.preventDefault()
  const point = pointFromEvent(event)
  drawing.value = true
  startPoint.value = point
  draft.value = { x: point.x, y: point.y, w: 0, h: 0 }
}

const onDraw = (event) => {
  if (!drawing.value || !startPoint.value) return
  const point = pointFromEvent(event)
  draft.value = {
    x: Math.min(startPoint.value.x, point.x),
    y: Math.min(startPoint.value.y, point.y),
    w: Math.abs(point.x - startPoint.value.x),
    h: Math.abs(point.y - startPoint.value.y),
  }
}

const endDraw = () => {
  if (drawing.value && draft.value && draft.value.w > 5 && draft.value.h > 5) {
    rectangles.value.push({ ...draft.value })
  }
  drawing.value = false
  draft.value = null
  startPoint.value = null
}

const cancelDraw = () => {
  drawing.value = false
  draft.value = null
  startPoint.value = null
}

const removeRect = (index) => {
  rectangles.value.splice(index, 1)
}

const rectStyle = (rect) => {
  const displayScale = 1 / scale.value
  return {
    left: `${rect.x * displayScale}px`,
    top: `${rect.y * displayScale}px`,
    width: `${rect.w * displayScale}px`,
    height: `${rect.h * displayScale}px`,
  }
}

const complete = async () => {
  if (!currentPage.value) return
  submitting.value = true
  try {
    const response = await fetch(`/api/pages/${currentPage.value.id}/problems`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rectangles: rectangles.value }),
    })
    if (!response.ok) throw new Error('Failed to save')
    const result = await response.json()
    const completedPageId = currentPage.value.id
    const page = doc.value.pages.find((p) => p.id === completedPageId)
    if (page) page.status = result.page.status
    doc.value.status = result.document_status
    rectangles.value = []
    emit('changed')

    const completedIndex = doc.value.pages.findIndex((p) => p.id === completedPageId)
    const nextPage =
      doc.value.pages.slice(completedIndex + 1).find((p) => p.status !== 'completed') ||
      doc.value.pages.find((p) => p.status !== 'completed')
    if (nextPage) goToPage(nextPage)
  } finally {
    submitting.value = false
  }
}

onMounted(fetchDocument)
watch(
  () => props.documentId,
  () => {
    doc.value = null
    currentPageId.value = null
    rectangles.value = []
    fetchDocument()
  }
)
</script>

<style scoped>
.viewer-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.viewer-header h2 {
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

.viewer-body {
  display: flex;
  gap: 1.5rem;
}

.page-nav {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  width: 140px;
  flex-shrink: 0;
}

.page-nav-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.5rem 0.75rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  background: white;
  cursor: pointer;
  font-size: 0.9rem;
}

.page-nav-item.active {
  border-color: #2563eb;
  color: #2563eb;
}

.page-nav-item.completed {
  color: #166534;
}

.check {
  color: #16a34a;
}

.page-content {
  flex: 1;
  min-width: 0;
}

.image-wrapper {
  position: relative;
  display: inline-block;
  user-select: none;
  cursor: crosshair;
  max-width: 100%;
}

.page-image {
  display: block;
  max-width: 100%;
  user-select: none;
}

.rect-box {
  position: absolute;
  border: 2px solid #ef4444;
  background: rgba(239, 68, 68, 0.15);
  pointer-events: none;
}

.rect-box.draft {
  border-style: dashed;
}

.rect-delete {
  position: absolute;
  top: -0.6rem;
  right: -0.6rem;
  width: 1.2rem;
  height: 1.2rem;
  line-height: 1;
  border-radius: 50%;
  border: none;
  background: #ef4444;
  color: white;
  cursor: pointer;
  pointer-events: auto;
  font-size: 0.8rem;
}

.page-actions {
  margin-top: 1rem;
  display: flex;
  align-items: center;
  gap: 1rem;
}

.complete-button {
  padding: 0.6rem 1.1rem;
  border: none;
  border-radius: 0.5rem;
  background: #16a34a;
  color: white;
  cursor: pointer;
}

.complete-button:disabled {
  background: #86efac;
  cursor: not-allowed;
}

.review-button {
  padding: 0.6rem 1.1rem;
  border: none;
  border-radius: 0.5rem;
  background: #2563eb;
  color: white;
  cursor: pointer;
}

.info {
  color: #166534;
}
</style>
