<template>
  <div>
    <div v-if="documents.length > 0" class="view-toggle">
      <button
        class="view-toggle-button"
        :class="{ active: viewMode === 'list' }"
        @click="viewMode = 'list'"
      >List</button>
      <button
        class="view-toggle-button"
        :class="{ active: viewMode === 'gallery' }"
        @click="viewMode = 'gallery'"
      >Gallery</button>
    </div>

    <ul v-if="documents.length === 0" class="document-list">
      <li class="empty">No documents uploaded yet.</li>
    </ul>

    <ul v-else-if="viewMode === 'list'" class="document-list">
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

    <div v-else class="document-gallery">
      <div
        v-for="doc in documents"
        :key="doc.id"
        class="document-card"
        @click="$emit('select', doc.id)"
      >
        <img v-if="doc.thumbnail_url" :src="doc.thumbnail_url" class="document-thumb" alt="" />
        <div v-else class="document-thumb document-thumb-placeholder">No preview</div>
        <div class="document-card-info">
          <span class="filename">{{ doc.filename }}</span>
          <span class="pages">{{ doc.total_pages }} pages</span>
          <span class="status" :class="doc.status">{{ statusLabel(doc.status) }}</span>
        </div>
        <button class="delete-button" @click.stop="confirmDelete(doc)">Delete</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  documents: {
    type: Array,
    required: true,
  },
})

const emit = defineEmits(['select', 'delete'])

const viewMode = ref('list')

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
.view-toggle {
  display: inline-flex;
  gap: 0.15rem;
  margin-bottom: 1rem;
  padding: 0.2rem;
  background: #f3f4f6;
  border-radius: 0.55rem;
}

.view-toggle-button {
  font-size: 0.85rem;
  padding: 0.4rem 0.9rem;
  border: none;
  border-radius: 0.4rem;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}

.view-toggle-button:hover:not(.active) {
  color: #374151;
}

.view-toggle-button.active {
  background: white;
  color: #2563eb;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}

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

.document-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 1rem;
}

.document-card {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.5rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  cursor: pointer;
}

.document-card:hover {
  background: #f9fafb;
}

.document-thumb {
  display: block;
  width: 100%;
  aspect-ratio: 3 / 4;
  object-fit: cover;
  border-radius: 0.3rem;
  background: #f3f4f6;
}

.document-thumb-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #9ca3af;
  font-size: 0.8rem;
}

.document-card-info {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.document-card-info .filename {
  font-size: 0.85rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-card .delete-button {
  font-size: 0.8rem;
  padding: 0.3rem 0.6rem;
}
</style>
