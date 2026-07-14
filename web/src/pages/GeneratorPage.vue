<template>
  <div class="generator-page">
    <div v-if="banner" class="banner-error">
      {{ banner }}
      <button type="button" class="ghost-btn" @click="banner = ''">닫기</button>
    </div>
    <div class="generator-split">
      <!-- 좌: 기준정보 입력 패널 (API 항목명 기반 — 지시 3) -->
      <section class="criteria-pane">
        <CriteriaPanel
          ref="criteriaRef"
          @update:criteria="criteria = $event"
          @update:missing="missing = $event"
        />
      </section>

      <!-- 중: 채팅 (생성/작성 요청 → 목차 생성 트리거 — 지시 5) -->
      <section class="gen-chat-pane">
        <ChatPanel
          :messages="messages"
          :sending="sending"
          :status-text="statusText"
          @send="sendMessage"
        />
      </section>

      <!-- 우: 목차 뷰 ⇄ 본문 뷰 -->
      <section class="gen-result-pane">
        <p v-if="phase === 'idle'" class="gen-empty">
          왼쪽에서 기준정보를 입력하고, 채팅창에 "재난안전계획서를 작성해줘"라고
          요청하면 목차가 생성됩니다.
        </p>
        <TocView
          v-else-if="phase === 'toc'"
          :title="toc.title"
          :sections="toc.sections"
          :busy="sending"
          @regenerate="requestToc"
          @generate-content="generateContent"
        />
        <ReportView
          v-else
          :title="toc.title"
          :leaves="leaves"
          :busy="phase === 'generating'"
          @back-to-toc="phase = 'toc'"
          @export="doExport"
        />
      </section>
    </div>
  </div>
</template>

<script setup>
// 재난안전계획서 생성 도구 (T3Q 전환 — docs/t3q_upgrade_design.md §4)
// 흐름: 기준정보 입력 → (채팅 트리거) 목차 생성 → 목차 편집 → 본문 스트리밍 → 내보내기
import { ref } from 'vue'
import ChatPanel from '../components/ChatPanel.vue'
import CriteriaPanel from '../components/CriteriaPanel.vue'
import ReportView from '../components/ReportView.vue'
import TocView from '../components/TocView.vue'
import { exportReport, generateToc, streamReportContent } from '../api.js'

const criteriaRef = ref(null)
const criteria = ref({})
const missing = ref([])

const messages = ref([])
const sending = ref(false)
const statusText = ref('')
const banner = ref('')

const phase = ref('idle') // idle | toc | generating | done
const toc = ref({ title: '', sections: [] })
const leaves = ref([]) // [{name, status, content, references, error}]

// 생성/작성 요청 감지 키워드 (지시 5 — LLM 분류 없이 휴리스틱)
const TRIGGER_RE = /(작성|생성|만들|초안|목차)/

function say(content) {
  messages.value.push({ role: 'assistant', content })
}

async function sendMessage(text) {
  if (sending.value) return
  messages.value.push({ role: 'user', content: text })

  if (!TRIGGER_RE.test(text)) {
    say(
      '이 채팅은 재난안전계획서 생성 전용입니다. 왼쪽 패널에 기준정보를 입력한 뒤 ' +
      '"작성해줘"라고 요청하면 목차부터 생성합니다.'
    )
    return
  }
  // 주제가 비어 있으면 요청 문장을 주제로 흡수
  criteriaRef.value?.setSubject(text.replace(TRIGGER_RE, '').trim() || text)

  if (missing.value.length) {
    say(`다음 필수 기준정보를 입력해 주세요: ${missing.value.join(', ')}`)
    return
  }
  await requestToc()
}

/** 목차 생성 (API-RPT-001) — 재요청도 같은 경로 */
async function requestToc() {
  if (missing.value.length) {
    say(`다음 필수 기준정보를 입력해 주세요: ${missing.value.join(', ')}`)
    return
  }
  sending.value = true
  statusText.value = '목차 생성 중…'
  banner.value = ''
  try {
    const result = await generateToc(criteria.value)
    toc.value = { title: result.title || '', sections: result.sections || [] }
    phase.value = 'toc'
    say(
      `목차를 생성했습니다 (${countNodes(toc.value.sections)}개 항목). ` +
      '오른쪽에서 목차를 확인·수정하고 [본문 생성]을 눌러 주세요.'
    )
  } catch (err) {
    banner.value = `목차 생성 실패: ${err.message}`
    say('목차 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.')
  } finally {
    sending.value = false
    statusText.value = ''
  }
}

function countNodes(nodes) {
  let n = 0
  for (const node of nodes || []) n += 1 + countNodes(node.children)
  return n
}

/** 목차 트리의 리프 이름을 문서 순서로 평면화 (RPT-002 스트림 단위) */
function flattenLeaves(nodes, out = []) {
  for (const node of nodes || []) {
    if (node.children && node.children.length) flattenLeaves(node.children, out)
    else out.push(String(node.name || ''))
  }
  return out
}

const norm = (s) => String(s || '').replace(/\s+/g, '')

/** 본문 생성 (API-RPT-002, SSE) — 목차별 도착 즉시 문서에 반영 (지시 7·8) */
async function generateContent() {
  leaves.value = flattenLeaves(toc.value.sections).map((name) => ({
    name, status: 'waiting', content: '', references: [], error: '',
  }))
  phase.value = 'generating'
  sending.value = true
  statusText.value = '본문 생성 중…'
  banner.value = ''

  const findLeaf = (name) => {
    const exact = leaves.value.find((l) => l.name === name && l.status === 'waiting')
    if (exact) return exact
    const fuzzy = leaves.value.find((l) => norm(l.name) === norm(name) && l.status === 'waiting')
    if (fuzzy) return fuzzy
    return leaves.value.find((l) => l.status === 'waiting') || null // 순서 폴백
  }

  await streamReportContent(
    { criteria: criteria.value, sections: toc.value.sections },
    {
      onStatus(data) {
        statusText.value = data.detail || '본문 생성 중…'
      },
      onSection(data) {
        const leaf = findLeaf(data.name)
        if (!leaf) return
        leaf.status = 'done'
        leaf.content = data.content
        leaf.references = data.references || []
        statusText.value = `본문 생성 중… (${data.seq}/${data.total})`
      },
      onSectionError(data) {
        const leaf = findLeaf(data.name)
        if (!leaf) return
        leaf.status = 'error'
        leaf.error = data.error || '생성 오류'
      },
      onDone(data) {
        for (const leaf of leaves.value) {
          if (leaf.status === 'waiting') {
            leaf.status = 'error'
            leaf.error = '결과가 수신되지 않았습니다'
          }
        }
        const ok = data.received ?? leaves.value.filter((l) => l.status === 'done').length
        say(`본문 생성이 완료되었습니다 (${ok}/${data.total ?? leaves.value.length}개 목차). 내보내기로 저장하세요.`)
      },
      onError(data) {
        banner.value = `본문 생성 오류: ${data.message || ''}`
        say('본문 생성이 중단되었습니다. 완료된 목차까지는 내보낼 수 있습니다.')
      },
    }
  )
  phase.value = 'done'
  sending.value = false
  statusText.value = ''
}

/** 결과를 목차 트리에 배치해 내보낸다 (리프 순서 = flattenLeaves 순서) */
function attachContents(nodes, queue) {
  return (nodes || []).map((node) => {
    const children = node.children && node.children.length
      ? attachContents(node.children, queue)
      : []
    const out = { name: node.name, children }
    if (!children.length) {
      const leaf = queue.shift()
      if (leaf && leaf.status === 'done') {
        out.content = leaf.content
        out.references = leaf.references
      }
    }
    return out
  })
}

async function doExport(format) {
  banner.value = ''
  try {
    const tree = attachContents(toc.value.sections, [...leaves.value])
    const { blob, filename } = await exportReport(toc.value.title, tree, format)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch (err) {
    banner.value = `내보내기 실패: ${err.message}`
  }
}
</script>
