"""
Estimator selisih iuran — PRD §11.4.

Hitung perkiraan kerugian iuran bulanan (low/mid/high) per modus fraud.
Seluruh tarif dan faktor pembatas dibaca dari rules.yaml — tidak ada konstanta rahasia.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import RulesConfig, get_rules


@dataclass
class IuranEstimate:
    modus: int
    est_low: int    # konservatif: p25 upah, pekerja terdampak minimum
    est_mid: int    # nilai tengah
    est_high: int   # optimistik: p75 upah, pekerja tengah
    assumptions: str


def _rates(rules: RulesConfig) -> tuple[float, float]:
    return float(rules.contribution.total_rate), float(rules.contribution.employer_rate)


def estimate_modus1(
    est_headcount: int,
    registered_headcount: int,
    median_wage: int,
    p25_wage: int,
    p75_wage: int,
    rules: RulesConfig | None = None,
) -> IuranEstimate:
    """Modus #1 — Pendaftaran Pekerja Sebagian: 5% × upah sektor × gap pekerja."""
    rules = rules or get_rules()
    total_rate, _ = _rates(rules)
    gap_workers = max(0, est_headcount - registered_headcount)
    low = round(total_rate * p25_wage * gap_workers)
    mid = round(total_rate * median_wage * gap_workers)
    high = round(total_rate * p75_wage * gap_workers)
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
    rules: RulesConfig | None = None,
    p25_wage: int | None = None,
    p75_wage: int | None = None,
) -> IuranEstimate:
    """Modus #2 — Under-reporting Upah: 5% × selisih upah × pekerja terdampak."""
    rules = rules or get_rules()
    est_cfg = rules.estimator
    total_rate, _ = _rates(rules)
    wage_gap = max(0, actual_wage - reported_wage)
    base_mid = round(total_rate * wage_gap * max(0, n_affected_workers))

    p25 = p25_wage or round(wage_gap * float(est_cfg["wage_gap_low_share"]))
    p75 = p75_wage or round(wage_gap * float(est_cfg["wage_gap_high_share"]))
    low = round(
        total_rate
        * p25
        * max(1, round(max(0, n_affected_workers) * float(est_cfg["wage_gap_low_share"])))
    )
    high = round(
        total_rate
        * p75
        * round(max(0, n_affected_workers) * float(est_cfg["wage_gap_high_share"]))
    )
    return IuranEstimate(
        modus=2,
        est_low=low,
        est_mid=base_mid,
        est_high=high,
        assumptions=f"Selisih upah: Rp{wage_gap:,}; pekerja terdampak estimasi: {n_affected_workers}",
    )


def estimate_modus3(
    wage_base: int,
    n_workers: int,
    months_unpaid: int,
    rules: RulesConfig | None = None,
) -> IuranEstimate:
    """Modus #3 — Penggelapan Iuran: 5% × upah dasar × pekerja × bulan tak setor."""
    rules = rules or get_rules()
    est_cfg = rules.estimator
    total_rate, _ = _rates(rules)
    monthly = round(total_rate * max(0, wage_base) * max(0, n_workers))
    mid = monthly * max(0, months_unpaid)
    low = round(mid * float(est_cfg["unpaid_low_factor"]))
    high = round(mid * float(est_cfg["unpaid_high_factor"]))
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
    rules: RulesConfig | None = None,
) -> IuranEstimate:
    """Modus #4 — Misklasifikasi Status Kerja: hanya porsi pemberi kerja 4% yang terhindar."""
    rules = rules or get_rules()
    est_cfg = rules.estimator
    _, employer_rate = _rates(rules)
    mid = round(employer_rate * max(0, wage_base) * max(0, n_misclassified_workers))
    low = round(mid * float(est_cfg["misclassification_low_factor"]))
    high = round(mid * float(est_cfg["misclassification_high_factor"]))
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
    rules: RulesConfig | None = None,
) -> IuranEstimate:
    """Modus #5 — Manipulasi Data Mutasi: 5% × upah × pekerja terlambat × bulan keterlambatan."""
    rules = rules or get_rules()
    est_cfg = rules.estimator
    total_rate, _ = _rates(rules)
    mid = round(total_rate * max(0, wage_base) * max(0, n_late_workers) * max(0.0, months_delay))
    low = round(mid * float(est_cfg["mutation_low_factor"]))
    high = round(mid * float(est_cfg["mutation_high_factor"]))
    return IuranEstimate(
        modus=5,
        est_low=low,
        est_mid=mid,
        est_high=high,
        assumptions=f"{n_late_workers} pekerja × {months_delay:.1f} bulan keterlambatan",
    )


def format_estimate_idr(estimate: IuranEstimate) -> str:
    """Format rentang estimasi jadi kalimat IDR siap tampil."""

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
