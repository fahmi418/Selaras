"""
Gemini Vision API client for payslip extraction.

Design contract:
- Gemini's ONLY job is to extract text into structured JSON.
- It must NOT calculate, interpret law, or decide verdicts.
- Structured output (response_schema) is used when the SDK supports it.
- Retries: 2 exponential backoff attempts on transient errors.
- On final failure: raises GeminiExtractionError for caller to handle.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import google.generativeai as genai
from google.api_core import exceptions as gapi_exc

from app.config import get_settings
from app.slip.schema import PayslipExtraction

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Anda adalah pengekstrak data slip gaji Indonesia. Ubah gambar slip menjadi JSON sesuai skema.

ATURAN WAJIB:
1. Jangan menebak. Jika angka tidak terbaca jelas, isi null.
2. Salin label komponen persis seperti tertulis pada slip.
3. Jangan menghitung, menjumlahkan, atau menafsirkan apakah suatu komponen 'tetap' atau 'tidak tetap'.
4. Angka rupiah: buang pemisah ribuan dan koma desimal, kembalikan bilangan bulat (contoh: "5.500.000" → 5500000).
5. Jika bukan slip gaji, set is_payslip=false dan hentikan ekstraksi.
6. Berikan confidence 0.0–1.0 per field berdasarkan keterbacaan.
7. Keluarkan HANYA JSON yang valid. Tidak ada teks tambahan."""

_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_payslip": {"type": "boolean"},
        "period": {
            "type": "object",
            "properties": {
                "month": {"type": "integer"},
                "year": {"type": "integer"},
                "confidence": {"type": "number"},
            },
        },
        "employer_name": {
            "type": "object",
            "properties": {"value": {"type": "string"}, "confidence": {"type": "number"}},
        },
        "currency": {"type": "string"},
        "earnings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "amount": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "deductions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "amount": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "gross_total": {
            "type": "object",
            "properties": {"value": {"type": "integer"}, "confidence": {"type": "number"}},
        },
        "net_total": {
            "type": "object",
            "properties": {"value": {"type": "integer"}, "confidence": {"type": "number"}},
        },
        "quality": {
            "type": "object",
            "properties": {
                "legible": {"type": "boolean"},
                "skew_ok": {"type": "boolean"},
                "notes": {"type": "string"},
            },
        },
    },
    "required": ["is_payslip"],
}


class GeminiExtractionError(Exception):
    """Raised when Gemini fails after all retries."""


def _build_client() -> genai.GenerativeModel:
    settings = get_settings()
    genai.configure(api_key=settings.gemini_api_key.get_secret_value())
    return genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )


_model: genai.GenerativeModel | None = None


def _get_model() -> genai.GenerativeModel:
    global _model
    if _model is None:
        _model = _build_client()
    return _model


async def extract_payslip(jpeg_bytes: bytes) -> PayslipExtraction:
    """
    Send preprocessed JPEG to Gemini and parse the JSON response into PayslipExtraction.

    Retry policy: 2 exponential backoff retries (1s, 2s) on rate-limit or server errors.
    On final failure: raises GeminiExtractionError.
    """
    model = _get_model()
    image_part = {"mime_type": "image/jpeg", "data": jpeg_bytes}
    prompt = "Ekstrak data slip gaji dari gambar berikut sesuai skema JSON:"

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                model.generate_content,
                [prompt, image_part],
            )
            raw_text = response.text.strip()

            # Strip markdown code fences if model adds them despite instructions
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]

            data = json.loads(raw_text)
            return PayslipExtraction.model_validate(data)

        except (gapi_exc.ResourceExhausted, gapi_exc.ServiceUnavailable) as exc:
            last_exc = exc
            wait = 2**attempt
            logger.warning("Gemini transient error (attempt %d), retrying in %ds: %s", attempt + 1, wait, exc)
            await asyncio.sleep(wait)

        except (json.JSONDecodeError, Exception) as exc:
            last_exc = exc
            logger.error("Gemini extraction failed on attempt %d: %s", attempt + 1, exc)
            if attempt < 2:
                await asyncio.sleep(2**attempt)

    raise GeminiExtractionError(f"Gemini extraction failed after 3 attempts: {last_exc}") from last_exc
