<template>
  <div class="content-row" :class="{ 'needs-review': flagged }">
    <div class="content-row-main">
      <div class="order-buttons">
        <button
          class="order-button"
          :disabled="index === 0 || reordering"
          title="Move up"
          @click="$emit('move', -1)"
        >&#9650;</button>
        <button
          class="order-button"
          :disabled="index === siblingCount - 1 || reordering"
          title="Move down"
          @click="$emit('move', 1)"
        >&#9660;</button>
      </div>
      <span class="type-tag" :class="content.type">{{ content.type }}</span>
      <input
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
      <span v-if="flagged" class="review-flag">&#9888; needs review</span>
      <button
        v-if="content.type === 'formula' || content.type === 'image' || content.type === 'choice'"
        class="adjust-button"
        :disabled="(adjustingContentId && adjustingContentId !== content.id) || (adjustingContentId === content.id && submittingRegion)"
        @click="$emit('adjust-region')"
      >
        {{
          adjustingContentId === content.id
            ? (submittingRegion ? 'Saving...' : 'Drawing on image...')
            : 'Adjust region'
        }}
      </button>
      <button class="delete-button" @click="$emit('delete')">Delete</button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  content: { type: Object, required: true },
  index: { type: Number, required: true },
  siblingCount: { type: Number, required: true },
  reordering: { type: Boolean, default: false },
  flagged: { type: Boolean, default: false },
  adjustingContentId: { type: String, default: null },
  submittingRegion: { type: Boolean, default: false },
})
defineEmits(['move', 'delete', 'adjust-region'])
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

.order-buttons {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  width: 1.6rem;
}

.order-button {
  font-size: 0.6rem;
  line-height: 1;
  padding: 0.15rem 0;
  border: 1px solid #d1d5db;
  border-radius: 0.3rem;
  background: white;
  color: #374151;
  cursor: pointer;
}

.order-button:disabled {
  color: #d1d5db;
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
</style>
