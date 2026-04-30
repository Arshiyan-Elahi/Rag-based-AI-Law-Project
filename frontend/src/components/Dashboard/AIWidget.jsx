import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { Send, Zap } from 'lucide-react'
import { nowTime, runUnifiedAssistantQuery, getAssistantRouteMeta } from '../../utils/chatAssistant'
import './DashboardComponents.css'

const STORAGE_KEY_BY_PATH = 'ai_widget_messages_by_path_v2_reset'

export default function AIWidget() {
  const location = useLocation()
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
  const chatEndRef = useRef(null)
  const messagesRef = useRef(messages)
  const suggestions = routeMeta.suggestions

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

  const sendMessage = useCallback(async (text) => {
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
      })
      const aiMsg = {
        id: Date.now() + 1,
        role: 'ai',
        text: result.answer || result.text || result.response || '—',
        tags: result.sources?.map(s => s.label) ?? [],
        time: nowTime(),
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
  }, [sending, location.pathname])

  const handleSend = () => sendMessage(input)

  // Clicking a suggestion triggers the actual query immediately
  const handleSuggestionClick = (text) => sendMessage(text)

  const contextLabel = routeMeta.contextLabel

  return (
    <div className="ai-widget-container">
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
            <p>{m.text}</p>
            {m.tags && m.tags.length > 0 && (
              <div className="ai-message-tags">
                {m.tags.map(tag => (
                  <span key={tag} className="ai-message-tag">{tag}</span>
                ))}
              </div>
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
    </div>
  )
}
