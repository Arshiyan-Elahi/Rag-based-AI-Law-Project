import { dispatchActionsTabRun } from './editorActionsBridge'
import { dispatchEditorAiActionRequest } from './editorAiBridge'

export const INLINE_PREVIEW_ACTIONS = new Set([
  'rewrite',
  'improve',
  'gap_check',
  'summarize',
  'analyze',
])

/**
 * Unified action dispatcher used by sidebar/chat/editor integrations.
 * Guarantees that content-editing actions always go through the preview-first
 * ActionsTab pipeline (generate -> preview -> user decision -> apply/reject).
 */
export function dispatchUnifiedAction({
  action,
  prompt = '',
  requestId = '',
  targetOptions = {},
  source = 'kl_assistant',
} = {}) {
  const normalizedAction = String(action || '').trim().toLowerCase()
  if (!normalizedAction) {
    throw new Error('dispatchUnifiedAction requires an action.')
  }

  const detail = {
    action: normalizedAction,
    prompt: String(prompt || ''),
    userPrompt: String(targetOptions.userPrompt || '').trim(),
    sectionHint: String(targetOptions.sectionHint || '').trim(),
    targetScope: String(targetOptions.targetScope || '').trim().toLowerCase(),
    lineNumber: targetOptions.lineNumber ?? null,
    recordId: String(targetOptions.recordId || '').trim(),
    preferFullSection: Boolean(targetOptions.preferFullSection),
    sourceContentOverride: targetOptions.sourceContentOverride || null,
  }

  if (INLINE_PREVIEW_ACTIONS.has(normalizedAction)) {
    dispatchActionsTabRun(detail)
    return { pipeline: 'inline_preview' }
  }

  const dispatchedId = dispatchEditorAiActionRequest({
    action: normalizedAction,
    prompt: detail.prompt,
    userPrompt: detail.userPrompt,
    sectionHint: detail.sectionHint,
    targetScope: detail.targetScope,
    lineNumber: detail.lineNumber,
    recordId: detail.recordId,
    preferFullSection: detail.preferFullSection,
    requestId: requestId || undefined,
    source,
  })
  return { pipeline: 'bridge', requestId: dispatchedId }
}
