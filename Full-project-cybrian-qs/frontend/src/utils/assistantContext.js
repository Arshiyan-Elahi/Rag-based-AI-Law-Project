const KL_EDITOR_CONTEXT_KEY = 'kl_assistant_editor_state_v1'
const KL_WORKSPACE_CONTEXT_KEY = 'kl_assistant_workspace_state_v1'

function readLocalJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : fallback
  } catch {
    return fallback
  }
}

function sanitizeText(value, maxLen = 3000) {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.length > maxLen ? `${text.slice(0, maxLen)}...` : text
}

export function getKLAssistantContext(pathname = '/') {
  const editorState = readLocalJson(KL_EDITOR_CONTEXT_KEY, {})
  const workspaceState = readLocalJson(KL_WORKSPACE_CONTEXT_KEY, {})
  const activeDocumentId = localStorage.getItem('current_document_id') || ''

  return {
    route: pathname,
    current_document_id: activeDocumentId || editorState?.sop?.id || '',
    current_sop: {
      id: editorState?.sop?.id || activeDocumentId || '',
      sop_number: editorState?.sop?.sop_number || editorState?.sop?.documentId || '',
      title: editorState?.sop?.title || '',
      version: editorState?.sop?.version || '',
      status: editorState?.sop?.status || '',
      references: Array.isArray(editorState?.sop?.references) ? editorState.sop.references : [],
    },
    linked_context: {
      deviations: Array.isArray(editorState?.linked?.deviations) ? editorState.linked.deviations : [],
      capas: Array.isArray(editorState?.linked?.capas) ? editorState.linked.capas : [],
      audits: Array.isArray(editorState?.linked?.audits) ? editorState.linked.audits : [],
      decisions: Array.isArray(editorState?.linked?.decisions) ? editorState.linked.decisions : [],
      related_sops: Array.isArray(editorState?.linked?.related_sops) ? editorState.linked.related_sops : [],
    },
    opened_tabs: Array.isArray(workspaceState?.opened_tabs) ? workspaceState.opened_tabs : [],
    active_tab_id: workspaceState?.active_tab_id || '',
    active_tab_label: workspaceState?.active_tab_label || '',
    editor_excerpt: sanitizeText(editorState?.editor_text, 5000),
    context_updated_at: editorState?.updated_at || workspaceState?.updated_at || null,
  }
}

export function getAssistantContextStorageKeys() {
  return {
    editor: KL_EDITOR_CONTEXT_KEY,
    workspace: KL_WORKSPACE_CONTEXT_KEY,
  }
}
