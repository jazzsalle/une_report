<template>
  <!-- 상단 바: 모드 탭 (생성 도구 | hwpx 편집) — 로그인 UI 없음 (T3Q 전환) -->
  <div class="app-shell">
    <header class="topbar">
      <span class="brand">재난안전계획서 생성 도구</span>
      <nav class="mode-tabs">
        <button
          type="button"
          :class="{ active: mode === 'generator' }"
          @click="mode = 'generator'"
        >
          생성 도구
        </button>
        <button
          type="button"
          :class="{ active: mode === 'editor' }"
          @click="mode = 'editor'"
        >
          hwpx 편집
        </button>
      </nav>
      <span class="topbar-spacer"></span>
    </header>

    <!-- v-show로 모드 전환 시 각 페이지 상태(입력값·문서)를 보존한다 -->
    <GeneratorPage v-show="mode === 'generator'" class="mode-page" />
    <EditorPage v-show="mode === 'editor'" class="mode-page" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import EditorPage from './pages/EditorPage.vue'
import GeneratorPage from './pages/GeneratorPage.vue'

const MODE_KEY = 'cadm_mode'
const mode = ref(sessionStorage.getItem(MODE_KEY) || 'generator')

import { watch } from 'vue'
watch(mode, (v) => sessionStorage.setItem(MODE_KEY, v))
</script>
