import React, { useEffect, useRef, useState } from 'react'
import { Sparkles, ShieldAlert, Wand2 } from 'lucide-react'

import { performAIAction } from '../../api/editorApi'
import AIComparisonModal from './AIComparisonModal'
import './AIAssistantUI.css'
import { formatAiSuggestionForUi } from '../../utils/aiOutputFormatter'
import {
  selectionLooksLikeFormattedAiReport,
  selectionMatchesLastAiSuggestion,
} from '../../utils/aiActionSelection'

const buildStructuredSelectionText = (editor, from, to) =>
  editor.state.doc.textBetween(from, to, '\n').trim()

const stripHtml = (value) =>
  String(value || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<\/div>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

const buildAcceptedContent = (aiResult, selectionMeta) => {
  const action = String(aiResult?.action || '').toLowerCase()
  const structured = aiResult?.structured_data || {}
  const selectedFraction = Number(selectionMeta?.selectedFraction || 0)
  const isPartialSelection = selectedFraction > 0 && selectedFraction < 0.6

  // Partial-range edits should stay text-safe to avoid accidental document-wide
  // structural rewrites when only a small snippet is selected.
  if (isPartialSelection && (action === 'rewrite' || action === 'improve' || action === 'gap_check')) {
    if (action === 'rewrite') {
      return stripHtml(structured.rewritten_text || aiResult?.suggested_text)
    }
    if (action === 'improve') {
      return stripHtml(structured.improved_text || structured.improved_version || aiResult?.suggested_text)
    }
    return stripHtml(structured.analysis || aiResult?.suggested_text)
  }

  // Full/large selections can preserve richer formatting output.
  return aiResult?.suggested_text || ''
}

const isEditorViewReady = (editor) =>
  Boolean(editor && editor.view && editor.view.dom && !editor.isDestroyed)

const ACTION_TEXT_WARNING_CHARS = 7000

const AIAssistantBubbleMenu = ({ editor, sopMetadata, isEditable = true, onPreviewSessionChange }) => {
  const [isAILoading, setIsAILoading] = useState(false)
  const [aiResult, setAIResult] = useState(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [menuPosition, setMenuPosition] = useState(null)
  const selectionRef = useRef(null)
  /** Last successful /api/ai/action response for re-apply without re-parsing formatted UI text. */
  const lastAiReplyRef = useRef(null)
  const menuRef = useRef(null)
  const isPointerSelectingRef = useRef(false)
  const [isEditorReady, setIsEditorReady] = useState(false)
  /** Suppresses bubble reposition updates while an AI request runs or the result modal is open (avoids render storms). */
  const pauseBubblePositioningRef = useRef(false)
  const actionInFlightRef = useRef(false)
  const lastMenuPositionRef = useRef(null)

  const notifyPreviewSession = (active) => {
    if (typeof onPreviewSessionChange === 'function') {
      onPreviewSessionChange(active)
    }
  }

  useEffect(() => {
    if (!editor || !isEditable) return undefined

    const updateReadyState = () => {
      setIsEditorReady(isEditorViewReady(editor))
    }

    updateReadyState()
    editor.on('create', updateReadyState)

    return () => {
      editor.off('create', updateReadyState)
      setIsEditorReady(false)
    }
  }, [editor, isEditable])

  useEffect(() => {
    if (!editor || !isEditable || !isEditorReady) return undefined

    const updatePosition = () => {
      if (pauseBubblePositioningRef.current) return
      if (!isEditorViewReady(editor)) {
        selectionRef.current = null
        lastMenuPositionRef.current = null
        setMenuPosition(null)
        return
      }
      if (isPointerSelectingRef.current) return
      const { selection } = editor.state

      if (selection.empty) {
        const activeElement = document.activeElement
        if (!menuRef.current?.contains(activeElement)) {
          selectionRef.current = null
          lastMenuPositionRef.current = null
          setMenuPosition(null)
        }
        return
      }

      try {
        const { from, to } = selection
        const selectedText = editor.state.doc.textBetween(from, to, ' ').trim()

        if (!selectedText) {
          selectionRef.current = null
          lastMenuPositionRef.current = null
          setMenuPosition(null)
          return
        }

        const structuredText = buildStructuredSelectionText(editor, from, to)
        // Anchor to current head position so reverse selection and Ctrl+A remain stable.
        const headPos = selection.$head?.pos || to
        const head = editor.view.coordsAtPos(headPos)
        const editorRect = editor.view.dom.getBoundingClientRect()
        const visibleRect = {
          left: Math.max(editorRect.left, 8),
          right: Math.min(editorRect.right, window.innerWidth - 8),
          top: Math.max(editorRect.top, 8),
          bottom: Math.min(editorRect.bottom, window.innerHeight - 8),
        }

        const menuWidth = menuRef.current?.offsetWidth || 360
        const menuHeight = menuRef.current?.offsetHeight || 70
        const margin = 8
        const offset = 12
        const selectionRatio = Math.abs(to - from) / Math.max(1, editor.state.doc.content.size)
        const isLargeSelection = selectedText.length > 900 || selectionRatio > 0.6

        let left = isLargeSelection
          ? visibleRect.right - margin - menuWidth / 2
          : head.left
        const leftMin = Math.max(margin + menuWidth / 2, visibleRect.left + margin + menuWidth / 2)
        const leftMax = Math.min(
          window.innerWidth - margin - menuWidth / 2,
          visibleRect.right - margin - menuWidth / 2,
        )
        left = Math.max(leftMin, Math.min(leftMax, left))

        const preferredTop = isLargeSelection
          ? visibleRect.bottom - margin - menuHeight - offset
          : head.top
        const spaceAbove = preferredTop - visibleRect.top
        const spaceBelow = visibleRect.bottom - head.bottom
        const placement = spaceAbove >= (menuHeight + offset + margin) || spaceAbove >= spaceBelow ? 'above' : 'below'

        let top = placement === 'above' ? preferredTop : head.bottom
        const topMin = visibleRect.top + margin + (placement === 'below' ? 0 : menuHeight + offset)
        const topMax = visibleRect.bottom - margin - (placement === 'below' ? menuHeight + offset : 0)
        top = Math.max(topMin, Math.min(topMax, top))

        const selectedFraction = Math.abs(to - from) / Math.max(1, editor.state.doc.content.size)
        selectionRef.current = { from, to, selectedText, structuredText, selectedFraction }
        const nextPos = { top, left, placement }
        const prev = lastMenuPositionRef.current
        if (
          prev &&
          prev.top === nextPos.top &&
          prev.left === nextPos.left &&
          prev.placement === nextPos.placement
        ) {
          return
        }
        lastMenuPositionRef.current = nextPos
        setMenuPosition(nextPos)
      } catch {
        selectionRef.current = null
        lastMenuPositionRef.current = null
        setMenuPosition(null)
      }
    }

    // Coalesce to one layout pass per frame. Do not subscribe to every ProseMirror `transaction`
    // (decorations, plugins, etc.) — that caused repeated setState and visible editor flicker.
    let positionRafId = null
    const delayedUpdate = () => {
      if (positionRafId != null) return
      positionRafId = window.requestAnimationFrame(() => {
        positionRafId = null
        updatePosition()
      })
    }
    editor.on('selectionUpdate', delayedUpdate)
    const startPointerSelection = () => {
      isPointerSelectingRef.current = true
      lastMenuPositionRef.current = null
      setMenuPosition(null)
    }
    const endPointerSelection = () => {
      if (!isPointerSelectingRef.current) return
      isPointerSelectingRef.current = false
      delayedUpdate()
    }
    const handleGlobalKeyDown = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
        // Wait for browser/editor to finish applying select-all before positioning.
        window.requestAnimationFrame(() => window.requestAnimationFrame(updatePosition))
      }
    }

    const dom = editor.view?.dom
    if (!dom) return undefined

    dom.addEventListener('mousedown', startPointerSelection)
    window.addEventListener('mouseup', delayedUpdate)
    window.addEventListener('keyup', delayedUpdate)
    window.addEventListener('mouseup', endPointerSelection)
    window.addEventListener('scroll', delayedUpdate, true)
    window.addEventListener('resize', delayedUpdate)
    document.addEventListener('selectionchange', delayedUpdate)
    window.addEventListener('keydown', handleGlobalKeyDown)
    updatePosition()

    return () => {
      if (positionRafId != null) {
        window.cancelAnimationFrame(positionRafId)
        positionRafId = null
      }
      editor.off('selectionUpdate', delayedUpdate)
      if (dom) {
        dom.removeEventListener('mousedown', startPointerSelection)
      }
      window.removeEventListener('mouseup', delayedUpdate)
      window.removeEventListener('keyup', delayedUpdate)
      window.removeEventListener('mouseup', endPointerSelection)
      window.removeEventListener('scroll', delayedUpdate, true)
      window.removeEventListener('resize', delayedUpdate)
      document.removeEventListener('selectionchange', delayedUpdate)
      window.removeEventListener('keydown', handleGlobalKeyDown)
    }
  }, [editor, isEditable, isEditorReady])

  if (!editor || !isEditable || !isEditorReady) return null

  const handleAction = async (action) => {
    const savedSelection = selectionRef.current
    const selectedText = savedSelection?.selectedText || ''

    if (!selectedText) return

    const structuredForCheck = savedSelection.structuredText || selectedText
    if (
      (action === 'improve' || action === 'rewrite') &&
      selectionLooksLikeFormattedAiReport(structuredForCheck)
    ) {
      const last = lastAiReplyRef.current
      const canReapplyStructured =
        last &&
        last.action === action &&
        last.structured &&
        selectionMatchesLastAiSuggestion(structuredForCheck, last.suggestedPlain)
      if (!canReapplyStructured) {
        alert(
          'This selection looks like a formatted AI report (for example a prior gap check or review output), not plain SOP text. Those actions need raw procedure text.\n\n' +
            'Tip: use “Accept and Insert” in the review dialog to apply a suggestion you already generated, or select the original SOP paragraph before running Improve or Rewrite.',
        )
        return
      }
    }

    let sectionName = 'Selected text'
    let sectionType = 'Paragraph'

    try {
      const resolvedPos = editor.state.doc.resolve(savedSelection.from)
      for (let depth = resolvedPos.depth; depth >= 0; depth -= 1) {
        const node = resolvedPos.node(depth)
        if (node.type.name === 'heading') {
          sectionName = node.textContent
          sectionType = 'Heading'
          break
        }
        if (node.type.name === 'table') {
          sectionType = 'Table'
        } else if (node.type.name === 'bulletList' || node.type.name === 'orderedList' || node.type.name === 'listItem') {
          sectionType = 'List'
        } else if (node.type.name === 'paragraph') {
          sectionType = 'Paragraph'
        }
      }
    } catch {
      // Best-effort section inference only.
    }

    const structuredPayload = savedSelection.structuredText || selectedText
    const lastReply = lastAiReplyRef.current
    let textForApi = structuredPayload
    let clientStructured = null
    if (
      lastReply &&
      lastReply.action === action &&
      lastReply.structured &&
      selectionMatchesLastAiSuggestion(structuredPayload, lastReply.suggestedPlain)
    ) {
      clientStructured = lastReply.structured
      textForApi = lastReply.originalText || structuredPayload
    }
    if ((textForApi || '').length > ACTION_TEXT_WARNING_CHARS) {
      const proceed = window.confirm(
        'This selection may be too long for the local model and can fail with context-limit errors.\n\nPlease select a smaller section if possible.\n\nContinue anyway?',
      )
      if (!proceed) return
    }

    if (actionInFlightRef.current) return
    actionInFlightRef.current = true
    pauseBubblePositioningRef.current = true
    notifyPreviewSession(true)
    setIsAILoading(true)
    try {
      const result = await performAIAction({
        action,
        text: textForApi,
        document_id: sopMetadata?.documentId || null,
        section_id: `${savedSelection.from}-${savedSelection.to}`,
        sop_title: sopMetadata?.title || 'Untitled SOP',
        section_name: sectionName,
        section_type: sectionType,
        client_structured_json: clientStructured,
      })

      const safeSuggestedText = formatAiSuggestionForUi({
        action: result?.action || action,
        suggestedText: result?.suggested_text,
        structuredData: result?.structured_data,
      })

      lastAiReplyRef.current = {
        action: result?.action || action,
        structured: result?.structured_data || null,
        suggestedPlain: stripHtml(safeSuggestedText),
        originalText: result?.original_text || structuredPayload,
      }

      setAIResult({
        ...result,
        suggested_text: safeSuggestedText,
        section_name: sectionName,
      })
      setIsModalOpen(true)
      lastMenuPositionRef.current = null
      setMenuPosition(null)
    } catch (err) {
      pauseBubblePositioningRef.current = false
      notifyPreviewSession(false)
      console.error('AI action failed:', err)
      const lines = [err.message || 'AI action failed. Please try again.']
      if (err.validationOrParseError) {
        lines.push('Technical detail (validation / parse):', err.validationOrParseError)
      }
      if (err.hint) {
        lines.push('Hint:', err.hint)
      }
      alert(lines.join('\n\n'))
    } finally {
      actionInFlightRef.current = false
      setIsAILoading(false)
    }
  }

  const handleAccept = () => {
    if (!aiResult || !selectionRef.current) return

    const { from, to } = selectionRef.current
    const acceptedContent = buildAcceptedContent(aiResult, selectionRef.current)
    if (!acceptedContent) return
    editor.chain().focus().insertContentAt({ from, to }, acceptedContent).run()

    setIsModalOpen(false)
    setAIResult(null)
    pauseBubblePositioningRef.current = false
    notifyPreviewSession(false)
    selectionRef.current = null
    lastAiReplyRef.current = null
  }

  return (
    <>
      {menuPosition ? (
        <div
          ref={menuRef}
          className="ai-action-menu"
          data-placement={menuPosition.placement || 'above'}
          style={{
            top: menuPosition.top,
            left: menuPosition.left,
          }}
          onMouseDown={(event) => event.preventDefault()}
        >
          <div className="ai-action-menu__header">AI actions</div>
          <div className="ai-action-menu__actions">
            <button
              onClick={() => handleAction('gap_check')}
              className="ai-action-menu__button ai-action-menu__button--blue"
              disabled={isAILoading}
            >
              <ShieldAlert size={15} />
              <span>Gap Check</span>
            </button>

            <button
              onClick={() => handleAction('rewrite')}
              className="ai-action-menu__button ai-action-menu__button--green"
              disabled={isAILoading}
            >
              <Wand2 size={15} />
              <span>Rewrite</span>
            </button>

            <button
              onClick={() => handleAction('improve')}
              className="ai-action-menu__button ai-action-menu__button--purple"
              disabled={isAILoading}
            >
              <Sparkles size={15} />
              <span>Improve</span>
            </button>
          </div>

          {isAILoading && (
            <div className="ai-action-menu__loading">
              <div className="ai-action-menu__spinner" />
              <span>Generating suggestion...</span>
            </div>
          )}
        </div>
      ) : null}

      <AIComparisonModal
        isOpen={isModalOpen}
        onClose={() => {
          pauseBubblePositioningRef.current = false
          notifyPreviewSession(false)
          setIsModalOpen(false)
          setAIResult(null)
        }}
        action={aiResult?.action}
        originalText={aiResult?.original_text}
        suggestedText={aiResult?.suggested_text}
        explanation={aiResult?.explanation}
        structuredData={aiResult?.structured_data}
        onAccept={handleAccept}
        sectionName={aiResult?.section_name}
        sopTitle={sopMetadata?.title}
      />
    </>
  )
}

export default AIAssistantBubbleMenu
