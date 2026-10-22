"""
Multimodal Voice PANDAWA — Horizon 3 Pilar 2.

Voice note pekerja → kategori keluhan (deterministik lewat kamus rules.yaml)
→ rekonsiliasi dengan vonis slip → laporan tersimpan TANPA PII
(angka panjang dimasukkan placeholder sebelum disimpan).
"""

from __future__ import annotations

import re
from typing import Optional

from app.config import RulesConfig, get_rules


def redact(text: str, rules: RulesConfig | None = None) -> str:
    """Tenggelamkan angka panjang & bentuk NIK/NPP/kontak — nol-PII retention."""
    rules = rules or get_rules()
    if not bool(rules.voice.get("redact_digits", True)):
        return text
    text = re.sub(r"\d{6,}", "[NUMBER]", text)
    text = re.sub(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{3,}\b", "[NUMBER]", text)  # NPP-likes
    text = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "[EMAIL]", text)
    return text


def clip_transcript(text: str, rules: RulesConfig | None = None) -> str:
    rules = rules or get_rules()
    limit = int(rules.voice["max_transcript_chars"])
    return text[:limit]


def classify_complaint(
    transcript: str, rules: RulesConfig | None = None
) -> tuple[str, list[str]]:
    """(kode kategori W1/W3/W4/OTHER, kata kunci yang mendekan) — kecocokan terbanyak menang."""
    rules = rules or get_rules()
    catalog: dict[str, list[str]] = rules.voice["complaint_keywords"]
    lowered = transcript.lower()

    scores: dict[str, tuple[int, list[str]]] = {}
    for code, keywords in catalog.items():
        hits = [kw for kw in keywords if kw.lower() in lowered]
        if hits:
            scores[code] = (len(hits), hits)

    if not scores:
        return "OTHER", []

    best_code = max(scores, key=lambda code: (scores[code][0], code))
    return best_code, scores[best_code][1]


def reconcile_with_slip(
    category: str, verdict_codes: list[str], rules: RulesConfig | None = None
) -> list[str]:
    """
    Verifikasi silang teks-vs-aris: selaras/berlawanan keluhan dengan vonis slip.
    Mengembalikan daftar nota singkat untuk petugas (tanpa LLM).
    """
    rules = rules or get_rules()
    notes: list[str] = []

    category_code = category if category in {"W1", "W3", "W4"} else None
    if category_code is None:
        return notes

    notes.append(f"Keluhan produk dari kategori {category_code}.")

    if category == "W4" and "DEDUCTION_MISSING" in verdict_codes:
        notes.append("Keluhan 'tidak disetor' selaras vonis DEDUCTION_MISSING.")
    elif category == "W1" and any(code.startswith("DEDUCTION") for code in verdict_codes):
        notes.append("Keluhan potongan selaras vonis potongan slip.")
    else:
        notes.append(
            f"Keluhan kategori {category_code} namun vonis slip belum mendukung — perlu cek manusia."
        )
    return notes
