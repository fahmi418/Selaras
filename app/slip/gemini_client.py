"""
Multi-Provider Vision Extraction Client (Gemini -> NVIDIA NIM -> Demo Fallback).

Design contract:
- The vision model's ONLY job is to extract text into structured JSON.
- It must NOT calculate, interpret law, or decide verdicts.
- Primary provider: Google Gemini (gemini-1.5-flash / gemini-2.5-flash).
- Secondary fallback: NVIDIA NIM (OpenAI-compatible Vision endpoint e.g. LLaMA-3.2-11B-Vision).
- Tertiary fallback: Deterministic extraction parser for offline/demo reliability.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import google.generativeai as genai
import httpx
from google.api_core import exceptions as gapi_exc

from app.config import get_settings
from app.slip.schema import (
    DeductionItem,
    EarningsItem,
    ExtractionField,
    PayslipExtraction,
    SlipPeriod,
    SlipQuality,
)

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
    """Raised when extraction fails across all AI providers."""


def _clean_json_markdown(raw_text: str) -> str:
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    return raw_text.strip()


async def _extract_gemini(jpeg_bytes: bytes, settings) -> PayslipExtraction | None:
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key or "FakeKey" in api_key:
        return None

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=settings.gemini_model,
            system_instruction=_SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        image_part = {"mime_type": "image/jpeg", "data": jpeg_bytes}
        prompt = "Ekstrak data slip gaji dari gambar berikut sesuai skema JSON:"

        response = await asyncio.to_thread(model.generate_content, [prompt, image_part])
        cleaned_json = _clean_json_markdown(response.text)
        data = json.loads(cleaned_json)
        return PayslipExtraction.model_validate(data)
    except Exception as exc:
        logger.warning("Gemini extraction failed, attempting fallback: %s", exc)
        return None


async def _extract_nvidia_nim(jpeg_bytes: bytes, settings) -> PayslipExtraction | None:
    api_key = settings.nvidia_nim_api_key.get_secret_value()
    if not api_key or "FakeKey" in api_key:
        return None

    try:
        b64_img = base64.b64encode(jpeg_bytes).decode("utf-8")
        url = f"{settings.nvidia_nim_base_url.rstrip('/')}/chat/completions"

        payload = {
            "model": settings.nvidia_nim_model,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Ekstrak data slip gaji ini menjadi format JSON murni:"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                        },
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                cleaned_json = _clean_json_markdown(content)
                data = json.loads(cleaned_json)
                return PayslipExtraction.model_validate(data)
            else:
                logger.warning("NVIDIA NIM returned status %d: %s", resp.status_code, resp.text)
                return None
    except Exception as exc:
        logger.warning("NVIDIA NIM extraction failed: %s", exc)
        return None


def _fallback_demo_extraction() -> PayslipExtraction:
    """Deterministic fallback slip for offline local / demo testing."""
    return PayslipExtraction(
        is_payslip=True,
        period=SlipPeriod(month=9, year=2024, confidence=0.98),
        employer_name=ExtractionField[str](value="PT Cipta Logistik Nusantara", confidence=0.95),
        currency="IDR",
        earnings=[
            EarningsItem(label="Gaji Pokok", amount=5_000_000, confidence=0.98),
            EarningsItem(label="Tunjangan Jabatan", amount=600_000, confidence=0.95),
            EarningsItem(label="Uang Makan Harian", amount=400_000, confidence=0.92),
        ],
        deductions=[
            DeductionItem(label="BPJS Kesehatan (1%)", amount=38_000, confidence=0.97),
            DeductionItem(label="BPJS Ketenagakerjaan", amount=120_000, confidence=0.95),
            DeductionItem(label="PPh 21", amount=75_000, confidence=0.90),
        ],
        gross_total=ExtractionField[int](value=6_000_000, confidence=0.98),
        net_total=ExtractionField[int](value=5_767_000, confidence=0.98),
        quality=SlipQuality(legible=True, skew_ok=True, notes="Fallback demo extractor"),
    )


async def extract_payslip(jpeg_bytes: bytes) -> PayslipExtraction:
    """
    Extract payslip with multi-tier provider fallback:
    Tier 1: Google Gemini Vision
    Tier 2: NVIDIA NIM Vision API
    Tier 3: Offline Demo Extractor (if in demo mode or keys unset)
    """
    settings = get_settings()

    # Tier 1: Try Gemini
    result = await _extract_gemini(jpeg_bytes, settings)
    if result is not None:
        logger.info("Extraction succeeded using provider: Google Gemini")
        return result

    # Tier 2: Try NVIDIA NIM
    result = await _extract_nvidia_nim(jpeg_bytes, settings)
    if result is not None:
        logger.info("Extraction succeeded using provider: NVIDIA NIM")
        return result

    # Tier 3: Demo fallback
    if settings.demo_mode:
        logger.info("Using deterministic Demo Extractor fallback (demo_mode=true)")
        return _fallback_demo_extraction()

    raise GeminiExtractionError("All AI Vision providers (Gemini & NVIDIA NIM) failed or are unconfigured.")
