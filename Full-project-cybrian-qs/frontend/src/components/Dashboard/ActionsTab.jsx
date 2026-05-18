import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { AlertTriangle, Check, Sparkles, Wand2, X } from 'lucide-react'
import { performAIAction } from '../../api/editorApi'
import { selectionLooksLikeFormattedAiReport } from '../../utils/aiActionSelection'
import { buildGapCheckSidebarReport } from '../../utils/actionsTabGapReport'
import {
  buildAcceptedInsertContent,
  buildInlineSuggestionHtml,
  normalizeAiActionResult,
} from '../../utils/editorAiActionShared'
import { buildActionSummary } from '../../utils/actionsTabSummary'
import {
  applyEditorInlineSuggestion,
  appendGapFindingsToEditor,
  clearEditorInlineSuggestion,
  requestEditorSnapshot,
  scrollEditorToRange,
  showEditorInlineSuggestion,
  subscribeActionsTabRun,
} from '../../utils/editorActionsBridge'
import {
  AI_ACTION_TRIGGERED_BY,
  getActiveEditorDocumentId,
  hasActiveSopEditor,
} from '../../utils/editorAiBridge'
import { inferEditScope } from '../../utils/editScopeInference'
import { wantsFullSopIntent } from '../../utils/sopActionIntent'
import { runEditorGapCheck } from '../../utils/editorGapCheck'
import { sanitizeRenderedHtml } from '../../utils/aiOutputFormatter'

const ACTION_TEXT_WARNING_CHARS = 7000
const INLINE_SHOWN_EVENT = 'editor-actions-inline-shown'
const INLINE_APPLIED_EVENT = 'editor-actions-inline-applied'

export default function ActionsTab({ onSwitchToActions }) {
  const location = useLocation()
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(null)
  const inFlightRef = useRef(false)
  const pendingRef = useRef(null)

  useEffect(() => {
    pendingRef.current = pending
  }, [pending])

  const clearPending = useCallback((requestId) => {
    const current = pendingRef.current
    if (requestId && current?.requestId && current.requestId !== requestId) return
    if (current?.action !== 'gap_check') {
      clearEditorInlineSuggestion(current?.requestId)
    }
    setPending(null)
  }, [])

  const runGapCheck = useCallback(async (instructionText = '') => {
    const instruction = String(instructionText || prompt || '').trim()
    const { target, result, normalized } = await runEditorGapCheck({ instruction })
    const report = buildGapCheckSidebarReport(result)

    setPending({
      requestId: `gap-${Date.now().toString(36)}`,
      action: 'gap_check',
      sectionName: target.sectionName,
      isFullDoc: Boolean(target.isFullDoc),
      gapReport: report,
      previewHtml: normalized.suggestedHtml,
      range: { from: target.from, to: target.to },
      appendHtml: normalized.suggestedHtml,
    })
  }, [prompt])

  const runRewriteImprove = useCallback(async (action, instructionText = '') => {
    const documentId = getActiveEditorDocumentId()
    const instruction = String(instructionText || prompt || '').trim()
    const snapshot = await requestEditorSnapshot({ prompt: instruction })
    const target = snapshot.target
    if (target?.from == null || target?.to == null || !target?.text) {
      throw new Error(snapshot.error || 'Could not find that heading or paragraph in the open SOP.')
    }

    if (selectionLooksLikeFormattedAiReport(target.text)) {
      throw new Error('That region looks like AI report output. Select original SOP prose instead.')
    }

    if (target.text.length > ACTION_TEXT_WARNING_CHARS) {
      const proceed = window.confirm('This section is long and may time out. Continue?')
      if (!proceed) return
    }

    scrollEditorToRange(target.from, target.to)

    const docSize = snapshot.docSize || target.to
    const selectedFraction =
      !target.isFullDoc && docSize > 0
        ? Math.abs(target.to - target.from) / docSize
        : target.isFullDoc
          ? 1
          : 0.3

    const result = await performAIAction({
      action,
      text: target.text,
      document_id: documentId,
      section_id: `${target.from}-${target.to}`,
      sop_title: snapshot.sopTitle || 'Untitled SOP',
      section_name: target.sectionName || 'Selected text',
      section_type: target.isFullDoc || wantsFullSopIntent(instruction)
        ? 'Full Document'
        : target.sectionType || 'Paragraph',
      edit_scope: target.isFullDoc || wantsFullSopIntent(instruction)
        ? 'full_document'
        : inferEditScope({
            text: target.text,
            from: target.from,
            to: target.to,
            docSize: snapshot.docSize || target.to,
            instruction,
          }),
      sop_entity_id: documentId,
      triggered_by: AI_ACTION_TRIGGERED_BY.EDITOR_BUBBLE,
    })

    const normalized = normalizeAiActionResult(action, result)
    if (!normalized.suggestedPlain) {
      throw new Error('No suggestion returned.')
    }

    const acceptedContent = buildAcceptedInsertContent(normalized.raw, {
      selectedFraction,
      isFullDoc: Boolean(target.isFullDoc),
    })
    const inlineHtml = buildInlineSuggestionHtml(normalized)
    const requestId = `actions-${Date.now().toString(36)}`

    await new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        window.removeEventListener(INLINE_SHOWN_EVENT, onShow)
        reject(new Error('Could not show inline suggestion at the target location.'))
      }, 12000)

      const onShow = (event) => {
        if (event.detail?.requestId !== requestId) return
        window.clearTimeout(timer)
        window.removeEventListener(INLINE_SHOWN_EVENT, onShow)
        resolve(event.detail || {})
      }
      window.addEventListener(INLINE_SHOWN_EVENT, onShow)
      showEditorInlineSuggestion({
        requestId,
        from: target.from,
        to: target.to,
        originalText: target.text,
        suggestedPlain: normalized.suggestedPlain,
        suggestedHtml: inlineHtml,
        structuredData: normalized.structured,
        action,
        isFullDoc: Boolean(target.isFullDoc),
        acceptedContent,
        selectedFraction,
      })
    })

    setPending({
      requestId,
      action,
      sectionName: target.sectionName,
      isFullDoc: Boolean(target.isFullDoc),
      summarySections: buildActionSummary(action, result),
      previewHtml: inlineHtml,
      range: { from: target.from, to: target.to },
    })
  }, [prompt])

  const runAction = useCallback(async (action, instructionText = '') => {
    if (inFlightRef.current) return
    if (!hasActiveSopEditor(location.pathname)) {
      setError('Open an SOP in the editor to use Actions.')
      return
    }

    if (!getActiveEditorDocumentId()) {
      setError('No active SOP. Open a document in the editor first.')
      return
    }

    const instruction = String(instructionText || prompt || '').trim()
    if (!instruction) {
      setError(
        'Describe the target, e.g. "gap check CAPAs (zugehörig zu SOP-IT-003)" or "DEVIATIONS rewrite this".',
      )
      return
    }

    clearPending()
    inFlightRef.current = true
    setLoading(true)
    setError('')

    try {
      if (action === 'gap_check') {
        await runGapCheck(instruction)
      } else {
        await runRewriteImprove(action, instruction)
      }
    } catch (err) {
      setError(err?.message || 'Action failed.')
      clearPending()
    } finally {
      inFlightRef.current = false
      setLoading(false)
    }
  }, [location.pathname, prompt, clearPending, runGapCheck, runRewriteImprove])

  useEffect(() => {
    const unsubscribe = subscribeActionsTabRun(({ action, prompt: runPrompt }) => {
      if (typeof onSwitchToActions === 'function') onSwitchToActions()
      if (runPrompt) setPrompt(runPrompt)
      const normalizedAction =
        action === 'gap_check' ? 'gap_check' : action === 'improve' ? 'improve' : 'rewrite'
      runAction(normalizedAction, runPrompt || '')
    })
    return unsubscribe
  }, [runAction, onSwitchToActions])

  const handleAccept = useCallback(() => {
    if (!pending?.requestId || pending.action === 'gap_check') return
    applyEditorInlineSuggestion(pending.requestId)
  }, [pending])

  const handleAppendGap = useCallback(() => {
    if (!pending?.appendHtml) return
    appendGapFindingsToEditor(pending.appendHtml)
    setPending(null)
    setPrompt('')
    setError('')
  }, [pending])

  const handleReject = useCallback(() => {
    clearPending()
  }, [clearPending])

  useEffect(() => {
    const onApplied = (event) => {
      const { requestId, ok, message } = event.detail || {}
      if (!pendingRef.current || pendingRef.current.requestId !== requestId) return
      if (!ok) {
        setError(message || 'Could not apply suggestion.')
        return
      }
      setPending(null)
      setPrompt('')
      setError('')
    }
    window.addEventListener(INLINE_APPLIED_EVENT, onApplied)
    return () => window.removeEventListener(INLINE_APPLIED_EVENT, onApplied)
  }, [])

  useEffect(() => () => clearPending(), [clearPending])

  const isGapPending = pending?.action === 'gap_check'

  return (
    <div className="ai-actions-tab">
      <p className="ai-actions-tab__hint">
        Name a <strong>section</strong> or the <strong>full SOP</strong> — Rewrite/Improve show inline diff in the editor;
        <strong> Gap Check</strong> shows the full audit report here (same as select → Gap Check).
      </p>

      <label className="ai-actions-tab__label" htmlFor="ai-actions-prompt">
        What to run on the open SOP
      </label>
      <textarea
        id="ai-actions-prompt"
        className="ai-actions-tab__textarea"
        rows={4}
        placeholder='gap check 🟠 CAPAs (zugehörig zu SOP-IT-003) — or — gap check this SOP'
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        disabled={loading}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            runAction('gap_check', prompt)
          }
        }}
      />

      <div className="ai-actions-tab__chips" role="group" aria-label="Editor actions">
        <button
          type="button"
          className="ai-action-chip ai-action-chip--gap"
          disabled={loading}
          onClick={() => runAction('gap_check', prompt)}
        >
          <AlertTriangle size={14} />
          <span className="ai-action-chip__text">Gap Check</span>
        </button>
        <button
          type="button"
          className="ai-action-chip ai-action-chip--primary"
          disabled={loading}
          onClick={() => runAction('rewrite', prompt)}
        >
          <Wand2 size={14} />
          <span className="ai-action-chip__text">Rewrite</span>
        </button>
        <button type="button" className="ai-action-chip" disabled={loading} onClick={() => runAction('improve', prompt)}>
          <Sparkles size={14} />
          <span className="ai-action-chip__text">Improve</span>
        </button>
      </div>

      {loading ? <p className="ai-actions-tab__status" role="status">Running…</p> : null}
      {error ? <p className="ai-actions-tab__error" role="alert">{error}</p> : null}

      {pending ? (
        <div className={`ai-actions-tab__review${isGapPending ? ' ai-actions-tab__review--gap' : ''}`}>
          <div className="ai-actions-tab__review-header">
            <h4 className="ai-actions-tab__review-title">
              {isGapPending ? 'Gap check report' : 'Review at target location'}
            </h4>
            <span className="ai-actions-tab__review-scope">{pending.sectionName}</span>
          </div>

          {!isGapPending ? (
            <p className="ai-actions-tab__pending-hint">
              In the editor: <span className="ai-actions-tab__strike-sample">removed</span> →
              <span className="ai-actions-tab__add-sample"> suggested</span>. Accept replaces only that range.
            </p>
          ) : (
            <p className="ai-actions-tab__pending-hint">
              Full compliance gap analysis for this scope. The editor scrolls to the audited section; findings are not
              shown inline.
            </p>
          )}

          {isGapPending && pending.gapReport?.sections?.map((section) => (
            <div key={section.id} className="ai-actions-tab__summary-block ai-actions-tab__gap-block">
              <h5 className="ai-actions-tab__summary-title">{section.title}</h5>
              {section.body ? <p className="ai-actions-tab__summary-body ai-actions-tab__gap-body">{section.body}</p> : null}
              {section.gapItems?.map((gap, index) => (
                <div key={`${section.id}-gap-${index}`} className="ai-actions-tab__gap-item">
                  <p className="ai-actions-tab__gap-issue">{gap.issue}</p>
                  {gap.explanation ? <p className="ai-actions-tab__gap-meta">{gap.explanation}</p> : null}
                  {gap.recommendation ? <p className="ai-actions-tab__gap-rec">{gap.recommendation}</p> : null}
                </div>
              ))}
            </div>
          ))}

          {!isGapPending
            ? pending.summarySections?.map((section) => (
                <div key={section.id} className="ai-actions-tab__summary-block">
                  <h5 className="ai-actions-tab__summary-title">{section.title}</h5>
                  {section.body ? <p className="ai-actions-tab__summary-body">{section.body}</p> : null}
                  {section.items?.length ? (
                    <ul className="ai-actions-tab__summary-list">
                      {section.items.map((item, index) => (
                        <li key={`${section.id}-${index}`}>{item}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))
            : null}

          {pending.previewHtml ? (
            <details className="ai-actions-tab__preview" open={isGapPending}>
              <summary>{isGapPending ? 'Full formatted report' : 'Preview new SOP text'}</summary>
              <div
                className="ai-actions-tab__preview-html tiptap ai-actions-tab__gap-html"
                dangerouslySetInnerHTML={{ __html: sanitizeRenderedHtml(pending.previewHtml) }}
              />
            </details>
          ) : null}

          <div className="ai-actions-tab__decision" role="group" aria-label={isGapPending ? 'Gap check actions' : 'Accept or reject'}>
            {isGapPending ? (
              <>
                <button type="button" className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--reject" onClick={handleReject}>
                  <X size={14} />
                  Close
                </button>
                <button
                  type="button"
                  className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--accept"
                  onClick={handleAppendGap}
                >
                  <Check size={14} />
                  Append to SOP
                </button>
              </>
            ) : (
              <>
                <button type="button" className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--reject" onClick={handleReject}>
                  <X size={14} />
                  Reject
                </button>
                <button type="button" className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--accept" onClick={handleAccept}>
                  <Check size={14} />
                  Accept
                </button>
              </>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}
