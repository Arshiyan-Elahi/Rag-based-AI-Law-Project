import React, { useState, useCallback } from 'react'
import { ArrowLeft } from 'lucide-react'
import ConversationList from '../components/Chat/ConversationList'
import ChatPanel from '../components/Chat/ChatPanel'
import './ChatPage.css'

/* ─── Mock data ─── */
const MOCK_CONVERSATIONS = [
  {
    id: '1',
    title: 'Reinigungsvalidierung Linie 3',
    description: 'Was waren die Hauptursachen bei DEV-2024-019',
    time: '09:41',
    dateGroup: 'Heute',
    hasAlert: true,
    tags: [
      { id: 't1', label: 'SOP-QA-042', type: 'sop' },
      { id: 't2', label: 'DEV-2024-019', type: 'dev' },
      { id: 't3', label: 'CAPA-015', type: 'capa' }
    ]
  },
  {
    id: '2',
    title: 'Audit A-2024-07 Beanstandungen',
    description: 'Welche SOPs sind direkt betroffen?',
    time: '09:41',
    dateGroup: 'Heute',
    hasAlert: false,
    tags: [
      { id: 't4', label: 'A-2024-07', type: 'audit' },
      { id: 't5', label: 'SOP-HL-033', type: 'sop' }
    ]
  },
  {
    id: '3',
    title: 'Offene CAPA Maßnahmen Q2',
    description: 'Welche CAPAs haben eine Frist bis Juni 2025?',
    time: '16:30',
    dateGroup: 'Gestern',
    hasAlert: false,
    tags: [
      { id: 't6', label: 'CAPA-2024-015', type: 'capa' },
      { id: 't7', label: 'CAPA-005', type: 'capa' }
    ]
  },
  {
    id: '4',
    title: 'SOP Hygienemonitoring Analyse',
    description: 'Fasse SOP-HL-033 zusammen und zeige Risiken',
    time: '11:05',
    dateGroup: 'Gestern',
    hasAlert: false,
    tags: [
      { id: 't8', label: 'SOP-HL-033', type: 'sop' },
      { id: 't9', label: '7 Abweichungen', type: 'dev' }
    ]
  },
  {
    id: '5',
    title: 'Entscheidung Ventiltyp V-14',
    description: 'Welche Entscheidung wurde 2023 zu V-14 getroff..',
    time: 'Mo, 14:22',
    dateGroup: 'Diese Woche',
    hasAlert: false,
    tags: [
      { id: 't10', label: 'ENT-2023-004', type: 'ent' },
      { id: 't11', label: 'SOP-QA-042', type: 'sop' }
    ]
  },
  {
    id: '6',
    title: 'Wochenzusammenfassung KW 14',
    description: 'Zusammenfassung aller Ereignisse letzte Woche',
    time: '09:41',
    dateGroup: 'Diese Woche',
    hasAlert: false,
    tags: [
      { id: 't12', label: 'SOPs', type: 'sop' },
      { id: 't13', label: 'Abweichungen', type: 'dev' },
      { id: 't14', label: 'Audits', type: 'audit' }
    ]
  },
  {
    id: '7',
    title: 'Abweichungsanalyse Q1 2025',
    description: 'Was waren die Hauptursachen der Abweichungen?',
    time: 'So, 10:14',
    dateGroup: 'Diese Woche',
    hasAlert: false,
    tags: [
      { id: 't15', label: 'DEV Q1', type: 'dev' },
      { id: 't16', label: '4 CAPAs', type: 'capa' }
    ]
  }
]

const MOCK_CONVERSATION_DETAIL = {
  id: '1',
  title: 'Reinigungsvalidierung Linie 3',
  subtitleParts: ['Heute, 09:41', '8 Nachrichten', '4 Quellen referenziert'],
  dateDivider: 'Heute, 09:41',
  activeSources: [
    { id: 'as1', label: 'SOP-QA-042', type: 'sop' },
    { id: 'as2', label: 'SOP-QA-001', type: 'sop' },
    { id: 'as3', label: 'DEV-2024-019', type: 'dev' },
    { id: 'as4', label: 'CAPA-2024-015', type: 'capa' },
    { id: 'as5', label: 'A-2024-07', type: 'audit' }
  ],
  contextTags: [
    { id: 'ct1', label: 'SOP-QA-042', type: 'sop' },
    { id: 'ct2', label: 'DEV-2024-019', type: 'dev' }
  ],
  messages: [
    {
      id: 'm1',
      sender: 'ai',
      time: '09:41',
      content: '<p>Guten Morgen, Haider. Ich habe Linie 3 – Reinigungsvalidierung als Kontext geladen. Es gibt 2 kritische Abweichungen, 1 offene CAPA-Frist am 30.05.2025 und einen direkten Audit-Bezug. Womit soll ich beginnen?</p>',
      tags: [
        { id: 'mt1', label: 'SOP-QA-042', type: 'sop' },
        { id: 'mt2', label: 'DEV-2024-019', type: 'dev' },
        { id: 'mt3', label: 'CAPA-2024-015', type: 'capa' }
      ],
      showActions: false
    },
    {
      id: 'm2',
      sender: 'user',
      time: '09:42',
      content: '<p>Was waren die Hauptursachen bei DEV-2024-019?</p>',
      tags: [],
      showActions: false
    },
    {
      id: 'm3',
      sender: 'ai',
      time: '09:42',
      content: `<p>DEV-2024-019 – Reinigung vor Freigabe, Linie 3, wurde am 19.03.2024 gemeldet. Die Ursachenanalyse ergab zwei wesentliche Faktoren:</p>
<ul>
<li><strong>Primärursache:</strong> Defektes Dichtungsventil V-14 im CIP-Kreislauf — dadurch unvollständige Reinigung des Ventilbereichs.</li>
<li><strong>Systemursache:</strong> Prüfintervall für Ventildichtungen war in SOP-QA-042, §4.3 nicht risikobewertungsbasiert definiert.</li>
</ul>
<p>Als Sofortmaßnahme wurde Linie 3 gesperrt und Ventil V-14 ausgetauscht. CAPA-005 ist formal abgeschlossen — die Wirksamkeitsprüfung steht jedoch noch aus.</p>`,
      tags: [
        { id: 'mt4', label: 'SOP-QA-042', type: 'sop' },
        { id: 'mt5', label: 'DEV-2024-019', type: 'dev' },
        { id: 'mt6', label: 'CAPA-2024-015', type: 'capa' }
      ],
      showActions: true
    },
    {
      id: 'm4',
      sender: 'user',
      time: '09:48',
      content: '<p>Fasse den gesamten Kontext zu dieser Abweichung zusammen — SOP, CAPA und Audit.</p>',
      tags: [],
      showActions: false
    },
    {
      id: 'm5',
      sender: 'ai',
      time: '09:49',
      content: `<p>Hier ist die vollständige Kontextzusammenfassung zu DEV-2024-019:</p>
<p>DEV-2024-019 – Reinigung vor Freigabe, Linie 3, wurde am 19.03.2024 gemeldet. Die Ursachenanalyse ergab zwei wesentliche Faktoren:</p>
<ul>
<li><strong>Primärursache:</strong> Defektes Dichtungsventil V-14 im CIP-Kreislauf — dadurch unvollständige Reinigung des Ventilbereichs.</li>
<li><strong>Systemursache:</strong> Prüfintervall für Ventildichtungen war in SOP-QA-042, §4.3 nicht risikobewertungsbasiert definiert.</li>
</ul>
<p>Als Sofortmaßnahme wurde Linie 3 gesperrt und Ventil V-14 ausgetauscht. CAPA-005 ist formal abgeschlossen — die Wirksamkeitsprüfung steht jedoch noch aus.</p>`,
      tags: [
        { id: 'mt7', label: 'SOP-QA-042', type: 'sop' },
        { id: 'mt8', label: 'DEV-2024-019', type: 'dev' },
        { id: 'mt9', label: 'CAPA-005', type: 'capa' },
        { id: 'mt10', label: 'CAPA-2024-015', type: 'capa' },
        { id: 'mt11', label: 'A-2024-07', type: 'audit' }
      ],
      showActions: true
    }
  ]
}

/**
 * ChatPage — Full "Gespräche" page combining list + detail.
 */
export default function ChatPage() {
  const [activeConvId, setActiveConvId] = useState('1')
  const [showChat, setShowChat] = useState(false)

  // In a real app this would fetch from API — for now use mock
  const activeConversation = activeConvId === '1' ? MOCK_CONVERSATION_DETAIL : null

  const handleSelect = useCallback((id) => {
    setActiveConvId(id)
    setShowChat(true) // On mobile, switch to chat view
  }, [])

  const handleBack = useCallback(() => {
    setShowChat(false)
  }, [])

  const handleNewConversation = useCallback(() => {
    // Placeholder for new conversation logic
    console.log('New conversation')
  }, [])

  const handleSendMessage = useCallback((text) => {
    // Placeholder for send message logic
    console.log('Send:', text)
  }, [])

  const mobileClass = showChat ? 'chat-page--show-chat' : 'chat-page--show-list'

  return (
    <div className={`chat-page ${mobileClass}`}>
      <ConversationList
        conversations={MOCK_CONVERSATIONS}
        activeId={activeConvId}
        onSelect={handleSelect}
        onNewConversation={handleNewConversation}
      />

      <div className="chat-page__detail">
        {showChat && (
          <button className="chat-page__back-btn" onClick={handleBack}>
            <ArrowLeft size={16} />
            Zurück zur Liste
          </button>
        )}
        <ChatPanel
          conversation={activeConversation}
          onSendMessage={handleSendMessage}
        />
      </div>
    </div>
  )
}
