"""
Voice-to-BAP Assistant — Horizon 2 Pilar 1.

Transkrip voice note petugas menjadi draf Berita Acara Pemeriksaan (BAP)
terstruktur. LLM ada di BELAKANG antarmuka: hasil selalu divalidasi pydantic,
tanpa nilai palsu, dan data mentah disertakan untuk review manusia.
"""

from __future__ import annotations

import json
import re
import asyncio
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.config import RulesConfig, get_rules, get_settings


class BAPNotConfigured(RuntimeError):
    """Voice-to-BAP tidak dikonfigurasi (kunci LLM/STT tidak ada & bukan demo)."""


class BAPGenerationError(RuntimeError):
    """Provider gagal atau menghasilkan JSON yang tidak valid."""


class BAPPoint(BaseModel):
    question: str
    answer: str

    @field_validator("question", "answer")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Konten BAP tidak boleh kosong")
        return value


class BAPDraft(BaseModel):
    case_id: str
    company_name: str
    transcript: str
    summary: str = ""
    points: list[BAPPoint] = Field(default_factory=list)
    provider: str

    model_config = {"extra": "ignore"}


class BAPProvider(ABC):
    """Antarmuka suara/teks → BAP. Implementasi nyata di belakang pengujian ketat."""

    name: str = "abstract"

    @abstractmethod
    async def compose(self, transcript: str, company_name: str, case_id: str) -> dict:
        """Return {summary: str, points: [{question, answer}, ...]}."""

    async def draft(
        self, *, case_id: str, company_name: str, transcript: str
    ) -> BAPDraft:
        raw = await self.compose(transcript, company_name, case_id)
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError) as exc:
            raise BAPGenerationError(f"Keluaran provider bukan JSON valid: {exc}") from exc
        payload.pop("case_id", None)
        payload.pop("company_name", None)
        payload.pop("provider", None)
        return BAPDraft(
            case_id=case_id,
            company_name=company_name,
            transcript=transcript,
            provider=self.name,
            **payload,
        )


class DemoScriptedBAPProvider(BAPProvider):
    """
    Provider deterministik untuk demo & pengujian tanpa jaringan.

    Format transkrip:
      P: pertanyaan petugas
      J: jawaban pihak perusahaan
    Baris tanpa tanda P:/J: jatuh ke ringkasan.
    """

    name = "demo-scripted"

    async def compose(self, transcript: str, company_name: str, case_id: str) -> dict:
        points: list[dict[str, str]] = []
        summary_lines: list[str] = []
        question: Optional[str] = None

        for line in transcript.splitlines():
            line = line.strip()
            if not line:
                continue
            if re.match(r"(?i)^p\s*[:.-]", line):
                if question is not None:
                    points.append({"question": question, "answer": "(tidak dijawab)"})
                question = re.sub(r"(?i)^p\s*[:.-]\s*", "", line)
            elif re.match(r"(?i)^j\s*[:.-]", line):
                answer = re.sub(r"(?i)^j\s*[:.-]\s*", "", line)
                if question is not None:
                    points.append({"question": question, "answer": answer})
                    question = None
                else:
                    summary_lines.append(line)
            else:
                summary_lines.append(line)

        if question is not None:
            points.append({"question": question, "answer": "(tidak dijawab)"})

        if not points:
            text = " ".join(summary_lines).strip() or "(transkrip kosong)"
            points = [{"question": "Ringkasan temuan pemeriksaan", "answer": text}]

        summary = (
            f"Draf BAP untuk {company_name}: {len(points)} poin dari catatan suara."
        )
        return {"summary": summary, "points": points}


class GeminiBAPProvider(BAPProvider):
    """Provider teks/JSON memakai Gemini (kunci settings.gemini_api_key)."""

    name = "gemini"

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def _model_name(self) -> str:
        return self._settings.gemini_model

    async def compose(self, transcript: str, company_name: str, case_id: str) -> dict:
        import google.generativeai as genai

        genai.configure(api_key=self._settings.gemini_api_key.get_secret_value())
        prompt = (
            "Kamu asisten pemeriksa BPJS Kesehatan. Ubah transkrip wawancara berikut "
            f"menjadi draf Berita Acara Pemeriksaan untuk perusahaan {company_name}. "
            "Balas HANYA JSON: {\"summary\": string, \"points\": [{\"question\": string, "
            "\"answer\": string}]}. Bahasa Indonesia formal, tanpa PII pekerja."
        )

        def _run() -> str:
            model = genai.GenerativeModel(self._model_name)
            response = model.generate_content(f"{prompt}\n\nTRANSKRIP:\n{transcript}")
            return response.text or ""

        text = await asyncio.to_thread(_run)
        return _parse_json_object(text)


def _parse_json_object(text: str) -> dict:
    matches = re.findall(r"\{.*\}", text, flags=re.DOTALL)
    if not matches:
        raise BAPGenerationError("Tidak ditemukan objek JSON pada keluaran provider")
    return json.loads(matches[0])


class TranscriptService:
    """Transkripsi audio catatan suara petugas (OGG/Opus) — belakang antarmuka."""

    async def transcribe(
        self, filename: str, audio: bytes, rules: RulesConfig | None = None
    ) -> str:
        rules = rules or get_rules()
        mode = str(rules.briefing.get("transcript_provider", "auto"))
        if mode == "off":
            raise BAPNotConfigured("Konfigurasi briefing.transcript_provider dinonaktifkan")

        settings = get_settings()
        if settings.gemini_api_key.get_secret_value():
            return await self._transcribe_with_gemini(filename, audio)

        if settings.demo_mode and audio:
            from base64 import b64decode

            return _DEMO_TRANSCRIPT
        raise BAPNotConfigured("Tidak ada penyedia transkripsi; atur GEMINI_API_KEY")

    async def _transcribe_with_gemini(self, filename: str, audio: bytes) -> str:
        import google.generativeai as genai

        settings = get_settings()
        genai.configure(api_key=settings.gemini_api_key.get_secret_value())
        part = {"mime_type": "audio/ogg", "data": audio}

        def _run() -> str:
            model = genai.GenerativeModel(settings.gemini_model)
            response = model.generate_content(
                ["Transkripsikan rekaman wawancara audit berikut secara verbatim.", part]
            )
            return response.text or ""

        return await asyncio.to_thread(_run)


_DEMO_TRANSCRIPT = (
    "P: Berapa jumlah pekerja aktif di lapangan saat ini?\n"
    "J: Sebanyak 42 orang sesuai daftar hadir bulan ini.\n"
    "P: Bukti setoran iuran 3 bulan terakhir mana?\n"
    "J: Akan dikirimkan besok setelah dikonfirmasi bagian keuangan."
)


def resolve_bap_provider(rules: RulesConfig | None = None) -> BAPProvider:
    """Pilih provider sesuai konfigurasi & kunci lingkungan; deterministik."""
    rules = rules or get_rules()
    mode = str(rules.briefing.get("transcript_provider", "auto"))
    if mode == "off":
        raise BAPNotConfigured("Voice-to-BAP dinonaktifkan lewat konfigurasi")

    settings = get_settings()
    if settings.gemini_api_key.get_secret_value():
        return GeminiBAPProvider()
    if settings.demo_mode:
        return DemoScriptedBAPProvider()
    raise BAPNotConfigured("Voice-to-BAP butuh GEMINI_API_KEY atau DEMO_MODE=true")


async def generate_bap(
    *,
    case_id: str,
    company_name: str,
    transcript: str,
    rules: RulesConfig | None = None,
) -> BAPDraft:
    """Susun draf BAP dari transkrip (atau transkipsi audio di sisi pemanggil)."""
    transcript = (transcript or "").strip()
    if not transcript:
        raise BAPGenerationError("Transkrip kosong; tidak ada yang bisa disusun")
    provider = resolve_bap_provider(rules)
    return await provider.draft(case_id=case_id, company_name=company_name, transcript=transcript)
