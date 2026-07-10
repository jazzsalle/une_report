// api.js — 백엔드 REST/SSE 래퍼. Bearer 토큰은 메모리 + sessionStorage에 보관한다.

const TOKEN_KEY = 'hwpx_chat_token'

let token = sessionStorage.getItem(TOKEN_KEY) || null

export function getToken() {
  return token
}

export function setToken(value) {
  token = value || null
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

export function clearToken() {
  setToken(null)
}

function authHeaders() {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 응답이 실패면 detail을 뽑아 ApiError로 던지고, 성공이면 JSON을 반환한다. */
async function handleJson(res) {
  if (!res.ok) {
    throw new ApiError(await extractErrorMessage(res), res.status)
  }
  return res.json()
}

async function extractErrorMessage(res) {
  let msg = `요청 실패 (HTTP ${res.status})`
  try {
    const body = await res.json()
    if (body && body.detail) {
      msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } else if (body && body.message) {
      msg = body.message
    }
  } catch {
    /* JSON 본문이 아니면 기본 메시지 유지 */
  }
  return msg
}

// ---------------------------------------------------------------- REST

/** POST /api/auth/login → { token, user_name }. 성공 시 토큰을 저장한다. */
export async function login(account, password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account, password }),
  })
  const data = await handleJson(res)
  setToken(data.token)
  return data
}

/** POST /api/documents (multipart, 필드명 file) → { document_id, title, pages, version } */
export async function uploadDocument(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/documents', {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  return handleJson(res)
}

/** GET /api/documents/{id}/preview?version= → { html, page_count, version } */
export async function getPreview(documentId, version) {
  const query = version != null ? `?version=${encodeURIComponent(version)}` : ''
  const res = await fetch(`/api/documents/${documentId}/preview${query}`, {
    headers: authHeaders(),
  })
  return handleJson(res)
}

/** GET /api/sessions/{id}/messages → [{ role, content, intent, created_at }] */
export async function getMessages(sessionId) {
  const res = await fetch(`/api/sessions/${sessionId}/messages`, {
    headers: authHeaders(),
  })
  return handleJson(res)
}

/** Content-Disposition 헤더에서 파일명을 추출한다 (filename* 우선, RFC 5987). */
export function parseContentDisposition(header) {
  if (!header) return null
  const star = header.match(/filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      return star[1].trim()
    }
  }
  const quoted = header.match(/filename\s*=\s*"([^"]+)"/)
  if (quoted) return quoted[1]
  const bare = header.match(/filename\s*=\s*([^;]+)/)
  if (bare) return bare[1].trim()
  return null
}

/** POST /api/documents/{id}/export → { blob, filename } */
export async function exportDocument(documentId, format = 'hwpx') {
  const res = await fetch(`/api/documents/${documentId}/export`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ format }),
  })
  if (!res.ok) {
    throw new ApiError(await extractErrorMessage(res), res.status)
  }
  const blob = await res.blob()
  const filename =
    parseContentDisposition(res.headers.get('Content-Disposition')) || `document.${format}`
  return { blob, filename }
}

// ---------------------------------------------------------------- SSE

/**
 * POST /api/chat 를 fetch + ReadableStream으로 소비한다.
 * (EventSource는 POST를 지원하지 않으므로 event:/data: 라인을 직접 파싱.
 *  빈 줄이 이벤트 경계이고, data: 가 여러 줄이면 \n으로 결합한다.)
 *
 * @param {{session_id?: string, document_id?: string, message: string, selection?: number[]}} payload
 * @param {{onStatus?, onToken?, onDocumentUpdated?, onDone?, onError?}} callbacks
 */
export async function streamChat(payload, callbacks = {}) {
  let res
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        ...authHeaders(),
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify(payload),
    })
  } catch (e) {
    callbacks.onError?.({ code: 'network', message: `서버에 연결할 수 없습니다: ${e.message}` })
    return
  }

  if (!res.ok || !res.body) {
    const message = await extractErrorMessage(res)
    callbacks.onError?.({
      code: res.status === 401 ? 'auth' : 'bad_request',
      message,
    })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let eventName = 'message'
  let dataLines = []

  const dispatch = () => {
    if (dataLines.length === 0) {
      eventName = 'message'
      return
    }
    const raw = dataLines.join('\n')
    const name = eventName
    dataLines = []
    eventName = 'message'
    let data
    try {
      data = JSON.parse(raw)
    } catch {
      data = { text: raw }
    }
    switch (name) {
      case 'status':
        callbacks.onStatus?.(data)
        break
      case 'token':
        callbacks.onToken?.(data)
        break
      case 'document_updated':
        callbacks.onDocumentUpdated?.(data)
        break
      case 'done':
        callbacks.onDone?.(data)
        break
      case 'error':
        callbacks.onError?.(data)
        break
      default:
        break // 알 수 없는 이벤트는 무시
    }
  }

  const handleLine = (line) => {
    if (line === '') {
      dispatch()
      return
    }
    if (line.startsWith(':')) return // SSE 주석(keep-alive)
    const idx = line.indexOf(':')
    const field = idx === -1 ? line : line.slice(0, idx)
    let value = idx === -1 ? '' : line.slice(idx + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') eventName = value
    else if (field === 'data') dataLines.push(value)
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let nl
      while ((nl = buffer.indexOf('\n')) !== -1) {
        let line = buffer.slice(0, nl)
        buffer = buffer.slice(nl + 1)
        if (line.endsWith('\r')) line = line.slice(0, -1)
        handleLine(line)
      }
    }
    // 스트림 종료: 남은 버퍼 처리 후 미완 이벤트 강제 디스패치
    buffer += decoder.decode()
    if (buffer) {
      for (const rawLine of buffer.split('\n')) {
        handleLine(rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine)
      }
    }
    dispatch()
  } catch (e) {
    callbacks.onError?.({ code: 'network', message: `스트림이 중단되었습니다: ${e.message}` })
  }
}
