<template>
  <!-- 로그인 화면 -->
  <div v-if="!user" class="login-screen">
    <LoginForm @success="onLogin" />
    <p v-if="authNotice" class="auth-notice">{{ authNotice }}</p>
  </div>

  <!-- 메인 화면: 상단바 + 좌 채팅 / 우 미리보기 -->
  <div v-else class="app-shell">
    <header class="topbar">
      <span class="brand">hwpx 대화 편집기</span>
      <button type="button" :disabled="uploading" @click="fileInput?.click()">
        {{ uploading ? '업로드 중…' : 'hwpx 업로드' }}
      </button>
      <input
        ref="fileInput"
        type="file"
        accept=".hwpx"
        class="hidden-input"
        @change="onFileChange"
      />
      <span class="doc-title">{{ doc ? doc.title : '열린 문서 없음' }}</span>
      <span class="topbar-spacer"></span>
      <button type="button" :disabled="!doc || exporting" @click="download">
        {{ exporting ? '내보내는 중…' : 'hwpx 다운로드' }}
      </button>
      <span class="user-name">{{ user.user_name }}</span>
      <button type="button" class="ghost-btn" @click="logout">로그아웃</button>
    </header>

    <div v-if="banner" class="banner-error">
      {{ banner }}
      <button type="button" class="ghost-btn" @click="banner = ''">닫기</button>
    </div>

    <div ref="mainRef" class="main-split">
      <section class="chat-pane" :style="{ width: leftPct + '%' }">
        <ChatPanel
          :messages="messages"
          :sending="sending"
          :status-text="statusText"
          @send="sendMessage"
        />
      </section>
      <div
        class="divider"
        role="separator"
        aria-orientation="vertical"
        @mousedown.prevent="startDrag"
      ></div>
      <section class="preview-pane">
        <PreviewPanel
          :html="previewHtml"
          :changed-ids="changedIds"
          :selected-ids="selectedIds"
          :page-count="doc ? doc.pages : 0"
          :version="doc ? doc.version : 0"
          @toggle-select="toggleSelect"
          @clear-selection="selectedIds = []"
        />
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import LoginForm from './components/LoginForm.vue'
import ChatPanel from './components/ChatPanel.vue'
import PreviewPanel from './components/PreviewPanel.vue'
import {
  clearToken,
  exportDocument,
  getMessages,
  getPreview,
  getToken,
  streamChat,
  uploadDocument,
} from './api.js'

const STATE_KEY = 'hwpx_chat_state'

// ---- 인증 상태
const user = ref(null)
const authNotice = ref('')

// ---- 문서/세션 상태
const doc = ref(null) // { id, title, pages, version }
const previewHtml = ref('')
const changedIds = ref([])
const selectedIds = ref([])
const sessionId = ref(null)

// ---- 채팅 상태
const messages = ref([]) // { role, content, pending? }
const sending = ref(false)
const statusText = ref('')

// ---- 기타 UI 상태
const banner = ref('')
const uploading = ref(false)
const exporting = ref(false)
const fileInput = ref(null)

const INTENT_LABEL = { edit: '문서 편집 중', fill: '내용 작성 중', query: '답변 생성 중' }

// ---------------------------------------------------------------- 인증

function onLogin(data) {
  user.value = data
  authNotice.value = ''
  sessionStorage.setItem('hwpx_chat_user', JSON.stringify(data))
}

function logout(notice = '') {
  clearToken()
  user.value = null
  doc.value = null
  previewHtml.value = ''
  messages.value = []
  sessionId.value = null
  selectedIds.value = []
  changedIds.value = []
  authNotice.value = typeof notice === 'string' ? notice : ''
  sessionStorage.removeItem('hwpx_chat_user')
  sessionStorage.removeItem(STATE_KEY)
}

/** 새로고침 후 토큰·세션 복원 (실패해도 조용히 무시) */
onMounted(async () => {
  if (!getToken()) return
  try {
    const savedUser = sessionStorage.getItem('hwpx_chat_user')
    if (savedUser) user.value = JSON.parse(savedUser)
    else user.value = { user_name: '(세션 복원)' }
    const saved = sessionStorage.getItem(STATE_KEY)
    if (!saved) return
    const state = JSON.parse(saved)
    if (state.documentId) {
      const preview = await getPreview(state.documentId)
      doc.value = {
        id: state.documentId,
        title: state.title || '문서',
        pages: preview.page_count,
        version: preview.version,
      }
      previewHtml.value = preview.html
    }
    if (state.sessionId) {
      sessionId.value = state.sessionId
      const rows = await getMessages(state.sessionId)
      messages.value = rows.map((r) => ({ role: r.role, content: r.content }))
    }
  } catch {
    // 복원 실패는 치명적이지 않음 — 새 상태로 시작
  }
})

function persistState() {
  sessionStorage.setItem(
    STATE_KEY,
    JSON.stringify({
      documentId: doc.value?.id || null,
      title: doc.value?.title || '',
      sessionId: sessionId.value,
    })
  )
}

// ---------------------------------------------------------------- 업로드/미리보기

async function onFileChange(e) {
  const file = e.target.files?.[0]
  e.target.value = '' // 같은 파일 재선택 허용
  if (!file) return
  uploading.value = true
  banner.value = ''
  try {
    const meta = await uploadDocument(file)
    doc.value = {
      id: meta.document_id,
      title: meta.title,
      pages: meta.pages,
      version: meta.version,
    }
    selectedIds.value = []
    changedIds.value = []
    const preview = await getPreview(meta.document_id, meta.version)
    previewHtml.value = preview.html
    doc.value.pages = preview.page_count
    doc.value.version = preview.version
    persistState()
  } catch (err) {
    if (err.status === 401) return logout('세션이 만료되었습니다. 다시 로그인하세요.')
    banner.value = `업로드 실패: ${err.message}`
  } finally {
    uploading.value = false
  }
}

/** 서버 최신 버전으로 미리보기를 다시 로드한다 (버전 충돌 복구용) */
async function refreshPreview(version) {
  if (!doc.value) return
  try {
    const preview = await getPreview(doc.value.id, version)
    previewHtml.value = preview.html
    doc.value.pages = preview.page_count
    doc.value.version = preview.version
    changedIds.value = []
    selectedIds.value = []
  } catch {
    // 갱신 실패는 배너 메시지로 충분 — 다음 상호작용에서 재시도된다
  }
}

// ---------------------------------------------------------------- 다운로드

async function download() {
  if (!doc.value) return
  exporting.value = true
  banner.value = ''
  try {
    const { blob, filename } = await exportDocument(doc.value.id, 'hwpx')
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch (err) {
    if (err.status === 401) return logout('세션이 만료되었습니다. 다시 로그인하세요.')
    banner.value = `다운로드 실패: ${err.message}`
  } finally {
    exporting.value = false
  }
}

// ---------------------------------------------------------------- 채팅 (SSE)

function toggleSelect(id) {
  const idx = selectedIds.value.indexOf(id)
  if (idx === -1) selectedIds.value = [...selectedIds.value, id]
  else selectedIds.value = selectedIds.value.filter((v) => v !== id)
}

async function sendMessage(text) {
  if (sending.value) return
  sending.value = true
  statusText.value = ''
  banner.value = ''
  messages.value.push({ role: 'user', content: text })
  const assistant = { role: 'assistant', content: '', pending: true }
  messages.value.push(assistant)

  const payload = { message: text }
  if (sessionId.value) payload.session_id = sessionId.value
  if (doc.value) {
    payload.document_id = doc.value.id
    payload.base_version = doc.value.version // 버전 핀: 미리보기 중인 버전 기준으로만 적용
  }
  if (selectedIds.value.length) payload.selection = [...selectedIds.value]

  await streamChat(payload, {
    onStatus(data) {
      statusText.value = [INTENT_LABEL[data.intent] || data.intent, data.detail]
        .filter(Boolean)
        .join(' — ')
    },
    onToken(data) {
      assistant.content += data.text || ''
    },
    onDocumentUpdated(data) {
      previewHtml.value = data.html
      changedIds.value = data.changed_ids || []
      if (doc.value && doc.value.id === data.document_id) {
        doc.value.version = data.version
      } else if (!doc.value) {
        doc.value = { id: data.document_id, title: '생성된 문서', pages: 0, version: data.version }
      }
      selectedIds.value = []
      persistState()
    },
    onDone(data) {
      if (data.session_id) sessionId.value = data.session_id
      assistant.pending = false
      statusText.value = ''
      persistState()
    },
    onError(data) {
      assistant.pending = false
      statusText.value = ''
      if (data.code === 'auth') {
        logout('인증이 만료되었습니다. 다시 로그인하세요.')
        return
      }
      if (data.code === 'version_conflict') {
        // 문서가 다른 곳에서 수정됨 — 최신 미리보기로 갱신해 다시 시도할 수 있게 한다
        banner.value = data.message || '문서가 다른 곳에서 수정되었습니다. 미리보기를 갱신했습니다.'
        if (doc.value && data.current_version != null) refreshPreview(data.current_version)
        if (!assistant.content) assistant.content = '(문서 버전 충돌로 요청이 적용되지 않았습니다.)'
        return
      }
      const label =
        data.code === 'llm_unavailable'
          ? 'LLM 서버에 연결할 수 없습니다'
          : data.code === 'llm_timeout'
            ? 'LLM 응답 시간이 초과되었습니다'
            : '요청 처리 중 오류가 발생했습니다'
      banner.value = `${label}: ${data.message || ''}`
      if (!assistant.content) assistant.content = '(오류로 응답을 받지 못했습니다.)'
    },
  })

  assistant.pending = false
  sending.value = false
  statusText.value = ''
}

// ---------------------------------------------------------------- divider 드래그

const mainRef = ref(null)
const leftPct = ref(42)

function startDrag() {
  const onMove = (e) => {
    const rect = mainRef.value?.getBoundingClientRect()
    if (!rect) return
    const pct = ((e.clientX - rect.left) / rect.width) * 100
    leftPct.value = Math.min(75, Math.max(20, pct))
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.classList.remove('dragging')
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  document.body.classList.add('dragging')
}
</script>
