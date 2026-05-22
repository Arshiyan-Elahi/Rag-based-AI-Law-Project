import React, { memo } from 'react'
import { Check, X } from 'lucide-react'
import { sanitizeRenderedHtml } from '../../utils/aiOutputFormatter'

/**
 * Rewrite/improve action preview shown inside a chat message (not a separate panel).
 */
function ChatActionPreview({
  action,
  sectionName = '',
  previewHtml = '',
  disabled = false,
  onAccept,
  onReject,
}) {
  const actionLabel = action === 'improve' ? 'Improvement' : 'Rewrite'
  const scope = sectionName ? ` — ${sectionName}` : ''

  return (
    <div className="ai-message-action-preview" role="region" aria-label={`${actionLabel} preview`}>
      <div className="ai-message-action-preview__header">
        <span className="ai-message-action-preview__title">{actionLabel} preview{scope}</span>
        <p className="ai-actions-tab__pending-hint">
          Review the suggested text below. The editor highlights the same change. Accept applies structured
          content only; Reject leaves the SOP unchanged.
        </p>
      </div>

      {previewHtml ? (
        <div
          className="ai-actions-tab__preview-html tiptap ai-message-action-preview__html"
          dangerouslySetInnerHTML={{ __html: sanitizeRenderedHtml(previewHtml) }}
        />
      ) : (
        <p className="ai-message-action-preview__empty">No preview content returned.</p>
      )}

      <div className="ai-actions-tab__decision" role="group" aria-label="Accept or reject">
        <button
          type="button"
          className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--reject"
          onClick={onReject}
          disabled={disabled}
        >
          <X size={14} />
          Reject
        </button>
        <button
          type="button"
          className="ai-inline-suggestion-toolbar__btn ai-inline-suggestion-toolbar__btn--accept"
          onClick={onAccept}
          disabled={disabled}
        >
          <Check size={14} />
          Accept
        </button>
      </div>
    </div>
  )
}

export default memo(ChatActionPreview)
