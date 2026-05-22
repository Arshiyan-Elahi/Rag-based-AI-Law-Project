/**
 * TipTap scope helpers: block-level selection and stable t1..tn node ids (matches backend walk).
 */

function normalizeWs(text) {
  return String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

/** Walk doc JSON like backend extract_editable_text_nodes; return ids overlapping section text. */
export function collectEditableNodeIdsForScope(docJson, sectionText) {
  if (!docJson || docJson.type !== 'doc') return []
  const sectionNorm = normalizeWs(sectionText)
  if (!sectionNorm) return []

  const nodes = []
  let counter = 0

  const walk = (node, blockType = 'paragraph') => {
    if (!node || typeof node !== 'object') return
    if (node.type === 'text') {
      const raw = node.text
      if (raw == null || !String(raw).trim()) return
      counter += 1
      const nodeNorm = normalizeWs(raw)
      if (nodeNorm) {
        nodes.push({ id: `t${counter}`, norm: nodeNorm })
      }
      return
    }
    const children = node.content
    if (!Array.isArray(children)) return
    let childBlock = blockType
    if (
      [
        'heading',
        'paragraph',
        'table',
        'tableRow',
        'tableCell',
        'tableHeader',
        'bulletList',
        'orderedList',
        'listItem',
        'doc',
      ].includes(node.type)
    ) {
      childBlock = node.type
    }
    children.forEach((child) => walk(child, childBlock))
  }

  ;(docJson.content || []).forEach((block) => walk(block, String(block?.type || 'paragraph')))

  const strict = nodes.filter(({ norm }) => {
    if (norm.length < 4) return false
    return (
      sectionNorm.includes(norm)
      || norm.includes(sectionNorm)
      || (sectionNorm.length >= 12
        && norm.slice(0, Math.min(48, norm.length)) === sectionNorm.slice(0, Math.min(48, sectionNorm.length)))
    )
  })
  if (strict.length) {
    return strict.map((n) => n.id)
  }

  const tokens = sectionNorm.split(' ').filter((t) => t.length >= 5)
  if (tokens.length) {
    const loose = nodes.filter(({ norm }) => tokens.some((tok) => norm.includes(tok)))
    if (loose.length) return loose.map((n) => n.id)
  }

  return []
}

/**
 * Collect patch node ids for text nodes overlapping a ProseMirror [from, to) range.
 * Uses the live editor walk order (must match backend id assignment).
 */
export function collectEditableNodeIdsInRange(editor, from, to) {
  if (!editor || editor.isDestroyed) return []

  const ids = []
  let counter = 0

  editor.state.doc.descendants((node, pos) => {
    if (!node.isText) return true
    const raw = node.text
    if (raw == null || !String(raw).trim()) return true
    counter += 1
    const start = pos
    const end = pos + node.nodeSize
    if (end > from && start < to) {
      ids.push(`t${counter}`)
    }
    return true
  })

  return ids
}

/** Expand a range to the enclosing paragraph, list item, table cell, or heading block. */
export function expandSelectionToEditableBlock(editor, from, to) {
  if (!editor || editor.isDestroyed) {
    return { from, to, text: '' }
  }
  const doc = editor.state.doc
  const $from = doc.resolve(from)
  const $to = doc.resolve(to)
  const blockTypes = new Set([
    'paragraph',
    'heading',
    'tableCell',
    'tableHeader',
    'listItem',
    'codeBlock',
  ])

  let start = from
  let end = to
  for (let d = $from.depth; d > 0; d -= 1) {
    if (blockTypes.has($from.node(d).type.name)) {
      start = $from.start(d)
      break
    }
  }
  for (let d = $to.depth; d > 0; d -= 1) {
    if (blockTypes.has($to.node(d).type.name)) {
      end = $to.end(d)
      break
    }
  }

  const text = doc.textBetween(start, end, '\n').trim()
  return { from: start, to: end, text }
}

/** Caret or collapsed selection: current paragraph / table cell / list item. */
export function resolveCurrentBlockAtCursor(editor) {
  if (!editor || editor.isDestroyed) return null
  const { selection } = editor.state
  const pos = selection.empty ? selection.from : selection.from
  const expanded = expandSelectionToEditableBlock(editor, pos, selection.empty ? pos : selection.to)
  if (!expanded.text) return null

  let sectionName = 'Current block'
  let sectionType = 'Paragraph'
  try {
    const $pos = editor.state.doc.resolve(expanded.from)
    for (let d = $pos.depth; d >= 0; d -= 1) {
      const node = $pos.node(d)
      if (node.type.name === 'heading') {
        sectionName = node.textContent || sectionName
        sectionType = 'Heading'
        break
      }
      if (node.type.name === 'tableCell' || node.type.name === 'tableHeader') {
        sectionType = 'Table cell'
        break
      }
      if (node.type.name === 'listItem') {
        sectionType = 'List item'
      }
    }
  } catch {
    // best-effort
  }

  return {
    from: expanded.from,
    to: expanded.to,
    text: expanded.text,
    isFullDoc: false,
    sectionName,
    sectionType,
  }
}

export function buildPatchScopePayload(editor, { from, to, text, contentJson }) {
  const doc = contentJson || (editor && !editor.isDestroyed ? editor.getJSON() : null)

  if (editor && !editor.isDestroyed && Number.isFinite(from) && Number.isFinite(to)) {
    const rangeIds = collectEditableNodeIdsInRange(editor, from, to)
    if (rangeIds.length) {
      return { patch_node_ids: rangeIds }
    }
  }

  const patchNodeIds = doc ? collectEditableNodeIdsForScope(doc, text) : []
  return { patch_node_ids: patchNodeIds.length ? patchNodeIds : undefined }
}
