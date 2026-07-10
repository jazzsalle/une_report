<template>
  <form class="login-form" @submit.prevent="submit">
    <h1>hwpx 대화 편집기</h1>
    <p class="login-desc">UNI RAG 계정으로 로그인하세요.</p>
    <label>
      계정
      <input
        v-model="account"
        type="text"
        autocomplete="username"
        placeholder="계정 ID"
        required
      />
    </label>
    <label>
      비밀번호
      <input
        v-model="password"
        type="password"
        autocomplete="current-password"
        placeholder="비밀번호"
        required
      />
    </label>
    <button type="submit" :disabled="loading">
      {{ loading ? '로그인 중…' : '로그인' }}
    </button>
    <p v-if="error" class="form-error">{{ error }}</p>
  </form>
</template>

<script setup>
import { ref } from 'vue'
import { login } from '../api.js'

const emit = defineEmits(['success'])

const account = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const data = await login(account.value.trim(), password.value)
    emit('success', data)
  } catch (e) {
    error.value = e.status === 401 ? '계정 또는 비밀번호가 올바르지 않습니다.' : e.message
  } finally {
    loading.value = false
  }
}
</script>
