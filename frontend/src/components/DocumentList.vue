<template>
  <ul class="document-list">
    <li v-if="documents.length === 0" class="empty">No documents uploaded yet.</li>
    <li
      v-for="doc in documents"
      :key="doc.id"
      class="document-item"
      @click="$emit('select', doc.id)"
    >
      <span class="filename">{{ doc.filename }}</span>
      <span class="pages">{{ doc.total_pages }} pages</span>
      <span class="status" :class="doc.status">{{ statusLabel(doc.status) }}</span>
      <button class="delete-button" @click.stop="confirmDelete(doc)">Delete</button>
    </li>
  </ul>
</template>

<script setup>
defineProps({
  documents: {
    type: Array,
    required: true,
  },
})

const emit = defineEmits(['select', 'delete'])

const confirmDelete = (doc) => {
  if (confirm(`Delete "${doc.filename}"? This cannot be undone.`)) {
    emit('delete', doc.id)
  }
}

const statusLabel = (status) => {
  if (status === 'selection_completed') return 'Selection completed'
  if (status === 'uploaded') return 'Uploaded'
  return status
}
</script>

<style scoped>
.document-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.document-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding: 0.85rem 1rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  margin-bottom: 0.75rem;
  cursor: pointer;
}

.document-item:hover {
  background: #f9fafb;
}

.filename {
  flex: 1;
  font-weight: 500;
}

.pages {
  color: #6b7280;
  font-size: 0.9rem;
}

.status {
  font-size: 0.85rem;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  background: #e5e7eb;
  color: #374151;
}

.status.selection_completed {
  background: #dcfce7;
  color: #166534;
}

.delete-button {
  border: none;
  border-radius: 0.5rem;
  padding: 0.45rem 0.8rem;
  background: #ef4444;
  color: white;
  cursor: pointer;
}

.empty {
  color: #6b7280;
  padding: 1rem 0;
}
</style>
