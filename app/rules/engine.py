"""
Deterministic rule engine for JKN contribution verification.

This module implements the decision tree from PRD §9.4 using only Python arithmetic.
NO LLM calls here. Every verdict is reproducible given the same inputs.

Verdict codes (PRD §10.1):
    OK                  — all aligned
    DEDUCTION_LOW       — slip deduction < expected (W1)
    DEDUCTION_MISSING   — no JKN deduction found (W2)
    DEDUCTION_HIGH      — slip deduction > expected (informational)
    WAGE_BASE_MISMATCH  — implied wage ≠ BPJS-recorded wage (W3)
    NOT_REMITTED        — deduction present but no payment record (W4)
    NOT_REGISTERED      — worker not found in company enrollment (W5)
    BELOW_UMK           — computed wage base < UMK (W6, informational)
    NEEDS_REVIEW        — confidence too low or ambiguous components
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.config import RulesConfig, get_rules
from app.rules.dictionary import ComponentType, classify_component, is_jkn_deduction
from app.slip.schema import PayslipExtraction


# ---------------------------------------------------------------------------
# Verdict constants
# ---------------------------------------------------------------------------

class Verdict:
    OK = "OK"
    DEDUCTION_LOW = "DEDUCTION_LOW"
    DEDUCTION_MISSING = "DEDUCTION_MISSING"
    DEDUCTION_HIGH = "DEDUCTION_HIGH"
    WAGE_BASE_MISMATCH = "WAGE_BASE_MISMATCH"
    NOT_REMITTED = "NOT_REMITTED"
    NOT_REGISTERED = "NOT_REGISTERED"
    BELOW_UMK = "BELOW_UMK"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class BPJSRecord:
    """Synthetic BPJS enrollment/billing data for one worker+company+period."""

    recorded_wage_base: Optional[int]   # upah dasar tercatat (None = not found)
    is_remitted: Optional[bool]         # setoran bulan ini tercatat?
    is_registered: bool                 # pekerja terdaftar di kepesertaan perusahaan?


@dataclass
class VerdictResult:
    codes: list[str] = field(default_factory=list)
    expected_deduction: Optional[int] = None
    actual_deduction: Optional[int] = None
    implied_wage_base: Optional[int] = None
    wage_base_used: Optional[int] = None        # after clamp
    explanation: str = ""
    confidence: str = "high"                    # high | medium | low
    rule_version: str = ""
    has_ambiguous_components: bool = False
    ambiguous_labels: list[str] = field(default_factory=list)

    def is_clean(self) -> bool:
        return self.codes == [Verdict.OK]

    def primary_verdict(self) -> str:
        return self.codes[0] if self.codes else Verdict.NEEDS_REVIEW


# ---------------------------------------------------------------------------
# Core computation helpers
# ---------------------------------------------------------------------------

_CRITICAL_CONFIDENCE = 0.70


def _compute_wage_base(extraction: PayslipExtraction, umk: int, rules: RulesConfig) -> tuple[int, list[str]]:
    """
    Sum gaji_pokok + tunjangan_tetap from extraction, clamped to [UMK, wage_cap].
    Returns (clamped_base, list_of_ambiguous_labels).

    Variable components (lembur, uang makan, etc.) are excluded per JKN regulation.
    Ambiguous components generate a NEEDS_REVIEW signal to ask the worker.
    """
    fixed_sum = 0
    ambiguous: list[str] = []

    for item in extraction.earnings:
        kind = classify_component(item.label)
        amount = item.amount or 0
        if kind == ComponentType.FIXED:
            fixed_sum += amount
        elif kind == ComponentType.AMBIGUOUS:
            ambiguous.append(item.label)
        # VARIABLE and OTHER are excluded from wage base

    capped = min(max(fixed_sum, umk), rules.contribution.wage_cap)
    return capped, ambiguous


def _expected_deduction(wage_base: int, rules: RulesConfig) -> int:
    return round(rules.contribution.worker_rate * wage_base)


def _implied_wage_from_deduction(actual_deduction: int, rules: RulesConfig) -> int:
    """Back-calculate wage base from the deduction amount."""
    return round(actual_deduction / rules.contribution.worker_rate)


# ---------------------------------------------------------------------------
# Main verdict function
# ---------------------------------------------------------------------------

def verdict_slip(
    extraction: PayslipExtraction,
    bpjs: BPJSRecord,
    umk: int,
    rules: Optional[RulesConfig] = None,
) -> VerdictResult:
    """
    Apply the PRD §9.4 decision tree and return a VerdictResult.

    Args:
        extraction: Parsed Gemini output (Pydantic model).
        bpjs:       Synthetic BPJS enrollment/billing data for this worker.
        umk:        Upah Minimum Kabupaten/Kota for the worker's region (Rp).
        rules:      RulesConfig; defaults to singleton from config.

    This function performs ZERO LLM calls. It is fully deterministic.
    """
    if rules is None:
        rules = get_rules()

    result = VerdictResult(rule_version=rules.version)

    # Step 1: Was extraction valid?
    if not extraction.is_payslip:
        result.codes = [Verdict.NEEDS_REVIEW]
        result.explanation = "Gambar bukan slip gaji."
        result.confidence = "low"
        return result

    min_conf = extraction.min_confidence_critical()
    if min_conf < _CRITICAL_CONFIDENCE:
        result.codes = [Verdict.NEEDS_REVIEW]
        result.explanation = f"Keterbacaan gambar rendah (confidence {min_conf:.0%}). Harap kirim ulang foto yang lebih jelas."
        result.confidence = "low"
        return result

    # Step 2: Is JKN deduction present in slip?
    jkn_item = extraction.jkn_deduction()
    actual_deduction = jkn_item.amount if (jkn_item and jkn_item.amount is not None) else None

    if actual_deduction is None:
        result.codes = [Verdict.DEDUCTION_MISSING]
        result.explanation = "Potongan BPJS Kesehatan tidak ditemukan di slip."
        result.confidence = "high"
        return result

    result.actual_deduction = actual_deduction

    # Step 3: Compute expected deduction
    wage_base, ambiguous = _compute_wage_base(extraction, umk, rules)
    result.wage_base_used = wage_base
    result.has_ambiguous_components = bool(ambiguous)
    result.ambiguous_labels = ambiguous

    # Compute the raw (unclamped) wage base to check if it's below UMK
    raw_fixed_sum = sum(
        (item.amount or 0)
        for item in extraction.earnings
        if classify_component(item.label) == ComponentType.FIXED
    )

    # Ambiguous components → two-scenario NEEDS_REVIEW (ask worker)
    if ambiguous:
        result.codes = [Verdict.NEEDS_REVIEW]
        labels_str = ", ".join(f'"{l}"' for l in ambiguous)
        result.explanation = (
            f"Terdapat komponen yang belum jelas: {labels_str}. "
            "Apakah tunjangan tersebut dibayar tetap setiap bulan?"
        )
        result.confidence = "medium"
        return result

    expected = _expected_deduction(wage_base, rules)
    result.expected_deduction = expected

    implied_base = _implied_wage_from_deduction(actual_deduction, rules)
    result.implied_wage_base = implied_base

    codes: list[str] = []

    # BELOW_UMK — fires when actual salary is below UMK (informational)
    if raw_fixed_sum < umk:
        codes.append(Verdict.BELOW_UMK)

    # Step 4: Deduction amount check
    if not rules.tolerance.is_within(actual_deduction, expected):
        if actual_deduction < expected:
            codes.append(Verdict.DEDUCTION_LOW)
        else:
            codes.append(Verdict.DEDUCTION_HIGH)

    # Step 5: BPJS record matching
    if not bpjs.is_registered:
        codes.append(Verdict.NOT_REGISTERED)
    else:
        # Wage base mismatch
        if bpjs.recorded_wage_base is not None:
            if not rules.tolerance.is_within(bpjs.recorded_wage_base, wage_base):
                codes.append(Verdict.WAGE_BASE_MISMATCH)

        # Not remitted
        if bpjs.is_remitted is False:
            codes.append(Verdict.NOT_REMITTED)

    if not codes:
        codes = [Verdict.OK]

    result.codes = codes
    result.confidence = "high"
    result.explanation = _build_explanation(result, umk)
    return result


def _build_explanation(r: VerdictResult, umk: int) -> str:
    """Generate a plain-language Indonesian explanation of the verdict."""
    if r.codes == [Verdict.OK]:
        return (
            f"Potongan JKN Anda sesuai (upah dasar Rp{r.wage_base_used:,} × 1% = "
            f"Rp{r.expected_deduction:,}). Setoran tercatat."
        )

    parts = []
    if Verdict.DEDUCTION_LOW in r.codes:
        parts.append(
            f"Potongan di slip (Rp{r.actual_deduction:,}) lebih kecil dari seharusnya "
            f"(Rp{r.expected_deduction:,} untuk upah dasar Rp{r.wage_base_used:,})."
        )
    if Verdict.DEDUCTION_MISSING in r.codes:
        parts.append("Tidak ada potongan BPJS Kesehatan di slip.")
    if Verdict.WAGE_BASE_MISMATCH in r.codes:
        parts.append(
            f"Upah dasar yang tercatat di BPJS mungkin berbeda dari yang sebenarnya "
            f"(upah tersirat dari slip: Rp{r.implied_wage_base:,})."
        )
    if Verdict.NOT_REMITTED in r.codes:
        parts.append("Setoran iuran bulan ini belum tercatat di BPJS.")
    if Verdict.NOT_REGISTERED in r.codes:
        parts.append("Anda tampaknya belum terdaftar sebagai peserta di perusahaan ini.")
    if Verdict.BELOW_UMK in r.codes:
        parts.append(f"Upah dasar terindikasi di bawah UMK (Rp{umk:,}).")
    return " ".join(parts)
