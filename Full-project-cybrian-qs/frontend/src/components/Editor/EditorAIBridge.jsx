import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import AIComparisonModal from './AIComparisonModal'
import EditorInlineSuggestionToolbar from './EditorInlineSuggestionToolbar'
import { performAIAction } from '../../api/editorApi'
import { formatAiSuggestionForUi } from '../../utils/aiOutputFormatter'
import { applyTipTapContentToEditor } from '../../utils/editorUtils'
import {
  buildAcceptedInsertContent,
  coerceTipTapDocForApply,
} from '../../utils/editorAiActionShared'
import {
  dispatchEditorSnapshotResponse,
  subscribeEditorInlineSuggestionApply,
  subscribeEditorInlineSuggestionClear,
  subscribeEditorInlineSuggestionShow,
  subscribeEditorSnapshotRequest,
  EDITOR_GAP_APPEND_EVENT,
  EDITOR_SCROLL_TO_RANGE_EVENT,
  EDITOR_SELECTION_QUERY_EVENT,
  EDITOR_SELECTION_RESPONSE_EVENT,
} from '../../utils/editorActionsBridge'
import {
  clearInlineAiSuggestion,
  setInlineAiSuggestion,
} from '../../utils/editorInlineSuggestionPlugin'
import { resolveTargetInEditor } from '../../utils/editorTargetResolver'
import { isExplicitFullSopRequest, wantsFullSopIntent } from '../../utils/sopActionIntent'
import { buildPatchScopePayload } from '../../utils/tiptapScope'
import {
  AI_ACTION_TRIGGERED_BY,
  EDITOR_AI_ACTIONS,
  EDITOR_AI_ACTION_STATUS,
  dispatchEditorAiActionResult,
  subscribeEditorAiActionRequest,
} from '../../utils/editorAiBridge'
import {
  INLINE_APPLIED_EVENT,
  INLINE_SHOWN_EVENT,
  resolveRewriteImproveTarget,
  runRewriteImproveWithInlinePreview,
} from '../../utils/editorInlineAiFlow'
import { getAppLanguage, getFriendlyErrorMessage } from '../../utils/friendlyErrorMessage'

const ACTION_TEXT_WARNING_CHARS = 7000

const stripHtml = (value) =>
  String(value || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<\/div>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

const buildFindingsHtml = (structuredData) => {
  if (!structuredData || typeof structuredData !== 'object') return ''
  const gaps = Array.isArray(structuredData.gaps) ? structuredData.gaps : []
  const items = gaps.length > 0
    ? gaps
    : [{
      issue: structuredData.issue,
      explanation: structuredData.explanation,
      recommendation: structuredData.recommendation,
    }].filter((entry) => entry && (entry.issue || entry.explanation || entry.recommendation))

  if (items.length === 0) return ''

  const itemsHtml = items
    .map((gap) => {
      const issue = gap?.issue ? `<p><strong>Issue:</strong> ${gap.issue}</p>` : ''
      const explanation = gap?.explanation ? `<p><strong>Explanation:</strong> ${gap.explanation}</p>` : ''
      const recommendation = gap?.recommendation
        ? `<p><strong>Recommendation:</strong> ${gap.recommendation}</p>`
        : ''
      return `<li>${issue}${explanation}${recommendation}</li>`
    })
    .join('')

  return `<h3>AI Gap Check Findings</h3><ul>${itemsHtml}</ul>`
}

const ALLOWED_ACTIONS = new Set([
  EDITOR_AI_ACTIONS.REWRITE,
  EDITOR_AI_ACTIONS.IMPROVE,
  EDITOR_AI_ACTIONS.GAP_CHECK,
  EDITOR_AI_ACTIONS.SUMMARIZE,
  EDITOR_AI_ACTIONS.ANALYZE,
])

/**
 * Bridges KL/KI Assistant action requests into the live SOP editor.
 *
 * Subscribes to {@link EDITOR_AI_ACTION_REQUEST_EVENT} dispatched by chat
 * surfaces (AIWidget, ChatPage). For rewrite / improve / gap_check it runs
 * `/api/ai/action` on the current selection (or whole document when no
 * selection exists), shows the standard {@link AIComparisonModal}, then
 * applies the result into the editor on accept. A
 * {@link EDITOR_AI_ACTION_RESULT_EVENT} is emitted so the chat surface can
 * report status back to the user.
 *
 * The component renders nothing besides the modal portal.
 */
const EditorAIBridge = ({
  editor,
  documentId,
  sopMetadata,
  isEditable = true,
  onPreviewSessionChange,
  onAfterApply,
  onVersionCompareRequest,
}) => {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [aiResult, setAIResult] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [loadingMessage, setLoadingMessage] = useState('KI-Assistent bearbeitet die SOP…')
  /** Snapshot of the request that opened the current modal. */
  const activeRequestRef = useRef(null)
  /** Range in the editor that should receive the accepted content. */
  const targetRangeRef = useRef(null)
  /** Tracks whether we are currently using the full document as the source. */
  const isFullDocRef = useRef(false)
  const inFlightRef = useRef(false)
  const editorRef = useRef(editor)
  const sopMetadataRef = useRef(sopMetadata)
  const documentIdRef = useRef(documentId)
  const isEditableRef = useRef(isEditable)
  /** Pending inline suggestion from the sidebar Actions tab. */
  const inlinePendingRef = useRef(null)

  useEffect(() => { editorRef.current = editor }, [editor])
  useEffect(() => { sopMetadataRef.current = sopMetadata }, [sopMetadata])
  useEffect(() => { documentIdRef.current = documentId }, [documentId])
  useEffect(() => { isEditableRef.current = isEditable }, [isEditable])

  const notifyPreviewSession = useCallback((active) => {
    if (typeof onPreviewSessionChange === 'function') {
      onPreviewSessionChange(active)
    }
  }, [onPreviewSessionChange])

  const emitResult = useCallback((detail) => {
    dispatchEditorAiActionResult(detail)
  }, [])

  const closeModal = useCallback(() => {
    setIsModalOpen(false)
    setAIResult(null)
    activeRequestRef.current = null
    targetRangeRef.current = null
    isFullDocRef.current = false
    notifyPreviewSession(false)
  }, [notifyPreviewSession])

  const sopTitle = useMemo(() => {
    const metadata = sopMetadata || {}
    return (metadata.title || metadata.documentId || 'Untitled SOP').toString().trim() || 'Untitled SOP'
  }, [sopMetadata])

  const runActionRequest = useCallback(async (request) => {
    const { action, requestId } = request || {}
    if (action === EDITOR_AI_ACTIONS.COMPARE) {
      const liveEditor = editorRef.current
      if (!liveEditor || liveEditor.isDestroyed) {
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.NOT_AVAILABLE,
          reason: 'editor_unavailable',
          message: 'Editor nicht bereit.',
        })
        console.warn('[kl-editor-action-failed]', { action, requestId, reason: 'editor_unavailable' })
        return
      }
      if (typeof onVersionCompareRequest !== 'function') {
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.ERROR,
          message: 'Versionsvergleich ist hier nicht verfügbar.',
        })
        console.warn('[kl-editor-action-failed]', { action, requestId, reason: 'no_compare_handler' })
        return
      }
      try {
        console.info('[kl-editor-bridge-received]', { action, requestId, phase: 'compare' })
        await Promise.resolve(onVersionCompareRequest())
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.DISPLAYED,
          action: EDITOR_AI_ACTIONS.COMPARE,
        })
      } catch (err) {
        console.error('[kl-editor-action-failed]', err)
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.ERROR,
          message: getFriendlyErrorMessage(getAppLanguage()),
        })
      }
      return
    }

    if (!ALLOWED_ACTIONS.has(action)) {
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.NOT_AVAILABLE,
        reason: 'unsupported_action',
      })
      return
    }
    const liveEditor = editorRef.current
    if (!liveEditor || liveEditor.isDestroyed || !isEditableRef.current) {
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.NOT_AVAILABLE,
        reason: 'editor_unavailable',
      })
      return
    }

    if (inFlightRef.current) {
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.ERROR,
        message: 'Es läuft bereits eine Editor-Aktion.',
      })
      return
    }

    const actionPrompt = String(request?.prompt || '').trim()

    if (action === EDITOR_AI_ACTIONS.REWRITE || action === EDITOR_AI_ACTIONS.IMPROVE) {
      inFlightRef.current = true
      activeRequestRef.current = request
      notifyPreviewSession(true)
      setLoadingMessage('Preparing inline preview…')
      setIsLoading(true)
      const bridgeRequestId = request.requestId || `kl-${Date.now().toString(36)}`

      try {
        const { target, normalized } = await runRewriteImproveWithInlinePreview({
          editor: liveEditor,
          action,
          requestId: bridgeRequestId,
          sopTitle,
          documentId: documentIdRef.current || sopMetadataRef.current?.documentId || null,
          triggeredBy: AI_ACTION_TRIGGERED_BY.KL_ASSISTANT,
          instruction: actionPrompt,
          userMessage: String(request?.userMessage || '').trim() || actionPrompt,
          targetScope: String(request?.targetScope || '').trim().toLowerCase(),
          sectionHint: String(request?.sectionHint || '').trim(),
        })
        targetRangeRef.current = { from: target.from, to: target.to }
        isFullDocRef.current = target.isFullDoc
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.DISPLAYED,
          action,
          section_name: target.sectionName,
          preview_excerpt: normalized.suggestedHtml,
          is_full_sop: target.isFullDoc,
          explanation: normalized.explanation || '',
        })
        console.info('[kl-editor-inline-shown]', { action, requestId: bridgeRequestId })
      } catch (err) {
        console.error('[kl-editor-action-failed]', err)
        const message =
          err?.message && /Could not find/i.test(String(err.message))
            ? String(err.message)
            : getFriendlyErrorMessage(getAppLanguage())
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.ERROR,
          message,
        })
        notifyPreviewSession(false)
        activeRequestRef.current = null
        targetRangeRef.current = null
        isFullDocRef.current = false
      } finally {
        inFlightRef.current = false
        setIsLoading(false)
      }
      return
    }

    const { state } = liveEditor
    const { selection } = state
    const hasSelection = Boolean(selection && !selection.empty)
    const selectionPayload = hasSelection
      ? { from: selection.from, to: selection.to, empty: false }
      : { empty: true }

    let from = 0
    let to = state.doc.content.size
    let selectedText = ''
    let isFullDoc = false
    let sectionName = 'Current block'
    let sectionType = 'Paragraph'
    if (actionPrompt) {
      try {
        const resolved = resolveTargetInEditor(liveEditor, {
          prompt: String(request?.userMessage || '').trim() || actionPrompt,
          selection: selectionPayload,
          sectionHint: String(request?.sectionHint || '').trim(),
          targetScope: String(request?.targetScope || '').trim(),
        })
        if (resolved?.text && resolved.from != null && resolved.to != null) {
          from = resolved.from
          to = resolved.to
          selectedText = resolved.text
          isFullDoc = Boolean(resolved.isFullDoc) && wantsFullSopIntent(actionPrompt)
          sectionName = resolved.sectionName || sectionName
          sectionType = resolved.sectionType || sectionType
        }
      } catch (err) {
        const message =
          err?.message && /Could not find/i.test(String(err.message))
            ? String(err.message)
            : getFriendlyErrorMessage(getAppLanguage())
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.ERROR,
          message,
        })
        return
      }
    } else if (hasSelection) {
      from = selection.from
      to = selection.to
      const fragment = state.doc.textBetween(from, to, '\n').trim()
      if (fragment.length > 0) {
        selectedText = fragment
        isFullDoc = false
        sectionName = 'Selected text'
        sectionType = 'Paragraph'
      }
    } else {
      const block = resolveCurrentBlockAtCursor(liveEditor)
      if (block?.text) {
        from = block.from
        to = block.to
        selectedText = block.text
        isFullDoc = false
        sectionName = block.sectionName || sectionName
        sectionType = block.sectionType || sectionType
      }
    }

    if (!selectedText) {
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.NOT_AVAILABLE,
        reason: 'empty_document',
      })
      return
    }

    let explicitFullSop = isExplicitFullSopRequest({ instruction: actionPrompt })
    if (explicitFullSop) {
      const docSize = state.doc.content.size
      from = 0
      to = docSize
      selectedText = state.doc.textBetween(from, to, '\n').trim()
      isFullDoc = true
      sectionName = 'Full SOP'
      sectionType = 'Full Document'
    }

    if (explicitFullSop && selectedText.length > ACTION_TEXT_WARNING_CHARS) {
      const proceed = window.confirm(
        'Full-SOP rewrite/improve may take several minutes. Continue?',
      )
      if (!proceed) {
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.CANCELLED,
          reason: 'user_declined_long_text',
        })
        return
      }
    }

    inFlightRef.current = true
    activeRequestRef.current = request
    targetRangeRef.current = { from, to }
    isFullDocRef.current = explicitFullSop
    notifyPreviewSession(true)
    setLoadingMessage(explicitFullSop ? 'Preparing full SOP preview…' : 'Preparing preview…')
    setIsLoading(true)

    try {
      console.info('[kl-editor-bridge-received]', {
        action,
        requestId,
        documentId: documentIdRef.current,
        textLen: selectedText.length,
        isFullDoc,
        source: request?.source || 'unknown',
      })
      const editorDocJson =
        liveEditor && !liveEditor.isDestroyed ? liveEditor.getJSON() : null
      const patchScope = explicitFullSop
        ? {}
        : buildPatchScopePayload(liveEditor, {
            from,
            to,
            text: selectedText,
            contentJson: editorDocJson,
          })
      const result = await performAIAction(
        {
          action,
          text: selectedText,
          document_id: documentIdRef.current || sopMetadataRef.current?.documentId || null,
          section_id: `kl-assistant-${requestId || Date.now()}`,
          sop_title: sopTitle,
          section_name: sectionName,
          section_type: sectionType,
          edit_scope: explicitFullSop ? 'full_document' : 'section_only',
          patch_node_ids: patchScope.patch_node_ids,
          content_json: editorDocJson,
          sop_entity_id: documentIdRef.current || null,
          triggered_by: AI_ACTION_TRIGGERED_BY.KL_ASSISTANT,
          assistant_instruction: actionPrompt.trim() || null,
        },
      )
      if (!result) {
        emitResult({
          ...request,
          status: EDITOR_AI_ACTION_STATUS.CANCELLED,
          reason: 'duplicate_request',
        })
        return
      }

      const safeSuggestedHtml = formatAiSuggestionForUi({
        action: result?.action || action,
        suggestedText: result?.suggested_text,
        structuredData: result?.structured_data,
      })

      setAIResult({
        ...result,
        action: result?.action || action,
        suggested_text: safeSuggestedHtml,
        section_name: sectionName,
      })
      setIsModalOpen(true)
      const unchangedCount = Array.isArray(result?.structured_data?.unchanged_chunks)
        ? result.structured_data.unchanged_chunks.length
        : 0
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.DISPLAYED,
        action: result?.action || action,
        section_name: sectionName,
        preview_excerpt: safeSuggestedHtml,
        is_full_sop: explicitFullSop,
        unchanged_chunks: unchangedCount,
        explanation: result?.explanation || '',
      })
      console.info('[kl-editor-action-modal-open]', { action: result?.action || action, requestId, isFullDoc: explicitFullSop })
    } catch (err) {
      console.error('[kl-editor-action-failed]', err)
      const message = getFriendlyErrorMessage(getAppLanguage())
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.ERROR,
        message,
      })
      notifyPreviewSession(false)
      activeRequestRef.current = null
      targetRangeRef.current = null
      isFullDocRef.current = false
      window.alert(message)
    } finally {
      inFlightRef.current = false
      setIsLoading(false)
    }
  }, [emitResult, notifyPreviewSession, sopTitle, onVersionCompareRequest])

  const handleReadRequest = useCallback((request) => {
    const liveEditor = editorRef.current
    if (!liveEditor || liveEditor.isDestroyed) {
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.NOT_AVAILABLE,
        reason: 'editor_unavailable',
      })
      return
    }
    const metadata = sopMetadataRef.current || {}
    const preview = (liveEditor.getText() || '').slice(0, 400)
    emitResult({
      ...request,
      status: EDITOR_AI_ACTION_STATUS.DISPLAYED,
      sop_id: documentIdRef.current || null,
      sop_title: metadata.title || '',
      sop_number: metadata.documentId || '',
      preview,
    })
  }, [emitResult])

  const resolveDocRange = useCallback((detail) => {
    const liveEditor = editorRef.current
    if (!liveEditor || liveEditor.isDestroyed) return null
    const size = liveEditor.state.doc.content.size
    const from = Number.isFinite(detail.from) ? detail.from : 0
    const to = Number.isFinite(detail.to) ? detail.to : size
    return {
      from: Math.max(0, Math.min(from, size)),
      to: Math.max(from, Math.min(to, size)),
    }
  }, [])

  const emitInlineShown = useCallback((requestId, range, action) => {
    const liveEditor = editorRef.current
    let toolbarCoords = null
    if (liveEditor?.view && range) {
      try {
        const coords = liveEditor.view.coordsAtPos(range.to)
        toolbarCoords = { top: coords.top + window.scrollY - 48, left: coords.left + window.scrollX }
      } catch {
        toolbarCoords = null
      }
    }
    window.dispatchEvent(
      new CustomEvent(INLINE_SHOWN_EVENT, {
        detail: {
          requestId,
          action,
          toolbarCoords,
          from: range?.from,
          to: range?.to,
        },
      }),
    )
  }, [])

  const emitInlineApplied = useCallback((requestId, ok, message = '') => {
    window.dispatchEvent(
      new CustomEvent(INLINE_APPLIED_EVENT, {
        detail: { requestId, ok, message },
      }),
    )
  }, [])

  useEffect(() => {
    const onSelectionQuery = (event) => {
      const requestId = event.detail?.requestId
      const liveEditor = editorRef.current
      if (!requestId) return
      let hasSelection = false
      if (liveEditor && !liveEditor.isDestroyed && isEditableRef.current) {
        const sel = liveEditor.state.selection
        hasSelection = Boolean(sel && !sel.empty)
      }
      window.dispatchEvent(
        new CustomEvent(EDITOR_SELECTION_RESPONSE_EVENT, {
          detail: { requestId, hasSelection },
        }),
      )
    }
    window.addEventListener(EDITOR_SELECTION_QUERY_EVENT, onSelectionQuery)
    return () => window.removeEventListener(EDITOR_SELECTION_QUERY_EVENT, onSelectionQuery)
  }, [])

  useEffect(() => {
    const unsubSnapshot = subscribeEditorSnapshotRequest(({ requestId, prompt, sectionHint, targetScope }) => {
      const liveEditor = editorRef.current
      if (!liveEditor || liveEditor.isDestroyed || !isEditableRef.current) {
        dispatchEditorSnapshotResponse({
          requestId,
          ok: false,
          message: 'Editor is not available or is read-only.',
        })
        return
      }
      const { state } = liveEditor
      const { selection } = state
      const hasSelection = Boolean(selection && !selection.empty)
      const selectionPayload = hasSelection
        ? {
            from: selection.from,
            to: selection.to,
            text: state.doc.textBetween(selection.from, selection.to, '\n'),
            empty: false,
          }
        : { empty: true }

      try {
        const target = resolveRewriteImproveTarget(liveEditor, {
          instruction: String(prompt || ''),
          sectionHint: String(sectionHint || ''),
          targetScope: String(targetScope || ''),
        })
        if (!target?.text || target.from == null || target.to == null) {
          dispatchEditorSnapshotResponse({
            requestId,
            ok: false,
            error: 'Could not find that heading or paragraph in the open SOP. Check the text or select it in the editor.',
          })
          return
        }
        dispatchEditorSnapshotResponse({
          requestId,
          ok: true,
          target,
          fullText: state.doc.textBetween(0, state.doc.content.size, '\n'),
          docSize: state.doc.content.size,
          contentJson: liveEditor.getJSON(),
          selection: selectionPayload,
          sopTitle: (sopMetadataRef.current?.title || 'Untitled SOP').toString(),
          sopNumber: (sopMetadataRef.current?.documentId || '').toString(),
        })
      } catch (err) {
        dispatchEditorSnapshotResponse({
          requestId,
          ok: false,
          error: getFriendlyErrorMessage(getAppLanguage()),
        })
      }
    })

    const unsubShow = subscribeEditorInlineSuggestionShow((detail) => {
      const liveEditor = editorRef.current
      const requestId = detail?.requestId
      if (!requestId || !liveEditor || liveEditor.isDestroyed || !isEditableRef.current) {
        emitInlineShown(requestId, null)
        return
      }

      if (inlinePendingRef.current?.requestId && inlinePendingRef.current.requestId !== requestId) {
        clearInlineAiSuggestion(liveEditor)
      }

      let range = resolveDocRange(detail)
      const docSize = liveEditor.state.doc.content.size
      if (detail.isFullDoc && docSize > 0) {
        range = { from: 0, to: Math.max(docSize, 1) }
      }
      if (!range || range.to <= range.from) {
        emitInlineShown(requestId, null)
        return
      }

      const suggestedPlain = String(detail.suggestedPlain || '').trim()
      const suggestedHtml = detail.suggestedHtml || null
      if (!suggestedPlain && !suggestedHtml && !detail.suggestedContentJson) {
        emitInlineShown(requestId, null)
        return
      }

      const bridgeRequest =
        activeRequestRef.current?.requestId === requestId ? activeRequestRef.current : null

      inlinePendingRef.current = {
        requestId,
        ...range,
        suggestedPlain: suggestedPlain || ' ',
        suggestedHtml,
        acceptedContent: detail.acceptedContent || null,
        selectedFraction: Number(detail.selectedFraction) || 0,
        structuredData: detail.structuredData || null,
        suggestedContentJson: detail.suggestedContentJson || null,
        action: detail.action,
        isFullDoc: Boolean(detail.isFullDoc),
        originalText: detail.originalText || liveEditor.state.doc.textBetween(range.from, range.to, '\n'),
        bridgeRequest,
      }

      notifyPreviewSession(true)
      setInlineAiSuggestion(liveEditor, {
        from: range.from,
        to: range.to,
        suggestedPlain,
        suggestedHtml: detail.suggestedHtml || null,
      })
      try {
        liveEditor.commands.focus()
        liveEditor.commands.setTextSelection({ from: range.from, to: range.to })
        liveEditor.commands.scrollIntoView()
      } catch {
        // non-fatal
      }
      emitInlineShown(requestId, range, detail.action)
    })

    const unsubClear = subscribeEditorInlineSuggestionClear(({ requestId }) => {
      const liveEditor = editorRef.current
      const pending = inlinePendingRef.current
      if (requestId && pending?.requestId && pending.requestId !== requestId) return
      if (liveEditor && !liveEditor.isDestroyed) {
        clearInlineAiSuggestion(liveEditor)
      }
      const bridgeReq = pending?.bridgeRequest
      inlinePendingRef.current = null
      notifyPreviewSession(false)
      if (bridgeReq) {
        emitResult({
          ...bridgeReq,
          status: EDITOR_AI_ACTION_STATUS.CANCELLED,
          action: bridgeReq.action,
        })
        activeRequestRef.current = null
        targetRangeRef.current = null
        isFullDocRef.current = false
      }
    })

    const unsubApply = subscribeEditorInlineSuggestionApply(({ requestId }) => {
      const liveEditor = editorRef.current
      const pending = inlinePendingRef.current
      if (!pending || pending.requestId !== requestId) {
        emitInlineApplied(requestId, false, 'No pending suggestion to apply.')
        return
      }
      if (!liveEditor || liveEditor.isDestroyed) {
        emitInlineApplied(requestId, false, 'Editor is not available.')
        return
      }

      try {
        const {
          from,
          to,
          suggestedPlain,
          suggestedHtml,
          acceptedContent,
          isFullDoc,
          action,
        } = pending
        const tiptapPayload = coerceTipTapDocForApply(
          acceptedContent?.type === 'doc'
            ? acceptedContent
            : pending.suggestedContentJson,
        )
        const insertPayload =
          tiptapPayload
          || acceptedContent
          || (isFullDoc ? suggestedHtml : suggestedPlain)
          || suggestedHtml
          || suggestedPlain

        if (action === EDITOR_AI_ACTIONS.REWRITE || action === EDITOR_AI_ACTIONS.IMPROVE) {
          if (tiptapPayload) {
            applyTipTapContentToEditor(liveEditor, { docJson: tiptapPayload })
          } else if (pending.suggestedContentJson) {
            emitInlineApplied(requestId, false, getFriendlyErrorMessage(getAppLanguage()))
            return
          } else {
            emitInlineApplied(
              requestId,
              false,
              'Suggestion could not be applied: structured document data was missing or invalid.',
            )
            return
          }
        } else if (isFullDoc) {
          liveEditor.commands.setContent(insertPayload || '<p></p>', false)
        } else if (typeof insertPayload === 'string' && /<\/?[a-z]/i.test(insertPayload)) {
          liveEditor.chain().focus().insertContentAt({ from, to }, insertPayload).run()
        } else {
          liveEditor.chain().focus().insertContentAt({ from, to }, insertPayload || '').run()
        }
        clearInlineAiSuggestion(liveEditor)
        const bridgeReq = pending.bridgeRequest
        inlinePendingRef.current = null
        notifyPreviewSession(false)
        emitInlineApplied(requestId, true)
        if (bridgeReq) {
          emitResult({
            ...bridgeReq,
            status: EDITOR_AI_ACTION_STATUS.APPLIED,
            action,
            applied_scope: isFullDoc ? 'full_document' : 'selection',
            sop_id: documentIdRef.current || null,
          })
          activeRequestRef.current = null
          targetRangeRef.current = null
          isFullDocRef.current = false
        }
        if (typeof onAfterApply === 'function') {
          onAfterApply({
            action,
            applied_scope: isFullDoc ? 'full_document' : 'selection',
            source: bridgeReq ? 'kl_assistant' : 'actions_tab',
          })
        }
      } catch (err) {
        console.error('[editor-actions-bridge] apply failed', err)
        emitInlineApplied(requestId, false, getFriendlyErrorMessage(getAppLanguage()))
      }
    })

    const onScrollToRange = (event) => {
      const liveEditor = editorRef.current
      const { from, to } = event.detail || {}
      if (!liveEditor || liveEditor.isDestroyed || from == null || to == null) return
      try {
        liveEditor.chain().focus().setTextSelection({ from, to }).scrollIntoView().run()
      } catch (err) {
        console.warn('[editor-actions-bridge] scrollIntoView failed', err)
      }
    }
    window.addEventListener(EDITOR_SCROLL_TO_RANGE_EVENT, onScrollToRange)

    const onGapAppend = (event) => {
      const liveEditor = editorRef.current
      const html = event.detail?.html
      if (!liveEditor || liveEditor.isDestroyed || !html) return
      try {
        const docEnd = liveEditor.state.doc.content.size
        const appendix = /<h3/i.test(String(html))
          ? html
          : `<h3>AI Gap Check Findings</h3>${html}`
        liveEditor.chain().focus().insertContentAt(docEnd, appendix, { updateSelection: false }).run()
      } catch (err) {
        console.warn('[editor-actions-bridge] gap append failed', err)
      }
    }
    window.addEventListener(EDITOR_GAP_APPEND_EVENT, onGapAppend)

    return () => {
      unsubSnapshot()
      unsubShow()
      unsubClear()
      unsubApply()
      window.removeEventListener(EDITOR_SCROLL_TO_RANGE_EVENT, onScrollToRange)
      window.removeEventListener(EDITOR_GAP_APPEND_EVENT, onGapAppend)
    }
  }, [editor, emitInlineApplied, emitInlineShown, notifyPreviewSession, onAfterApply, resolveDocRange])

  useEffect(() => {
    const unsubscribe = subscribeEditorAiActionRequest((request) => {
      if (!request || !request.action) return
      console.info('[kl-editor-bridge-received]', {
        action: request.action,
        requestId: request.requestId,
        source: request.source,
      })
      if (request.action === EDITOR_AI_ACTIONS.READ) {
        handleReadRequest(request)
        return
      }
      runActionRequest(request)
    })
    return unsubscribe
  }, [handleReadRequest, runActionRequest])

  const handleAccept = useCallback(() => {
    const liveEditor = editorRef.current
    const request = activeRequestRef.current
    const target = targetRangeRef.current
    if (!liveEditor || liveEditor.isDestroyed || !aiResult || !request) {
      closeModal()
      return
    }

    const action = String(aiResult.action || request.action || '').toLowerCase()
    const suggestedHtml = aiResult.suggested_text || ''
    const structuredData = aiResult.structured_data || {}

    try {
      if (action === EDITOR_AI_ACTIONS.GAP_CHECK) {
        const appendix = buildFindingsHtml(structuredData) || `<h3>AI Gap Check Findings</h3>${suggestedHtml}`
        const docEnd = liveEditor.state.doc.content.size
        liveEditor
          .chain()
          .focus()
          .insertContentAt(docEnd, appendix, { updateSelection: false })
          .run()
        console.info('[kl-editor-action-inserted]', { action, scope: 'append', requestId: request?.requestId })
      } else if (isFullDocRef.current) {
        const acceptedDoc = buildAcceptedInsertContent(aiResult, {
          selectedFraction: 1,
          isFullDoc: true,
        })
        const tiptapDoc = coerceTipTapDocForApply(
          acceptedDoc?.type === 'doc' ? acceptedDoc : aiResult?.suggested_content_json,
        )
        if (tiptapDoc) {
          applyTipTapContentToEditor(liveEditor, { docJson: tiptapDoc })
        } else {
          console.warn('[kl-editor-action] full SOP apply skipped — no TipTap structure in result')
          window.alert(getFriendlyErrorMessage(getAppLanguage()))
          closeModal()
          emitResult({
            ...request,
            status: EDITOR_AI_ACTION_STATUS.ERROR,
            message: getFriendlyErrorMessage(getAppLanguage()),
          })
          return
        }
        console.info('[kl-editor-action-inserted]', { action, scope: 'full_document', requestId: request?.requestId })
      } else {
        const from = target?.from ?? 0
        const to = target?.to ?? liveEditor.state.doc.content.size
        const acceptedDoc = buildAcceptedInsertContent(aiResult, {
          selectedFraction: isFullDocRef.current ? 1 : Math.abs(to - from) / Math.max(1, liveEditor.state.doc.content.size),
          isFullDoc: false,
        })
        const tiptapDoc = coerceTipTapDocForApply(
          acceptedDoc?.type === 'doc' ? acceptedDoc : aiResult?.suggested_content_json,
        )
        if (action === EDITOR_AI_ACTIONS.REWRITE || action === EDITOR_AI_ACTIONS.IMPROVE) {
          if (tiptapDoc) {
            applyTipTapContentToEditor(liveEditor, { docJson: tiptapDoc })
          } else {
            console.warn('[kl-editor-action] TipTap apply skipped — invalid suggested_content_json')
            window.alert(getFriendlyErrorMessage(getAppLanguage()))
            closeModal()
            emitResult({
              ...request,
              status: EDITOR_AI_ACTION_STATUS.ERROR,
              message: getFriendlyErrorMessage(getAppLanguage()),
            })
            return
          }
        } else {
          const plainContent = stripHtml(aiResult?.suggested_text)
          liveEditor
            .chain()
            .focus()
            .insertContentAt({ from, to }, plainContent || '')
            .run()
        }
        console.info('[kl-editor-action-inserted]', { action, scope: 'selection', requestId: request?.requestId })
      }

      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.APPLIED,
        action,
        applied_scope: isFullDocRef.current ? 'full_document' : 'selection',
        sop_id: documentIdRef.current || null,
      })
      console.info('[kl-editor-action-accepted]', { action, requestId: request?.requestId })

      if (typeof onAfterApply === 'function') {
        try {
          onAfterApply({ action, applied_scope: isFullDocRef.current ? 'full_document' : 'selection' })
        } catch (err) {
          console.error('[editor-ai-bridge] onAfterApply failed', err)
        }
      }
    } catch (err) {
      console.error('[editor-ai-bridge] failed to apply suggestion', err)
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.ERROR,
        message: getFriendlyErrorMessage(getAppLanguage()),
      })
    } finally {
      closeModal()
    }
  }, [aiResult, closeModal, emitResult, onAfterApply])

  const handleReject = useCallback(() => {
    const request = activeRequestRef.current
    const action = String(aiResult?.action || request?.action || '').toLowerCase()
    if (request) {
      emitResult({
        ...request,
        status: EDITOR_AI_ACTION_STATUS.CANCELLED,
        action,
      })
    }
    closeModal()
  }, [aiResult, closeModal, emitResult])

  const modalAction = aiResult?.action
  const useComparisonModal =
    isModalOpen
    && modalAction
    && modalAction !== EDITOR_AI_ACTIONS.REWRITE
    && modalAction !== EDITOR_AI_ACTIONS.IMPROVE

  return (
    <>
      <EditorInlineSuggestionToolbar />
      {useComparisonModal ? (
        <AIComparisonModal
          isOpen={isModalOpen}
          onClose={handleReject}
          action={aiResult?.action}
          originalText={aiResult?.original_text}
          suggestedText={aiResult?.suggested_text}
          explanation={aiResult?.explanation}
          structuredData={aiResult?.structured_data}
          onAccept={handleAccept}
          sectionName={aiResult?.section_name}
          sopTitle={sopTitle}
        />
      ) : null}
      {isLoading ? (
        <div className="editor-ai-bridge-loading" role="status" aria-live="polite">
          <div className="editor-ai-bridge-loading__inner">
            <span className="editor-ai-bridge-loading__spinner" />
            <span>{loadingMessage}</span>
          </div>
        </div>
      ) : null}
    </>
  )
}

export default EditorAIBridge
