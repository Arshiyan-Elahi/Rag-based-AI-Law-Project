import { getCybrainAccessToken } from './authSession'
import { getAppLanguage, getFriendlyErrorMessage } from './friendlyErrorMessage'

const API_BASE = import.meta.env.VITE_API_BASE || ''

/** One HTTP round-trip; wait for the full /api/ai/action response (no job polling). */
const AI_ACTION_TIMEOUT_MS = 600000

const _inFlightKeys = new Set()

function buildOptionalAuthHeaders() {
  if (typeof window === 'undefined') return {}
  const token = getCybrainAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function normalizeAiActionName(action) {
  return String(action || '').trim().toLowerCase().replace(/-/g, '_')
}

function buildInFlightKey(payload) {
  const action = normalizeAiActionName(payload?.action)
  const scope = payload?.edit_scope || ''
  const sop = payload?.sop_entity_id || payload?.document_id || ''
  const textKey = String(payload?.text || '').slice(0, 120)
  return `${action}:${scope}:${sop}:${textKey}`
}

function friendlyClientError(err, context) {
  console.error(`[${context}]`, err)
  const msg = getFriendlyErrorMessage(getAppLanguage())
  const e = new Error(msg)
  e.isFriendlyError = true
  if (err?.status) e.status = err.status
  return e
}

async function parseApiError(res, fallbackMsg) {
  let detail = fallbackMsg
  try {
    const rawBody = await res.text()
    if (rawBody) {
      try {
        const body = JSON.parse(rawBody)
        const d = body?.detail
        if (typeof d === 'object' && d?.message) detail = String(d.message)
        else if (typeof d === 'string') detail = d
        else if (body?.message) detail = String(body.message)
      } catch {
        detail = rawBody.slice(0, 200)
      }
    }
  } catch {
    // ignore
  }
  const err = new Error(detail)
  err.status = res.status
  throw err
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } catch (err) {
    if (err?.name === 'AbortError') {
      const timeoutErr = new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`)
      timeoutErr.name = 'TimeoutError'
      throw friendlyClientError(timeoutErr, 'fetch-timeout')
    }
    throw err
  } finally {
    window.clearTimeout(timer)
  }
}

function buildActionRequestBody(payload) {
  const action = normalizeAiActionName(payload?.action)
  return {
    action,
    text: payload?.text || '',
    sop_title: payload?.sop_title || null,
    section_name: payload?.section_name || payload?.section_title || null,
    section_type: payload?.section_type || null,
    edit_scope: payload?.edit_scope || null,
    client_structured_json: payload?.client_structured_json || null,
    content_json: payload?.content_json || null,
    patch_node_ids: payload?.patch_node_ids || null,
    sop_entity_id: payload?.sop_entity_id || null,
    triggered_by: payload?.triggered_by || null,
    assistant_instruction: payload?.assistant_instruction || null,
  }
}

export async function performAIActionSync(payload) {
  const res = await fetchWithTimeout(
    `${API_BASE}/api/ai/action`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...buildOptionalAuthHeaders() },
      body: JSON.stringify(buildActionRequestBody(payload)),
    },
    AI_ACTION_TIMEOUT_MS,
  )
  if (!res.ok) await parseApiError(res, 'AI action failed')
  const data = await res.json()
  const suggested = data?.suggested_text
  const hasStructured = Boolean(data?.structured_data && typeof data.structured_data === 'object')
  if (
    !suggested
    && !data?.suggested_content_json
    && !hasStructured
  ) {
    throw friendlyClientError(new Error('empty response'), 'ai-action-empty')
  }
  return data
}

/**
 * Run an editor AI action via synchronous POST /api/ai/action.
 * @param {object} payload
 * @param {{ allowDuplicate?: boolean }} [options]
 */
export async function runAIAction(payload, options = {}) {
  const key = buildInFlightKey(payload)
  if (!options?.allowDuplicate) {
    if (_inFlightKeys.has(key)) {
      const err = friendlyClientError(new Error('duplicate'), 'ai-action-duplicate')
      err.isDuplicate = true
      throw err
    }
    _inFlightKeys.add(key)
  }

  try {
    return await performAIActionSync(payload)
  } catch (err) {
    if (err?.isFriendlyError || err?.isDuplicate) throw err
    throw friendlyClientError(err, 'ai-action')
  } finally {
    _inFlightKeys.delete(key)
  }
}

const CHAT_QUERY_TIMEOUT_MS = 150000
const CHAT_QUERY_RETRIES = 1

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

function isRetryableClientError(err) {
  if (!err) return false
  if (err?.name === 'AbortError' || err?.name === 'TimeoutError') return true
  const msg = String(err?.message || '').toLowerCase()
  return (
    msg.includes('timeout')
    || msg.includes('timed out')
    || msg.includes('empty')
    || msg.includes('no suggestion')
  )
}

export async function fetchChatQueryWithRetry(payload, timeoutMs = CHAT_QUERY_TIMEOUT_MS) {
  let lastErr
  for (let attempt = 0; attempt <= CHAT_QUERY_RETRIES; attempt += 1) {
    try {
      const res = await fetchWithTimeout(
        `${API_BASE}/api/ai/query`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...buildOptionalAuthHeaders() },
          body: JSON.stringify(payload),
        },
        timeoutMs,
      )
      if (!res.ok) await parseApiError(res, 'AI query failed')
      const data = await res.json()
      const answer = String(data?.answer ?? data?.response ?? '').trim()
      if (!answer && attempt < CHAT_QUERY_RETRIES) {
        await sleep(800)
        continue
      }
      return data
    } catch (err) {
      lastErr = err
      if (attempt < CHAT_QUERY_RETRIES && (err?.name === 'AbortError' || isRetryableClientError(err))) {
        await sleep(800)
        continue
      }
      if (err?.isFriendlyError) throw err
      throw friendlyClientError(err, 'chat-query')
    }
  }
  throw lastErr || friendlyClientError(new Error('unknown'), 'chat-query')
}
