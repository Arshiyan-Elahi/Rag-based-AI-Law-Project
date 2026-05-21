"""
Local Marker PDF extraction (datalab-to/marker) for SOP import.
No external API — models stay loaded in the import worker process.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from .marker_blocks import markdown_to_extraction_blocks
from .pdf_extractor import sanitize_extracted_text

logger = logging.getLogger(__name__)

EXTRACTION_ENGINE = "local_marker"
_MARKER_RUNTIME: Optional["_LocalMarkerRuntime"] = None
_RUNTIME_LOCK = threading.Lock()
_WARMUP_STARTED = False
# Serialize Marker PDF conversions; models loaded once via create_model_dict().
_MARKER_EXECUTOR: ThreadPoolExecutor | None = None
_MARKER_EXECUTOR_LOCK = threading.Lock()


@dataclass
class LocalMarkerResult:
    markdown: str
    metadata: Dict[str, Any]
    images: Dict[str, str]
    cache_key: str
    from_cache: bool


class LocalMarkerError(RuntimeError):
    pass


PhaseCallback = Callable[[str, str], None]

PHASE_CACHE_HIT = "cache_hit"
PHASE_PROCESSING_MARKER = "processing_marker"
PHASE_CONVERTING_BLOCKS = "converting_blocks"


def _marker_executor() -> ThreadPoolExecutor:
    global _MARKER_EXECUTOR
    with _MARKER_EXECUTOR_LOCK:
        if _MARKER_EXECUTOR is None:
            workers = max(1, int(os.getenv("SOP_MARKER_WORKER_THREADS", "1")))
            _MARKER_EXECUTOR = ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="marker-pdf",
            )
        return _MARKER_EXECUTOR


def _log_marker(
    level: int,
    msg: str,
    *,
    job_id: str | None = None,
    cache_key: str | None = None,
    **extra: Any,
) -> None:
    parts = ["[local-marker]"]
    if job_id:
        parts.append(f"job_id={job_id}")
    if cache_key:
        parts.append(f"cache_key={cache_key[:12]}")
    for key, value in extra.items():
        if value is not None:
            parts.append(f"{key}={value}")
    parts.append(msg)
    logger.log(level, " ".join(parts))


def is_local_marker_enabled() -> bool:
    return os.getenv("SOP_USE_LOCAL_MARKER", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_local_marker_available() -> bool:
    if not is_local_marker_enabled():
        return False
    try:
        import marker  # noqa: F401
        from marker.converters.pdf import PdfConverter  # noqa: F401

        return True
    except ImportError:
        return False


def marker_cache_dir() -> Path:
    raw = os.getenv("SOP_MARKER_CACHE_DIR", "data/marker_cache")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _page_range_config() -> str | None:
    """Empty env => full document. Set e.g. 0-1 only for local testing."""
    value = (os.getenv("SOP_MARKER_PAGE_RANGE") or "").strip()
    return value or None


def _force_ocr_default(scanned: bool) -> bool:
    if scanned:
        return True
    return os.getenv("SOP_MARKER_FORCE_OCR", "false").strip().lower() in ("1", "true", "yes")


def cache_key_for_pdf(file_bytes: bytes, *, scanned: bool) -> str:
    return build_cache_key(
        file_bytes,
        page_range=_page_range_config(),
        force_ocr=_force_ocr_default(scanned),
    )


def build_cache_key(file_bytes: bytes, *, page_range: str | None, force_ocr: bool) -> str:
    h = hashlib.sha256()
    h.update(file_bytes)
    h.update(b"|")
    h.update((page_range or "").encode("utf-8"))
    h.update(b"|")
    h.update(b"1" if force_ocr else b"0")
    return h.hexdigest()


def _cache_paths(cache_key: str) -> Dict[str, Path]:
    root = marker_cache_dir() / cache_key
    return {
        "root": root,
        "markdown": root / "document.md",
        "meta": root / "meta.json",
        "blocks": root / "blocks.json",
    }


def load_cached_marker_result(cache_key: str) -> LocalMarkerResult | None:
    paths = _cache_paths(cache_key)
    if not paths["markdown"].is_file():
        return None
    markdown = paths["markdown"].read_text(encoding="utf-8", errors="replace")
    meta: Dict[str, Any] = {}
    if paths["meta"].is_file():
        try:
            meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    return LocalMarkerResult(
        markdown=markdown,
        metadata=meta if isinstance(meta, dict) else {},
        images={},
        cache_key=cache_key,
        from_cache=True,
    )


def save_cached_marker_result(result: LocalMarkerResult, blocks: List[Dict[str, Any]]) -> None:
    paths = _cache_paths(result.cache_key)
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["markdown"].write_text(result.markdown, encoding="utf-8")
    paths["meta"].write_text(
        json.dumps(result.metadata or {}, ensure_ascii=False, indent=0),
        encoding="utf-8",
    )
    paths["blocks"].write_text(
        json.dumps(blocks, ensure_ascii=False),
        encoding="utf-8",
    )


def load_cached_blocks(cache_key: str) -> List[Dict[str, Any]] | None:
    path = _cache_paths(cache_key)["blocks"]
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else None
    except Exception:
        return None


class _LocalMarkerRuntime:
    """Singleton Marker model cache for the import worker process."""

    def __init__(self) -> None:
        self._models = None
        self._model_lock = threading.Lock()
        self._convert_lock = threading.Lock()

    def _ensure_models(self) -> Any:
        with self._model_lock:
            if self._models is None:
                started = time.monotonic()
                _log_marker(logging.INFO, "loading models via create_model_dict()…")
                from marker.models import create_model_dict

                self._models = create_model_dict()
                _log_marker(
                    logging.INFO,
                    "models ready",
                    elapsed_s=round(time.monotonic() - started, 1),
                )
            return self._models

    def convert_pdf_file(
        self,
        pdf_path: Path,
        *,
        page_range: str | None,
        force_ocr: bool,
        timeout_sec: float,
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        from marker.config.parser import ConfigParser
        from marker.converters.pdf import PdfConverter
        from marker.output import text_from_rendered

        options: Dict[str, Any] = {
            "filepath": str(pdf_path),
            "force_ocr": force_ocr,
            "paginate_output": os.getenv("SOP_MARKER_PAGINATE", "false").lower()
            in ("1", "true", "yes"),
            "output_format": "markdown",
        }
        if page_range:
            options["page_range"] = page_range

        config_parser = ConfigParser(options)
        config_dict = config_parser.generate_config_dict()
        config_dict["pdftext_workers"] = int(os.getenv("SOP_MARKER_PDFTEXT_WORKERS", "1"))

        def _run() -> Any:
            converter = PdfConverter(
                config=config_dict,
                artifact_dict=self._ensure_models(),
                processor_list=config_parser.get_processors(),
                renderer=config_parser.get_renderer(),
                llm_service=config_parser.get_llm_service(),
            )
            return converter(str(pdf_path))

        with self._convert_lock:
            start = time.monotonic()
            _log_marker(
                logging.INFO,
                "PDF conversion started",
                path=pdf_path.name,
                page_range=page_range or "all",
                force_ocr=force_ocr,
                timeout_sec=timeout_sec,
            )
            rendered = _run_with_timeout(_run, timeout_sec=timeout_sec)
            markdown, _ext, images = text_from_rendered(rendered)
            metadata = getattr(rendered, "metadata", None) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            elapsed = time.monotonic() - start
            _log_marker(
                logging.INFO,
                "PDF conversion finished",
                path=pdf_path.name,
                elapsed_s=round(elapsed, 1),
                markdown_chars=len(markdown or ""),
            )
            return str(markdown or ""), metadata, images if isinstance(images, dict) else {}


def _run_with_timeout(fn: Callable[[], Any], *, timeout_sec: float) -> Any:
    if timeout_sec <= 0:
        return fn()

    result: Dict[str, Any] = {}
    error: List[BaseException] = []

    def target() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    thread = threading.Thread(target=target, name="local-marker-convert", daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)
    if thread.is_alive():
        raise LocalMarkerError(
            f"Local Marker PDF timed out after {timeout_sec:.0f}s. "
            "Set SOP_MARKER_PAGE_RANGE=0-1 for quick tests or increase SOP_MARKER_TIMEOUT_SEC."
        )
    if error:
        raise LocalMarkerError(str(error[0])) from error[0]
    if "value" not in result:
        raise LocalMarkerError("Local Marker PDF returned no output.")
    return result["value"]


def get_marker_runtime() -> _LocalMarkerRuntime:
    global _MARKER_RUNTIME
    with _RUNTIME_LOCK:
        if _MARKER_RUNTIME is None:
            _MARKER_RUNTIME = _LocalMarkerRuntime()
        return _MARKER_RUNTIME


def warmup_local_marker() -> Dict[str, Any]:
    """Pre-load Marker models in a background thread (worker/API startup)."""
    global _WARMUP_STARTED
    out: Dict[str, Any] = {
        "enabled": is_local_marker_enabled(),
        "available": is_local_marker_available(),
        "warmed": False,
    }
    if not out["enabled"] or not out["available"]:
        return out
    if os.getenv("SOP_MARKER_WARMUP", "true").strip().lower() not in ("1", "true", "yes", "on"):
        out["skipped"] = "SOP_MARKER_WARMUP=false"
        return out
    if _WARMUP_STARTED:
        out["skipped"] = "already_started"
        return out
    _WARMUP_STARTED = True

    def _load() -> None:
        try:
            get_marker_runtime()._ensure_models()
            logger.info("[local-marker] warmup complete")
        except Exception as exc:
            logger.warning("[local-marker] warmup failed: %s", exc)

    threading.Thread(target=_load, name="local-marker-warmup", daemon=True).start()
    out["warmed"] = True
    return out


def _blocks_from_marker_result(
    result: LocalMarkerResult,
    *,
    job_id: str | None = None,
    on_phase: PhaseCallback | None = None,
) -> Tuple[List[Dict[str, Any]], str]:
    from .document_structure import refine_blocks

    blocks = load_cached_blocks(result.cache_key)
    if blocks is not None:
        text = sanitize_extracted_text(_flatten_from_blocks(blocks) or result.markdown)
        return refine_blocks(blocks, text), text

    if on_phase:
        on_phase(PHASE_CONVERTING_BLOCKS, "Converting Marker markdown to structured blocks…")
    _log_marker(logging.INFO, "markdown → blocks", job_id=job_id, cache_key=result.cache_key)
    started = time.monotonic()
    blocks, text = markdown_to_extraction_blocks(result.markdown)
    blocks = refine_blocks(blocks, text)
    _log_marker(
        logging.INFO,
        "blocks ready",
        job_id=job_id,
        cache_key=result.cache_key,
        block_count=len(blocks),
        elapsed_s=round(time.monotonic() - started, 2),
    )
    return blocks, text


def try_extract_pdf_from_cache(
    pdf_bytes: bytes,
    *,
    scanned: bool | None = None,
) -> Tuple[List[Dict[str, Any]], str, LocalMarkerResult] | None:
    """
    Fast path for sync requests: return structured blocks only when disk cache exists.
    Never runs Marker inference (safe inside HTTP handlers).
    """
    if not is_local_marker_enabled() or not is_local_marker_available():
        return None
    if scanned is None:
        from .pdf_extractor import _pdf_is_scanned

        scanned = _pdf_is_scanned(pdf_bytes)
    cache_key = cache_key_for_pdf(pdf_bytes, scanned=scanned)
    cached = load_cached_marker_result(cache_key)
    if not cached:
        return None
    _log_marker(logging.INFO, "disk cache hit (sync fast path)", cache_key=cache_key)
    blocks, text = _blocks_from_marker_result(cached)
    if not blocks and not (text or "").strip():
        return None
    return blocks, text, cached


def _run_marker_inference(
    pdf_bytes: bytes,
    filename: str,
    *,
    page_range: str | None,
    force_ocr: bool,
    cache_key: str,
    job_id: str | None,
    timeout_sec: float,
) -> LocalMarkerResult:
    suffix = Path(filename or "upload.pdf").suffix.lower() or ".pdf"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)

        runtime = get_marker_runtime()

        def _convert() -> Tuple[str, Dict[str, Any], Dict[str, str]]:
            return runtime.convert_pdf_file(
                tmp_path,
                page_range=page_range,
                force_ocr=force_ocr,
                timeout_sec=timeout_sec,
            )

        future = _marker_executor().submit(_convert)
        markdown, metadata, images = future.result(timeout=timeout_sec + 30)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    markdown = sanitize_extracted_text(markdown)
    if not markdown.strip():
        raise LocalMarkerError("Local Marker PDF produced empty markdown.")

    return LocalMarkerResult(
        markdown=markdown,
        metadata=metadata,
        images=images if isinstance(images, dict) else {},
        cache_key=cache_key,
        from_cache=False,
    )


def convert_pdf_bytes_local_marker(
    pdf_bytes: bytes,
    filename: str = "upload.pdf",
    *,
    scanned: bool = False,
    job_id: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_phase: PhaseCallback | None = None,
) -> Tuple[List[Dict[str, Any]], str, LocalMarkerResult]:
    """
    Convert PDF bytes with local Marker, using disk cache keyed by file hash + page range + OCR.
    Returns (blocks, flattened_text, marker_result). Runs Marker on the marker worker thread.
    """
    if not is_local_marker_available():
        raise LocalMarkerError(
            "marker-pdf is not installed. Install with: pip install marker-pdf"
        )

    def _emit_phase(phase: str, message: str) -> None:
        if on_phase:
            on_phase(phase, message)
        if on_progress:
            on_progress(message)

    page_range = _page_range_config()
    force_ocr = _force_ocr_default(scanned)
    cache_key = build_cache_key(pdf_bytes, page_range=page_range, force_ocr=force_ocr)

    cached = load_cached_marker_result(cache_key)
    if cached:
        _emit_phase(PHASE_CACHE_HIT, "Loaded cached Marker extraction.")
        _log_marker(
            logging.INFO,
            "disk cache hit",
            job_id=job_id,
            cache_key=cache_key,
            page_range=page_range or "all",
        )
        blocks, text = _blocks_from_marker_result(cached, job_id=job_id, on_phase=on_phase)
        return blocks, text, cached

    _log_marker(
        logging.INFO,
        "disk cache miss — starting Marker",
        job_id=job_id,
        cache_key=cache_key,
        bytes=len(pdf_bytes),
        page_range=page_range or "all",
        force_ocr=force_ocr,
        scanned=scanned,
    )
    _emit_phase(PHASE_PROCESSING_MARKER, "Running local Marker PDF (this may take several minutes)…")

    timeout_sec = float(os.getenv("SOP_MARKER_TIMEOUT_SEC", "1800"))
    started = time.monotonic()
    try:
        result = _run_marker_inference(
            pdf_bytes,
            filename,
            page_range=page_range,
            force_ocr=force_ocr,
            cache_key=cache_key,
            job_id=job_id,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        _log_marker(
            logging.ERROR,
            f"Marker inference failed: {exc}",
            job_id=job_id,
            cache_key=cache_key,
            elapsed_s=round(time.monotonic() - started, 1),
        )
        raise

    _log_marker(
        logging.INFO,
        "Marker inference complete",
        job_id=job_id,
        cache_key=cache_key,
        elapsed_s=round(time.monotonic() - started, 1),
    )

    blocks, text = _blocks_from_marker_result(result, job_id=job_id, on_phase=on_phase)
    save_cached_marker_result(result, blocks)
    _log_marker(
        logging.INFO,
        "disk cache saved",
        job_id=job_id,
        cache_key=cache_key,
        block_count=len(blocks),
    )
    return blocks, text, result


def _flatten_from_blocks(blocks: List[Dict[str, Any]]) -> str:
    from .pdf_extractor import _flatten_blocks_text

    return _flatten_blocks_text(blocks)


def check_local_marker_setup() -> Dict[str, Any]:
    return {
        "enabled": is_local_marker_enabled(),
        "available": is_local_marker_available(),
        "cache_dir": str(marker_cache_dir()),
        "page_range": _page_range_config(),
        "warmup": os.getenv("SOP_MARKER_WARMUP", "true"),
    }
