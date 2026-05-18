import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Send, Zap } from 'lucide-react'
import {
  nowTime,
  runUnifiedAssistantQuery,
  getAssistantRouteMeta,
  toVisibleUserMessage,
  toHtml,
  formatChatTimeFromIso,
  readKlAssistantMode,
  writeKlAssistantMode,
} from '../../utils/chatAssistant'
import {
  createDocument,
  getChatSessionMessages,
} from '../../api/editorApi'
import { htmlToPlainText, deriveSopTitleFromText, plainTextToTiptapDoc } from '../../utils/chatSopSave'
import { getAssistantContextStorageKeys, resetAssistantStateOnce } from '../../utils/assistantContext'
import {
  EDITOR_AI_ACTIONS,
  EDITOR_AI_ACTION_STATUS,
  describeEditorAiResult,
  detectEditorIntent,
  dispatchEditorAiActionRequest,
  getActiveEditorDocumentId,
  hasActiveSopEditor,
  makeEditorAiRequestId,
  SOP_EDITOR_CONTEXT_EVENT,
  subscribeEditorAiActionResult,
} from '../../utils/editorAiBridge'
import ActionsTab from './ActionsTab'
import { dispatchActionsTabRun } from '../../utils/editorActionsBridge'
import { runEditorGapCheck } from '../../utils/editorGapCheck'
import { buildGapCheckSidebarReport } from '../../utils/actionsTabGapReport'
import './DashboardComponents.css'

const WIDGET_TABS = [
  { id: 'chat', label: 'Chat' },
  { id: 'actions', label: 'Actions' },
]

const LS_SESSION_BY_PATH = 'cybrain_kl_chat_session_by_path'

resetAssistantStateOnce()

function readSessionIdForPath(pathname) {
  try {
    const raw = localStorage.getItem(LS_SESSION_BY_PATH)
    const j = raw ? JSON.parse(raw) : {}
    const sid = j?.[pathname]
    return sid && String(sid).trim() ? String(sid).trim() : null
  } catch {
    return null
  }
}

function writeSessionIdForPath(pathname, sessionId) {
  try {
    const raw = localStorage.getItem(LS_SESSION_BY_PATH)
    const j = raw ? JSON.parse(raw) : {}
    j[pathname] = sessionId
    localStorage.setItem(LS_SESSION_BY_PATH, JSON.stringify(j))
  } catch {
    // ignore
  }
}

function defaultGreeting() {
  return [
    {
      id: `greeting-${Date.now()}`,
      role: 'ai',
      text: 'Chatbot ist verbunden. Stelle eine Frage zu SOPs, Abweichungen, CAPAs, Audits oder Entscheidungen.',
      tags: [],
      time: nowTime(),
    },
  ]
}

function mapSourcesToWidgetTags(sources) {
  if (!Array.isArray(sources)) return []
  return sources.slice(0, 5).map((s, idx) => s?.label || s?.id || `Quelle ${idx + 1}`)
}

function dbMessagesToWidget(rows) {
  if (!Array.isArray(rows) || rows.length === 0) return defaultGreeting()
  return rows.map((m) => ({
    id: m.id,
    role: m.role === 'user' ? 'user' : 'ai',
    text:
      m.role === 'user'
        ? toVisibleUserMessage(m.content)
        : String(m.content || ''),
    tags: m.role === 'user' ? [] : mapSourcesToWidgetTags(m.sources),
    time: formatChatTimeFromIso(m.created_at),
  }))
}

export default function AIWidget() {
  const location = useLocation()
  const navigate = useNavigate()
  const routeMeta = getAssistantRouteMeta(location.pathname)
  const [messages, setMessages] = useState(() => defaultGreeting())
  const [input, setInput] = useState('')
  const [assistantMode, setAssistantMode] = useState(() => readKlAssistantMode())
  const [sending, setSending] = useState(false)
  const [pendingDeleteAction, setPendingDeleteAction] = useState(null)
  const [actionToast, setActionToast] = useState('')
  const chatEndRef = useRef(null)
  const messagesScrollRef = useRef(null)
  const messagesRef = useRef(messages)
  /** requestId -> { messageId, action } for in-flight editor bridge requests. */
  const pendingBridgeRef = useRef(new Map())
  const suggestions = routeMeta.suggestions
  const [sopEditorActive, setSopEditorActive] = useState(() => hasActiveSopEditor(location.pathname))
  const [widgetTab, setWidgetTab] = useState('chat')

  useEffect(() => {
    const syncEditorContext = () => {
      setSopEditorActive(hasActiveSopEditor(location.pathname))
    }
    syncEditorContext()
    window.addEventListener(SOP_EDITOR_CONTEXT_EVENT, syncEditorContext)
    window.addEventListener('storage', syncEditorContext)
    return () => {
      window.removeEventListener(SOP_EDITOR_CONTEXT_EVENT, syncEditorContext)
      window.removeEventListener('storage', syncEditorContext)
    }
  }, [location.pathname])

  useEffect(() => {
    setAssistantMode(readKlAssistantMode())
    if (!hasActiveSopEditor(location.pathname)) {
      setWidgetTab('chat')
    }
  }, [location.pathname])

  useEffect(() => {
    if (sopEditorActive && widgetTab === 'actions') {
      setAssistantMode('action')
      writeKlAssistantMode('action')
    }
  }, [sopEditorActive, widgetTab])

  useEffect(() => {
    if (assistantMode === 'query') setPendingDeleteAction(null)
  }, [assistantMode])

  const emitSOPRefresh = useCallback((reason, sopId) => {
    if (typeof window === 'undefined') return
    window.dispatchEvent(
      new CustomEvent('sops-refresh-request', {
        detail: { reason, sop_id: sopId || null },
      }),
    )
  }, [])

  const showToast = useCallback((text) => {
    setActionToast(text)
    window.setTimeout(() => setActionToast(''), 2400)
  }, [])
  const clearAssistantActiveContext = useCallback(() => {
    const keys = getAssistantContextStorageKeys()
    localStorage.removeItem('current_document_id')
    try {
      const editorRaw = localStorage.getItem(keys.editor)
      if (editorRaw) {
        const parsed = JSON.parse(editorRaw)
        const next = { ...(parsed || {}), sop: {}, linked: {}, editor_text: '' }
        localStorage.setItem(keys.editor, JSON.stringify(next))
      }
    } catch {
      // ignore storage parse failures
    }
    console.info('[assistant-delete-ui] cleared active assistant context')
  }, [])

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  useEffect(() => {
    const el = messagesScrollRef.current
    if (el) {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
      return
    }
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  const loadChatHistory = useCallback(async () => {
    const path = location.pathname
    const sid = readSessionIdForPath(path)
    if (!sid) {
      setMessages(defaultGreeting())
      return
    }
    try {
      const rows = await getChatSessionMessages(sid)
      setMessages(dbMessagesToWidget(rows))
    } catch (e) {
      console.error('[chat-history-load] AIWidget messages', e)
      setMessages(defaultGreeting())
    }
  }, [location.pathname])

  useEffect(() => {
    loadChatHistory()
  }, [loadChatHistory])

  useEffect(() => {
    if (widgetTab === 'chat') {
      loadChatHistory()
    }
  }, [widgetTab, loadChatHistory])

  useEffect(() => {
    const pending = pendingBridgeRef.current
    const unsubscribe = subscribeEditorAiActionResult((detail) => {
      const requestId = detail?.requestId
      if (!requestId) return
      const entry = pending.get(requestId)
      if (!entry) return
      console.info('[kl-editor-bridge-received]', { requestId, status: detail?.status, action: detail?.action })
      pending.delete(requestId)
      const statusText = describeEditorAiResult(detail)
      const isError = detail?.status === EDITOR_AI_ACTION_STATUS.ERROR
      setMessages((prev) => prev.map((m) => (
        m.id === entry.messageId
          ? { ...m, text: statusText, isError, _pendingBridge: false, time: nowTime() }
          : m
      )))
    })
    return () => {
      unsubscribe()
      pending.clear()
    }
  }, [])

  const handleWidgetTabChange = useCallback((tabId) => {
    setWidgetTab(tabId)
    if (tabId === 'chat') {
      setAssistantMode('query')
      writeKlAssistantMode('query')
    } else if (tabId === 'actions') {
      setAssistantMode('action')
      writeKlAssistantMode('action')
    }
  }, [])

  /**
   * Try to handle the message as an editor-side action (rewrite / improve /
   * gap_check / read of the active SOP) instead of a chat-only RAG query.
   * Returns true when the message was routed to the editor bridge.
   */
  const tryBridgeEditorAction = useCallback((text, userMessage, opts = {}) => {
    const { explicitAction = null } = opts
    if (widgetTab === 'chat') {
      const intent = explicitAction || detectEditorIntent(text)
      if (intent === EDITOR_AI_ACTIONS.GAP_CHECK && hasActiveSopEditor(location.pathname)) {
        setInput('')
        setSending(true)
        ;(async () => {
          try {
            const { result, target } = await runEditorGapCheck({ instruction: text })
            const report = buildGapCheckSidebarReport(result)
            const parts = report.sections.map((s) => {
              if (s.gapItems?.length) {
                return `${s.title}\n${s.gapItems.map((g) => `- ${g.issue}${g.recommendation ? `\n  → ${g.recommendation}` : ''}`).join('\n')}`
              }
              return s.body ? `${s.title}\n${s.body}` : s.title
            })
            const plain = parts.filter(Boolean).join('\n\n') || report.analysisPlain
            setMessages((prev) => [
              ...prev,
              userMessage,
              {
                id: `gap-chat-${Date.now()}`,
                role: 'ai',
                text: `Gap check — ${target.sectionName}${target.isFullDoc ? ' (full SOP)' : ''}\n\n${plain}`,
                tags: ['Gap Check'],
                time: nowTime(),
              },
            ])
          } catch (err) {
            setMessages((prev) => [
              ...prev,
              userMessage,
              {
                id: `gap-err-${Date.now()}`,
                role: 'ai',
                text: err?.message || 'Gap check failed.',
                isError: true,
                time: nowTime(),
              },
            ])
          } finally {
            setSending(false)
          }
        })()
        return true
      }
      if (intent === EDITOR_AI_ACTIONS.REWRITE || intent === EDITOR_AI_ACTIONS.IMPROVE) {
        dispatchActionsTabRun({ action: intent, prompt: text || '' })
        handleWidgetTabChange('actions')
        setMessages((prev) => [
          ...prev,
          userMessage,
          {
            id: `actions-redirect-${Date.now()}`,
            role: 'ai',
            text: 'Rewrite/Improve runs in the **Actions** tab with inline preview in the editor.',
            tags: [],
            time: nowTime(),
          },
        ])
        setInput('')
        return true
      }
      return false
    }
    if (assistantMode !== 'action') return false
    if (!hasActiveSopEditor(location.pathname)) return false
    const intent = explicitAction || detectEditorIntent(text)
    if (!intent) return false

    if (intent === EDITOR_AI_ACTIONS.GAP_CHECK) {
      dispatchActionsTabRun({ action: EDITOR_AI_ACTIONS.GAP_CHECK, prompt: text || '' })
      handleWidgetTabChange('actions')
      setMessages((prev) => [...prev, userMessage])
      setInput('')
      return true
    }

    if (intent === EDITOR_AI_ACTIONS.REWRITE || intent === EDITOR_AI_ACTIONS.IMPROVE) {
      dispatchActionsTabRun({ action: intent, prompt: text || '' })
      handleWidgetTabChange('actions')
      const userMsg = { id: Date.now(), role: 'user', text: text || '' }
      const statusMsg = {
        id: `actions-redirect-${Date.now()}`,
        role: 'ai',
        text: 'Rewrite/Improve läuft im Tab **Actions** mit Inline-Vorschau im Editor. Bitte dort Accept oder Reject wählen.',
        tags: [],
        time: nowTime(),
      }
      setMessages((prev) => [...prev, userMsg, statusMsg])
      setInput('')
      return true
    }

    const activeDocumentId = getActiveEditorDocumentId()
    if (!activeDocumentId) {
      const noSopMsg = {
        id: `no-sop-${Date.now()}`,
        role: 'ai',
        text: 'Please open an SOP in the editor first.',
        tags: [],
        time: nowTime(),
      }
      setMessages((prev) => [...prev, userMessage, noSopMsg])
      setInput('')
      return true
    }

    const placeholderId = `bridge-${Date.now()}`
    const placeholderText =
      intent === EDITOR_AI_ACTIONS.READ
        ? 'Bestätige aktive SOP im Editor…'
        : intent === EDITOR_AI_ACTIONS.GAP_CHECK
          ? 'Gap Check im Editor wird vorbereitet…'
          : intent === EDITOR_AI_ACTIONS.IMPROVE
            ? 'Verbesserungen werden im Editor vorbereitet…'
            : intent === EDITOR_AI_ACTIONS.SUMMARIZE
              ? 'Zusammenfassung wird im Editor vorbereitet…'
              : intent === EDITOR_AI_ACTIONS.ANALYZE
                ? 'Analyse wird im Editor vorbereitet…'
                : intent === EDITOR_AI_ACTIONS.COMPARE
                  ? 'Versionsvergleich wird geöffnet…'
                  : 'Rewrite wird im Editor vorbereitet…'
    const placeholderMsg = {
      id: placeholderId,
      role: 'ai',
      text: placeholderText,
      tags: [],
      time: nowTime(),
      _pendingBridge: true,
    }
    setMessages((prev) => [...prev, userMessage, placeholderMsg])

    const requestId = makeEditorAiRequestId()
    pendingBridgeRef.current.set(requestId, { messageId: placeholderId, action: intent })
    dispatchEditorAiActionRequest({
      action: intent,
      prompt: text || '',
      requestId,
      source: 'kl_assistant',
    })

    window.setTimeout(() => {
      const stillPending = pendingBridgeRef.current.get(requestId)
      if (!stillPending) return
      pendingBridgeRef.current.delete(requestId)
      setMessages((prev) => prev.map((m) => (
        m.id === stillPending.messageId
          ? { ...m, text: 'Editor-Aktion hat zu lange gedauert. Bitte erneut versuchen.', isError: true, _pendingBridge: false }
          : m
      )))
    }, 360000)

    return true
  }, [assistantMode, location.pathname, widgetTab, handleWidgetTabChange, setSending, setMessages])

  const sendMessage = useCallback(async (text, opts = {}) => {
    const trimmed = text.trim()
    if (!trimmed || sending) return

    // Append user message immediately
    const userMsg = { id: Date.now(), role: 'user', text: trimmed }

    // Editor-bridge fast path: when the user is in the SOP editor and the
    // message clearly targets the active SOP (rewrite/improve/gap_check/read),
    // run the structured /api/ai/action flow against the editor instead of a
    // chat-only RAG response. Chat output is then a status line confirming
    // the editor change so the assistant stays editor-connected.
    if (!opts.assistantActionConfirmation && tryBridgeEditorAction(trimmed, userMsg)) {
      setInput('')
      return
    }

    setMessages(prev => [...prev, userMsg])
    setInput('')
    setSending(true)

    try {
      const chatHistoryPayload = [
        ...messagesRef.current.map((msg) => ({
          role: msg.role === 'ai' ? 'assistant' : 'user',
          content: msg.text,
        })),
        { role: 'user', content: trimmed },
      ]
      const sid = readSessionIdForPath(location.pathname)
      const ragMode = widgetTab === 'chat' ? 'query' : assistantMode
      const result = await runUnifiedAssistantQuery({
        question: trimmed,
        pathname: location.pathname,
        chatHistory: chatHistoryPayload,
        assistantActionConfirmation: opts.assistantActionConfirmation || null,
        surface: 'kl_assistant',
        sessionId: sid,
        assistantMode: ragMode,
      })
      const action = result?.assistant_action
      if (assistantMode === 'action' && action?.requires_confirmation && action?.type === 'delete_sop') {
        setPendingDeleteAction({
          question: trimmed,
          action,
        })
      } else {
        setPendingDeleteAction(null)
      }
      if (assistantMode === 'action' && action?.ok && action?.type === 'create_sop' && action?.sop_id) {
        emitSOPRefresh('create', action.sop_id)
        showToast('SOP created successfully')
        navigate(`/editor/${action.sop_id}`)
      }
      if (assistantMode === 'action' && action?.ok && action?.type === 'update_sop') {
        showToast('SOP updated successfully')
      }
      if (assistantMode === 'action' && action?.ok && action?.type === 'delete_sop') {
        emitSOPRefresh('delete', action.sop_id)
        showToast('SOP deleted successfully')
        console.info('[assistant-delete-ui] delete success', action)
        const activeId = localStorage.getItem('current_document_id')
        if (activeId && action?.sop_id && String(activeId) === String(action.sop_id)) {
          clearAssistantActiveContext()
          navigate('/sops')
        }
      }
      if (result?.session_id) {
        writeSessionIdForPath(location.pathname, result.session_id)
        const rows = await getChatSessionMessages(result.session_id)
        setMessages(dbMessagesToWidget(rows))
      } else {
        const aiMsg = {
          id: Date.now() + 1,
          role: 'ai',
          text: result.answer || result.text || result.response || '—',
          tags: mapSourcesToWidgetTags(result.sources || result.citations),
          time: nowTime(),
        }
        setMessages((prev) => [...prev, aiMsg])
      }
    } catch (err) {
      // Graceful error message in chat
      const errMsg = {
        id: Date.now() + 1,
        role: 'ai',
        text: `Fehler: ${err.message || 'Unbekannter Fehler'}`,
        isError: true,
        time: nowTime(),
      }
      setMessages(prev => [...prev, errMsg])
    } finally {
      setSending(false)
    }
  }, [sending, location.pathname, navigate, emitSOPRefresh, showToast, clearAssistantActiveContext, assistantMode, widgetTab, tryBridgeEditorAction])

  const handleSend = () => sendMessage(input)

  // Clicking a suggestion triggers the actual query immediately
  const handleSuggestionClick = (text) => sendMessage(text)

  const handleCreateSOP = useCallback(async (messageText) => {
    if (assistantMode === 'query') return
    try {
      if (!messageText) return
      const htmlText = toHtml(messageText)
      const plain = htmlToPlainText(htmlText)
      const title = deriveSopTitleFromText(plain)
      const docJson = plainTextToTiptapDoc(plain)
      
      const created = await createDocument({
        title,
        doc_type: 'sop',
        doc_json: docJson,
        metadata_json: {
          sopStatus: 'draft',
          sopMetadata: {
            title,
            author: 'AI Assistant',
            reviewer: '',
            riskLevel: 'Medium',
            department: 'Quality',
            documentId: '',
            references: [],
            reviewDate: '',
            effectiveDate: '',
            regulatoryReferences: [],
          },
          auditTrail: [
            {
              action: 'generated_from_chatbot',
              note: 'SOP created from KL Assistant-generated content.',
              actor: 'AI Assistant',
              createdAt: new Date().toISOString(),
            },
          ],
        },
      })
      if (created?.id) {
        navigate(`/editor/${created.id}`)
      }
    } catch (err) {
      console.error('Failed to create SOP from AIWidget:', err)
    }
  }, [navigate, assistantMode])

  const contextLabel = routeMeta.contextLabel

  const confirmDelete = async () => {
    if (!pendingDeleteAction) return
    await sendMessage(pendingDeleteAction.question, {
      assistantActionConfirmation: {
        action: 'delete_sop',
        confirmed: true,
      },
    })
  }

  return (
    <div className="ai-widget-container">
      {actionToast ? (
        <div className="assistant-action-toast" role="status" aria-live="polite">
          {actionToast}
        </div>
      ) : null}
      {/* Header (n_93fb9) */}
      <div className="ai-widget-header-section">
        {/* Title row with status dot, title, and Aktiv badge (n_00925, n_93f3c, n_36ff5, n_a93c8, n_cf5e4) */}
        <div className="ai-widget-header-row">
          <div className="ai-widget-title-group">
            <span className="ai-status-dot" />
            <h3 className="ai-widget-title">KI Assistent</h3>
          </div>
          <span className="ai-aktiv-badge">Aktiv</span>
        </div>
        <div className="ai-widget-divider" />
      </div>

      {sopEditorActive ? (
        <div className="ai-widget-tabs" role="tablist" aria-label="Assistant panels">
          {WIDGET_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={widgetTab === tab.id}
              className={`ai-widget-tab${widgetTab === tab.id ? ' ai-widget-tab--active' : ''}`}
              onClick={() => handleWidgetTabChange(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      ) : null}

      {sopEditorActive && widgetTab === 'actions' ? (
        <ActionsTab onSwitchToActions={() => handleWidgetTabChange('actions')} />
      ) : (
        <>
      {/* Context section (n_e1120) */}
      <div className="ai-context-section">
        {/* Context label row (n_36782, n_8a4b0, n_1632b) */}
        <div className="ai-context-row">
          <Zap size={14} className="ai-context-icon" />
          <span className="ai-context-label">{contextLabel}</span>
        </div>
      </div>

      <div className="ai-widget-divider" />

      {/* Chat messages (DB-backed when authenticated) */}
      <div className="ai-messages-section" ref={messagesScrollRef}>
        {messages.map((m, idx) => (
          <div
            key={m.id}
            className={`ai-chat-message ${m.role}${m.isError ? ' error' : ''}${idx === 0 && String(m.id).startsWith('greeting') ? ' ai-greeting-bubble' : ''}`}
          >
            {idx === 0 && String(m.id).startsWith('greeting') ? (
              <p className="ai-greeting-text">{m.text}</p>
            ) : (
              <div className="ai-message-content" dangerouslySetInnerHTML={{ __html: toHtml(m.text) }} />
            )}
            {m.tags && m.tags.length > 0 && (
              <div className="ai-message-tags">
                {m.tags.map((tag) => (
                  <span key={tag} className="ai-message-tag">
                    {tag}
                  </span>
                ))}
              </div>
            )}
            {m.role === 'ai' && !m.isError && assistantMode === 'action' && /purpose|zweck|scope|geltungsbereich|procedure|verfahren|responsibilities|verantwortlichkeiten/i.test(m.text) && (
              <button
                className="ai-kontext-btn"
                type="button"
                style={{ marginTop: '10px', padding: '6px 12px', fontSize: '11px', minHeight: 'auto', borderRadius: '4px' }}
                onClick={() => handleCreateSOP(m.text)}
              >
                Als SOP speichern
              </button>
            )}
          </div>
        ))}

        {sending && (
          <div
            className="ai-typing-indicator"
            role="status"
            aria-live="polite"
            aria-label="Antwort wird generiert"
          >
            <span />
            <span />
            <span />
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      <div className="ai-widget-divider" />

      {/* Quick suggestions section (n_8bfec, n_497dc, n_a2601, n_11cef, n_9d5ed) */}
      <div className="ai-quick-section">
        <h4 className="ai-quick-title">Schnelle Fragen</h4>
        <div className="ai-quick-list">
          {suggestions.map(text => (
            <button
              key={text}
              className="ai-quick-item"
              onClick={() => handleSuggestionClick(text)}
              disabled={sending}
            >
              {text}
            </button>
          ))}
        </div>
      </div>

      <div className="ai-widget-divider" />

      {/* Bottom input area (n_5a7d9, n_0cb35, n_afcae) */}
      <div className="ai-bottom-input-section">
        <div className="ai-bottom-input-group">
          <input
            type="text"
            placeholder="Frage zur SOP stellen…"
            className="ai-bottom-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSend()}
            disabled={sending}
          />
          <button
            className="ai-bottom-send-btn"
            onClick={handleSend}
            disabled={sending || !input.trim()}
            aria-label="Senden"
          >
            <Send size={14} />
          </button>
        </div>
      </div>

        </>
      )}

      {pendingDeleteAction ? (
        <div className="sop-delete-modal-overlay" role="presentation">
          <div className="sop-delete-modal" role="dialog" aria-modal="true" aria-labelledby="assistant-delete-title">
            <h3 id="assistant-delete-title" className="sop-delete-title">SOP wirklich löschen?</h3>
            <p className="sop-delete-message">
              Diese Aktion blendet die aktuell aktive SOP aus dem Workspace aus. Sie können den Löschvorgang jetzt bestätigen oder abbrechen.
            </p>
            <div className="sop-delete-actions">
              <button
                type="button"
                className="sop-delete-btn sop-delete-btn-cancel"
                onClick={() => setPendingDeleteAction(null)}
                disabled={sending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="sop-delete-btn sop-delete-btn-confirm"
                onClick={confirmDelete}
                disabled={sending}
              >
                {sending ? 'Deleting...' : 'OK'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
