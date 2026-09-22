"""PP-StructureV3 table-structure recovery runner.

Chandra flattens complex 2-D task-organization tables into vertical lists with
no ``<table>`` markup (validated blind spot; see CHANDRA_OCR_DESIGN.md and
markdown_structure.detect_flattened_tables). A PoC (2026-09-22) established that
PaddleOCR's **PP-StructureV3** recovers the 2-D grid Chandra drops, and that the
**server** text models materially out-transcribe the mobile ones on this content
(``TROOP ASSIGNMENTS`` / ``489`` / ``Hq&HqBtry`` read correctly where mobile
garbled them). This module captures that validated configuration as an in-repo,
version-controlled runner so the capability lives in the tool rather than a
throwaway script.

Design points carried from the PoC:

* **Server text models** (``PP-OCRv5_server_det`` / ``PP-OCRv5_server_rec``) for
  accuracy; ``text_det_limit_side_len`` bounds detection input so the server det
  model does not attempt a multi-GB allocation on a full 300-DPI render.
* **oneDNN disabled** to avoid a PIR-attribute conversion bug in the paddlepaddle
  3.3.x MKL-DNN CPU kernels (``onednn_instruction.cc``). Harmless on GPU.
* **GPU when available, CPU fallback.** On the project's A10G Batch instances the
  full render can be used; on CPU the side-length cap keeps it in RAM.
* **Formula recognition off** — irrelevant to these tables, saves a model load.

PaddleOCR is an **optional dependency**: it is heavy (paddlepaddle + paddlex +
model weights) and only needed on the OCR worker, not for the rest of the
pipeline. This module therefore imports it lazily and exposes
:func:`is_available` so callers (and tests) can skip gracefully when it is not
installed. Nothing here imports paddle at module load time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# --- Validated PoC configuration (see module docstring) ------------------
SERVER_DET_MODEL = "PP-OCRv5_server_det"
SERVER_REC_MODEL = "PP-OCRv5_server_rec"
# Cap the detection input side. On CPU the full 2550px render made the server
# det model attempt a ~43GB alloc; 2048 is the largest that fit in the PoC box
# while keeping server-model quality. On GPU this can be raised.
DEFAULT_DET_LIMIT = 2048


def is_available() -> bool:
    """Return True if PaddleOCR (PP-StructureV3) can be imported.

    Cheap probe used by callers/tests to skip gracefully when the optional
    dependency is absent. Does not construct any pipeline or load weights.
    """
    try:
        import paddleocr  # noqa: F401  pylint: disable=import-error,unused-import
    except Exception:  # pragma: no cover - import failure is the signal
        return False
    return True


@dataclass
class RecoveredTable:
    """A table recovered by PP-StructureV3 from a page image.

    Attributes:
        html: The recovered ``<table>`` HTML (empty string if none found).
        markdown: PP-StructureV3's full markdown for the page (tables as HTML).
        table_count: Number of ``<table>`` blocks recovered.
        engine: Identifier of the engine + model tier used, for provenance.
        det_limit_side_len: The detection side-length cap actually used.
        device: "gpu" or "cpu" — the device the run used.
        notes: Free text (e.g. why empty).
    """

    html: str = ""
    markdown: str = ""
    table_count: int = 0
    engine: str = "ppstructurev3-server"
    det_limit_side_len: int = DEFAULT_DET_LIMIT
    device: str = "cpu"
    notes: str = ""


def _select_device(prefer_gpu: bool) -> str:
    """Return 'gpu' when a CUDA device is usable and preferred, else 'cpu'."""
    if not prefer_gpu:
        return "cpu"
    try:
        import paddle  # type: ignore[import-not-found]  # pylint: disable=import-error

        if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count():
            return "gpu"
    except Exception:  # pragma: no cover - absence/again means CPU
        pass
    return "cpu"


class PaddleStructureRunner:
    """Lazily-constructed PP-StructureV3 runner (server-model config).

    The heavy pipeline (models + weights) is built on first use, not at
    construction, so importing/constructing this class is cheap and safe even
    where PaddleOCR is not installed. Call :meth:`recover` with a page image.
    """

    def __init__(
        self,
        *,
        prefer_gpu: bool = True,
        det_limit_side_len: int = DEFAULT_DET_LIMIT,
    ) -> None:
        self._det_limit = det_limit_side_len
        self._device = _select_device(prefer_gpu)
        self._pipeline: Optional[Any] = None

    @property
    def device(self) -> str:
        """The device this runner will use ('gpu' or 'cpu')."""
        return self._device

    def _build_pipeline(self) -> Any:
        """Construct the PP-StructureV3 pipeline with the validated config."""
        from paddleocr import PPStructureV3  # pylint: disable=import-error

        # enable_mkldnn=False only matters on CPU (avoids the paddlepaddle 3.3.x
        # MKL-DNN PIR-attribute bug); harmless on GPU.
        pipeline = PPStructureV3(
            device=self._device,
            enable_mkldnn=False,
            text_detection_model_name=SERVER_DET_MODEL,
            text_recognition_model_name=SERVER_REC_MODEL,
            text_det_limit_side_len=self._det_limit,
            text_det_limit_type="max",
            use_formula_recognition=False,
        )
        logger.info(
            "Built PP-StructureV3 (device=%s, det_limit=%d, server models)",
            self._device,
            self._det_limit,
        )
        return pipeline

    def _ensure_pipeline(self) -> Any:
        if self._pipeline is None:
            self._pipeline = self._build_pipeline()
        return self._pipeline

    def recover(self, image_path: Path) -> RecoveredTable:
        """Run PP-StructureV3 on a page image and return recovered table(s).

        Args:
            image_path: Path to a rendered page image (PNG/JPG). Callers render
                the PDF page (e.g. at 300 DPI) before invoking.

        Returns:
            A :class:`RecoveredTable`. On any failure or when PaddleOCR is not
            installed, returns an empty result with a note rather than raising,
            so the caller can fall back to Chandra's (flagged) output.
        """
        if not is_available():
            return RecoveredTable(
                det_limit_side_len=self._det_limit,
                device=self._device,
                notes="PaddleOCR not installed; recovery skipped",
            )
        try:
            pipeline = self._ensure_pipeline()
            results = pipeline.predict(str(image_path))
        except Exception as exc:  # pragma: no cover - runtime/model failure
            logger.warning("PP-StructureV3 recovery failed on %s: %s", image_path, exc)
            return RecoveredTable(
                det_limit_side_len=self._det_limit,
                device=self._device,
                notes=f"recovery failed: {exc}",
            )

        markdown_parts: List[str] = []
        for res in results:
            md = _result_markdown(res)
            if md:
                markdown_parts.append(md)
        markdown = "\n\n".join(markdown_parts).strip()
        tables = _extract_tables(markdown)
        return RecoveredTable(
            html="\n\n".join(tables),
            markdown=markdown,
            table_count=len(tables),
            det_limit_side_len=self._det_limit,
            device=self._device,
            notes="recovered" if tables else "no <table> recovered",
        )


def _result_markdown(res: Any) -> str:
    """Best-effort extraction of markdown text from a PP-StructureV3 result.

    PaddleOCR result objects expose markdown differently across versions; try
    the known accessors and degrade to "" rather than raising.
    """
    md = getattr(res, "markdown", None)
    if isinstance(md, str):
        return md
    if isinstance(md, dict):  # some versions: {"markdown_texts": "..."}
        text = md.get("markdown_texts") or md.get("text")
        if isinstance(text, str):
            return text
    # Fallback: some versions expose .json with a markdown field.
    data = getattr(res, "json", None)
    if isinstance(data, dict):
        text = data.get("markdown") or data.get("markdown_texts")
        if isinstance(text, str):
            return text
    return ""


def _extract_tables(markdown: str) -> List[str]:
    """Return the ``<table>...</table>`` blocks found in markdown, in order."""
    if "<table" not in markdown:
        return []
    tables: List[str] = []
    lower = markdown.lower()
    start = 0
    while True:
        open_at = lower.find("<table", start)
        if open_at == -1:
            break
        close_at = lower.find("</table>", open_at)
        if close_at == -1:
            break
        end = close_at + len("</table>")
        tables.append(markdown[open_at:end])
        start = end
    return tables
