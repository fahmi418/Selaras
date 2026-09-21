"""
Keyword dictionary for wage component and JKN deduction classification.
All matching is case-insensitive and stripped.

This is the ONLY place where classification rules are defined.
Rule engine reads this; LLM does not participate in classification.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# JKN deduction detection — Appendix C of PRD
# ---------------------------------------------------------------------------

_JKN_PATTERNS = re.compile(
    r"bpjs\s*kes(ehatan)?|jkn|iuran\s+kesehatan|bpjskes|pot\.?\s*kesehatan",
    re.IGNORECASE,
)

# Non-JKN deductions — explicitly excluded to avoid false positives
_NON_JKN_DEDUCTION_PATTERNS = re.compile(
    r"jht|jp\b|bpjs\s*(tk|ketenaga)|ketenagakerjaan|pph|pinjaman|kasbon",
    re.IGNORECASE,
)


def is_jkn_deduction(label: str) -> bool:
    """True if the deduction label matches known JKN contribution keywords."""
    label = label.strip()
    return bool(_JKN_PATTERNS.search(label)) and not bool(_NON_JKN_DEDUCTION_PATTERNS.search(label))


# ---------------------------------------------------------------------------
# Wage component classification
# ---------------------------------------------------------------------------

_FIXED_PATTERNS = re.compile(
    r"gaji\s*(pokok|dasar)|basic|tunj\.\s*(jabatan|keluarga|tetap|posisi)|tunjangan\s+(jabatan|keluarga|tetap|posisi)",
    re.IGNORECASE,
)

_VARIABLE_PATTERNS = re.compile(
    r"lembur|uang\s+makan|transport|bonus|insentif|thr|shift|hari\s+raya|komisi",
    re.IGNORECASE,
)

_AMBIGUOUS_PATTERNS = re.compile(
    r"tunj\.\s*(kinerja|lain|khusus|prestasi)|tunjangan\s+(kinerja|lain|khusus|prestasi)|allowance",
    re.IGNORECASE,
)

_DEDUCTION_PATTERNS = re.compile(
    r"potongan|pot\.|pph|pinjaman|kasbon|absen|bpjs|jkn|jht|jp\b",
    re.IGNORECASE,
)


class ComponentType:
    FIXED = "TETAP"
    VARIABLE = "TIDAK_TETAP"
    DEDUCTION = "POTONGAN"
    AMBIGUOUS = "AMBIGU"
    OTHER = "LAINNYA"


def classify_component(label: str) -> str:
    """
    Classify a payslip component label into ComponentType.

    Returns one of: TETAP | TIDAK_TETAP | POTONGAN | AMBIGU | LAINNYA
    """
    label = label.strip()

    if _DEDUCTION_PATTERNS.search(label):
        return ComponentType.DEDUCTION
    if _FIXED_PATTERNS.search(label):
        return ComponentType.FIXED
    if _AMBIGUOUS_PATTERNS.search(label):
        return ComponentType.AMBIGUOUS
    if _VARIABLE_PATTERNS.search(label):
        return ComponentType.VARIABLE
    return ComponentType.OTHER


# ---------------------------------------------------------------------------
# Work status keyword mapping (for P1 modus #4)
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, str] = {
    "pkwtt": "karyawan_tetap",
    "tetap": "karyawan_tetap",
    "pkwt": "kontrak",
    "kontrak": "kontrak",
    "harian lepas": "harian",
    "harian": "harian",
    "borongan": "harian",
    "mitra": "mitra",
    "freelance": "mitra",
    "outsourcing": "mitra",
}


def normalize_work_status(raw: str) -> str:
    """Map raw self-reported status string to canonical status key."""
    normalized = raw.strip().lower()
    for key, canonical in _STATUS_MAP.items():
        if key in normalized:
            return canonical
    return "tidak_tahu"
