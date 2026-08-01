<template>
  <main :class="{ wide: view === 'review' }">
    <h1>Math Problem Extractor</h1>

    <template v-if="view === 'list'">
      <DocumentUpload @uploaded="fetchDocuments" />
      <DocumentList :documents="documents" @select="openDocument" @delete="deleteDocument" />
    </template>

    <PageViewer
      v-else-if="view === 'viewer'"
      :document-id="selectedDocumentId"
      @back="backToList"
      @changed="fetchDocuments"
      @review="openReview"
    />

    <ProblemReview
      v-else
      :page-id="selectedPageId"
      @back="backToViewer"
    />
  </main>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import DocumentUpload from './components/DocumentUpload.vue'
import DocumentList from './components/DocumentList.vue'
import PageViewer from './components/PageViewer.vue'
import ProblemReview from './components/ProblemReview.vue'

const view = ref('list')
const documents = ref([])
const selectedDocumentId = ref(null)
const selectedPageId = ref(null)

const fetchDocuments = async () => {
  const response = await fetch('/api/documents')
  documents.value = await response.json()
}

const openDocument = (id) => {
  selectedDocumentId.value = id
  view.value = 'viewer'
}

const deleteDocument = async (id) => {
  const response = await fetch(`/api/documents/${id}`, { method: 'DELETE' })
  if (response.ok) {
    fetchDocuments()
  }
}

const backToList = () => {
  view.value = 'list'
  fetchDocuments()
}

const openReview = (pageId) => {
  selectedPageId.value = pageId
  view.value = 'review'
}

const backToViewer = () => {
  selectedPageId.value = null
  view.value = 'viewer'
}

onMounted(fetchDocuments)
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
</style>
