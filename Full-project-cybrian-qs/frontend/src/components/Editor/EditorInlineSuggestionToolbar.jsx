import React, { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, X } from 'lucide-react'

import {
  applyEditorInlineSuggestion,
  clearEditorInlineSuggestion,
} from '../../utils/editorActionsBridge'
import { INLINE_SHOWN_EVENT } from '../../utils/editorInlineAiFlow'

/**
 * Floating Accept/Reject toolbar for inline rewrite/improve previews (all entry points).
 */
export default function EditorInlineSuggestionToolbar() {
  const [pending, setPending] = useState(null)

  useEffect(() => {
    const onShown = (event) => {
      const { requestId, toolbarCoords, from, to, action } = event.detail || {}
      if (!requestId) return
      setPending({
        requestId,
        action: action || 'rewrite',
        coords: toolbarCoords,
        from,
        to,
      })
    }

    const onApplied = () => {
      setPending(null)
    }

    window.addEventListener(INLINE_SHOWN_EVENT, onShown)
    window.addEventListener('editor-actions-inline-applied', onApplied)
    return () => {
      window.removeEventListener(INLINE_SHOWN_EVENT, onShown)
      window.removeEventListener('editor-actions-inline-applied', onApplied)
    }
  }, [])

  const handleAccept = useCallback(() => {
    if (!pending?.requestId) return
    applyEditorInlineSuggestion(pending.requestId)
  }, [pending])

  const handleReject = useCallback(() => {
    if (!pending?.requestId) return
    clearEditorInlineSuggestion(pending.requestId)
    setPending(null)
  }, [pending])

  if (!pending) return null

  const top = Number(pending.coords?.top) || 120
  const left = Number(pending.coords?.left) || 24

  const toolbar = (
    <div
      className="ai-inline-suggestion-toolbar ai-inline-suggestion-toolbar--floating"
      role="group"
      aria-label="Accept or reject AI suggestion"
      style={{ top: `${top}px`, left: `${left}px` }}
    >
      <button
        type="button"
        className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--reject"
        onClick={handleReject}
      >
        <X size={14} />
        Reject
      </button>
      <button
        type="button"
        className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--accept"
        onClick={handleAccept}
      >
        <Check size={14} />
        Accept
      </button>
    </div>
  )

  if (typeof document === 'undefined') return toolbar
  return createPortal(toolbar, document.body)
}
