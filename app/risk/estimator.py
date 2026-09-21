"""
Iuran gap estimator — PRD §11.4.

Computes estimated monthly iuran loss (low/mid/high range) per modus.
All formulas are deterministic from PRD rules; no LLM involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class IuranEstimate:
    modus: int
    est_low: int    # konservatif: p25 upah, pekerja terdampak minimum
    est_mid: int    # nilai tengah
    est_high: int   # optimistik: p75 upah, pekerja tengah
    assumptions: str


_TOTAL_RATE = 0.05
_EMPLOYER_RATE = 0.04


def estimate_modus1(
    est_headcount: int,
    registered_headcount: int,
    median_wage: int,
    p25_wage: int,
    p75_wage: int,
) -> IuranEstimate:
    """
    Modus #1 — Pendaftaran Pekerja Sebagian.
    est_bulanan = 5% × upah_median_sektor × (est_workers − registered_workers)
    """
    gap_workers = max(0, est_headcount - registered_headcount)
    low = round(_TOTAL_RATE * p25_wage * gap_workers)
    mid = round(_TOTAL_RATE * median_wage * gap_workers)
    high = round(_TOTAL_RATE * p75_wage * gap_workers)

    return IuranEstimate(
        modus=1,
        est_low=low,
        est_mid=mid,
        est_high=high,
        assumptions=f"Gap pekerja: {gap_workers} orang; upah sektor p25/median/p75",
    )


def estimate_modus2(
    actual_wage: int,
    reported_wage: int,
    n_affected_workers: int,
    p25_wage: Optional[int] = None,
    p75_wage: Optional[int] = None,
) -> IuranEstimate:
    """
    Modus #2 — Under-reporting Upah.
    gap_per_pekerja = 5% × max(0, wage_actual − wage_reported)
    """
    wage_gap = max(0, actual_wage - reported_wage)
    base_mid = round(_TOTAL_RATE * wage_gap * n_affected_workers)

    p25 = p25_wage or round(wage_gap * 0.7)
    p75 = p75_wage or round(wage_gap * 1.3)

    low = round(_TOTAL_RATE * p25 * max(1, round(n_affected_workers * 0.7)))
    high = round(_TOTAL_RATE * p75 * round(n_affected_workers * 1.3))

    return IuranEstimate(
        modus=2,
        est_low=low,
        est_mid=base_mid,
        est_high=high,
        assumptions=(
            f"Selisih upah: Rp{wage_gap:,}; pekerja terdampak estimasi: {n_affected_workers}"
        ),
    )


def estimate_modus3(
    wage_base: int,
    n_workers: int,
    months_unpaid: int,
) -> IuranEstimate:
    """
    Modus #3 — Penggelapan Iuran.
    est = 5% × upah_dasar × n_pekerja × bulan_tak_setor
    """
    monthly = round(_TOTAL_RATE * wage_base * n_workers)
    mid = monthly * months_unpaid
    low = round(mid * 0.7)
    high = round(mid * 1.4)

    return IuranEstimate(
        modus=3,
        est_low=low,
        est_mid=mid,
        est_high=high,
        assumptions=f"{n_workers} pekerja × {months_unpaid} bulan tidak disetor",
    )


def estimate_modus4(
    wage_base: int,
    n_misclassified_workers: int,
) -> IuranEstimate:
    """
    Modus #4 — Misklasifikasi Status Kerja.
    Hanya porsi pemberi kerja 4% yang terhindar.
    """
    mid = round(_EMPLOYER_RATE * wage_base * n_misclassified_workers)
    low = round(mid * 0.6)
    high = round(mid * 1.5)

    return IuranEstimate(
        modus=4,
        est_low=low,
        est_mid=mid,
        est_high=high,
        assumptions=f"{n_misclassified_workers} pekerja terindikasi misklasifikasi (porsi PK 4%)",
    )


def estimate_modus5(
    wage_base: int,
    n_late_workers: int,
    months_delay: float,
) -> IuranEstimate:
    """
    Modus #5 — Manipulasi Data Mutasi.
    est = 5% × upah × n_pekerja_terlambat × bulan_keterlambatan
    """
    mid = round(_TOTAL_RATE * wage_base * n_late_workers * months_delay)
    low = round(mid * 0.6)
    high = round(mid * 1.5)

    return IuranEstimate(
        modus=5,
        est_low=low,
        est_mid=mid,
        est_high=high,
        assumptions=f"{n_late_workers} pekerja × {months_delay:.1f} bulan keterlambatan",
    )


def format_estimate_idr(estimate: IuranEstimate) -> str:
    """Format estimate range as human-readable IDR string for display."""
    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"Rp{n / 1_000_000:.1f} jt"
        if n >= 1_000:
            return f"Rp{n // 1_000} rb"
        return f"Rp{n:,}"

    return (
        f"{_fmt(estimate.est_low)} – {_fmt(estimate.est_high)} per bulan "
        f"(estimasi; asumsi: {estimate.assumptions})"
    )
