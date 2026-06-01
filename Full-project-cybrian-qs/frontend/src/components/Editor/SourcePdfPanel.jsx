import { useMemo } from 'react'
import { getSourcePdfUrl } from '../../api/editorApi'

/**
 * Side-by-side original PDF preview for scanned imports (editable content stays in TipTap).
 */
export default function SourcePdfPanel({ versionId, metadata = {}, t = {} }) {
  const sourceMeta = metadata?._source_pdf
  const available = Boolean(sourceMeta?.available && versionId)

  const pdfUrl = useMemo(() => {
    if (!available || !versionId) return null
    return getSourcePdfUrl(versionId)
  }, [available, versionId])

  if (!available || !pdfUrl) return null

  const title = t.originalPdfPreview || 'Original PDF (import)'
  const hint =
    t.originalPdfPreviewHint
    || 'Editable SOP content is in the editor. This panel shows the uploaded PDF unchanged.'

  return (
    <section className="source-pdf-panel" aria-label={title}>
      <div className="source-pdf-panel__header">
        <h3 className="source-pdf-panel__title">{title}</h3>
        <p className="source-pdf-panel__hint">{hint}</p>
      </div>
      <iframe
        className="source-pdf-panel__frame"
        src={pdfUrl}
        title={title}
      />
    </section>
  )
}
