<template>
  <div class="page-viewer">
    <div class="viewer-header">
      <button class="back-button" @click="$emit('back')">&larr; Back to list</button>
      <h2>{{ doc?.filename }}</h2>
      <span v-if="doc?.status === 'selection_completed'" class="badge">Selection completed</span>
      <button class="print-preview-button" @click="$emit('print', props.documentId)">Print Preview</button>
    </div>

    <div v-if="doc" class="viewer-body">
      <aside class="page-nav">
        <button
          v-for="page in doc.pages"
          :key="page.id"
          class="page-nav-item"
          :class="{
            active: page.id === currentPage?.id,
            reviewed: page.status === 'completed' && page.reviewed,
            'needs-review': page.status === 'completed' && !page.reviewed,
          }"
          @click="goToPage(page)"
        >
          Page {{ page.page_number }}
          <span v-if="page.status === 'completed' && page.reviewed" class="check" title="Reviewed">&#10003;</span>
          <span
            v-else-if="page.status === 'completed' && !page.reviewed"
            class="review-dot"
            title="Needs review"
          ></span>
        </button>
      </aside>

      <section v-if="currentPage" class="page-content">
        <RegionEditor
          v-if="currentPage.status !== 'completed'"
          :page="currentPage"
          @completed="onRegionCompleted"
        />

        <div v-else class="page-actions">
          <p class="info">This page's selections are already completed.</p>
          <span v-if="currentPage.reviewed" class="review-status reviewed">&#10003; Reviewed</span>
          <span v-else class="review-status needs-review">&#9679; Needs review</span>
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
import RegionEditor from './RegionEditor.vue'

const props = defineProps({
  documentId: {
    type: String,
    required: true,
  },
})

const emit = defineEmits(['back', 'changed', 'review', 'print'])

const doc = ref(null)
const currentPageId = ref(null)

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
}

// RegionEditor owns the drawing state itself (rectangles/draft/etc.) and
// resets it on its own when the page prop changes - this only needs to
// fold the save result back into the document-level page list and decide
// where to go next.
const onRegionCompleted = (result) => {
  const completedPageId = currentPage.value.id
  const page = doc.value.pages.find((p) => p.id === completedPageId)
  if (page) page.status = result.page.status
  doc.value.status = result.document_status
  emit('changed')

  const completedIndex = doc.value.pages.findIndex((p) => p.id === completedPageId)
  const nextPage =
    doc.value.pages.slice(completedIndex + 1).find((p) => p.status !== 'completed') ||
    doc.value.pages.find((p) => p.status !== 'completed')
  if (nextPage) goToPage(nextPage)
}

onMounted(fetchDocument)
watch(
  () => props.documentId,
  () => {
    doc.value = null
    currentPageId.value = null
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

.print-preview-button {
  font-size: 0.85rem;
  padding: 0.4rem 0.8rem;
  border: 1px solid #d1d5db;
  border-radius: 0.4rem;
  background: white;
  color: #374151;
  cursor: pointer;
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

.page-nav-item.reviewed {
  color: #166534;
}

.page-nav-item.needs-review {
  color: #b45309;
}

.check {
  color: #16a34a;
}

.review-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: #f59e0b;
}

.review-status {
  font-size: 0.85rem;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
}

.review-status.reviewed {
  background: #dcfce7;
  color: #166534;
}

.review-status.needs-review {
  background: #fef3c7;
  color: #b45309;
}

.page-content {
  flex: 1;
  min-width: 0;
}

.page-actions {
  margin-top: 1rem;
  display: flex;
  align-items: center;
  gap: 1rem;
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
