import { extractText } from '../api/editorApi'
import { mapBlocksToTipTapDoc } from './editorUtils'
import { formatOCRText } from './formatOCRText'
import { mapOCRBlocksToHTML } from './mapOCRBlocksToHTML'
import { DEFAULT_SOP_VERSION_METADATA } from './sopConstants'

export const SOP_IMPORT_ACCEPT = '.pdf,.docx,.txt,.md'

export async function extractSOPImport(file) {
  const response = await extractText(file)
  const blocks = Array.isArray(response?.blocks) ? response.blocks : []
  const text = response?.text || ''
  const metadata = normalizeSOPImportMetadata(response?.sop_metadata_ui)

  return {
    response,
    blocks,
    text,
    metadata,
    hasContent: Boolean(text.trim() || blocks.length),
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

  return normalized
}

export function buildSOPDisplayLabel(metadata = {}, fallback = '') {
  return [
    metadata.documentId,
    metadata.title,
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

  if (!next.docType) next.docType = 'SOP'
  return next
}

export function prepareSOPMetadataJson(importedMetadata = {}, overrides = {}) {
  return {
    sopStatus: DEFAULT_SOP_VERSION_METADATA.sopStatus,
    sopMetadata: {
      ...DEFAULT_SOP_VERSION_METADATA.sopMetadata,
      ...importedMetadata,
      ...overrides,
    },
    auditTrail: [],
    versionNote: '',
  }
}

export async function prepareEditorSOPImport(file) {
  const importResult = await extractSOPImport(file)
  const html = importResult.blocks.length
    ? mapOCRBlocksToHTML(importResult.blocks, 'sop')
    : formatOCRText(importResult.text)

  if (!html || !String(html).trim()) {
    throw new Error('No structured content extracted from file.')
  }

  return {
    ...importResult,
    html,
    tabLabel: buildSOPDisplayLabel(importResult.metadata),
  }
}

export async function prepareNewSOPImport(file) {
  const importResult = await extractSOPImport(file)
  validateSOPImportContent(importResult)

  const fallbackTitle = file.name.replace(/\.[^/.]+$/, '') || 'Imported SOP'
  const resolvedTitle = (importResult.metadata.title || '').trim() || fallbackTitle
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
