/**
 * Global user-facing error copy. Technical details must be logged separately
 * (console / backend) and must never be shown in the UI.
 */

export const FRIENDLY_ERROR_EN =
  'Please try again. Sorry for the inconvenience.'

export const FRIENDLY_ERROR_DE =
  'Bitte versuchen Sie es erneut. Entschuldigen Sie die Unannehmlichkeiten.'

/** Sentinel error message for unsupported SOP import file types (PDF/DOCX/TXT only). */
export const UNSUPPORTED_FILE_TYPE_CODE = 'UNSUPPORTED_FILE_TYPE'

export const UNSUPPORTED_FILE_TYPE_EN =
  'Unsupported file type. Please upload a PDF, DOCX, or TXT file.'

export const UNSUPPORTED_FILE_TYPE_DE =
  'Nicht unterstützter Dateityp. Bitte laden Sie eine PDF-, DOCX- oder TXT-Datei hoch.'

const LEGACY_UNSUPPORTED_FILE_TYPE_PATTERNS = [
  /^this file format is not supported/i,
  /^unsupported file type/i,
  /not supported.*\.?pdf.*\.?docx.*\.?txt/i,
  /binary file/i,
  /nicht unterstützter dateityp/i,
]

/** Read persisted app language (matches LanguageContext). */
export function getAppLanguage() {
  if (typeof window === 'undefined') return 'de'
  const raw = localStorage.getItem('app_language') || 'de'
  return String(raw).toLowerCase().startsWith('en') ? 'en' : 'de'
}

/**
 * @param {string} [language] - 'en' | 'de' (defaults to getAppLanguage())
 * @returns {string}
 */
export function getUnsupportedFileTypeMessage(language) {
  const lang =
    language != null
      ? String(language).toLowerCase()
      : getAppLanguage()
  if (lang === 'en' || lang.startsWith('en')) {
    return UNSUPPORTED_FILE_TYPE_EN
  }
  return UNSUPPORTED_FILE_TYPE_DE
}

export function isUnsupportedFileTypeError(err) {
  const msg = String(
    err?.message
    || err?.detail
    || (typeof err === 'string' ? err : ''),
  ).trim()
  if (!msg) return false
  if (msg === UNSUPPORTED_FILE_TYPE_CODE) return true
  if (msg === UNSUPPORTED_FILE_TYPE_EN || msg === UNSUPPORTED_FILE_TYPE_DE) return true
  return LEGACY_UNSUPPORTED_FILE_TYPE_PATTERNS.some((pattern) => pattern.test(msg))
}

/**
 * User-facing import/upload error: localized unsupported-type copy, else global friendly message.
 */
export function resolveImportUiError(err, language) {
  if (isUnsupportedFileTypeError(err)) {
    return getUnsupportedFileTypeMessage(language)
  }
  return toFriendlyUiError(err, language, 'import')
}

export function getFriendlyErrorMessage(language) {
  const lang =
    language != null
      ? String(language).toLowerCase()
      : getAppLanguage()
  if (lang === 'en' || lang.startsWith('en')) {
    return FRIENDLY_ERROR_EN
  }
  return FRIENDLY_ERROR_DE
}

/**
 * Log a technical failure and return the friendly UI message.
 * @param {unknown} err
 * @param {string} [language]
 * @param {string} [context]
 */
export function toFriendlyUiError(err, language, context = 'ui-error') {
  if (err != null) {
    console.error(`[${context}]`, err)
  }
  return getFriendlyErrorMessage(language)
}
