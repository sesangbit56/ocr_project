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
    </nav>

    <template v-if="view === 'list'">
      <DocumentUpload @uploaded="onUploaded" />
      <DocumentList :documents="documents" @select="openDocument" @delete="deleteDocument" />
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
import { ref, onMounted, watch } from 'vue'
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

onMounted(() => {
  fetchDocuments()
  fetchQueueCounts()
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
