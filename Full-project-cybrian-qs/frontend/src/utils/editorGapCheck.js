/**
 * Shared gap_check flow for Actions tab and Chat (same API as bubble menu).
 */

import { performAIAction } from '../api/editorApi'
import { getAppLanguage, getFriendlyErrorMessage } from './friendlyErrorMessage'
import { normalizeAiActionResult } from './editorAiActionShared'
import { requestEditorSnapshot, scrollEditorToRange } from './editorActionsBridge'
import { AI_ACTION_TRIGGERED_BY, getActiveEditorDocumentId } from './editorAiBridge'
import { inferEditScope } from './editScopeInference'
import { isExplicitFullSopRequest, wantsFullSopIntent } from './sopActionIntent'
import { buildPatchScopePayload } from './tiptapScope'

export async function resolveGapCheckTarget(instruction) {
  const snapshot = await requestEditorSnapshot({ prompt: instruction })
  const target = snapshot.target
  if (!target?.text || target.from == null || target.to == null) {
    throw new Error(snapshot.error || 'Could not find that section in the open SOP.')
  }
  return { snapshot, target }
}

export async function runEditorGapCheck({ instruction, documentId: docIdOverride } = {}) {
  const instructionText = String(instruction || '').trim()
  if (!instructionText) {
    throw new Error('Describe what to check, e.g. "gap check CAPAs (zugehörig zu SOP-IT-003)" or "gap check this SOP".')
  }

  const documentId = docIdOverride || getActiveEditorDocumentId()
  if (!documentId) {
    throw new Error('No active SOP. Open a document in the editor first.')
  }

  const { snapshot, target } = await resolveGapCheckTarget(instructionText)

  scrollEditorToRange(target.from, target.to)

  const explicitFullSop = isExplicitFullSopRequest({ instruction: instructionText })
  const patchScope = buildPatchScopePayload(null, {
    text: target.text,
    contentJson: snapshot.contentJson || null,
  })
  const result = await performAIAction({
    action: 'gap_check',
    text: target.text,
    document_id: documentId,
    section_id: `${target.from}-${target.to}`,
    sop_title: snapshot.sopTitle || 'Untitled SOP',
    section_name: target.sectionName || 'Selected text',
    section_type: explicitFullSop ? 'Full Document' : target.sectionType || 'Paragraph',
    edit_scope: explicitFullSop ? 'full_document' : inferEditScope({ text: target.text, instruction: instructionText }),
    patch_node_ids: explicitFullSop ? undefined : patchScope.patch_node_ids,
    content_json: snapshot.contentJson || null,
    sop_entity_id: documentId,
    triggered_by: AI_ACTION_TRIGGERED_BY.EDITOR_BUBBLE,
    assistant_instruction: instructionText || null,
  })
  if (!result) {
    throw new Error(getFriendlyErrorMessage(getAppLanguage()))
  }

  const normalized = normalizeAiActionResult('gap_check', result)
  if (!normalized.suggestedPlain && !normalized.suggestedHtml) {
    throw new Error('No gap check report returned.')
  }

  return { target, snapshot, result, normalized }
}
