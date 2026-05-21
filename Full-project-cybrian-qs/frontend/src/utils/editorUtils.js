/**
 * editorUtils.js
 * ==============
 * Utilities for inspecting TipTap editor state.
 * Kept separate from App.jsx so they can be unit-tested independently.
 */

/**
 * Recursively extract all plain text from a TipTap node tree.
 * Returns a flat string of every text leaf joined by spaces.
 *
 * @param {object} node - Any TipTap JSON node
 * @returns {string}
 */
export function extractTextFromNode(node) {
  if (!node || typeof node !== 'object') return ''
  if (node.type === 'text') return (node.text || '').trim()

  const children = node.content || []
  return children
    .map(extractTextFromNode)
    .filter(Boolean)
    .join(' ')
    .trim()
}

/**
 * Determine whether a TipTap JSON document is effectively empty.
 *
 * A document is considered empty when ALL of the following are true:
 *   - It has no nodes, OR
 *   - Every node is a blank paragraph (type=paragraph with no content children), OR
 *   - Every text leaf in the entire tree is whitespace-only
 *
 * A document is NOT empty if it contains:
 *   - A heading with any text
 *   - A paragraph with any non-whitespace text
 *   - A list, table, image, or any other non-empty block
 *
 * @param {object|null} tiptapJson - The result of editor.getJSON()
 * @returns {boolean} true if the document has no meaningful content
 */
export function isEditorContentEmpty(tiptapJson) {
  if (!tiptapJson || typeof tiptapJson !== 'object') return true

  const nodes = tiptapJson.content || []
  if (nodes.length === 0) return true

  // Extract all text from the entire document tree
  const allText = extractTextFromNode(tiptapJson)
  if (allText.length > 0) return false

  // Check for non-text meaningful nodes (images, horizontal rules, etc.)
  const hasMeaningfulNode = nodes.some((node) => {
    const t = node.type || ''
    // These node types are meaningful even without text
    return ['image', 'horizontalRule', 'codeBlock', 'table'].includes(t)
  })

  return !hasMeaningfulNode
}

/**
 * Count the approximate number of words in a TipTap document.
 *
 * @param {object|null} tiptapJson
 * @returns {number}
 */
export function countWordsInDocument(tiptapJson) {
  const text = extractTextFromNode(tiptapJson || {})
  if (!text) return 0
  return text.split(/\s+/).filter(Boolean).length
}

const _text = (s) => ({ type: 'text', text: String(s ?? '') })
const _strongText = (s) => ({ type: 'text', text: String(s ?? ''), marks: [{ type: 'bold' }] })

const splitParagraphLines = (text = '') =>
  String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

const isBulletLine = (line = '') => /^[-*•]\s+/.test(line)
const isNumberedLine = (line = '') => /^\(?[A-Za-z0-9]+\)?[.)]\s+/.test(line)
const isKeyValueLine = (line = '') => /^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s/&()\-]{1,40}:\s+\S+/.test(line)

const paragraphNode = (text = '') => ({ type: 'paragraph', content: [_text(text)] })
const headingNode = (text = '', level = 2) => ({ type: 'heading', attrs: { level }, content: [_text(text)] })
const listItemNode = (text = '') => ({ type: 'listItem', content: [paragraphNode(text)] })

/** Paragraph preserving intentional line breaks inside a block. */
const paragraphFromMultiline = (text = '') => {
  const raw = String(text || '')
  const lines = raw.split(/\r?\n/).map((line) => line.trimEnd())
  if (lines.length <= 1) {
    return paragraphNode(raw.trim())
  }
  const content = []
  lines.forEach((line, idx) => {
    if (line) content.push(_text(line))
    if (idx < lines.length - 1) {
      content.push({ type: 'hardBreak' })
    }
  })
  return content.length ? { type: 'paragraph', content } : paragraphNode('')
}

const HEADER_LABEL = /^(?:no\.?|#|item|step|phase|date|version|revision|status|title|name|role|description|requirement|reference|id|sop|action|owner|department|remarks?|comments?)\b/i
const CELL_BREAK = /\s{2,}|\t/

const cleanTableCell = (value) => String(value ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim()

export const normalizeTableRows = (rows = []) => {
  const cleaned = []
  let maxCols = 0
  for (const row of rows) {
    if (!Array.isArray(row)) continue
    const cells = row.map(cleanTableCell)
    if (!cells.some(Boolean)) continue
    cleaned.push(cells)
    maxCols = Math.max(maxCols, cells.length)
  }
  if (!maxCols || !cleaned.length) return []
  return cleaned.map((row) => {
    const padded = [...row, ...Array(Math.max(0, maxCols - row.length)).fill('')]
    return padded.slice(0, maxCols)
  })
}

const rowStats = (row = []) => {
  const nonempty = row.filter(Boolean)
  if (!nonempty.length) return { avg: 0, labels: 0 }
  const avg = nonempty.reduce((sum, c) => sum + c.length, 0) / nonempty.length
  const labels = nonempty.filter((c) => HEADER_LABEL.test(c)).length
  return { avg, labels }
}

export const inferHeaderRowCount = (rows = [], explicit) => {
  if (!rows.length) return 0
  if (explicit != null && !Number.isNaN(Number(explicit))) {
    const n = Number(explicit)
    return Math.max(0, Math.min(rows.length === 1 ? 1 : rows.length - 1, n))
  }
  if (rows.length === 1) return 0
  const r0 = rowStats(rows[0])
  const r1 = rowStats(rows[1])
  if (rows.length >= 3 && r0.avg < 42 && r1.avg < 42) {
    const r2 = rowStats(rows[2])
    if (r2.avg > Math.max(r0.avg, r1.avg) * 1.2) {
      if (r0.labels >= Math.max(1, Math.floor(rows[0].length / 3))
        && r1.labels >= Math.max(1, Math.floor(rows[1].length / 3))) {
        return 2
      }
      return 1
    }
  }
  if (r0.labels >= Math.max(2, Math.floor(rows[0].length / 2))) return 1
  if (r0.avg < 40 && r1.avg > r0.avg * 1.3) return 1
  if (r0.avg > 80 && r1.avg > 80) return 0
  return 0
}

const paragraphTextLooksLikeTable = (text = '') => {
  const lines = String(text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  if (lines.length < 2) return false
  const counts = []
  for (const line of lines.slice(0, 40)) {
    const parts = line.split(CELL_BREAK).map((p) => p.trim()).filter(Boolean)
    if (parts.length >= 2) counts.push(parts.length)
  }
  if (counts.length < 2) return false
  const dominant = counts.reduce((best, n) => {
    const freq = counts.filter((c) => c === n).length
    const bestFreq = counts.filter((c) => c === best).length
    return freq > bestFreq ? n : best
  }, counts[0])
  return counts.filter((c) => c === dominant).length >= Math.max(2, Math.floor(counts.length * 0.6))
    && dominant >= 2
}

const tableNodeFromBlock = (block = {}) => {
  const normalized = normalizeTableRows(block.rows || [])
  if (!normalized.length) return null
  let headerRows = block.header_rows
  if (headerRows == null) headerRows = inferHeaderRowCount(normalized)
  else headerRows = inferHeaderRowCount(normalized, headerRows)

  const tableRows = normalized.map((row, rowIndex) => {
    const cells = row.map((cell) => ({
      type: rowIndex < headerRows ? 'tableHeader' : 'tableCell',
      content: [paragraphFromMultiline(String(cell ?? ''))],
    }))
    return cells.length ? { type: 'tableRow', content: cells } : null
  }).filter(Boolean)

  return tableRows.length ? { type: 'table', content: tableRows } : null
}

const tableBlockFromParagraphText = (text = '') => {
  const lines = String(text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  if (lines.length < 2) return null
  const rows = lines
    .map((line) => line.split(CELL_BREAK).map((p) => p.trim()).filter(Boolean))
    .filter((parts) => parts.length >= 2)
  if (rows.length < 2) return null
  return { type: 'table', rows: normalizeTableRows(rows) }
}

/**
 * Map backend PDF/OCR blocks to a TipTap-compatible doc JSON (StarterKit + table).
 * @param {Array<{type: string, text?: string, items?: string[], rows?: string[][]}>} blocks
 * @param {string} fallbackText
 * @returns {{ type: 'doc', content: object[] }}
 */
export function mapBlocksToTipTapDoc(blocks, fallbackText = '') {
  const content = []
  if (!Array.isArray(blocks) || blocks.length === 0) {
    const t = String(fallbackText || '').trim()
    if (t) content.push({ type: 'paragraph', content: [_text(t)] })
    return { type: 'doc', content }
  }

  for (const block of blocks) {
    const typ = String(block.type || '').toLowerCase()
    if ((typ === 'section_heading' || typ === 'heading') && block.text) {
      const level = Math.min(3, Math.max(1, Number(block.level) || 2))
      content.push(headingNode(block.text, level))
    } else if (typ === 'paragraph' && block.text) {
      const raw = String(block.text || '')
      if (paragraphTextLooksLikeTable(raw)) {
        const pseudo = tableBlockFromParagraphText(raw)
        const node = pseudo ? tableNodeFromBlock(pseudo) : null
        if (node) {
          content.push(node)
          continue
        }
      }
      const lines = raw.includes('\n')
        ? raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
        : splitParagraphLines(raw)
      if (!lines.length) continue
      if (lines.every(isBulletLine)) {
        content.push({
          type: 'bulletList',
          content: lines.map((line) => listItemNode(line.replace(/^[-*•]\s+/, '').trim())),
        })
        continue
      }
      if (lines.every(isNumberedLine)) {
        content.push({
          type: 'orderedList',
          content: lines.map((line) => listItemNode(line.replace(/^\(?[A-Za-z0-9]+\)?[.)]\s+/, '').trim())),
        })
        continue
      }
      if (lines.length > 1 && !raw.includes('\n')) {
        for (const line of lines) {
          if (isKeyValueLine(line)) {
            const [key, ...rest] = line.split(':')
            const value = rest.join(':').trim()
            content.push({
              type: 'paragraph',
              content: [_strongText(`${key.trim()}: `), _text(value)],
            })
          } else {
            content.push(paragraphNode(line))
          }
        }
        continue
      }
      content.push(paragraphFromMultiline(raw))
    } else if ((typ === 'two_column_row' || typ === 'key_value') && (block.left || block.right)) {
      const left = String(block.left || '').trim()
      const right = String(block.right || '').trim()
      if (left && right) {
        content.push({
          type: 'paragraph',
          content: [
            _strongText(`${left}: `),
            _text(right),
          ],
        })
      } else {
        content.push(paragraphNode(left || right))
      }
    } else if (
      (typ === 'bullet_list' || typ === 'numbered_list' || typ === 'ordered_list' || typ === 'list')
      && Array.isArray(block.items)
    ) {
      const listType = (typ === 'numbered_list' || typ === 'ordered_list') ? 'orderedList' : 'bulletList'
      const items = block.items
        .filter((it) => String(it ?? '').trim())
        .map((it) => listItemNode(it))
      if (items.length) content.push({ type: listType, content: items })
    } else if (typ === 'table' && Array.isArray(block.rows) && block.rows.length) {
      const node = tableNodeFromBlock(block)
      if (node) content.push(node)
    }
  }

  if (!content.length) {
    const t = String(fallbackText || '').trim()
    if (t) content.push({ type: 'paragraph', content: [_text(t)] })
  }

  return { type: 'doc', content }
}

const ALLOWED_BLOCK_TYPES = new Set([
  'paragraph',
  'heading',
  'bulletList',
  'orderedList',
  'listItem',
  'table',
  'tableRow',
  'tableCell',
  'tableHeader',
  'codeBlock',
  'blockquote',
  'horizontalRule',
])

const sanitizeInlineContent = (content) => {
  if (!Array.isArray(content)) return []
  return content
    .map((node) => {
      if (!node || typeof node !== 'object') return null
      if (node.type === 'text') {
        const text = String(node.text ?? '')
        if (!text) return null
        const out = { type: 'text', text }
        if (Array.isArray(node.marks) && node.marks.length) {
          out.marks = node.marks.filter((m) => m && typeof m === 'object' && m.type)
        }
        return out
      }
      if (node.type === 'hardBreak') return { type: 'hardBreak' }
      return null
    })
    .filter(Boolean)
}

const sanitizeTextblock = (node, defaultType = 'paragraph') => {
  const type = node?.type === 'heading' ? 'heading' : defaultType
  const content = sanitizeInlineContent(node?.content)
  if (!content.length) return null
  if (type === 'heading') {
    const level = Math.min(6, Math.max(1, Number(node?.attrs?.level) || 2))
    return { type: 'heading', attrs: { level }, content }
  }
  return { type: 'paragraph', content }
}

const sanitizeListItem = (node) => {
  const children = Array.isArray(node?.content) ? node.content : []
  const paragraph = children.find((c) => c?.type === 'paragraph') || children[0]
  const sanitized = sanitizeTextblock(paragraph || { type: 'paragraph', content: node?.content }, 'paragraph')
  if (!sanitized) return null
  return { type: 'listItem', content: [sanitized] }
}

const sanitizeTable = (node) => {
  const rows = (node?.content || [])
    .filter((r) => r?.type === 'tableRow')
    .map((row) => {
      const cells = (row.content || [])
        .map((cell) => {
          const cellType = cell?.type === 'tableHeader' ? 'tableHeader' : 'tableCell'
          const block = sanitizeTextblock(
            (cell?.content || []).find((c) => c?.type === 'paragraph') || { type: 'paragraph', content: [] },
            'paragraph',
          )
          if (!block) return null
          return { type: cellType, content: [block] }
        })
        .filter(Boolean)
      return cells.length ? { type: 'tableRow', content: cells } : null
    })
    .filter(Boolean)

  if (!rows.length) return null
  const width = Math.max(...rows.map((r) => r.content.length))
  let headerRows = 0
  for (const row of rows) {
    const cells = row.content || []
    if (cells.length && cells.every((c) => c?.type === 'tableHeader')) {
      headerRows += 1
    } else {
      break
    }
  }
  if (!headerRows) {
    headerRows = inferHeaderRowCount(
      rows.map((r) => (r.content || []).map((cell) => extractTextFromNode(cell))),
    )
  }
  const normalizedRows = rows.map((row, rowIndex) => {
    const cells = [...row.content]
    while (cells.length < width) {
      const cellType = rowIndex < headerRows ? 'tableHeader' : 'tableCell'
      cells.push({
        type: cellType,
        content: [{ type: 'paragraph', content: [{ type: 'text', text: '' }] }],
      })
    }
    return { type: 'tableRow', content: cells }
  })
  return { type: 'table', content: normalizedRows }
}

const sanitizeBlockNode = (node) => {
  if (!node || typeof node !== 'object' || !node.type) return null
  const type = String(node.type)
  if (!ALLOWED_BLOCK_TYPES.has(type) && type !== 'doc') return null

  if (type === 'paragraph' || type === 'heading') {
    return sanitizeTextblock(node, type)
  }
  if (type === 'bulletList' || type === 'orderedList') {
    const items = (node.content || []).map(sanitizeListItem).filter(Boolean)
    return items.length ? { type, content: items } : null
  }
  if (type === 'table') return sanitizeTable(node)
  if (type === 'listItem') return sanitizeListItem(node)
  return null
}

/**
 * Normalize TipTap JSON so ProseMirror can parse it (tables, lists, headings).
 * @param {object|null} docJson
 * @returns {{ type: 'doc', content: object[] }}
 */
export function sanitizeTipTapDoc(docJson) {
  if (!docJson || typeof docJson !== 'object') {
    return { type: 'doc', content: [] }
  }
  const nodes = Array.isArray(docJson.content) ? docJson.content : []
  const content = nodes.map(sanitizeBlockNode).filter(Boolean)
  return { type: 'doc', content }
}

/**
 * Apply extracted/imported content to a TipTap editor (JSON first, HTML fallback).
 * @param {import('@tiptap/core').Editor} editor
 * @param {{ docJson?: object, html?: string }} payload
 * @returns {boolean} whether meaningful content was applied
 */
export function applyTipTapContentToEditor(editor, { docJson, html } = {}) {
  if (!editor || editor.isDestroyed) return false

  const sanitized = sanitizeTipTapDoc(docJson)
  if (!isEditorContentEmpty(sanitized)) {
    try {
      const applied = editor.commands.setContent(sanitized, false)
      if (applied && !isEditorContentEmpty(editor.getJSON())) {
        return true
      }
    } catch (err) {
      console.warn('[editor] Failed to apply TipTap JSON content:', err)
    }
  }

  const htmlPayload = typeof html === 'string' ? html.trim() : ''
  if (htmlPayload) {
    try {
      const applied = editor.commands.setContent(htmlPayload, false)
      if (applied && editor.getText().trim()) {
        return true
      }
    } catch (err) {
      console.warn('[editor] Failed to apply HTML fallback content:', err)
    }
  }

  return false
}
