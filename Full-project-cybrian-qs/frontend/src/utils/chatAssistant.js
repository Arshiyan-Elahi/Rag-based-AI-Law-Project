import { queryAI } from '../api/editorApi'
import { getKLAssistantContext } from './assistantContext'

const ROUTE_CONFIG = {
  '/sops': {
    category: 'sops',
    contextLabel: 'Kontext: SOP-Ansicht',
    suggestions: [
      'Welche SOP ist besonders relevant?',
      'Gab es Audit-Bezug?',
      'Was war die letzte Abweichung?',
      'Zusammenfassung letzter Woche',
    ],
  },
  '/deviations': {
    category: 'deviations',
    contextLabel: 'Kontext: Abweichungen',
    suggestions: [
      'Welche Abweichung ist kritisch?',
      'Zeige offene Abweichungen mit Impact',
      'Gibt es verknuepfte SOPs?',
      'Welche CAPA ist ueberfaellig?',
    ],
  },
  '/capa': {
    category: 'capas',
    contextLabel: 'Kontext: CAPA',
    suggestions: [
      'Welche CAPA ist am dringendsten?',
      'Welche CAPA ist noch offen?',
      'Welche CAPA ist mit Audits verknuepft?',
      'Was ist die naechste Eskalation?',
    ],
  },
  '/audits': {
    category: 'audits',
    contextLabel: 'Kontext: Audit Findings',
    suggestions: [
      'Welche Findings sind offen?',
      'Welche Findings sind kritisch?',
      'Zeige Audit-zu-SOP Bezug',
      'Welche Findings brauchen CAPA?',
    ],
  },
  '/decisions': {
    category: 'decisions',
    contextLabel: 'Kontext: Entscheidungen',
    suggestions: [
      'Welche Entscheidung ist zuletzt getroffen worden?',
      'Welche Entscheidung ist noch offen?',
      'Welche SOP ist davon betroffen?',
      'Zeige begruendete Entscheidungen',
    ],
  },
  '/knowledge': {
    category: undefined,
    contextLabel: 'Kontext: Wissenssuche',
    suggestions: [
      'Zeige relevante SOPs zum Thema',
      'Welche Quellen stuetzen das?',
      'Welche Risiken sind erkennbar?',
      'Fasse den Kontext kurz zusammen',
    ],
  },
  '/editor': {
    category: 'sops',
    contextLabel: 'Kontext: SOP Editor',
    suggestions: [
      'Analysiere den aktuellen SOP-Kontext',
      'Welche Verbesserungen sind noetig?',
      'Pruefe auf Compliance-Luecken',
      'Fasse den SOP-Inhalt zusammen',
    ],
  },
}

const DEFAULT_CONFIG = {
  category: undefined,
  contextLabel: 'Kontext: Keine Analyse, Startscreen',
  suggestions: [
    'Welche SOP ist besonders relevant?',
    'Gab es Audit-Bezug?',
    'Was war die letzte Abweichung?',
    'Zusammenfassung letzter Woche',
  ],
}

export function nowTime() {
  return new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
}

export function toHtml(text) {
  if (!text) return '<p></p>'
  const raw = String(text || '').trim()
  const escaped = raw
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')

  const lines = escaped
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  const sectionHeading = (line) =>
    /^(summary|details|status|cross-refs|cross refs|sources|references)\s*:/i.test(line)
  const bulletLine = (line) => /^[-*•]\s+/.test(line) || /^\d+[\.\)]\s+/.test(line)

  const parts = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (sectionHeading(line)) {
      const [labelRaw, firstBodyRaw = ''] = line.split(/:\s*/, 2)
      const label = labelRaw.trim()
      const firstBody = firstBodyRaw.trim()
      const bullets = []
      const paras = []
      if (firstBody) paras.push(firstBody)

      let j = i + 1
      while (j < lines.length && !sectionHeading(lines[j])) {
        const row = lines[j]
        if (bulletLine(row)) bullets.push(row.replace(/^([-*•]|\d+[\.\)])\s+/, '').trim())
        else paras.push(row)
        j += 1
      }

      parts.push(`<h4>${label}</h4>`)
      paras.forEach((p) => parts.push(`<p>${p}</p>`))
      if (bullets.length) {
        parts.push(`<ul>${bullets.map((b) => `<li>${b}</li>`).join('')}</ul>`)
      }
      i = j
      continue
    }

    if (bulletLine(line)) {
      const bullets = []
      let j = i
      while (j < lines.length && bulletLine(lines[j])) {
        bullets.push(lines[j].replace(/^([-*•]|\d+[\.\)])\s+/, '').trim())
        j += 1
      }
      parts.push(`<ul>${bullets.map((b) => `<li>${b}</li>`).join('')}</ul>`)
      i = j
      continue
    }

    parts.push(`<p>${line}</p>`)
    i += 1
  }

  return parts.join('')
}

export function stripHtml(html) {
  return String(html || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .trim()
}

function matchRouteConfig(pathname = '/') {
  const matchedKey = Object.keys(ROUTE_CONFIG).find((route) => pathname.startsWith(route))
  return matchedKey ? ROUTE_CONFIG[matchedKey] : DEFAULT_CONFIG
}

function buildContextualQuestion(question, pathname) {
  const route = pathname || '/'
  if (route.startsWith('/editor')) {
    const activeDocumentId = localStorage.getItem('current_document_id')
    if (activeDocumentId) {
      return `Active SOP context: ${activeDocumentId}. User request: ${question}`
    }
  }
  return question
}

export function getAssistantRouteMeta(pathname = '/') {
  return matchRouteConfig(pathname)
}

export async function runUnifiedAssistantQuery({
  question,
  pathname = '/',
  chatHistory = [],
  assistantActionConfirmation = null,
  surface = 'global_chatbot',
}) {
  const routeMeta = matchRouteConfig(pathname)
  const contextualQuestion = buildContextualQuestion(question, pathname)
  const assistantContext = getKLAssistantContext(pathname)
  return queryAI(contextualQuestion, {
    chat_history: chatHistory,
    category: routeMeta.category,
    assistant_context: assistantContext,
    assistant_action_confirmation: assistantActionConfirmation,
    surface,
    route: pathname,
  })
}
