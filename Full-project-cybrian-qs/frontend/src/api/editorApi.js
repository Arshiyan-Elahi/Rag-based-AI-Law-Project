import { getCybrainAccessToken } from '../utils/authSession'
import { runAIAction, fetchChatQueryWithRetry } from '../utils/aiActionClient'
import { getAppLanguage, getFriendlyErrorMessage } from '../utils/friendlyErrorMessage'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const SIDEBAR_COUNTS_REFRESH_EVENT = 'sidebar-counts-refresh'

/** Merge Authorization when a JWT is stored (enables server-side chat history on /api/ai/query). */
function buildOptionalAuthHeaders() {
  if (typeof window === 'undefined') return {}
  const token = getCybrainAccessToken()
  if (token) {
    return { Authorization: `Bearer ${token}` }
  }
  return {}
}

/** True when a Bearer token is available (optional; chat persistence works without it). */
export function hasChatAuthToken() {
  return Boolean(getCybrainAccessToken())
}

function notifySidebarCountsRefresh() {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(SIDEBAR_COUNTS_REFRESH_EVENT))
}

// ─────────────────────────────────────────────────────
// Helper: parse error body and throw with backend message
// ─────────────────────────────────────────────────────

async function throwApiError(res, fallbackMsg) {
  let detail = fallbackMsg
  let rawBody = ''
  /** @type {{ validationOrParseError?: string, hint?: string }} */
  const extras = {}
  try {
    rawBody = await res.text()
    if (rawBody) {
      try {
        const body = JSON.parse(rawBody)
        if (body?.detail != null) {
          const d = body.detail
          if (Array.isArray(d)) {
            detail = d.map((x) => (typeof x === 'string' ? x : x?.msg || JSON.stringify(x))).join(' ')
          } else if (typeof d === 'object' && d !== null) {
            if (typeof d.message === 'string' && d.message) {
              detail = d.message
              if (d.validation_or_parse_error != null && String(d.validation_or_parse_error).trim()) {
                extras.validationOrParseError = String(d.validation_or_parse_error)
              }
              if (d.hint != null && String(d.hint).trim()) {
                extras.hint = String(d.hint)
              }
            } else {
              detail = JSON.stringify(d)
            }
          } else {
            detail = typeof d === 'string' ? d : JSON.stringify(d)
          }
        } else if (body?.message) {
          detail = String(body.message)
        } else if (body?.error) {
          detail = String(body.error)
        } else {
          detail = rawBody
        }
      } catch {
        detail = rawBody
      }
    }
  } catch {
    // Ignore non-JSON error bodies and fall back to the provided message.
  }
  if (import.meta?.env?.DEV) {
    console.error('[API Error]', {
      url: res.url,
      status: res.status,
      statusText: res.statusText,
      detail,
      rawBody: rawBody || null,
    })
  }
  const err = new Error(detail)
  err.status = res.status
  err.responseBody = rawBody
  if (extras.validationOrParseError) err.validationOrParseError = extras.validationOrParseError
  if (extras.hint) err.hint = extras.hint
  throw err
}

/**
 * Sort chat message rows for display: oldest first; when created_at ties (same DB flush),
 * user must precede assistant so the transcript reads in conversation order.
 * @param {unknown[]} rows
 * @returns {unknown[]}
 */
export function sortChatMessageRows(rows) {
  if (!Array.isArray(rows) || rows.length < 2) {
    return Array.isArray(rows) ? [...rows] : []
  }
  const roleRank = (r) => {
    const role = String(r?.role ?? '').toLowerCase()
    if (role === 'user') return 0
    if (role === 'assistant') return 1
    return 2
  }
  const timeMs = (r) => {
    const raw = r?.created_at
    if (raw == null) return 0
    const ms = new Date(raw).getTime()
    return Number.isFinite(ms) ? ms : 0
  }
  return [...rows].sort((a, b) => {
    const dt = timeMs(a) - timeMs(b)
    if (dt !== 0) return dt
    return roleRank(a) - roleRank(b)
  })
}

export async function listChatSessions() {
  const res = await fetch(`${API_BASE}/api/chat/sessions`, {
    headers: { ...buildOptionalAuthHeaders() },
  })
  if (!res.ok) await throwApiError(res, 'Failed to load chat sessions')
  return res.json()
}

export async function createChatSession(payload = {}) {
  const res = await fetch(`${API_BASE}/api/chat/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...buildOptionalAuthHeaders() },
    body: JSON.stringify({
      title: payload.title ?? null,
      collection_name: payload.collection_name || 'docs_sops',
      sop_id: payload.sop_id ? String(payload.sop_id).trim() : null,
    }),
  })
  if (!res.ok) await throwApiError(res, 'Failed to create chat session')
  return res.json()
}

/** Backend get-or-create: one active chat session per SOP (not browser storage). */
export async function resolveChatSessionBySop(sopId) {
  const sid = String(sopId || '').trim()
  if (!sid) throw new Error('sop_id is required')
  const res = await fetch(`${API_BASE}/api/chat/sessions/by-sop/${encodeURIComponent(sid)}`, {
    headers: { ...buildOptionalAuthHeaders() },
  })
  if (!res.ok) await throwApiError(res, 'Failed to resolve SOP chat session')
  return res.json()
}

export async function getChatSessionMessages(sessionId) {
  const sid = String(sessionId || '').trim()
  if (!sid) return []
  const res = await fetch(`${API_BASE}/api/chat/sessions/${encodeURIComponent(sid)}/messages`, {
    headers: { ...buildOptionalAuthHeaders() },
  })
  if (!res.ok) await throwApiError(res, 'Failed to load chat messages')
  const data = await res.json()
  return sortChatMessageRows(Array.isArray(data) ? data : [])
}

// ─────────────────────────────────────────────────────
// Document (sops) operations
// ─────────────────────────────────────────────────────

export async function createDocument(payload, options = {}) {
  const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 600000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  let res
  try {
    res = await fetch(`${API_BASE}/api/editor/docs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...buildOptionalAuthHeaders() },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
  } catch (err) {
    clearTimeout(timer)
    if (err?.name === 'AbortError') {
      throw new Error(
        'Saving the imported SOP timed out. The document may be very large; try again or import a smaller file.',
      )
    }
    throw err
  }
  clearTimeout(timer)
  if (import.meta?.env?.DEV) {
    console.debug('[createDocument] request payload', payload)
  }
  if (!res.ok) await throwApiError(res, 'Failed to create document')
  const data = await res.json()
  notifySidebarCountsRefresh()
  return data
}

export async function getDocuments() {
  // NOTE: GET /api/editor/docs (list) does NOT exist on the backend.
  // This function exists only for legacy editor compat — do NOT use for dashboard/SOP listing.
  // Use getSOPs() instead for any list/read flows.
  throw new Error('getDocuments() is not supported. Use getSOPs() for listing SOPs.')
}

export async function getDocument(docId) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}`)
  if (!res.ok) await throwApiError(res, 'Failed to load document')
  return res.json()
}

export async function updateDocument(docId, payload) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwApiError(res, 'Failed to update document')
  return res.json()
}

export async function duplicateDocument(docId, payload) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}/duplicate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwApiError(res, 'Failed to duplicate document')
  return res.json()
}

export async function deleteDocument(docId) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}`, {
    method: 'DELETE',
  })
  if (!res.ok) await throwApiError(res, 'Failed to delete document')
  const data = await res.json()
  notifySidebarCountsRefresh()
  return data
}

// ─────────────────────────────────────────────────────
// Version (sop_versions) operations
// ─────────────────────────────────────────────────────

export async function getVersions(docId) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}/versions`)
  if (!res.ok) await throwApiError(res, 'Failed to load versions')
  return res.json()
}

export async function createVersion(docId, payload) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwApiError(res, 'Failed to create version')
  return res.json()
}

export async function getVersion(docId, versionId) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}/versions/${versionId}`)
  if (!res.ok) await throwApiError(res, 'Failed to load version')
  return res.json()
}

export async function updateVersionStatus(docId, versionId, payload) {
  const res = await fetch(`${API_BASE}/api/editor/docs/${docId}/versions/${versionId}/status`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwApiError(res, 'Failed to update version status')
  return res.json()
}

// ─────────────────────────────────────────────────────
// Dashboard data APIs
// ─────────────────────────────────────────────────────

export async function getPublicSOPs(status = 'all') {
  const res = await fetch(`${API_BASE}/api/public/sops?status=${status}`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch SOPs')
  return res.json()
}

export async function getSOPs() {
  const res = await fetch(`${API_BASE}/api/sops`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch SOPs')
  return res.json()
}

export async function getDeviations() {
  const res = await fetch(`${API_BASE}/api/deviations`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch deviations')
  return res.json()
}

export async function getDeviationContext(deviationId) {
  const res = await fetch(`${API_BASE}/api/deviations/${deviationId}/context`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch deviation context')
  return res.json()
}

export async function getCAPAs() {
  const res = await fetch(`${API_BASE}/api/capas`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch CAPAs')
  return res.json()
}

export async function getAuditFindings() {
  const res = await fetch(`${API_BASE}/api/audits`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch audit findings')
  return res.json()
}

export async function getDecisions() {
  const res = await fetch(`${API_BASE}/api/decisions`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch decisions')
  return res.json()
}

export async function searchKnowledge(query) {
  const params = new URLSearchParams({ q: query })
  const res = await fetch(`${API_BASE}/api/search?${params}`)
  if (!res.ok) await throwApiError(res, 'Failed to perform knowledge search')
  return res.json()
}

export async function getKnowledgeStats() {
  const controller = new AbortController()
  const timeoutMs = 15000
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let res
  try {
    res = await fetch(`${API_BASE}/api/stats`, { signal: controller.signal })
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error('Knowledge stats request timed out. Check the API and network.')
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) await throwApiError(res, 'Failed to fetch knowledge stats')
  return res.json()
}

// TODO: Connect to real AI endpoint when available (e.g. POST /api/ai/query)
// The AI endpoint should accept { question: string } and return
// { answer: string, sources: Array<{ id: string, type: string, label: string }> }
/**
 * Semantic intent classification for the unified KL/KI Assistant chat panel.
 */
export async function classifyAssistantIntent(payload = {}) {
  const body = {
    message: payload.message || payload.question || '',
    route: payload.route || '',
    has_active_sop: Boolean(payload.has_active_sop),
    has_editor_selection: Boolean(payload.has_editor_selection),
  }
  if (payload.assistant_context && typeof payload.assistant_context === 'object') {
    body.assistant_context = payload.assistant_context
  }

  const controller = new AbortController()
  const timeoutMs = 25000
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let res
  try {
    res = await fetch(`${API_BASE}/api/ai/classify-intent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...buildOptionalAuthHeaders() },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error('Intent classification timed out.')
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) await throwApiError(res, 'Intent classification failed')
  return res.json()
}

export async function queryAI(question, options = {}) {
  const payload = { question }
  if (Array.isArray(options.chat_history) && options.chat_history.length > 0) {
    payload.chat_history = options.chat_history
  }
  if (options.category) {
    payload.category = options.category
  }
  if (options.assistant_context && typeof options.assistant_context === 'object') {
    payload.assistant_context = options.assistant_context
  }
  if (options.assistant_action_confirmation && typeof options.assistant_action_confirmation === 'object') {
    payload.assistant_action_confirmation = options.assistant_action_confirmation
  }
  if (options.surface) {
    payload.surface = options.surface
  }
  if (options.route) {
    payload.route = options.route
  }
  if (options.session_id && String(options.session_id).trim()) {
    payload.session_id = String(options.session_id).trim()
  }
  if (options.assistant_mode === 'query' || options.assistant_mode === 'action') {
    payload.assistant_mode = options.assistant_mode
  }

  return fetchChatQueryWithRetry(payload)
}

export async function createLink(payload) {
  const res = await fetch(`${API_BASE}/api/links`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) await throwApiError(res, 'Failed to create link')
  return res.json()
}

export async function deleteLink(linkType, linkId) {
  const res = await fetch(`${API_BASE}/api/links/${linkType}/${linkId}`, {
    method: 'DELETE',
  })
  if (!res.ok) await throwApiError(res, 'Failed to delete link')
  return res.json()
}

export async function getRelatedContext(sopId) {
  const res = await fetch(`${API_BASE}/api/sops/${sopId}/related`)
  if (!res.ok) await throwApiError(res, 'Failed to fetch related context')
  return res.json()
}

/**
 * Editor AI actions: synchronous POST /api/ai/action (rewrite/improve/gap_check included).
 * Errors surface as the global friendly message only.
 * @param {object} payload
 */
export async function performAIAction(payload) {
  try {
    return await runAIAction(payload)
  } catch (err) {
    if (err?.isDuplicate) {
      return null
    }
    if (err?.isFriendlyError) {
      throw err
    }
    console.error('[performAIAction]', err)
    throw new Error(getFriendlyErrorMessage(getAppLanguage()))
  }
}

export async function semanticReindex(entityId) {
  const res = await fetch(`${API_BASE}/api/semantic/reindex`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entity_id: entityId }),
  })
  if (!res.ok) await throwApiError(res, 'Failed to trigger reindexing')
  return res.json()
}

/**
 * @param {File} file
 * @param {{ timeoutMs?: number }} [options] — large PDFs/OCR can exceed default browser/proxy patience
 */
/**
 * Fast SOP upload — creates the document shell and processes extraction in the background.
 * @param {File} file
 * @param {{ timeoutMs?: number }} [options]
 */
export async function importSOPAsync(file, options = {}) {
  const formData = new FormData()
  formData.append('file', file)

  const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 120000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(`${API_BASE}/api/editor/sops/import-async`, {
      method: 'POST',
      body: formData,
      headers: { ...buildOptionalAuthHeaders() },
      signal: controller.signal,
    })
    if (!res.ok) await throwApiError(res, 'Failed to start SOP import')
    const data = await res.json()
    notifySidebarCountsRefresh()
    return data
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error('Upload timed out while starting background import. Please try again.')
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

export async function getImportJobStatus(jobId, options = {}) {
  const params = new URLSearchParams()
  if (options.versionId) params.set('version_id', String(options.versionId))
  if (options.sopId) params.set('sop_id', String(options.sopId))
  const qs = params.toString()
  const url = `${API_BASE}/api/editor/import-jobs/${encodeURIComponent(jobId)}${qs ? `?${qs}` : ''}`
  const res = await fetch(url)
  if (!res.ok) await throwApiError(res, 'Failed to load import status')
  return res.json()
}

export async function extractText(file, options = {}) {
  const formData = new FormData()
  formData.append('file', file)

  const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 600000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(`${API_BASE}/api/extract-text`, {
      method: 'POST',
      body: formData,
      signal: controller.signal,
    })
    if (!res.ok) await throwApiError(res, 'OCR extraction failed')
    const data = await res.json()
    console.debug('[OCR] API response', data)
    return data
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error(
        'Text extraction timed out. Very large files can take several minutes; try again or split the document.',
      )
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

/** URL for archived source PDF preview (scanned imports only). */
export function getSourcePdfUrl(versionId) {
  if (!versionId) return ''
  return `${API_BASE}/api/editor/versions/${encodeURIComponent(versionId)}/source-pdf`
}
