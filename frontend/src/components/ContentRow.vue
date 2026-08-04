<template>
  <div
    class="content-row"
    :class="{ 'needs-review': flagged, 'drag-over': dragOver, compact, processing: content.processing }"
    @dragover.prevent="$emit('drag-over')"
    @dragleave="$emit('drag-leave')"
    @drop.prevent="$emit('drop')"
  >
    <div class="content-row-main">
      <span
        class="drag-handle"
        :class="{ disabled: reordering || !sortable }"
        :draggable="sortable && !reordering"
        :title="sortable ? 'Drag to reorder' : 'Order is automatic (by label)'"
        @dragstart="$emit('drag-start', $event)"
        @dragend="$emit('drag-end')"
      >&#9776;</span>
      <select
        :value="content.type"
        class="type-tag type-select"
        :class="content.type"
        title="Content type"
        :disabled="changingType"
        @change="$emit('type-change', $event.target.value)"
      >
        <option v-for="t in CONTENT_TYPES" :key="t" :value="t">{{ t }}</option>
      </select>
      <select
        v-if="content.type === 'choice'"
        v-model="content.label"
        class="label-input label-select"
        title="Choice number"
      >
        <option :value="null"></option>
        <option v-for="n in CHOICE_LABELS" :key="n" :value="n">{{ n }}</option>
      </select>
      <input
        v-else
        v-model="content.label"
        type="text"
        class="label-input"
        placeholder="label"
        title="Visible marker for this item, e.g. ①, ㄱ, (가)"
      />
      <textarea
        v-if="content.type === 'text'"
        v-model="content.content"
        rows="2"
        class="content-input"
        :class="content.type"
      ></textarea>
      <div v-else-if="content.type === 'image'" class="image-preview">
        <img v-if="content.image_url" :src="content.image_url" alt="" />
        <span v-else class="image-preview-placeholder">Adjust region to crop the image</span>
      </div>
      <math-field
        v-else
        class="math-field-input"
        smart-fence
        :value="content.content"
        @input="content.content = $event.target.value"
      ></math-field>
    </div>
    <div class="content-row-tools">
      <span v-if="content.processing" class="processing-flag">&#8987; recognizing&hellip;</span>
      <span v-else-if="flagged" class="review-flag">&#9888; needs review</span>
      <label
        v-if="content.type === 'formula'"
        class="display-mode-toggle"
        title="Render on its own full-width line instead of inline with surrounding text (applies to the printed/preview output)"
      >
        <input type="checkbox" v-model="content.display_mode" />
        Full line
      </label>
      <button
        v-if="content.type === 'formula' || content.type === 'image' || content.type === 'choice'"
        class="adjust-button"
        :disabled="content.processing || (adjustingContentId && adjustingContentId !== content.id) || (adjustingContentId === content.id && submittingRegion)"
        @click="$emit('adjust-region')"
      >
        {{
          content.processing
            ? 'Recognizing...'
            : adjustingContentId === content.id
              ? (submittingRegion ? 'Saving...' : 'Drawing on image...')
              : 'Adjust region'
        }}
      </button>
      <button class="delete-button" @click="$emit('delete')">Delete</button>
    </div>
  </div>
</template>

<script setup>
const CHOICE_LABELS = ['①', '②', '③', '④', '⑤']
const CONTENT_TYPES = ['text', 'formula', 'choice', 'image']

defineProps({
  content: { type: Object, required: true },
  reordering: { type: Boolean, default: false },
  flagged: { type: Boolean, default: false },
  adjustingContentId: { type: String, default: null },
  submittingRegion: { type: Boolean, default: false },
  dragOver: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  changingType: { type: Boolean, default: false },
  sortable: { type: Boolean, default: true },
})
defineEmits(['delete', 'adjust-region', 'type-change', 'drag-start', 'drag-over', 'drag-leave', 'drop', 'drag-end'])
</script>

<style scoped>
.content-row {
  margin-bottom: 0.5rem;
  padding: 0.4rem;
  border-radius: 0.4rem;
  border: 1px solid transparent;
}

.content-row.needs-review {
  border-color: #fbbf24;
  background: #fffbeb;
}

.content-row.drag-over {
  border-color: #93c5fd;
  border-style: dashed;
}

.content-row.processing {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.content-row-main {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: flex-start;
}

.content-row-tools {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.35rem;
  padding-left: calc(1.6rem + 4.2rem + 1rem);
}

.review-flag {
  font-size: 0.78rem;
  color: #92400e;
}

.display-mode-toggle {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.78rem;
  color: #374151;
  cursor: pointer;
}

.processing-flag {
  font-size: 0.78rem;
  color: #1d4ed8;
}

.adjust-button,
.delete-button {
  font-size: 0.78rem;
  padding: 0.25rem 0.55rem;
  border: 1px solid #d1d5db;
  border-radius: 0.35rem;
  background: white;
  color: #374151;
  cursor: pointer;
}

.adjust-button:disabled {
  color: #9ca3af;
  cursor: not-allowed;
}

.delete-button {
  color: #b91c1c;
  border-color: #fecaca;
}

.drag-handle {
  flex-shrink: 0;
  width: 1.6rem;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  line-height: 1;
  color: #9ca3af;
  border: 1px solid #d1d5db;
  border-radius: 0.3rem;
  background: white;
  align-self: stretch;
  cursor: grab;
}

.drag-handle:active {
  cursor: grabbing;
}

.drag-handle.disabled {
  color: #e5e7eb;
  cursor: not-allowed;
}

.type-tag {
  flex-shrink: 0;
  width: 4.2rem;
  text-align: center;
  font-size: 0.75rem;
  padding: 0.3rem 0.3rem;
  border-radius: 0.4rem;
  background: #f3f4f6;
  color: #374151;
}

.type-select {
  border: none;
  text-align-last: center;
  cursor: pointer;
  font-family: inherit;
}

.type-tag.formula {
  background: #fef3c7;
  color: #92400e;
}

.type-tag.text {
  background: #e0e7ff;
  color: #3730a3;
}

.type-tag.choice {
  background: #d1fae5;
  color: #065f46;
}

.type-tag.image {
  background: #fce7f3;
  color: #9d174d;
}

.label-input {
  flex-shrink: 0;
  width: 3.6rem;
  font-size: 0.85rem;
  padding: 0.3rem 0.4rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  text-align: center;
}

.label-select {
  text-align: center;
  text-align-last: center;
  cursor: pointer;
}

.content-input {
  flex: 1;
  min-width: 0;
  font-family: inherit;
  font-size: 0.9rem;
  padding: 0.4rem 0.55rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  resize: vertical;
}

.math-field-input {
  flex: 1;
  min-width: 8rem;
  min-height: 6rem;
  padding: 0.6rem 0.8rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  background: white;
  font-size: 1.15rem;
}

.math-field-input:focus {
  border-color: #2563eb;
  outline: none;
}

.image-preview {
  flex: 1;
  min-width: 8rem;
  min-height: 6rem;
  padding: 0.5rem;
  border: 1px solid #e5e7eb;
  border-radius: 0.4rem;
  background: #fafafa;
  display: flex;
  align-items: center;
  justify-content: center;
}

.image-preview img {
  max-width: 100%;
  max-height: 16rem;
  display: block;
}

.image-preview-placeholder {
  font-size: 0.85rem;
  color: #9ca3af;
  text-align: center;
}

/* Used for the choices grid - each tile is a fixed, narrower width instead
   of stretching full-width, with every control scaled down to match. */
.content-row.compact {
  width: 13rem;
  margin-bottom: 0;
  padding: 0.3rem;
}

.content-row.compact .content-row-main {
  gap: 0.3rem;
}

.content-row.compact .drag-handle {
  width: 1.2rem;
  font-size: 0.85rem;
}

.content-row.compact .type-tag {
  width: 3rem;
  font-size: 0.65rem;
  padding: 0.2rem;
}

.content-row.compact .label-input {
  width: 2.6rem;
  font-size: 0.75rem;
  padding: 0.2rem 0.3rem;
}

.content-row.compact .content-input {
  font-size: 0.8rem;
  padding: 0.3rem 0.4rem;
  min-height: 2.4rem;
}

.content-row.compact .math-field-input {
  min-width: 100%;
  min-height: 2.4rem;
  font-size: 0.95rem;
  padding: 0.3rem 0.4rem;
}

.content-row.compact .image-preview {
  min-width: 100%;
  min-height: 2.4rem;
}

.content-row.compact .content-row-tools {
  padding-left: 0;
  gap: 0.4rem;
  margin-top: 0.25rem;
}

.content-row.compact .adjust-button,
.content-row.compact .delete-button {
  font-size: 0.68rem;
  padding: 0.15rem 0.35rem;
}
</style>
