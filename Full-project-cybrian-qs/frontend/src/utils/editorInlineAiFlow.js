/**
 * Shared rewrite/improve inline suggestion flow (preview → accept/reject).
 */

import { performAIAction } from '../api/editorApi'
import { showEditorInlineSuggestion } from './editorActionsBridge'
import {
  buildAcceptedInsertContent,
  buildInlineSuggestionHtml,
  normalizeAiActionResult,
} from './editorAiActionShared'
import { inferEditScope } from './editScopeInference'
import {
  extractUserInstructionPart,
  promptRequestsNamedSection,
  resolveSectionAtCursor,
  resolveTargetInEditor,
} from './editorTargetResolver'
import { isExplicitFullSopRequest } from './sopActionIntent'
import { buildPatchScopePayload } from './tiptapScope'
import { getAppLanguage, getFriendlyErrorMessage } from './friendlyErrorMessage'

export const INLINE_SHOWN_EVENT = 'editor-actions-inline-shown'
export const INLINE_APPLIED_EVENT = 'editor-actions-inline-applied'

const INLINE_SHOW_TIMEOUT_MS = 15000

function resolveBlockMetaAtPosition(editor, from) {
  let sectionName = 'Current block'
  let sectionType = 'Paragraph'
  try {
    const $pos = editor.state.doc.resolve(from)
    for (let d = $pos.depth; d >= 0; d -= 1) {
      const node = $pos.node(d)
      if (node.type.name === 'heading') {
        sectionName = node.textContent || sectionName
        sectionType = 'Heading'
        break
      }
      if (node.type.name === 'tableCell' || node.type.name === 'tableHeader') {
        sectionType = 'Table cell'
        sectionName = 'Table cell'
        break
      }
      if (node.type.name === 'listItem') {
        sectionType = 'List item'
      }
    }
  } catch {
    // best-effort
  }
  return { sectionName, sectionType }
}

/**
 * Resolve rewrite/improve target: selection → named section (prompt) → cursor block → full SOP.
 */
export function resolveRewriteImproveTarget(
  editor,
  { instruction = '', targetScope = '', sectionHint = '', userMessage = '' } = {},
) {
  if (!editor || editor.isDestroyed) {
    return null
  }

  const { state } = editor
  const { selection } = state
  const actionPrompt = String(instruction || '').trim()
  const targetingPrompt =
    String(userMessage || '').trim() || extractUserInstructionPart(actionPrompt) || actionPrompt
  const scope = String(targetScope || '').trim().toLowerCase()
  const hint = String(sectionHint || '').trim()
  const explicitFullSop =
    scope === 'full_document' || isExplicitFullSopRequest({ instruction: actionPrompt })
  const hasSelection = Boolean(selection && !selection.empty)
  const docSize = state.doc.content.size

  if (explicitFullSop) {
    return {
      from: 0,
      to: docSize,
      text: state.doc.textBetween(0, docSize, '\n').trim(),
      isFullDoc: true,
      sectionName: 'Full SOP',
      sectionType: 'Full Document',
      selectedFraction: 1,
      editScope: 'full_document',
    }
  }

  if (scope === 'selection' && hasSelection) {
    const from = selection.from
    const to = selection.to
    const text = state.doc.textBetween(from, to, '\n').trim()
    if (!text) return null
    const meta = resolveBlockMetaAtPosition(editor, from)
    return {
      from,
      to,
      text,
      isFullDoc: false,
      sectionName: 'Selected text',
      sectionType: meta.sectionType,
      selectedFraction: Math.abs(to - from) / Math.max(1, docSize),
      editScope: inferEditScope({ text, instruction: actionPrompt }),
    }
  }

  if (
    hasSelection
    && !promptRequestsNamedSection({ instruction: targetingPrompt, sectionHint: hint, targetScope: scope })
  ) {
    const from = selection.from
    const to = selection.to
    const text = state.doc.textBetween(from, to, '\n').trim()
    if (!text) return null
    const meta = resolveBlockMetaAtPosition(editor, from)
    return {
      from,
      to,
      text,
      isFullDoc: false,
      sectionName: 'Selected text',
      sectionType: meta.sectionType,
      selectedFraction: Math.abs(to - from) / Math.max(1, docSize),
      editScope: inferEditScope({ text, instruction: actionPrompt }),
    }
  }

  if (targetingPrompt || hint || scope === 'section') {
    const selectionPayload = hasSelection
      ? { from: selection.from, to: selection.to, empty: false }
      : { empty: true }
    const resolved = resolveTargetInEditor(editor, {
      prompt: targetingPrompt,
      selection: selectionPayload,
      sectionHint: hint,
      targetScope: scope,
    })
    if (resolved?.text && resolved.from != null && resolved.to != null) {
      return {
        from: resolved.from,
        to: resolved.to,
        text: resolved.text,
        isFullDoc: false,
        sectionName: resolved.sectionName || 'Current block',
        sectionType: resolved.sectionType || 'Paragraph',
        selectedFraction: Math.abs(resolved.to - resolved.from) / Math.max(1, docSize),
        editScope: inferEditScope({ text: resolved.text, instruction: actionPrompt }),
      }
    }
    if (promptRequestsNamedSection({ instruction: targetingPrompt, sectionHint: hint, targetScope: scope })) {
      return null
    }
  }

  const section = resolveSectionAtCursor(editor)
  if (!section?.text) return null

  return {
    from: section.from,
    to: section.to,
    text: section.text,
    isFullDoc: false,
    sectionName: section.sectionName || 'Current block',
    sectionType: section.sectionType || 'Paragraph',
    selectedFraction: Math.abs(section.to - section.from) / Math.max(1, docSize),
    editScope: inferEditScope({ text: section.text, instruction: actionPrompt }),
  }
}

/**
 * Call /api/ai/action and return normalized result (does not show inline UI).
 */
export async function fetchRewriteImproveSuggestion({
  editor = null,
  action,
  target,
  contentJson: contentJsonOverride = null,
  sopTitle = 'Untitled SOP',
  documentId = null,
  triggeredBy,
  instruction = null,
}) {
  const contentJson =
    contentJsonOverride
    || (editor && !editor.isDestroyed ? editor.getJSON() : null)
  const patchScope = target.isFullDoc
    ? {}
    : buildPatchScopePayload(editor, {
        from: target.from,
        to: target.to,
        text: target.text,
        contentJson,
      })

  const result = await performAIAction({
    action,
    text: target.text,
    document_id: documentId,
    section_id: `${target.from}-${target.to}`,
    sop_title: sopTitle,
    section_name: target.sectionName,
    section_type: target.sectionType,
    edit_scope: target.editScope,
    patch_node_ids: patchScope.patch_node_ids,
    content_json: contentJson,
    sop_entity_id: documentId,
    triggered_by: triggeredBy,
    assistant_instruction: instruction,
  })

  if (!result) {
    return null
  }

  const normalized = normalizeAiActionResult(action, result)
  if (!normalized.suggestedPlain && !normalized.suggestedContentJson) {
    throw new Error('No suggestion returned.')
  }

  return { result, normalized }
}

/**
 * Show inline diff in the editor and wait until decorations are mounted.
 */
export function presentInlineRewriteImproveSuggestion({
  requestId,
  target,
  normalized,
  action,
}) {
  const acceptedContent = buildAcceptedInsertContent(normalized.raw, {
    selectedFraction: target.selectedFraction,
    isFullDoc: target.isFullDoc,
  })
  const inlineHtml = buildInlineSuggestionHtml(normalized)

  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener(INLINE_SHOWN_EVENT, onShow)
      reject(new Error(getFriendlyErrorMessage(getAppLanguage())))
    }, INLINE_SHOW_TIMEOUT_MS)

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
      suggestedContentJson: normalized.suggestedContentJson,
      action,
      isFullDoc: Boolean(target.isFullDoc),
      acceptedContent,
      selectedFraction: target.selectedFraction,
    })
  })
}

/**
 * Full flow: API + inline preview.
 */
export async function runRewriteImproveWithInlinePreview({
  editor,
  action,
  requestId,
  sopTitle,
  documentId,
  triggeredBy,
  instruction = '',
  target: targetOverride = null,
  targetScope = '',
  sectionHint = '',
  userMessage = '',
}) {
  const target =
    targetOverride
    || resolveRewriteImproveTarget(editor, { instruction, targetScope, sectionHint, userMessage })
  if (!target?.text) {
    throw new Error('Select text in the SOP or place the cursor in a paragraph.')
  }

  const { result, normalized } = await fetchRewriteImproveSuggestion({
    editor,
    action,
    target,
    sopTitle,
    documentId,
    triggeredBy,
    instruction: instruction || null,
  })

  await presentInlineRewriteImproveSuggestion({
    requestId,
    target,
    normalized,
    action,
  })

  return { target, result, normalized }
}
