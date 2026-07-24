<template>
  <!-- 상단 바(56px): 브랜드 + 모드 탭 + 다크 모드 스위치 (UNE 디자인 시스템 — 텍스트 브랜드만) -->
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-wrap">
        <span class="brand-mark">une</span>
        <span class="brand">재난안전계획서 생성 도구</span>
      </div>
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
      <button
        type="button"
        class="theme-toggle"
        title="라이트/다크 테마 전환"
        @click="toggleTheme"
      >
        <span class="theme-toggle-label">다크 모드</span>
        <span class="switch" :class="{ on: theme === 'dark' }" role="switch" :aria-checked="theme === 'dark'"></span>
      </button>
    </header>

    <!-- v-show로 모드 전환 시 각 페이지 상태(입력값·문서)를 보존한다 -->
    <GeneratorPage v-show="mode === 'generator'" class="mode-page" />
    <EditorPage v-show="mode === 'editor'" class="mode-page" />
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import EditorPage from './pages/EditorPage.vue'
import GeneratorPage from './pages/GeneratorPage.vue'

const MODE_KEY = 'cadm_mode'
const mode = ref(sessionStorage.getItem(MODE_KEY) || 'generator')
watch(mode, (v) => sessionStorage.setItem(MODE_KEY, v))

// 다크 모드: data-theme 토글만으로 fig-tokens.css 전체 토큰이 전환된다
const THEME_KEY = 'une_theme'
const theme = ref(localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light')
watch(
  theme,
  (v) => {
    document.documentElement.dataset.theme = v
    localStorage.setItem(THEME_KEY, v)
  },
  { immediate: true }
)
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
}
</script>
