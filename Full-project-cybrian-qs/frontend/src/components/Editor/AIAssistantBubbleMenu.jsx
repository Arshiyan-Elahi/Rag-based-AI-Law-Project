import React, { useEffect, useRef, useState } from 'react'
import { Sparkles, ShieldAlert, Wand2 } from 'lucide-react'

import { performAIAction } from '../../api/editorApi'
import AIComparisonModal from './AIComparisonModal'
import './AIAssistantUI.css'
import { formatAiSuggestionForUi } from '../../utils/aiOutputFormatter'

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

const AIAssistantBubbleMenu = ({ editor, sopMetadata, isEditable = true }) => {
  const [isAILoading, setIsAILoading] = useState(false)
  const [aiResult, setAIResult] = useState(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [menuPosition, setMenuPosition] = useState(null)
  const selectionRef = useRef(null)
  const menuRef = useRef(null)
  const isPointerSelectingRef = useRef(false)
  const [isEditorReady, setIsEditorReady] = useState(false)

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
      if (!isEditorViewReady(editor)) {
        selectionRef.current = null
        setMenuPosition(null)
        return
      }
      if (isPointerSelectingRef.current) return
      const { selection } = editor.state

      if (selection.empty) {
        const activeElement = document.activeElement
        if (!menuRef.current?.contains(activeElement)) {
          selectionRef.current = null
          setMenuPosition(null)
        }
        return
      }

      try {
        const { from, to } = selection
        const selectedText = editor.state.doc.textBetween(from, to, ' ').trim()

        if (!selectedText) {
          selectionRef.current = null
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
        setMenuPosition({
          top,
          left,
          placement,
        })
      } catch {
        selectionRef.current = null
        setMenuPosition(null)
      }
    }

    editor.on('selectionUpdate', updatePosition)
    editor.on('transaction', updatePosition)
    const delayedUpdate = () => window.requestAnimationFrame(updatePosition)
    const startPointerSelection = () => {
      isPointerSelectingRef.current = true
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
      editor.off('selectionUpdate', updatePosition)
      editor.off('transaction', updatePosition)
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

    setIsAILoading(true)
    try {
      const result = await performAIAction({
        action,
        text: savedSelection.structuredText || selectedText,
        document_id: sopMetadata?.documentId || null,
        section_id: `${savedSelection.from}-${savedSelection.to}`,
        sop_title: sopMetadata?.title || 'Untitled SOP',
        section_name: sectionName,
        section_type: sectionType,
      })

      const safeSuggestedText = formatAiSuggestionForUi({
        action: result?.action || action,
        suggestedText: result?.suggested_text,
        structuredData: result?.structured_data,
      })

      setAIResult({
        ...result,
        suggested_text: safeSuggestedText,
        section_name: sectionName,
      })
      setIsModalOpen(true)
      setMenuPosition(null)
    } catch (err) {
      console.error('AI action failed:', err)
      alert(err.message || 'AI action failed. Please try again.')
    } finally {
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
    selectionRef.current = null
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
