"""
Post-extraction consistency validator.

After Gemini returns a PayslipExtraction, this module:
1. Verifies arithmetic consistency: sum(earnings) - sum(deductions) ≈ net_total
2. Checks for critical field confidence below threshold
3. Returns a ValidationResult with actionable warnings
"""

from __future__ import annotations

from app.slip.schema import PayslipExtraction, ValidationResult

_NET_TOLERANCE_PCT = 0.01   # 1% discrepancy is acceptable
_CRITICAL_CONFIDENCE_THRESHOLD = 0.70


def validate_extraction(extraction: PayslipExtraction) -> ValidationResult:
    if not extraction.is_payslip:
        return ValidationResult(
            is_consistent=False,
            sum_earnings=0,
            sum_deductions=0,
            reported_net=None,
            discrepancy_pct=None,
            warnings=["Gambar bukan slip gaji."],
        )

    warnings: list[str] = []

    sum_earnings = sum(e.amount or 0 for e in extraction.earnings)
    sum_deductions = sum(d.amount or 0 for d in extraction.deductions)
    computed_net = sum_earnings - sum_deductions

    reported_net: int | None = None
    discrepancy_pct: float | None = None

    if extraction.net_total and extraction.net_total.value is not None:
        reported_net = extraction.net_total.value
        if reported_net != 0:
            discrepancy_pct = abs(computed_net - reported_net) / abs(reported_net)
            if discrepancy_pct > _NET_TOLERANCE_PCT:
                warnings.append(
                    f"Selisih total: dihitung Rp{computed_net:,} vs slip Rp{reported_net:,} "
                    f"({discrepancy_pct:.1%}). Mungkin ada komponen yang tidak terbaca."
                )

    min_conf = extraction.min_confidence_critical()
    if min_conf < _CRITICAL_CONFIDENCE_THRESHOLD:
        warnings.append(
            f"Confidence rendah pada field kritis ({min_conf:.0%}). Harap konfirmasi angka."
        )

    if not extraction.earnings:
        warnings.append("Tidak ada komponen pendapatan yang terbaca.")

    jkn = extraction.jkn_deduction()
    if jkn is None:
        warnings.append("Potongan BPJS Kesehatan tidak ditemukan dalam slip.")

    is_consistent = (
        not any("Selisih total" in w for w in warnings)
        and bool(extraction.earnings)
    )

    return ValidationResult(
        is_consistent=is_consistent,
        sum_earnings=sum_earnings,
        sum_deductions=sum_deductions,
        reported_net=reported_net,
        discrepancy_pct=discrepancy_pct,
        warnings=warnings,
    )
