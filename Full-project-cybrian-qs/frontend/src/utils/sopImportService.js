import { extractText, getDocument, getImportJobStatus, importSOPAsync } from '../api/editorApi'
import { mapBlocksToTipTapDoc } from './editorUtils'
import { formatOCRText } from './formatOCRText'
import { mapOCRBlocksToHTML } from './mapOCRBlocksToHTML'
import { DEFAULT_SOP_VERSION_METADATA } from './sopConstants'
import {
  UNSUPPORTED_FILE_TYPE_CODE,
  getUnsupportedFileTypeMessage,
} from './friendlyErrorMessage'

export const SOP_IMPORT_ACCEPT = '.pdf,.docx,.txt'

const SOP_IMPORT_ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt']

/** MIME prefixes/types that are never valid SOP imports (images, markup, spreadsheets, etc.). */
const SOP_IMPORT_BLOCKED_MIME_PREFIXES = ['image/', 'audio/', 'video/']
const SOP_IMPORT_BLOCKED_MIME_TYPES = new Set([
  'text/html',
  'text/markdown',
  'image/svg+xml',
  'application/json',
  'application/xml',
  'text/xml',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/msword',
])

/** @deprecated Use {@link getUnsupportedFileTypeMessage} — kept for legacy string checks */
export const SOP_IMPORT_UNSUPPORTED_MESSAGE = UNSUPPORTED_FILE_TYPE_CODE

/** @typedef {'uploading'|'extracting'|'ocr_processing'|'creating_sop'|'rendering_ready'|'semantic_processing'|'indexing'|'success'|'error'} SOPImportModalPhase */

const GENERATED_IMPORT_SOP_NUMBER = /^IMPORT-[A-Z0-9-]{6,}$/i

export function isGeneratedImportSopNumber(value = '') {
  const token = String(value || '').trim().toUpperCase()
  return GENERATED_IMPORT_SOP_NUMBER.test(token)
}

/** Document ID safe for tabs/headers (hides internal IMPORT-* placeholders). */
export function resolveDisplayDocumentId(metadata = {}) {
  const id = String(metadata?.documentId || '').trim()
  return isGeneratedImportSopNumber(id) ? '' : id
}

export const SOP_IMPORT_STATUS_MESSAGES = {
  uploading: 'SOP is uploading...',
  queued: 'Queued for document extraction…',
  processing_marker: 'Extracting document structure…',
  converting_blocks: 'Extracting document structure in reading order…',
  saving_editor_content: 'Saving structured content to the editor…',
  processing: 'Extracting document structure…',
  extracting: 'Extracting document structure…',
  ocr_processing: 'Extracting scanned document (OCR)…',
  creating_sop: 'Saving structured content to the editor…',
  rendering_ready: 'Opening document in the editor…',
  semantic_processing: 'Indexing and linking content in the background…',
  indexing: 'Indexing and linking content in the background…',
  completed: 'SOP uploaded successfully.',
  failed: 'Import failed.',
}

const CONTENT_READY_STATUSES = new Set([
  'rendering_ready',
  'completed',
])

export function importStatusToModalPhase(status) {
  const s = String(status || '').toLowerCase()
  if (s === 'completed') return 'success'
  if (s === 'failed') return 'error'
  if (
    s === 'uploading'
    || s === 'queued'
    || s === 'processing_marker'
    || s === 'converting_blocks'
    || s === 'processing'
    || s === 'extracting'
    || s === 'ocr_processing'
  ) {
    return 'extracting'
  }
  if (s === 'saving_editor_content' || s === 'creating_sop') {
    return 'creating_sop'
  }
  if (s === 'rendering_ready' || s === 'semantic_processing' || s === 'indexing') {
    return s === 'indexing' ? 'semantic_processing' : s
  }
  return 'extracting'
}

export function importStatusToModalMessage(status, fallback = '') {
  const s = String(status || '').toLowerCase()
  return fallback
    || SOP_IMPORT_STATUS_MESSAGES[s]
    || SOP_IMPORT_STATUS_MESSAGES.extracting
}

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms))

/**
 * Poll background import job until completed or failed.
 * @param {string} jobId
 * @param {{ onStatus?: (status: object) => void, intervalMs?: number, timeoutMs?: number }} [options]
 */
export async function pollImportJobUntilDone(jobId, options = {}) {
  const intervalMs = Number(options.intervalMs) > 0 ? Number(options.intervalMs) : 1500
  const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 600000
  const started = Date.now()
  let contentReadyNotified = false

  while (Date.now() - started < timeoutMs) {
    const status = await getImportJobStatus(jobId, {
      versionId: options.versionId,
      sopId: options.sopId,
    })
    options.onStatus?.(status)
    const phase = String(status?.status || '').toLowerCase()
    const contentReady = Boolean(status?.doc_json_ready) || CONTENT_READY_STATUSES.has(phase)
    if (contentReady && !contentReadyNotified) {
      contentReadyNotified = true
      options.onContentReady?.(status)
    }
    // Editor content is ready after extraction/save; do not wait for semantic/RAG jobs.
    if (
      phase === 'completed'
      || phase === 'rendering_ready'
      || (contentReady && contentReadyNotified)
    ) {
      return status
    }
    if (phase === 'failed') {
      const extractionErr = status?.extraction?.extraction_error
      throw new Error(
        extractionErr
        || status?.error
        || status?.message
        || SOP_IMPORT_STATUS_MESSAGES.failed,
      )
    }
    await sleep(intervalMs)
  }

  throw new Error(
    'Import is still running but status polling timed out. Open the SOP from the list when processing finishes.',
  )
}

/**
 * Upload SOP via async pipeline (fast shell + background extraction/OCR/indexing).
 * @param {File} file
 * @param {{ onStatus?: (status: object) => void }} [options]
 */
export async function prepareNewSOPImportAsync(file, options = {}) {
  assertSOPImportFileAllowed(file)
  const started = await importSOPAsync(file)
  const jobId = started?.job_id
  const shellDoc = started?.document
  const versionId = started?.import_status?.version_id || shellDoc?.version_id
  const sopId = started?.import_status?.sop_id || shellDoc?.sop_id
  if (!jobId || !shellDoc?.id) {
    throw new Error('Background import did not return a job id.')
  }

  options.onStatus?.(started?.import_status || { status: 'uploading' })

  let finalStatus = started?.import_status
  if (String(finalStatus?.status || '').toLowerCase() !== 'completed') {
    finalStatus = await pollImportJobUntilDone(jobId, {
      ...options,
      versionId,
      sopId,
    })
  }

  const doc = await getDocument(String(shellDoc.id))
  const metadataJson = doc?.metadata_json && typeof doc.metadata_json === 'object'
    ? doc.metadata_json
    : shellDoc.metadata_json
  const fallbackTitle = file.name.replace(/\.[^/.]+$/, '') || 'Imported SOP'
  const sm = metadataJson?.sopMetadata || {}
  const resolvedTitle = normalizeSOPTitleForDisplay(sm.title || '', sm.documentId || doc.sop_number || '')
    || doc.title
    || fallbackTitle

  const metadata = normalizeSOPImportMetadata(
    metadataJson?.sopMetadata
      ? { ...metadataJson.sopMetadata, sopStatus: metadataJson.sopStatus }
      : {},
  )

  return {
    jobId,
    document: doc,
    docJson: doc?.doc_json || { type: 'doc', content: [] },
    resolvedTitle,
    metadataJson: prepareSOPMetadataJson(metadata, {
      author: 'System (Import)',
      reviewer: '',
    }),
    metadata,
    tabLabel: buildSOPDisplayLabel(metadata) || resolvedTitle,
    importStatus: finalStatus,
  }
}

export function getSOPImportFileExtension(file) {
  const name = String(file?.name || '').trim().toLowerCase()
  if (!name || !name.includes('.')) return ''
  return name.slice(name.lastIndexOf('.'))
}

export function isSupportedSOPImportFile(file) {
  if (!file) return false

  const ext = getSOPImportFileExtension(file)
  if (!SOP_IMPORT_ALLOWED_EXTENSIONS.includes(ext)) {
    return false
  }

  const mime = String(file.type || '').trim().toLowerCase()
  if (!mime) {
    return true
  }

  if (SOP_IMPORT_BLOCKED_MIME_PREFIXES.some((prefix) => mime.startsWith(prefix))) {
    return false
  }
  if (SOP_IMPORT_BLOCKED_MIME_TYPES.has(mime)) {
    return false
  }

  return true
}

/** @returns {string|null} Localized error when any file is not PDF, DOCX, or TXT */
export function validateSOPImportFileTypes(files, language) {
  const list = Array.isArray(files) ? files : []
  const unsupported = list.filter((file) => !isSupportedSOPImportFile(file))
  if (!unsupported.length) return null
  return getUnsupportedFileTypeMessage(language)
}

/** Throws when the file is not PDF, DOCX, or TXT (use {@link resolveImportUiError} in UI). */
export function assertSOPImportFileAllowed(file) {
  if (!isSupportedSOPImportFile(file)) {
    throw new Error(UNSUPPORTED_FILE_TYPE_CODE)
  }
}

export async function extractSOPImport(file) {
  assertSOPImportFileAllowed(file)
  const response = await extractText(file)

  // New sequential elements format (preferred) — produced by the backend
  // reading-order extractors (pdfplumber / fitz / docx sequential walker).
  // Falls back to legacy typed blocks when the server predates this change.
  const elements = Array.isArray(response?.elements) && response.elements.length
    ? response.elements
    : null
  const blocks = Array.isArray(response?.blocks) ? response.blocks : []

  // contentSource drives both the TipTap doc and the HTML preview.
  // mapBlocksToTipTapDoc / mapOCRBlocksToHTML both handle either format.
  const contentSource = elements ?? blocks

  const text = response?.text || ''
  const metadata = normalizeSOPImportMetadata(response?.sop_metadata_ui)

  return {
    response,
    // contentSource is what downstream callers (prepareEditorSOPImport etc.)
    // feed into mapBlocksToTipTapDoc and mapOCRBlocksToHTML.
    blocks: contentSource,
    // Raw originals preserved for callers that need them separately.
    elements: elements ?? [],
    rawBlocks: blocks,
    text,
    metadata,
    hasContent: Boolean(text.trim() || contentSource.length),
  }
}

export function validateSOPImportContent(importResult, message = 'No text content found in PDF.') {
  if (!importResult?.hasContent) {
    throw new Error(message)
  }
}

export function normalizeSOPImportMetadata(rawMetadata) {
  if (!rawMetadata || typeof rawMetadata !== 'object') return {}
  const source =
    rawMetadata?.sopMetadata && typeof rawMetadata.sopMetadata === 'object'
      ? { ...rawMetadata, ...rawMetadata.sopMetadata }
      : { ...rawMetadata }
  const normalized = { ...source }
  const aliasMap = {
    sop_id: 'documentId',
    sopId: 'documentId',
    document_id: 'documentId',
    sop_number: 'documentId',
    external_id: 'documentId',
    doc_type: 'docType',
    document_type: 'docType',
    type: 'docType',
    documentType: 'docType',
    version: 'sopVersion',
    revision: 'sopVersion',
    document_revision: 'sopVersion',
    sop_version: 'sopVersion',
    sop_status: 'sopStatus',
    status: 'sopStatus',
    effective_date: 'effectiveDate',
    date: 'effectiveDate',
    review_date: 'reviewDate',
    risk_level: 'riskLevel',
    regulatory_references: 'regulatoryReferences',
    compliance_elements: 'complianceElements',
    terminology_keywords: 'terminologyKeywords',
    keywords: 'terminologyKeywords',
  }

  Object.entries(aliasMap).forEach(([sourceKey, targetKey]) => {
    if (!normalized[targetKey] && source[sourceKey] != null) {
      normalized[targetKey] = source[sourceKey]
    }
  })

  if (import.meta?.env?.DEV) {
    console.debug('[SOP Status Debug] normalized import metadata', {
      rawStatus: source?.status || null,
      rawSopStatus: source?.sopStatus || null,
      normalizedSopStatus: normalized?.sopStatus || null,
      documentId: normalized?.documentId || null,
      title: normalized?.title || null,
    })
  }

  if (isGeneratedImportSopNumber(normalized.documentId)) {
    normalized.documentId = ''
  }

  return normalized
}

const escapeRegExp = (value = '') => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

export function normalizeSOPTitleForDisplay(title = '', documentId = '') {
  const rawTitle = String(title || '').trim()
  const docId = String(documentId || '').trim()
  if (!rawTitle) return ''
  if (!docId) return rawTitle

  const idPattern = escapeRegExp(docId)
  const prefixRegex = new RegExp(`^${idPattern}\\s*(?:[-–—:]\\s*)?`, 'i')
  const cleaned = rawTitle.replace(prefixRegex, '').trim()
  return cleaned || rawTitle
}

export function buildSOPDisplayLabel(metadata = {}, fallback = '') {
  const documentId = resolveDisplayDocumentId(metadata)
  const cleanTitle = normalizeSOPTitleForDisplay(metadata.title, documentId)
  return [
    documentId,
    cleanTitle,
    (metadata.sopVersion || '').trim(),
  ].filter(Boolean).join(' — ') || fallback
}

export function applySOPImportMetadata(previousMetadata, importedMetadata = {}) {
  const normalizedImported = normalizeSOPImportMetadata(importedMetadata)
  const managedFields = [
    'documentId',
    'title',
    'department',
    'docType',
    'category',
    'sopVersion',
    'effectiveDate',
    'reviewDate',
    'riskLevel',
    'regulatoryReferences',
    'roles',
    'workflow',
    'complianceElements',
    'risks',
    'gaps',
    'terminologyKeywords',
  ]
  const next = { ...previousMetadata }

  // Reset managed import fields so missing extractor values become empty inputs.
  managedFields.forEach((key) => {
    next[key] = key === 'regulatoryReferences' ? [] : ''
  })

  managedFields.forEach((key) => {
    const incoming = normalizedImported[key]
    if (incoming == null) return
    if (key === 'regulatoryReferences') {
      if (Array.isArray(incoming)) {
        next[key] = incoming
      } else if (typeof incoming === 'string' && incoming.trim()) {
        next[key] = incoming.split('\n').map((item) => item.trim()).filter(Boolean)
      } else {
        next[key] = []
      }
      return
    }
    next[key] = incoming
  })

  next.title = normalizeSOPTitleForDisplay(next.title, next.documentId)

  if (!next.docType) next.docType = 'SOP'
  return next
}

export function prepareSOPMetadataJson(importedMetadata = {}, overrides = {}) {
  const resolvedSopStatus =
    importedMetadata.sopStatus
    || importedMetadata.status
    || ''
  const statusToken = resolvedSopStatus || DEFAULT_SOP_VERSION_METADATA.sopStatus
  return {
    sopStatus: statusToken,
    sopMetadata: {
      ...DEFAULT_SOP_VERSION_METADATA.sopMetadata,
      ...importedMetadata,
      ...overrides,
      ...(resolvedSopStatus
        ? { sopStatus: resolvedSopStatus, status: resolvedSopStatus }
        : {}),
    },
    auditTrail: [],
    versionNote: '',
  }
}

export async function prepareEditorSOPImport(file) {
  const importResult = await extractSOPImport(file)
  validateSOPImportContent(importResult, 'No text content found in uploaded file.')
  const docJson = mapBlocksToTipTapDoc(importResult.blocks, importResult.text)
  const html = importResult.blocks.length
    ? mapOCRBlocksToHTML(importResult.blocks, 'sop')
    : formatOCRText(importResult.text)

  if (import.meta?.env?.DEV) {
    const isScanned = Boolean(importResult.response?.scanned_pdf)
    console.debug('[SOP Import] structured content', {
      scanned: isScanned,
      elements: importResult.elements?.length || 0,
      blocks: importResult.blocks?.length || 0,
      tiptapNodes: docJson?.content?.length || 0,
      tiptapNodeTypes: (docJson?.content || []).map((n) => n.type),
      docJson,
    })
  }

  if (!html || !String(html).trim()) {
    throw new Error('No structured content extracted from file.')
  }

  return {
    ...importResult,
    docJson,
    html,
    tabLabel: buildSOPDisplayLabel(importResult.metadata),
  }
}

export async function prepareNewSOPImport(file) {
  const importResult = await extractSOPImport(file)
  validateSOPImportContent(importResult)

  const fallbackTitle = file.name.replace(/\.[^/.]+$/, '') || 'Imported SOP'
  const resolvedTitle = normalizeSOPTitleForDisplay(
    importResult.metadata.title || '',
    importResult.metadata.documentId || '',
  ) || fallbackTitle
  const docJson = mapBlocksToTipTapDoc(importResult.blocks, importResult.text)

  return {
    ...importResult,
    docJson,
    resolvedTitle,
    metadataJson: prepareSOPMetadataJson(importResult.metadata, {
      author: 'System (Import)',
      reviewer: '',
    }),
    tabLabel: buildSOPDisplayLabel(importResult.metadata),
  }
}
