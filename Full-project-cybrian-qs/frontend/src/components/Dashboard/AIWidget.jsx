import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Send, Zap } from 'lucide-react'
import { nowTime, runUnifiedAssistantQuery, getAssistantRouteMeta, toHtml } from '../../utils/chatAssistant'
import { createDocument } from '../../api/editorApi'
import { htmlToPlainText, deriveSopTitleFromText, plainTextToTiptapDoc } from '../../utils/chatSopSave'
import { getAssistantContextStorageKeys, resetAssistantStateOnce } from '../../utils/assistantContext'
import './DashboardComponents.css'

const STORAGE_KEY_BY_PATH = 'ai_widget_messages_by_path_v3_reset'
resetAssistantStateOnce()

export default function AIWidget() {
  const location = useLocation()
  const navigate = useNavigate()
  const routeMeta = getAssistantRouteMeta(location.pathname)
  const [messages, setMessages] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_BY_PATH)
      const parsed = raw ? JSON.parse(raw) : {}
      const byPath = parsed?.[location.pathname]
      if (Array.isArray(byPath) && byPath.length > 0) return byPath
    } catch {
      // no-op
    }
    return [
      {
        id: `greeting-${Date.now()}`,
        role: 'ai',
        text: 'Chatbot ist verbunden. Stelle eine Frage zu SOPs, Abweichungen, CAPAs, Audits oder Entscheidungen.',
        tags: [],
        time: nowTime(),
      },
    ]
  })
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [pendingDeleteAction, setPendingDeleteAction] = useState(null)
  const [actionToast, setActionToast] = useState('')
  const chatEndRef = useRef(null)
  const messagesRef = useRef(messages)
  const serverSessionIdRef = useRef(null)
  const suggestions = routeMeta.suggestions

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

  // Auto-scroll to bottom when messages change or when loading indicator appears
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_BY_PATH)
      const parsed = raw ? JSON.parse(raw) : {}
      parsed[location.pathname] = messages
      localStorage.setItem(STORAGE_KEY_BY_PATH, JSON.stringify(parsed))
    } catch {
      // ignore storage failures
    }
  }, [location.pathname, messages])

  useEffect(() => {
    serverSessionIdRef.current = null
    try {
      const raw = localStorage.getItem(STORAGE_KEY_BY_PATH)
      const parsed = raw ? JSON.parse(raw) : {}
      const byPath = parsed?.[location.pathname]
      if (Array.isArray(byPath) && byPath.length > 0) {
        setMessages(byPath)
        return
      }
    } catch {
      // no-op
    }
    setMessages([
      {
        id: `greeting-${Date.now()}`,
        role: 'ai',
        text: 'Chatbot ist verbunden. Stelle eine Frage zu SOPs, Abweichungen, CAPAs, Audits oder Entscheidungen.',
        tags: [],
        time: nowTime(),
      },
    ])
  }, [location.pathname])

  const sendMessage = useCallback(async (text, opts = {}) => {
    const trimmed = text.trim()
    if (!trimmed || sending) return

    // Append user message immediately
    const userMsg = { id: Date.now(), role: 'user', text: trimmed }
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
      const result = await runUnifiedAssistantQuery({
        question: trimmed,
        pathname: location.pathname,
        chatHistory: chatHistoryPayload,
        assistantActionConfirmation: opts.assistantActionConfirmation || null,
        surface: 'kl_assistant',
        sessionId: serverSessionIdRef.current,
      })
      const action = result?.assistant_action
      if (action?.requires_confirmation && action?.type === 'delete_sop') {
        setPendingDeleteAction({
          question: trimmed,
          action,
        })
      } else {
        setPendingDeleteAction(null)
      }
      if (action?.ok && action?.type === 'create_sop' && action?.sop_id) {
        emitSOPRefresh('create', action.sop_id)
        showToast('SOP created successfully')
        navigate(`/editor/${action.sop_id}`)
      }
      if (action?.ok && action?.type === 'update_sop') {
        showToast('SOP updated successfully')
      }
      if (action?.ok && action?.type === 'delete_sop') {
        emitSOPRefresh('delete', action.sop_id)
        showToast('SOP deleted successfully')
        console.info('[assistant-delete-ui] delete success', action)
        const activeId = localStorage.getItem('current_document_id')
        if (activeId && action?.sop_id && String(activeId) === String(action.sop_id)) {
          clearAssistantActiveContext()
          navigate('/sops')
        }
      }
      const aiMsg = {
        id: Date.now() + 1,
        role: 'ai',
        text: result.answer || result.text || result.response || '—',
        tags: result.sources?.map(s => s.label) ?? [],
        time: nowTime(),
      }
      if (result.session_id) {
        serverSessionIdRef.current = result.session_id
      }
      setMessages(prev => [...prev, aiMsg])
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
  }, [sending, location.pathname, navigate, emitSOPRefresh, showToast, clearAssistantActiveContext])

  const handleSend = () => sendMessage(input)

  // Clicking a suggestion triggers the actual query immediately
  const handleSuggestionClick = (text) => sendMessage(text)

  const handleCreateSOP = useCallback(async (messageText) => {
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
  }, [navigate])

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

      {/* Context section (n_e1120) */}
      <div className="ai-context-section">
        {/* Context label row (n_36782, n_8a4b0, n_1632b) */}
        <div className="ai-context-row">
          <Zap size={14} className="ai-context-icon" />
          <span className="ai-context-label">{contextLabel}</span>
        </div>

        {/* Input and send button (n_af201, n_dfc55, n_61f05) */}
        <div className="ai-context-input-group">
          <input
            type="text"
            className="ai-context-input"
            placeholder="Frage zur SOP stellen…"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSend()}
            disabled={sending}
          />
          <button
            className="ai-context-send-btn"
            onClick={handleSend}
            disabled={sending || !input.trim()}
            aria-label="Senden"
          >
            Senden
          </button>
        </div>
      </div>

      <div className="ai-widget-divider" />

      {/* Chat message bubble (n_50e03) */}
      <div className="ai-messages-section">
        {messages.length > 0 && (
          <div className="ai-greeting-bubble">
            <p className="ai-greeting-text">{messages[0]?.text}</p>
            {messages[0]?.tags && messages[0]?.tags.length > 0 && (
              <div className="ai-message-tags">
                {messages[0]?.tags.map(tag => (
                  <span key={tag} className="ai-message-tag">{tag}</span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Additional messages */}
        {messages.slice(1).map(m => (
          <div
            key={m.id}
            className={`ai-chat-message ${m.role}${m.isError ? ' error' : ''}`}
          >
            <div className="ai-message-content" dangerouslySetInnerHTML={{ __html: toHtml(m.text) }} />
            {m.tags && m.tags.length > 0 && (
              <div className="ai-message-tags">
                {m.tags.map(tag => (
                  <span key={tag} className="ai-message-tag">{tag}</span>
                ))}
              </div>
            )}
            {m.role === 'ai' && !m.isError && /purpose|zweck|scope|geltungsbereich|procedure|verfahren|responsibilities|verantwortlichkeiten/i.test(m.text) && (
              <button 
                className="ai-kontext-btn" 
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
