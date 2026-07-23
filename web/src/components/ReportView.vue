<template>
  <div class="report-view">
    <div class="report-toolbar">
      <span class="report-title">{{ title || '생성 문서' }}</span>
      <span v-if="progressText" class="report-progress">{{ progressText }}</span>
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
      <button type="button" class="ghost-btn" :disabled="busy" @click="$emit('back-to-toc')">
        목차로
      </button>
      <button type="button" :disabled="busy || !hasContent" @click="$emit('export', 'hwpx')">
        hwpx 내보내기
      </button>
      <button type="button" :disabled="busy || !hasContent" @click="$emit('export', 'docx')">
        docx 내보내기
      </button>
    </div>
    <div class="report-scroll">
      <div v-for="leaf in leaves" :key="leaf.name" class="report-section" :class="`is-${leaf.status}`">
        <div class="report-section-head">
          <span class="report-section-name">{{ leaf.name }}</span>
          <span class="report-status" :data-status="leaf.status">{{ STATUS_LABEL[leaf.status] }}</span>
        </div>
        <div v-if="leaf.error" class="report-section-error">{{ leaf.error }}</div>
        <div
          v-else-if="leaf.content"
          class="report-section-body markdown"
          v-html="renderMarkdown(leaf.content)"
        ></div>
        <ul v-if="leaf.references && leaf.references.length" class="report-refs">
          <li v-for="(ref, i) in leaf.references" :key="i">
            {{ ref.fileName }}<template v-if="ref.page"> (p.{{ ref.page }})</template>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
// 본문 생성 결과 뷰 (지시 8: 목차별 스트리밍 수신·상태 표시)
// 진행상태 구분은 요구사항 FUN-CADM-302004를 차용: 대기/진행중/완료/오류
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps({
  title: { type: String, default: '' },
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

/** 개요기호(□·○·ㅇ·-·※·* 등) 앞에서 항상 줄을 나눈다 — 문서 조립
 *  (app/services/numbering.split_outline_runs)과 같은 규칙. 표 행(|…)은 제외 */
const OUTLINE_BREAK_RE =
  /(?<=[\s.)\]!?])(?=[①-⑳㉠-㉻□■◇◆○●◎◦ㆍ·•※]|[-–—―]\s|ㅇ\s|\*\s|\(\d{1,2}\)\s|\([가-하]\)\s|\d{1,2}\)\s)/g

function withOutlineBreaks(text) {
  return text
    .split('\n')
    .map((line) => (line.trimStart().startsWith('|') ? line : line.replace(OUTLINE_BREAK_RE, '\n')))
    .join('\n')
}

/** 섹션 마크다운(표 포함) → HTML. 반드시 DOMPurify를 거친다.
 *  breaks: true — 개요기호 문장마다 <br>로 줄바꿈해 표시한다 */
function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(withOutlineBreaks(text), { async: false, breaks: true }))
}
</script>
