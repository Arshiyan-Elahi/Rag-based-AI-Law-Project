import React, { useRef, useState } from 'react'
import { useEditorState } from '@tiptap/react'
import { 
    Save, 
    FilePlus, 
    Copy, 
    PlusSquare, 
    Eye, 
    Bold, 
    Italic, 
    Underline as UnderlineIcon, 
    Strikethrough, 
    Heading1, 
    Heading2, 
    Heading3, 
    List, 
    ListOrdered, 
    Undo, 
    Redo, 
    Link as LinkIcon, 
    Table as TableIcon, 
    FileUp,
    ChevronDown,
    Columns,
    Rows,
    Trash2,
    Check
} from 'lucide-react'
import { menuBarStateSelector } from './menuBarState'
import { useLanguage } from '../../context/LanguageContext'
import './MenuBar.css'

// Utility to determine OS for keyboard shortcuts
const isMac =
    typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

const modKey = isMac ? 'Cmd' : 'Ctrl'
const shortcut = (key) => `${modKey} + ${key}`
const shortcutShift = (key) => `${modKey} + Shift + ${key}`
const shortcutAlt = (key) => `${modKey} + Alt + ${key}`

export const MenuBar = ({
    editor,
    onSave,
    onNewVersion,
    onCreateNewDocument,
    onDuplicateAsNewDocument,
    currentVersion,
    onLoadVersion,
    versions,
    onOpenLinkModal,
    onCompare,
    onOpenPreview,
    onInsertPlaceholder,
    profile,
    onToggleVariablesPanel,
    onSendForReview,
    onOCRUpload,
    isOcrLoading,
    ocrError,
    isReadOnly = false,
    canCreateNewVersion = true
}) => {
    const fileInputRef = useRef(null)
    const { t } = useLanguage()
    const [selectedPlaceholder, setSelectedPlaceholder] = useState('')

    const handleOCRButtonClick = () => {
        if (isReadOnly) return
        fileInputRef.current?.click()
    }

    const handleFileChange = async (event) => {
        if (isReadOnly) {
            event.target.value = ''
            return
        }

        const file = event.target.files?.[0]
        if (!file) return

        await onOCRUpload?.(file)
        event.target.value = ''
    }

    const editorState = useEditorState({
        editor,
        selector: menuBarStateSelector,
    })

    if (!editor) return null

    const isInTable = editor.isActive('table')

    const runIfEditable = (callback) => {
        if (isReadOnly) return
        callback?.()
    }

    const disabledIfReadOnly = (extraCondition = false) =>
        isReadOnly || extraCondition

    return (
        <div className="editor-menubar">
            <div className="menubar-group">
                <button
                    type="button"
                    onClick={() => runIfEditable(onSave)}
                    title={`${t.save} (${shortcut('S')})`}
                    className="menubar-btn primary"
                    disabled={isReadOnly}
                >
                    <Save size={18} />
                    <span>{t.save}</span>
                </button>

                <button
                    type="button"
                    onClick={onNewVersion}
                    title={`${t.newVersion} (${shortcutShift('V')})`}
                    className="menubar-btn secondary"
                    disabled={!canCreateNewVersion}
                >
                    <PlusSquare size={18} />
                    <span>{t.newVersion}</span>
                </button>
            </div>

            <div className="menubar-group">
                <button
                    type="button"
                    className="menubar-btn icon-only"
                    onClick={onCreateNewDocument}
                    title="Create a brand-new SOP document"
                >
                    <FilePlus size={18} />
                </button>

                <button
                    type="button"
                    className="menubar-btn icon-only"
                    onClick={onDuplicateAsNewDocument}
                    title="Duplicate current document"
                >
                    <Copy size={18} />
                </button>
            </div>

            <div className="menubar-divider" />

            <div className="menubar-group">
                <div className="version-selector-wrapper">
                    <select
                        value={currentVersion}
                        onChange={(e) => onLoadVersion(e.target.value)}
                        className="version-select-modern"
                    >
                        {versions.map((v) => (
                            <option key={v.id} value={v.id}>
                                v{v.versionNumber || '?'} ({v.timestamp})
                            </option>
                        ))}
                    </select>
                </div>

                <button
                    type="button"
                    onClick={onOpenPreview}
                    className="menubar-btn secondary"
                    title={`${t.previewExport} (${shortcutAlt('P')})`}
                >
                    <Eye size={18} />
                    <span>{t.previewExport}</span>
                </button>
            </div>

            <div className="menubar-divider" />

            <div className="menubar-group format-group">
                <button
                    type="button"
                    title={`${t.bold} (${shortcut('B')})`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleBold().run())}
                    disabled={disabledIfReadOnly(!editorState.canBold)}
                    className={`menubar-btn icon-only ${editorState.isBold ? 'active' : ''}`}
                >
                    <Bold size={18} />
                </button>

                <button
                    type="button"
                    title={`${t.italic} (${shortcut('I')})`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleItalic().run())}
                    disabled={disabledIfReadOnly(!editorState.canItalic)}
                    className={`menubar-btn icon-only ${editorState.isItalic ? 'active' : ''}`}
                >
                    <Italic size={18} />
                </button>

                <button
                    type="button"
                    title={`${t.underline} (${shortcut('U')})`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleUnderline().run())}
                    className={`menubar-btn icon-only ${editorState.isUnderline ? 'active' : ''}`}
                >
                    <UnderlineIcon size={18} />
                </button>

                <button
                    type="button"
                    title={`${t.strike} (${shortcutShift('X')})`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleStrike().run())}
                    disabled={disabledIfReadOnly(!editorState.canStrike)}
                    className={`menubar-btn icon-only ${editorState.isStrike ? 'active' : ''}`}
                >
                    <Strikethrough size={18} />
                </button>
            </div>

            <div className="menubar-group">
                <button
                    type="button"
                    title={`${t.heading1} (Alt + 1)`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleHeading({ level: 1 }).run())}
                    className={`menubar-btn icon-only ${editorState.isHeading1 ? 'active' : ''}`}
                >
                    <Heading1 size={18} />
                </button>
                <button
                    type="button"
                    title={`${t.heading2} (Alt + 2)`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleHeading({ level: 2 }).run())}
                    className={`menubar-btn icon-only ${editorState.isHeading2 ? 'active' : ''}`}
                >
                    <Heading2 size={18} />
                </button>
            </div>

            <div className="menubar-group">
                <button
                    type="button"
                    title={`${t.bulletList} (${shortcutShift('L')})`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleBulletList().run())}
                    className={`menubar-btn icon-only ${editorState.isBulletList ? 'active' : ''}`}
                >
                    <List size={18} />
                </button>
                <button
                    type="button"
                    title={`${t.numberedList} (${shortcutShift('7')})`}
                    onClick={() => runIfEditable(() => editor.chain().focus().toggleOrderedList().run())}
                    className={`menubar-btn icon-only ${editorState.isOrderedList ? 'active' : ''}`}
                >
                    <ListOrdered size={18} />
                </button>
            </div>

            <div className="menubar-divider" />

            <div className="menubar-group">
                <button
                    type="button"
                    onClick={() => runIfEditable(() => editor.chain().focus().undo().run())}
                    disabled={disabledIfReadOnly(!editorState.canUndo)}
                    className="menubar-btn icon-only"
                >
                    <Undo size={18} />
                </button>
                <button
                    type="button"
                    onClick={() => runIfEditable(() => editor.chain().focus().redo().run())}
                    disabled={disabledIfReadOnly(!editorState.canRedo)}
                    className="menubar-btn icon-only"
                >
                    <Redo size={18} />
                </button>
            </div>

            <div className="menubar-group">
                <button
                    type="button"
                    title={t.insertUrl}
                    onClick={() => runIfEditable(onOpenLinkModal)}
                    disabled={isReadOnly}
                    className="menubar-btn icon-only"
                >
                    <LinkIcon size={18} />
                </button>

                <button
                    type="button"
                    title={t.insertTable}
                    onClick={() => runIfEditable(() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run())}
                    disabled={isReadOnly}
                    className="menubar-btn icon-only"
                >
                    <TableIcon size={18} />
                </button>

                <button
                    type="button"
                    onClick={handleOCRButtonClick}
                    disabled={isReadOnly || isOcrLoading}
                    title={t.importOcrTooltip}
                    className={`menubar-btn icon-only ${isOcrLoading ? 'loading' : ''}`}
                >
                    <FileUp size={18} />
                </button>
                <input ref={fileInputRef} type="file" accept=".pdf,.docx,.doc,.txt" style={{ display: 'none' }} onChange={handleFileChange} />
            </div>

            {isInTable && (
                <div className="menubar-group table-tools">
                    <button type="button" onClick={() => runIfEditable(() => editor.chain().focus().addColumnBefore().run())} className="menubar-btn icon-only"><Columns size={16} /></button>
                    <button type="button" onClick={() => runIfEditable(() => editor.chain().focus().addRowBefore().run())} className="menubar-btn icon-only"><Rows size={16} /></button>
                    <button type="button" onClick={() => runIfEditable(() => editor.chain().focus().deleteTable().run())} className="menubar-btn icon-only danger"><Trash2 size={16} /></button>
                </div>
            )}
        </div>
    )
}

export default MenuBar