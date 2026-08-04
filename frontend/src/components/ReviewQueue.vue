<template>
  <div class="review-queue">
    <div class="queue-header">
      <h2>Review Queue</h2>
      <span class="badge">{{ queue.length }} pending</span>
    </div>

    <p v-if="loading" class="info">Loading...</p>
    <p v-else-if="queue.length === 0" class="empty">No pages need review right now.</p>

    <template v-else>
      <div class="queue-strip">
        <button
          v-for="item in queue"
          :key="item.id"
          class="queue-chip"
          :class="{ active: item.id === currentPageId }"
          @click="selectItem(item)"
        >
          {{ item.document_filename }} &middot; Page {{ item.page_number }}
        </button>
      </div>

      <ProblemReview
        v-if="currentPageId"
        :page-id="currentPageId"
        @switch-page="onSwitchPage"
        @done="onDone"
        @back="$emit('back', currentDocumentId)"
        @guide="$emit('guide')"
      />
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ProblemReview from './ProblemReview.vue'

const emit = defineEmits(['back', 'guide', 'progress'])

const queue = ref([])
const currentPageId = ref(null)
const currentDocumentId = ref(null)
const loading = ref(false)

const setCurrent = (item) => {
  currentPageId.value = item.id
  currentDocumentId.value = item.document_id
}

const fetchQueue = async () => {
  loading.value = true
  try {
    const response = await fetch('/api/queue/reviews')
    queue.value = await response.json()
  } finally {
    loading.value = false
  }
}

const selectItem = (item) => {
  setCurrent(item)
}

// ProblemReview found another unreviewed page in the same document - the
// just-confirmed one is no longer pending, so drop it locally instead of
// a round trip; the rest of the queue is still accurate.
const onSwitchPage = (pageId) => {
  queue.value = queue.value.filter((item) => item.id !== currentPageId.value)
  currentPageId.value = pageId
  emit('progress')
}

// ProblemReview exhausted every unreviewed page in the current document -
// refetch, since confirming several pages there may have changed what's
// pending elsewhere too (e.g. background OCR finishing on another page).
const onDone = async () => {
  await fetchQueue()
  if (queue.value.length > 0) {
    setCurrent(queue.value[0])
  } else {
    currentPageId.value = null
    currentDocumentId.value = null
  }
  emit('progress')
}

onMounted(async () => {
  await fetchQueue()
  if (queue.value.length > 0) setCurrent(queue.value[0])
})
</script>

<style scoped>
.queue-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}

.queue-header h2 {
  margin: 0;
  font-size: 1.3rem;
}

.badge {
  font-size: 0.85rem;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: #dbeafe;
  color: #1e40af;
}

.info,
.empty {
  color: #6b7280;
}

.queue-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
}

.queue-chip {
  font-size: 0.82rem;
  padding: 0.35rem 0.7rem;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: white;
  cursor: pointer;
  color: #374151;
}

.queue-chip.active {
  border-color: #2563eb;
  background: #eff6ff;
  color: #2563eb;
}
</style>
