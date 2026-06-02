import React, { memo, Suspense, lazy } from 'react'
import { EditorContent } from '@tiptap/react'
import EditorAIBridge from './EditorAIBridge'

const AIAssistantBubbleMenu = lazy(() => import('./AIAssistantBubbleMenu'))

class EditorOverlayErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error) {
    console.error('Editor overlay crashed:', error)
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false })
    }
  }

  render() {
    if (this.state.hasError) return null
    return this.props.children
  }
}

/**
 * Memoized TipTap surface — isolates ProseMirror DOM from unrelated EditorPage state
 * (autosave timestamps, sidebar metadata, etc.).
 */
const EditorTypingSurface = memo(function EditorTypingSurface({
  editor,
  isEditable,
  aiSopContext,
  documentId,
  onPreviewSessionChange,
  onAfterApply,
  onVersionCompareRequest,
}) {
  if (!editor || editor.isDestroyed) return null

  return (
    <div className="figma-paper editor-typing-surface">
      <EditorContent editor={editor} />
      <EditorOverlayErrorBoundary resetKey={String(documentId || '')}>
        <Suspense fallback={null}>
          <AIAssistantBubbleMenu
            editor={editor}
            sopMetadata={aiSopContext}
            isEditable={isEditable}
            onPreviewSessionChange={onPreviewSessionChange}
          />
        </Suspense>
        <EditorAIBridge
          editor={editor}
          documentId={documentId}
          sopMetadata={aiSopContext}
          isEditable={isEditable}
          onPreviewSessionChange={onPreviewSessionChange}
          onAfterApply={onAfterApply}
          onVersionCompareRequest={onVersionCompareRequest}
        />
      </EditorOverlayErrorBoundary>
    </div>
  )
})

export default EditorTypingSurface
