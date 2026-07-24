<template>
  <div class="preview-panel">
    <div class="result-toolbar">
      <span class="panel-title">미리보기</span>
      <span class="preview-meta">{{ metaText }}</span>
      <span v-if="selectedIds.length" class="pill pill-warning">
        선택: {{ selectedIds.length }}개 요소
      </span>
      <button
        v-if="selectedIds.length"
        type="button"
        class="link-btn"
        @click="$emit('clear-selection')"
      >
        해제
      </button>
      <span class="topbar-spacer"></span>
      <button type="button" class="btn btn-outline" :disabled="uploading" @click="$emit('upload')">
        {{ uploading ? '업로드 중…' : 'hwpx 업로드' }}
      </button>
      <button
        type="button"
        class="btn btn-primary"
        :disabled="!hasDoc || exporting"
        @click="$emit('download')"
      >
        {{ exporting ? '내보내는 중…' : 'hwpx 다운로드' }}
      </button>
    </div>
    <div class="preview-scroll">
      <!-- 업로드 전: 대시 보더 카드 (클릭 → 업로드) -->
      <div v-if="!html" class="upload-card" @click="$emit('upload')">
        <div class="upload-badge">hwpx</div>
        <span class="upload-title">hwpx 파일을 업로드하세요</span>
        <span class="upload-desc">클릭하여 파일 선택 — 원본 서식을 보존한 채 대화로 편집합니다</span>
      </div>
      <!-- 서버 HTML은 반드시 DOMPurify.sanitize를 거쳐 주입된다 -->
      <div v-show="html" ref="containerRef" class="preview-doc" @click="onClick"></div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import DOMPurify from 'dompurify'

const props = defineProps({
  html: { type: String, default: '' },
  changedIds: { type: Array, default: () => [] },
  selectedIds: { type: Array, default: () => [] },
  title: { type: String, default: '' },
  pageCount: { type: Number, default: 0 },
  version: { type: Number, default: 0 },
  hasDoc: { type: Boolean, default: false },
  uploading: { type: Boolean, default: false },
  exporting: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle-select', 'clear-selection', 'upload', 'download'])

const metaText = computed(() => {
  if (!props.hasDoc) return '업로드된 문서가 없습니다'
  const parts = [props.title]
  if (props.pageCount) parts.push(`${props.pageCount}페이지`)
  parts.push('원본 서식 보존')
  return parts.filter(Boolean).join(' · ')
})

const containerRef = ref(null)
let highlightTimer = null

function render() {
  const el = containerRef.value
  if (!el) return
  if (!props.html) {
    el.innerHTML = ''
    return
  }
  // self-contained HTML(<style> 포함)이므로 style 태그와 data-* 속성을 허용,
  // FORCE_BODY로 선두 <style>이 head로 밀려 제거되는 것을 방지한다.
  el.innerHTML = DOMPurify.sanitize(props.html, {
    ADD_TAGS: ['style'],
    ALLOW_DATA_ATTR: true,
    FORCE_BODY: true,
  })
  scopeInjectedStyles(el)
  applySelection()
  applyHighlights()
}

/** 서버 HTML의 전역 CSS가 앱으로 새지 않게 스코프한다.
 *  - body 규칙(회색 캔버스·여백)은 카드(.preview-doc)가 대신하므로 무력화
 *  - p/table/td/img 요소 규칙은 :where(.preview-doc) 하위로 한정
 *    (:where는 명시도 0 — .bfN 등 클래스 규칙이 계속 우선하도록 유지) */
function scopeInjectedStyles(el) {
  for (const st of el.querySelectorAll('style')) {
    st.textContent = st.textContent
      .replace(/(^|\})(\s*)body(\s*\{)/g, '$1$2.hwpx-doc-root-unused$3')
      .replace(/(^|\})(\s*)(p|table|td|img)(\s*\{)/g, '$1$2:where(.preview-doc) $3$4')
  }
}

/** changed_ids 요소에 교체 표시를 부여하고 첫 요소로 스크롤.
 *  .changed = 교체된 내용 빨간 글자 (다음 편집/문서 갱신까지 유지),
 *  .highlight = 일회성 노란 플래시 (위치 안내용) */
function applyHighlights() {
  const el = containerRef.value
  if (!el) return
  el.querySelectorAll('.changed').forEach((n) => n.classList.remove('changed'))
  if (props.changedIds.length === 0) return
  if (highlightTimer) clearTimeout(highlightTimer)
  let first = null
  for (const id of props.changedIds) {
    const target = el.querySelector(`[data-id="${CSS.escape(String(id))}"]`)
    if (!target) continue
    target.classList.add('changed', 'highlight')
    if (!first) first = target
  }
  if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' })
  // 플래시 애니메이션 종료 후 클래스 제거 → 다음 갱신 때 다시 발동 가능 (.changed는 유지)
  highlightTimer = setTimeout(() => {
    el.querySelectorAll('.highlight').forEach((n) => n.classList.remove('highlight'))
  }, 2500)
}

/** 선택 상태를 DOM 클래스로 동기화 */
function applySelection() {
  const el = containerRef.value
  if (!el) return
  el.querySelectorAll('.selected').forEach((n) => n.classList.remove('selected'))
  for (const id of props.selectedIds) {
    const target = el.querySelector(`[data-id="${CSS.escape(String(id))}"]`)
    if (target) target.classList.add('selected')
  }
}

/** data-id 요소 클릭 시 부분 편집 대상으로 선택 토글 (M6-5) */
function onClick(e) {
  const target = e.target.closest('[data-id]')
  if (!target || !containerRef.value?.contains(target)) return
  const id = Number(target.getAttribute('data-id'))
  if (Number.isNaN(id)) return
  emit('toggle-select', id)
}

onMounted(render)
watch(() => props.html, render)
watch(() => props.changedIds, applyHighlights)
watch(() => props.selectedIds, applySelection, { deep: true })
</script>
