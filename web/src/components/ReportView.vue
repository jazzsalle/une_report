<template>
  <div class="report-view">
    <div class="result-toolbar">
      <span class="report-title">{{ title || '생성 문서' }}</span>
      <span v-if="progressText" class="pill" :data-status="busy ? 'generating' : 'done'">
        {{ progressText }}
      </span>
      <span class="topbar-spacer"></span>
      <label v-if="templates.length" class="tpl-select">
        서식
        <select
          :value="template"
          :disabled="busy"
          @change="$emit('update:template', $event.target.value)"
        >
          <option value="">기본 (서식 없음)</option>
          <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
        </select>
      </label>
      <button type="button" class="btn btn-ghost" :disabled="busy" @click="$emit('back-to-toc')">
        목차로
      </button>
      <button
        type="button"
        class="btn btn-primary"
        :disabled="busy || !hasContent"
        @click="$emit('export', 'hwpx')"
      >
        hwpx 내보내기
      </button>
      <button
        type="button"
        class="btn btn-outline"
        :disabled="busy || !hasContent"
        @click="$emit('export', 'docx')"
      >
        docx 내보내기
      </button>
    </div>

    <!-- 회색 캔버스 위 문서 카드 (max-width 800px 중앙) -->
    <div class="report-scroll">
      <div class="doc-card">
        <h1 class="doc-title">{{ title || '생성 문서' }}</h1>
        <p v-if="subtitle" class="doc-subtitle">{{ subtitle }}</p>
        <template v-for="(block, i) in blocks" :key="i">
          <h2 v-if="block.type === 'chapter'" class="doc-chapter">{{ block.name }}</h2>
          <div v-else class="doc-section" :class="`is-${block.leaf.status}`">
            <div class="doc-section-head">
              <span class="doc-section-name">{{ block.leaf.name }}</span>
              <span class="pill" :data-status="block.leaf.status">
                {{ STATUS_LABEL[block.leaf.status] }}
              </span>
              <span v-if="block.leaf.status === 'generating'" class="spinner spinner-sm"></span>
            </div>
            <div v-if="block.leaf.error" class="doc-section-error">{{ block.leaf.error }}</div>
            <div v-else-if="block.leaf.content" v-html="renderSection(block.leaf.content)"></div>
            <div v-if="block.leaf.references && block.leaf.references.length" class="doc-refs">
              <span v-for="(ref, j) in block.leaf.references" :key="j" class="doc-ref">
                참조 · {{ ref.fileName }}<template v-if="ref.page"> (p.{{ ref.page }})</template>
              </span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
// 본문 생성 결과 뷰 (지시 8: 목차별 스트리밍 수신·상태 표시) — 문서형 리디자인 (UNE)
// 진행상태 구분은 요구사항 FUN-CADM-302004를 차용: 대기/진행중/완료/오류
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' }, // "서면 보고 / {보고일시} / {역할}"
  sections: { type: Array, default: () => [] }, // 목차 트리 — 장(챕터) 제목 표시에 사용
  // [{name, status: waiting|generating|done|error, content, references, error}]
  leaves: { type: Array, required: true },
  busy: { type: Boolean, default: false },
  templates: { type: Array, default: () => [] }, // 표준 템플릿 목록 [{id, name}]
  template: { type: String, default: '' },       // 선택된 템플릿 id
})
defineEmits(['export', 'back-to-toc', 'update:template'])

const STATUS_LABEL = {
  waiting: '대기',
  generating: '생성 중',
  done: '완료',
  error: '오류',
}

const hasContent = computed(() => props.leaves.some((l) => l.status === 'done'))
const progressText = computed(() => {
  const done = props.leaves.filter((l) => l.status === 'done' || l.status === 'error').length
  if (!props.leaves.length) return ''
  return props.busy ? `생성 중 ${done}/${props.leaves.length}` : `${done}/${props.leaves.length} 완료`
})

/** 장(최상위 노드) 제목 + 리프 블록 목록.
 *  리프 순서는 GeneratorPage.flattenLeaves와 동일하므로 큐에서 순서대로 소비한다. */
const blocks = computed(() => {
  if (!props.sections.length) return props.leaves.map((leaf) => ({ type: 'leaf', leaf }))
  const countLeaves = (node) =>
    node.children && node.children.length
      ? node.children.reduce((n, c) => n + countLeaves(c), 0)
      : 1
  const queue = [...props.leaves]
  const out = []
  for (const chapter of props.sections) {
    out.push({ type: 'chapter', name: chapter.name })
    for (let i = countLeaves(chapter); i > 0 && queue.length; i--) {
      out.push({ type: 'leaf', leaf: queue.shift() })
    }
  }
  return out
})

/** 개요기호(□·○·ㅇ·-·※·* 등) 앞에서 항상 줄을 나눈다 — 문서 조립
 *  (app/services/numbering.split_outline_runs)과 같은 규칙. 표 행(|…)은 제외 */
const OUTLINE_BREAK_RE =
  /(?<=[\s.)\]!?])(?=[①-⑳㉠-㉻□■◇◆○●◎◦ㆍ·•※]|[-–—―]\s|ㅇ\s|\*\s|\(\d{1,2}\)\s|\([가-하]\)\s|\d{1,2}\)\s)/g

/** 개조식 들여쓰기 단계 — □ 0 / ○ 16px / ― 30px (README·프로토타입 규칙) */
function indentLevel(line) {
  if (/^[○●◎◦ㅇ]/.test(line)) return 1
  if (/^[-–—―ㆍ·•]/.test(line)) return 2
  return 0
}

/** 섹션 마크다운 → 문서형 HTML: 표(|…)는 marked 표 렌더링,
 *  나머지 줄은 개요기호별 들여쓰기 문단으로. 반드시 DOMPurify를 거친다. */
function renderSection(text) {
  const lines = text
    .split('\n')
    .map((line) => (line.trimStart().startsWith('|') ? line : line.replace(OUTLINE_BREAK_RE, '\n')))
    .join('\n')
    .split('\n')

  const html = []
  let tableLines = []
  const flushTable = () => {
    if (!tableLines.length) return
    html.push(
      `<div class="doc-table-wrap markdown">${marked.parse(tableLines.join('\n'), { async: false })}</div>`
    )
    tableLines = []
  }
  for (const raw of lines) {
    if (raw.trimStart().startsWith('|')) {
      tableLines.push(raw)
      continue
    }
    flushTable()
    const line = raw.trim()
    if (!line) continue
    html.push(
      `<p class="doc-line indent-${indentLevel(line)}">${marked.parseInline(line, { async: false })}</p>`
    )
  }
  flushTable()
  return DOMPurify.sanitize(html.join(''))
}
</script>
