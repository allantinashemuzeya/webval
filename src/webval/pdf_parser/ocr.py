"""OCR backend abstraction.

Two interchangeable engines:

  - **tesseract** (system binary, best quality on dense text) — used when the
    binary is found on PATH or in the default Windows install locations.
  - **RapidOCR** (pure pip, ONNX runtime, no system install / no admin
    rights) — automatic fallback so `pip install` alone yields working OCR.

Selection is automatic (``pdf.ocr_backend: auto``) or forced via config.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

from webval.utils import get_logger

log = get_logger("pdf.ocr")

_TESSERACT_WINDOWS_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


@dataclass
class OcrHit:
    """A located keyword occurrence (origin of a callout box)."""

    x: int
    y: int


class OcrBackend(Protocol):
    name: str

    def text(self, img: Image.Image) -> str: ...
    def locate(self, img: Image.Image, keywords: set[str]) -> list[OcrHit]: ...


def find_tesseract() -> str | None:
    """Locate the tesseract binary: PATH first, then common Windows install dirs."""
    import shutil

    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in _TESSERACT_WINDOWS_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


class TesseractBackend:
    name = "tesseract"

    def __init__(self, binary: str) -> None:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = binary
        self._pt = pytesseract

    def text(self, img: Image.Image) -> str:
        return str(self._pt.image_to_string(img))

    def crop_text(self, img: Image.Image) -> str:
        return str(self._pt.image_to_string(img, config="--psm 6"))

    def locate(self, img: Image.Image, keywords: set[str]) -> list[OcrHit]:
        data = self._pt.image_to_data(img, config="--psm 11", output_type=self._pt.Output.DICT)
        hits: list[OcrHit] = []
        for i, word in enumerate(data["text"]):
            if (word or "").lower().strip("|(){}[]'\":;.,") in keywords:
                hits.append(OcrHit(x=data["left"][i], y=data["top"][i]))
        return hits


class RapidBackend:
    """Pure-pip fallback (rapidocr-onnxruntime). Line-level detection."""

    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    def _lines(self, img: Image.Image) -> list[tuple[str, int, int]]:
        import numpy as np

        result, _ = self._engine(np.asarray(img.convert("RGB")))
        lines: list[tuple[str, int, int]] = []
        for box, txt, _conf in result or []:
            x = int(min(pt[0] for pt in box))
            y = int(min(pt[1] for pt in box))
            lines.append((str(txt), x, y))
        lines.sort(key=lambda item: (item[2], item[1]))
        return lines

    def text(self, img: Image.Image) -> str:
        return "\n".join(txt for txt, _x, _y in self._lines(img))

    def crop_text(self, img: Image.Image) -> str:
        return self.text(img)

    def locate(self, img: Image.Image, keywords: set[str]) -> list[OcrHit]:
        hits: list[OcrHit] = []
        for txt, x, y in self._lines(img):
            tokens = {t.lower().strip("|(){}[]'\":;.,") for t in txt.split()}
            if tokens & keywords:
                hits.append(OcrHit(x=x, y=y))
        return hits


def get_backend(preference: str = "auto") -> TesseractBackend | RapidBackend | None:
    """Return the best available OCR backend, or None when none is usable."""
    if preference in ("auto", "tesseract"):
        binary = find_tesseract()
        if binary:
            try:
                return TesseractBackend(binary)
            except ImportError:
                log.warning("tesseract binary found but pytesseract not installed")
        if preference == "tesseract":
            log.warning("tesseract requested but not available")
            return None
    if preference in ("auto", "rapidocr"):
        try:
            return RapidBackend()
        except Exception as exc:  # ImportError, or onnxruntime binary/DLL issues
            log.warning(
                "RapidOCR fallback unavailable (%s) — install tesseract "
                "(Windows: winget install UB-Mannheim.TesseractOCR, no admin needed) "
                "or `pip install rapidocr-onnxruntime`.",
                exc,
            )
    return None
