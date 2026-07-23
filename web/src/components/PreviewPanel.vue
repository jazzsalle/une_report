<template>
  <div class="preview-panel">
    <div class="preview-toolbar">
      <span class="preview-label">문서 미리보기</span>
      <span v-if="pageCount" class="preview-meta">{{ pageCount }}페이지 · v{{ version }}</span>
      <span v-if="selectedIds.length" class="preview-selection">
        선택된 요소 {{ selectedIds.length }}개
        <button type="button" class="link-btn" @click="$emit('clear-selection')">해제</button>
      </span>
    </div>
    <div class="preview-scroll">
      <p v-if="!html" class="preview-empty">
        상단의 [hwpx 업로드] 버튼으로 문서를 열면 여기에 미리보기가 표시됩니다.
      </p>
      <!-- 서버 HTML은 반드시 DOMPurify.sanitize를 거쳐 주입된다 -->
      <div v-show="html" ref="containerRef" class="preview-doc" @click="onClick"></div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import DOMPurify from 'dompurify'

const props = defineProps({
  html: { type: String, default: '' },
  changedIds: { type: Array, default: () => [] },
  selectedIds: { type: Array, default: () => [] },
  pageCount: { type: Number, default: 0 },
  version: { type: Number, default: 0 },
})
const emit = defineEmits(['toggle-select', 'clear-selection'])

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
  applySelection()
  applyHighlights()
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
