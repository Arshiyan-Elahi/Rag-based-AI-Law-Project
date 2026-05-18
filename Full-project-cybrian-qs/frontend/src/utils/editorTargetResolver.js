/**
 * Find rewrite/improve/gap targets in the TipTap document (accurate from/to).
 */

import {
  extractSopRefs,
  getTraceabilitySectionKind,
  isGenericLabelToken,
  wantsFullSopIntent,
} from './sopActionIntent'

const FULL_SOP_PATTERNS = [
  /\brewrite\s+(?:the\s+)?full\s+sop\b/i,
  /\brewrite\s+this\s+full\s+sop\b/i,
  /\bimprove\s+this\s+full\s+sop\b/i,
  /\bfull\s+sop\s+rewrite\b/i,
  /\b(?:entire|whole|complete)\s+sop\b/i,
  /\bgesamte\s+sop\b/i,
  /\bkomplette\s+sop\b/i,
]

const REWRITE_IMPROVE_VERB = /\b(?:rewrite|re-?write|improve|enhance|rephrase|umschreiben|überarbeiten|verbessern?)\b/i

const GAP_CHECK_VERB =
  /\b(?:gap\s*check|gap\s*analysis|what\s+(?:is|are)\s+the\s+gaps?|gaps?\s+in|compliance\s+gap|lücken[\s-]?(?:analyse|prüfung|check)|welche\s+lücken)\b/i

const FULL_SOP_GAP_PATTERNS = [
  /\bgap\s*check\s+(?:this\s+)?sop\b/i,
  /\bwhat\s+(?:is|are)\s+the\s+gaps?\s+in\s+(?:this\s+)?sop\b/i,
  /\bgaps?\s+in\s+(?:this\s+)?sop\b/i,
]

const DOC_ID_PATTERN = /\b([A-Z]{2,}(?:-[A-Z0-9]+)+)\b/g

const RECORD_ENTRY_RE = /^\s*(?:DEV|CAPA|AUD|DEC)-[A-Z0-9]+-\d+/i

const GAP_LABEL_PATTERNS = [
  /what\s+(?:is|are)\s+the\s+gaps?\s+(?:in|for)\s+(.+?)(?:\s+section)?\s*$/i,
  /gap\s+check\s+(?:on|for|in)?\s*(?:the\s+)?(.+?)(?:\s+section)?\s*$/i,
  /gaps?\s+in\s+(?:the\s+)?(.+?)(?:\s+section)?\s*$/i,
]

const SECTION_LABEL_PATTERNS = [
  /^rewrite\s+this\s+(.+?)\s+section(?:\s+only)?\s*$/i,
  /^improve\s+this\s+(.+?)\s+section(?:\s+only)?\s*$/i,
  /^rewrite\s+(?:the\s+)?(.+?)\s+section(?:\s+only)?\s*$/i,
  /^improve\s+(?:the\s+)?(.+?)\s+section(?:\s+only)?\s*$/i,
  /^(.+?)\s+rewrite\s+this\s*$/i,
  /^(.+?)\s+improve\s+this\s*$/i,
  /^(.+?)\s+rewrite\b/i,
  /^(.+?)\s+improve\b/i,
]

function normalizeForMatch(value) {
  return String(value || '')
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, ' ')
    .replace(/^[^\p{L}\p{N}]+/gu, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

function cleanLabel(raw) {
  return String(raw || '')
    .trim()
    .replace(/\s+section\s+only\s*$/i, '')
    .replace(/\s+only\s*$/i, '')
    .replace(/\s*section\s*$/i, '')
    .replace(/\bfull\s+sop\b/gi, '')
    .replace(/[.!?]+$/, '')
    .trim()
}

function buildLabelCandidates(label) {
  const cleaned = cleanLabel(label)
  if (!cleaned || wantsFullSopIntent(cleaned)) return []

  const candidates = new Set()
  const norm = normalizeForMatch(cleaned)
  if (norm && !isGenericLabelToken(norm)) candidates.add(norm)

  const noParen = norm.replace(/\s*\([^)]*\)\s*/g, ' ').replace(/\s+/g, ' ').trim()
  if (noParen && !isGenericLabelToken(noParen)) candidates.add(noParen)

  const kind = getTraceabilitySectionKind(cleaned)
  if (kind) candidates.add(kind)

  for (const ref of extractSopRefs(cleaned)) {
    candidates.add(ref)
  }

  return [...candidates].filter((c) => c && c.length >= 3 && !isGenericLabelToken(c))
}

function extractLabelsFromPrompt(promptText) {
  if (wantsFullSopIntent(promptText)) return []

  const text = String(promptText || '').trim()
  const labels = []

  for (const pattern of GAP_LABEL_PATTERNS) {
    const match = text.match(pattern)
    if (match?.[1]) labels.push(cleanLabel(match[1]))
  }

  for (const pattern of SECTION_LABEL_PATTERNS) {
    const match = text.match(pattern)
    if (match?.[1]) labels.push(cleanLabel(match[1]))
  }

  const afterVerb = text.match(
    /\b(?:rewrite|improve|rephrase|umschreiben|verbessern?)\s+(?:this\s+)?(?:the\s+)?(.+?)(?:\s+section(?:\s+only)?)?\s*$/i,
  )
  if (afterVerb?.[1]) {
    const captured = cleanLabel(afterVerb[1])
    if (captured && !wantsFullSopIntent(captured)) labels.push(captured)
  }

  let m = DOC_ID_PATTERN.exec(text)
  while (m) {
    labels.push(m[1])
    m = DOC_ID_PATTERN.exec(text)
  }
  DOC_ID_PATTERN.lastIndex = 0

  return [...new Set(labels.map((l) => l.trim()).filter((l) => l.length >= 2 && !wantsFullSopIntent(l)))]
}

function isRecordEntryLine(text) {
  return RECORD_ENTRY_RE.test(String(text || '').trim())
}

const REGISTER_FIELD_LINE_RE =
  /^(?:Linked|Finding|Datum|Beschreibung|Ursache|Aktion|Verantwortlich|Verknüpfungen|Entscheidung|Risiko|Begründung|Status|Fällig|Schweregrad|Ergebnis)\s*:/i

function isTraceabilitySectionTitle(text) {
  const t = String(text || '').trim()
  if (!t || isRecordEntryLine(t) || REGISTER_FIELD_LINE_RE.test(t)) return false
  const kind = getTraceabilitySectionKind(t)
  if (!kind) return false
  if (/\bzugehörig zu\s+SOP-/i.test(t)) return true
  if (/[\u{1F534}\u{1F7E0}\u{1F7E1}\u{1F7E2}\u{1F535}\u{1F7E3}]/u.test(t)) return true
  return /^(?:DEVIATIONS?|CAPAS?|AUDIT(?:\s+FINDINGS?)?|DECISIONS?|ABWEICHUNGEN?)\b/i.test(t)
}

function isMajorSectionHeader(text, nodeType) {
  const t = String(text || '').trim()
  if (!t || isRecordEntryLine(t)) return false
  if (nodeType === 'heading') return true
  if (isTraceabilitySectionTitle(t)) return true
  if (/^\d+\.\s+\S/u.test(t) && t.length < 160) return true
  return false
}

function blockMatchesLabel(blockText, label) {
  const labelKind = getTraceabilitySectionKind(label)
  const blockKind = getTraceabilitySectionKind(blockText)

  if (labelKind && blockKind && labelKind !== blockKind) {
    return false
  }

  const sopRefs = extractSopRefs(label)
  const blockNorm = normalizeForMatch(blockText)
  if (sopRefs.length) {
    return sopRefs.some((ref) => blockNorm.includes(ref))
  }

  if (labelKind) {
    if (blockKind === labelKind) return true
    if (blockKind) return false
  }

  const candidates = buildLabelCandidates(label)
  return candidates.some((c) => {
    if (c.startsWith('sop-')) return blockNorm.includes(c)
    if (c.length < 5) return false
    return blockNorm.includes(c)
  })
}

function scoreSectionStartBlock(block, label) {
  if (!blockMatchesLabel(block.text, label)) return 0

  let score = 20
  const labelKind = getTraceabilitySectionKind(label)
  const blockKind = getTraceabilitySectionKind(block.text)

  if (labelKind && blockKind === labelKind) score += 80
  if (isMajorSectionHeader(block.text, block.node.type.name)) score += 25
  if (isRecordEntryLine(block.text)) score -= 40
  if (/\bzugehörig zu\s+SOP-/i.test(block.text)) score += 20

  const sopRefs = extractSopRefs(label)
  if (sopRefs.some((ref) => normalizeForMatch(block.text).includes(ref))) score += 60

  return score
}

function collectBlocks(doc) {
  const blocks = []
  doc.descendants((node, pos) => {
    if (!node.isBlock) return true
    const text = node.textContent.trim()
    if (!text) return true
    blocks.push({
      node,
      pos,
      start: pos + 1,
      end: pos + node.nodeSize - 1,
      text,
      isSectionHeader: isMajorSectionHeader(text, node.type.name),
      isRecordEntry: isRecordEntryLine(text),
    })
    return true
  })
  return blocks
}

function findSectionByLabelInDoc(doc, label) {
  const blocks = collectBlocks(doc)
  if (!blocks.length) return null

  let startIdx = -1
  let bestScore = 0
  for (let i = 0; i < blocks.length; i += 1) {
    const score = scoreSectionStartBlock(blocks[i], label)
    if (score > bestScore) {
      bestScore = score
      startIdx = i
    }
  }
  if (startIdx < 0 || bestScore < 35) return null

  let endIdx = startIdx
  for (let j = startIdx + 1; j < blocks.length; j += 1) {
    const block = blocks[j]
    if (block.isSectionHeader && !blockMatchesLabel(block.text, label)) {
      break
    }
    endIdx = j
  }

  const from = blocks[startIdx].start
  const to = blocks[endIdx].end
  const text = doc.textBetween(from, to, '\n').trim()
  if (!text || text.length < 5) return null

  return {
    from,
    to,
    text,
    isFullDoc: false,
    sectionName: blocks[startIdx].text.slice(0, 160),
    sectionType: getTraceabilitySectionKind(blocks[startIdx].text) ? 'Section' : 'Heading',
  }
}

function resolveFullDocument(doc) {
  const size = doc.content.size
  return {
    from: 0,
    to: size,
    text: doc.textBetween(0, size, '\n').trim(),
    isFullDoc: true,
    sectionName: 'Full SOP',
    sectionType: 'Full Document',
  }
}

function inferSectionName(editor, from) {
  try {
    const $pos = editor.state.doc.resolve(from)
    for (let d = $pos.depth; d >= 0; d -= 1) {
      const node = $pos.node(d)
      if (node.type.name === 'heading') return node.textContent
    }
  } catch {
    // ignore
  }
  return 'Selected text'
}

/**
 * Resolve target range inside the live editor document.
 */
export function resolveTargetInEditor(editor, { prompt = '', selection } = {}) {
  if (!editor || editor.isDestroyed) return null

  const doc = editor.state.doc
  const promptText = String(prompt || '').trim()

  if (wantsFullSopIntent(promptText) && REWRITE_IMPROVE_VERB.test(promptText)) {
    return resolveFullDocument(doc)
  }

  for (const pattern of FULL_SOP_PATTERNS) {
    if (pattern.test(promptText)) {
      return resolveFullDocument(doc)
    }
  }

  const isGapRequest = GAP_CHECK_VERB.test(promptText)
  const isRewriteImprove = REWRITE_IMPROVE_VERB.test(promptText)

  if (isGapRequest) {
    if (wantsFullSopIntent(promptText)) return resolveFullDocument(doc)
    for (const pattern of FULL_SOP_GAP_PATTERNS) {
      if (pattern.test(promptText)) return resolveFullDocument(doc)
    }
    const labels = extractLabelsFromPrompt(promptText)
    if (labels.length === 0 && /\b(?:this\s+)?sop\b/i.test(promptText) && !/\bsection\b/i.test(promptText)) {
      return resolveFullDocument(doc)
    }
    for (const label of labels) {
      const section = findSectionByLabelInDoc(doc, label)
      if (section) return section
    }
  }

  if (isRewriteImprove) {
    const labels = extractLabelsFromPrompt(promptText)
    for (const label of labels) {
      const section = findSectionByLabelInDoc(doc, label)
      if (section) return section
    }
  }

  if (selection && !selection.empty) {
    const from = selection.from
    const to = selection.to
    const text = doc.textBetween(from, to, '\n').trim()
    if (text) {
      return {
        from,
        to,
        text,
        isFullDoc: false,
        sectionName: inferSectionName(editor, from),
        sectionType: 'Paragraph',
      }
    }
  }

  if (isGapRequest || isRewriteImprove) {
    const labels = extractLabelsFromPrompt(promptText)
    const hint = labels[0] || 'that section'
    throw new Error(
      `Could not find "${hint}" in the open SOP. Use the exact section heading (e.g. "CAPAs (zugehörig zu SOP-IT-003)") or select the full section in the editor.`,
    )
  }

  return null
}
