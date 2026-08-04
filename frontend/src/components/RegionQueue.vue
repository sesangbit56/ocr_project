<template>
  <div class="region-queue">
    <div class="queue-header">
      <h2>Region Queue</h2>
      <span class="badge">{{ queue.length }} pending</span>
    </div>

    <p v-if="loading" class="info">Loading...</p>
    <p v-else-if="queue.length === 0" class="empty">No pages need region marking right now.</p>

    <div v-else class="queue-body">
      <aside class="queue-nav">
        <button
          v-for="(item, index) in queue"
          :key="item.id"
          class="queue-nav-item"
          :class="{ active: index === currentIndex }"
          @click="currentIndex = index"
        >
          <span class="queue-nav-doc">{{ item.document_filename }}</span>
          <span class="queue-nav-page">Page {{ item.page_number }}</span>
        </button>
      </aside>

      <section class="queue-content">
        <RegionEditor v-if="currentItem" :page="currentItem" @completed="onCompleted" />
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import RegionEditor from './RegionEditor.vue'

const emit = defineEmits(['progress'])

const queue = ref([])
const currentIndex = ref(0)
const loading = ref(false)

const currentItem = computed(() => queue.value[currentIndex.value] || null)

const fetchQueue = async () => {
  loading.value = true
  try {
    const response = await fetch('/api/queue/regions')
    queue.value = await response.json()
    currentIndex.value = 0
  } finally {
    loading.value = false
  }
}

// Splices the finished item out locally instead of refetching the whole
// queue - we already know exactly which one just finished, so there's no
// need for a round trip just to learn that.
const onCompleted = () => {
  queue.value.splice(currentIndex.value, 1)
  if (currentIndex.value >= queue.value.length) {
    currentIndex.value = Math.max(0, queue.value.length - 1)
  }
  emit('progress')
}

onMounted(fetchQueue)
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

.queue-body {
  display: flex;
  gap: 1.5rem;
}

.queue-nav {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  width: 180px;
  flex-shrink: 0;
  max-height: 75vh;
  overflow-y: auto;
}

.queue-nav-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.15rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  background: white;
  cursor: pointer;
  text-align: left;
  font-size: 0.85rem;
}

.queue-nav-item.active {
  border-color: #2563eb;
  color: #2563eb;
}

.queue-nav-doc {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.queue-nav-page {
  color: #6b7280;
  font-size: 0.78rem;
}

.queue-nav-item.active .queue-nav-page {
  color: #2563eb;
}

.queue-content {
  flex: 1;
  min-width: 0;
}
</style>
