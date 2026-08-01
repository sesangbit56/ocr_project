<template>
  <form class="upload-form" @submit.prevent="upload">
    <input ref="fileInput" type="file" accept="application/pdf" @change="onFileChange" />
    <button type="submit" :disabled="!file || uploading">
      {{ uploading ? 'Uploading...' : 'Upload PDF' }}
    </button>
    <p v-if="error" class="error">{{ error }}</p>
  </form>
</template>

<script setup>
import { ref } from 'vue'

const emit = defineEmits(['uploaded'])

const fileInput = ref(null)
const file = ref(null)
const uploading = ref(false)
const error = ref('')

const onFileChange = (event) => {
  file.value = event.target.files[0] || null
  error.value = ''
}

const upload = async () => {
  if (!file.value) return
  uploading.value = true
  error.value = ''

  const formData = new FormData()
  formData.append('file', file.value)

  try {
    const response = await fetch('/api/documents', {
      method: 'POST',
      body: formData,
    })
    if (!response.ok) {
      throw new Error('Upload failed')
    }
    file.value = null
    if (fileInput.value) fileInput.value.value = ''
    emit('uploaded')
  } catch (err) {
    error.value = err.message
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.upload-form {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  margin-bottom: 1.5rem;
}

.upload-form button {
  padding: 0.6rem 1.1rem;
  border: none;
  border-radius: 0.5rem;
  background: #2563eb;
  color: white;
  cursor: pointer;
}

.upload-form button:disabled {
  background: #93c5fd;
  cursor: not-allowed;
}

.error {
  color: #dc2626;
  margin: 0;
}
</style>
