<template>
  <div class="print-preview">
    <div class="preview-toolbar">
      <button class="back-button" @click="$emit('back')">&larr; Back</button>
      <h2>{{ document?.filename }} &mdash; Print Preview</h2>
      <button class="print-button" :disabled="loading" @click="doPrint">Print / Save as PDF</button>
    </div>

    <p v-if="loading" class="info">Loading...</p>

    <div v-else class="print-page">
      <div class="print-columns">
        <div v-for="(problem, index) in problems" :key="problem.id" class="problem-block">
          <ProblemPrintout :problem="problem" :number="index + 1" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ProblemPrintout from './ProblemPrintout.vue'

const props = defineProps({
  documentId: {
    type: String,
    required: true,
  },
})

defineEmits(['back'])

const document = ref(null)
const problems = ref([])
const loading = ref(false)

const fetchPrintData = async () => {
  loading.value = true
  try {
    const response = await fetch(`/api/documents/${props.documentId}/print`)
    const data = await response.json()
    document.value = data.document
    problems.value = data.problems
  } finally {
    loading.value = false
  }
}

const doPrint = () => {
  window.print()
}

onMounted(fetchPrintData)
</script>

<style scoped>
.preview-toolbar {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.preview-toolbar h2 {
  margin: 0;
  font-size: 1.1rem;
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

.print-button {
  padding: 0.5rem 0.9rem;
  border: none;
  border-radius: 0.5rem;
  background: #2563eb;
  color: white;
  cursor: pointer;
}

.print-button:disabled {
  background: #93c5fd;
  cursor: not-allowed;
}

.info {
  color: #6b7280;
}

.print-page {
  width: 210mm;
  min-height: 297mm;
  margin: 0 auto;
  padding: 15mm 12mm;
  background: white;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.15);
  box-sizing: border-box;
}

.print-columns {
  column-count: 2;
  column-gap: 8mm;
  column-rule: 1px solid #ddd;
  font-family: '(한)신중명조', '신명중명조', 'HY신명조', Batang, serif;
}

.problem-block {
  break-inside: avoid;
  margin-bottom: 7mm;
}

@page {
  size: A4;
  margin: 0;
}

@media print {
  .preview-toolbar {
    display: none;
  }

  .print-page {
    box-shadow: none;
    margin: 0;
  }
}
</style>
