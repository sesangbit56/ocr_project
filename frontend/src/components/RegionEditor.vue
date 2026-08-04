<template>
  <div class="region-editor">
    <div
      class="image-wrapper"
      @mousedown="startDraw"
      @mousemove="onDraw"
      @mouseup="endDraw"
      @mouseleave="cancelDraw"
    >
      <img
        ref="imgEl"
        :src="page.image_url"
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
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  page: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['completed'])

const rectangles = ref([])
const draft = ref(null)
const startPoint = ref(null)
const drawing = ref(false)
const submitting = ref(false)
const imgEl = ref(null)
const scale = ref(1)

// Keyed on the page's id rather than object identity, so this resets
// correctly whether the parent mutates the same page object in place
// (PageViewer) or swaps in a different one entirely (a queue advancing to
// its next item).
const resetDrawState = () => {
  rectangles.value = []
  draft.value = null
  startPoint.value = null
  drawing.value = false
}
watch(() => props.page?.id, resetDrawState)

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
  submitting.value = true
  try {
    const response = await fetch(`/api/pages/${props.page.id}/problems`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rectangles: rectangles.value }),
    })
    if (!response.ok) throw new Error('Failed to save')
    const result = await response.json()
    resetDrawState()
    emit('completed', result)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
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
</style>
