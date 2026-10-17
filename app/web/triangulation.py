"""
Triangulation table for the officer case page.

Every figure comes from stored data: BPJS enrollment (reported wage), the latest worker slip check
(wage base the slip implies), and the regional benchmark. A figure that has no source is None and is
rendered as "–". Nothing here estimates or pads missing data.
"""

from __future__ import annotations

from typing import Optional

from app.config import RulesConfig

MISSING = "–"


def format_idr(val: int | float | None) -> str:
    if val is None:
        return MISSING
    return f"Rp {int(val):,}".replace(",", ".")


def build_triangulation(
    *,
    reported_wage: Optional[float],
    slip_expected_deduction: Optional[int],
    slip_actual_deduction: Optional[int],
    benchmark_wage: Optional[int],
    rules: RulesConfig,
) -> dict:
    worker_rate = rules.contribution.worker_rate
    employer_rate = rules.contribution.employer_rate

    reported = round(reported_wage) if reported_wage else None
    # expected_deduction = worker_rate x wage base computed from the slip's fixed earnings
    slip_wage = round(slip_expected_deduction / worker_rate) if slip_expected_deduction else None

    def share(wage: Optional[int], rate: float) -> Optional[int]:
        return round(wage * rate) if wage is not None else None

    gap = None
    compliance_pct = None
    if reported is not None and slip_wage is not None:
        gap = max(0, round((slip_wage - reported) * (worker_rate + employer_rate)))
        compliance_pct = min(100, max(0, round(reported / slip_wage * 100)))

    return {
        "has_slip": slip_wage is not None,
        "reported_wage_fmt": format_idr(reported),
        "slip_wage_fmt": format_idr(slip_wage),
        "benchmark_wage_fmt": format_idr(benchmark_wage),
        "worker_rep_fmt": format_idr(share(reported, worker_rate)),
        "worker_slip_fmt": format_idr(share(slip_wage, worker_rate)),
        "employer_rep_fmt": format_idr(share(reported, employer_rate)),
        "employer_slip_fmt": format_idr(share(slip_wage, employer_rate)),
        "slip_actual_deduction_fmt": format_idr(slip_actual_deduction) if slip_actual_deduction else None,
        "slip_higher": slip_wage is not None and reported is not None and slip_wage > reported,
        "gap": gap,
        "gap_fmt": format_idr(gap) if gap is not None else None,
        "compliance_pct": compliance_pct,
    }
