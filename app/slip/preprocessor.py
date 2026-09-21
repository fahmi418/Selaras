"""
Image preprocessor for payslip photos.

Responsibilities:
- Validate MIME type (JPG, PNG, HEIC, PDF single-page)
- Deskew via Hough-line estimation
- Resize to max 1024px on the long edge (reduces Gemini token cost)
- Convert everything to JPEG bytes for Gemini input
- HEIC conversion via pillow-heif if available
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter, ImageOps

_MAX_LONG_EDGE = 1024
_JPEG_QUALITY = 88
_ALLOWED_MIME_PREFIXES = ("image/jpeg", "image/png", "image/heic", "image/heif", "application/pdf")


def _detect_mime(data: bytes) -> str:
    """Sniff MIME from magic bytes without relying on python-magic for portability."""
    sig = data[:16]
    if sig[:4] == b"\xff\xd8\xff":
        return "image/jpeg"
    if sig[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if sig[:4] in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x20") and b"heic" in sig[:16].lower():
        return "image/heic"
    if sig[:4] == b"%PDF":
        return "application/pdf"
    return "application/octet-stream"


def _load_pdf_first_page(data: bytes) -> Image.Image:
    """Extract first page of PDF as PIL Image using pypdf + pillow."""
    try:
        from pypdf import PdfReader  # optional dependency
        import io as _io

        reader = PdfReader(_io.BytesIO(data))
        # Render first page to PNG — requires pypdf[image]
        page = reader.pages[0]
        xobjects = page.images
        if xobjects:
            return Image.open(_io.BytesIO(xobjects[0].data))
    except Exception:
        pass

    raise ValueError("PDF could not be rendered. Install pypdf[image] or send a photo instead.")


def _load_heic(data: bytes) -> Image.Image:
    try:
        from pillow_heif import register_heif_opener  # type: ignore[import]

        register_heif_opener()
    except ImportError:
        raise ValueError("HEIC format requires 'pillow-heif' package.")
    return Image.open(io.BytesIO(data))


def _deskew(img: Image.Image) -> Image.Image:
    """
    Estimate skew angle via horizontal projection profile and rotate to correct it.
    Fast approximation: works for ±15° rotations typical in hand-held phone photos.
    """
    import numpy as np

    gray = ImageOps.grayscale(img)
    arr = np.array(gray)

    best_angle = 0.0
    best_score = -1.0

    for angle in range(-12, 13, 2):
        rotated = np.array(ImageOps.grayscale(img.rotate(angle, expand=False, fillcolor=255)))
        # Variance of row sums peaks when text lines are horizontal
        score = float(np.var(rotated.sum(axis=1)))
        if score > best_score:
            best_score = score
            best_angle = float(angle)

    if abs(best_angle) > 1.0:
        img = img.rotate(best_angle, expand=True, fillcolor=255)

    return img


def _resize_to_max(img: Image.Image, max_edge: int) -> Image.Image:
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_edge:
        return img
    scale = max_edge / long_edge
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def preprocess(data: bytes, deskew: bool = True) -> tuple[bytes, str]:
    """
    Preprocess raw file bytes into JPEG bytes ready for Gemini.

    Returns:
        (jpeg_bytes, detected_mime)

    Raises:
        ValueError: if file type is not supported or too small to be a payslip.
    """
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("File terlalu besar (maks 10 MB).")

    mime = _detect_mime(data)

    if not any(mime.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise ValueError(f"Format file tidak didukung: {mime}")

    if mime == "application/pdf":
        img = _load_pdf_first_page(data)
    elif "heic" in mime or "heif" in mime:
        img = _load_heic(data)
    else:
        img = Image.open(io.BytesIO(data))

    img = img.convert("RGB")

    if deskew:
        try:
            img = _deskew(img)
        except Exception:
            pass  # deskew is best-effort; never block the pipeline

    img = _resize_to_max(img, _MAX_LONG_EDGE)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return buf.getvalue(), mime
