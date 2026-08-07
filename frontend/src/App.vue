<template>
  <main :class="{ wide: view === 'review' || view === 'review-queue' }">
    <h1>Math Problem Extractor</h1>

    <nav class="top-tabs">
      <button class="top-tab" :class="{ active: view === 'list' }" @click="goToList">
        Documents
      </button>
      <button class="top-tab" :class="{ active: view === 'region-queue' }" @click="view = 'region-queue'">
        Region Queue ({{ regionQueueCount }})
      </button>
      <button class="top-tab" :class="{ active: view === 'review-queue' }" @click="view = 'review-queue'">
        Review Queue ({{ reviewQueueCount }})
      </button>
      <span
        class="backend-status"
        :class="{ offline: !backendOnline }"
        :title="backendOnline ? 'Backend server is reachable' : 'Backend server is not responding'"
      >
        <span class="backend-status-dot"></span>
        {{ backendOnline ? 'Backend online' : 'Backend offline' }}
      </span>
      <span class="ocr-status" :class="{ active: ocrStatus.active }" :title="ocrStatusTitle">
        <span class="ocr-status-dot"></span>
        {{ ocrStatusLabel }}
      </span>
    </nav>

    <template v-if="view === 'list'">
      <DocumentUpload @uploaded="onUploaded" />
      <DocumentList :documents="documents" @select="openDocument" @delete="deleteDocument" @rename="renameDocument" />
    </template>

    <PageViewer
      v-else-if="view === 'viewer'"
      :document-id="selectedDocumentId"
      @back="backToList"
      @changed="fetchDocuments"
      @review="openReview"
      @print="openPrintPreview"
    />

    <ProblemReview
      v-else-if="view === 'review'"
      :page-id="selectedPageId"
      @back="backToViewer"
      @guide="openGuide"
      @switch-page="switchReviewPage"
      @done="backToList"
    />

    <PrintPreview v-else-if="view === 'print'" :document-id="selectedDocumentId" @back="backToViewer" />

    <RegionQueue v-else-if="view === 'region-queue'" @progress="fetchQueueCounts" />

    <ReviewQueue
      v-else-if="view === 'review-queue'"
      @back="backToViewer"
      @guide="openGuide"
      @progress="fetchQueueCounts"
    />

    <ReviewGuide v-else @back="backFromGuide" />
  </main>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import DocumentUpload from './components/DocumentUpload.vue'
import DocumentList from './components/DocumentList.vue'
import PageViewer from './components/PageViewer.vue'
import ProblemReview from './components/ProblemReview.vue'
import PrintPreview from './components/PrintPreview.vue'
import RegionQueue from './components/RegionQueue.vue'
import ReviewQueue from './components/ReviewQueue.vue'
import ReviewGuide from './components/ReviewGuide.vue'

const view = ref('list')
const documents = ref([])
const selectedDocumentId = ref(null)
const selectedPageId = ref(null)
const regionQueueCount = ref(0)
const reviewQueueCount = ref(0)
const ocrStatus = ref({ active: false, current: null, queued: 0 })
const backendOnline = ref(true)
// Remembers which view opened the guide, so closing it returns to wherever
// it was opened from ('review' or 'review-queue') instead of always going
// back to the standalone review view.
const previousView = ref('review')

const fetchDocuments = async () => {
  const response = await fetch('/api/documents')
  documents.value = await response.json()
}

const fetchQueueCounts = async () => {
  const [regions, reviews] = await Promise.all([
    fetch('/api/queue/regions').then((r) => r.json()),
    fetch('/api/queue/reviews').then((r) => r.json()),
  ])
  regionQueueCount.value = regions.length
  reviewQueueCount.value = reviews.length
}

// OCR runs in a background worker independent of whatever view is on
// screen (see _ocr_worker_loop in app.py), so unlike the queue badges
// above this has to poll on a timer rather than just refresh on
// navigation - otherwise there's no way to tell "still working" from
// "stuck" without leaving the page open on a queue view.
const fetchOcrStatus = async () => {
  try {
    const response = await fetch('/api/ocr/status')
    ocrStatus.value = await response.json()
    backendOnline.value = true
  } catch (err) {
    // A transient fetch failure (e.g. dev server restarting) shouldn't
    // wipe out the last known OCR status - just skip this tick. It does
    // mean the backend is unreachable, though, so flip the status pill -
    // this is the only signal the UI has that the backend process died
    // (as opposed to just being idle).
    backendOnline.value = false
  }
}

const ocrStatusLabel = computed(() => {
  if (!ocrStatus.value.active) return 'OCR idle'
  const current = ocrStatus.value.current
  const where = current ? `${current.document_filename ?? '?'} p.${current.page_number ?? '?'}` : '...'
  return `OCR: ${where} (+${ocrStatus.value.queued} queued)`
})

const ocrStatusTitle = computed(() =>
  ocrStatus.value.active
    ? 'Background OCR is currently processing a page/region'
    : 'No OCR work in progress right now'
)

const onUploaded = () => {
  fetchDocuments()
  fetchQueueCounts()
}

const openDocument = (id) => {
  selectedDocumentId.value = id
  view.value = 'viewer'
}

const deleteDocument = async (id) => {
  const response = await fetch(`/api/documents/${id}`, { method: 'DELETE' })
  if (response.ok) {
    fetchDocuments()
    fetchQueueCounts()
  }
}

const renameDocument = async (id, filename) => {
  const response = await fetch(`/api/documents/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  })
  if (response.ok) fetchDocuments()
}

const goToList = () => {
  view.value = 'list'
}

const backToList = () => {
  view.value = 'list'
  fetchDocuments()
}

const openReview = (pageId) => {
  selectedPageId.value = pageId
  view.value = 'review'
}

const openPrintPreview = (documentId) => {
  selectedDocumentId.value = documentId
  view.value = 'print'
}

const switchReviewPage = (pageId) => {
  selectedPageId.value = pageId
}

// Accepts an optional documentId so ReviewQueue (where a document may
// never have been opened directly - selectedDocumentId could still be
// null) can route "Back to page" to the right document.
const backToViewer = (documentId) => {
  if (documentId) selectedDocumentId.value = documentId
  selectedPageId.value = null
  view.value = 'viewer'
}

const openGuide = () => {
  previousView.value = view.value
  view.value = 'guide'
}

const backFromGuide = () => {
  view.value = previousView.value
}

let ocrStatusTimer = null

onMounted(() => {
  fetchDocuments()
  fetchQueueCounts()
  fetchOcrStatus()
  ocrStatusTimer = setInterval(fetchOcrStatus, 2000)
})

onUnmounted(() => {
  if (ocrStatusTimer) clearInterval(ocrStatusTimer)
})
// Cheap enough at this scale to just refresh on every navigation - keeps
// the tab badges accurate without needing polling or a push mechanism.
watch(view, fetchQueueCounts)
</script>

<style>
body {
  font-family: system-ui, sans-serif;
  margin: 0;
  padding: 1.5rem;
  background: #f5f5f5;
}
main {
  max-width: 1100px;
  margin: 0 auto;
  padding: 1.5rem;
  background: white;
  border-radius: 0.75rem;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.08);
}
main.wide {
  max-width: min(1800px, 95vw);
}
h1 {
  margin-bottom: 1.2rem;
  font-size: 1.9rem;
}
.top-tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.2rem;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 0.75rem;
}
.top-tab {
  font-size: 0.9rem;
  padding: 0.5rem 0.9rem;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: white;
  color: #374151;
  cursor: pointer;
}
.top-tab.active {
  border-color: #2563eb;
  background: #eff6ff;
  color: #2563eb;
}

.backend-status {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-left: auto;
  padding: 0.4rem 0.8rem;
  border-radius: 999px;
  font-size: 0.82rem;
  background: #f3f4f6;
  color: #6b7280;
  white-space: nowrap;
}
.backend-status.offline {
  background: #fef2f2;
  color: #b91c1c;
}
.backend-status-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 999px;
  background: #22c55e;
  flex-shrink: 0;
}
.backend-status.offline .backend-status-dot {
  background: #dc2626;
  animation: ocr-status-pulse 1.2s ease-in-out infinite;
}

.ocr-status {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.8rem;
  border-radius: 999px;
  font-size: 0.82rem;
  background: #f3f4f6;
  color: #6b7280;
  white-space: nowrap;
}
.ocr-status.active {
  background: #eff6ff;
  color: #1d4ed8;
}
.ocr-status-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 999px;
  background: #9ca3af;
  flex-shrink: 0;
}
.ocr-status.active .ocr-status-dot {
  background: #2563eb;
  animation: ocr-status-pulse 1.2s ease-in-out infinite;
}
@keyframes ocr-status-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

@media print {
  body {
    background: white;
    padding: 0;
  }
  main {
    box-shadow: none;
    padding: 0;
    max-width: none;
    border-radius: 0;
  }
  h1,
  .top-tabs {
    display: none;
  }
}
</style>
