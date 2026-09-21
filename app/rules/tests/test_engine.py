"""
Unit tests for the rule engine — PRD §FR-B4 requires ≥ 40 test cases.

All tests are fully deterministic; no LLM or DB calls.
Test data is derived from PRD §Appendix D and §9.3 examples.
"""

from __future__ import annotations

import pytest

from app.rules.engine import (
    BPJSRecord,
    Verdict,
    VerdictResult,
    verdict_slip,
)
from app.slip.schema import DeductionItem, EarningsItem, PayslipExtraction, SlipPeriod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UMK = 3_000_000
_DEFAULT_BPJS = BPJSRecord(recorded_wage_base=None, is_remitted=True, is_registered=True)


def _make_slip(
    pokok: int = 0,
    tunj_tetap: int = 0,
    other_earnings: list[tuple[str, int]] | None = None,
    jkn_cut: int | None = None,
    net_total: int | None = None,
) -> PayslipExtraction:
    earnings = []
    if pokok:
        earnings.append(EarningsItem(label="Gaji Pokok", amount=pokok, confidence=0.99))
    if tunj_tetap:
        earnings.append(EarningsItem(label="Tunj. Jabatan", amount=tunj_tetap, confidence=0.99))
    for label, amt in (other_earnings or []):
        earnings.append(EarningsItem(label=label, amount=amt, confidence=0.99))

    deductions = []
    if jkn_cut is not None:
        deductions.append(DeductionItem(label="BPJS Kesehatan 1%", amount=jkn_cut, confidence=0.99))

    total = pokok + tunj_tetap + sum(a for _, a in (other_earnings or []))
    net = net_total if net_total is not None else total - (jkn_cut or 0)

    return PayslipExtraction(
        is_payslip=True,
        period=SlipPeriod(month=9, year=2026, confidence=0.99),
        earnings=earnings,
        deductions=deductions,
    )


def _bpjs(recorded_wage: int | None = None, remitted: bool = True, registered: bool = True) -> BPJSRecord:
    return BPJSRecord(
        recorded_wage_base=recorded_wage,
        is_remitted=remitted,
        is_registered=registered,
    )


# ---------------------------------------------------------------------------
# PRD §9.3 canonical example
# ---------------------------------------------------------------------------

def test_canonical_ok():
    """PRD §9.3: gaji 4.5jt + tunj 1jt → expected 55.000 → deduct 55.000 → OK."""
    slip = _make_slip(pokok=4_500_000, tunj_tetap=1_000_000, jkn_cut=55_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=5_500_000), umk=_UMK)
    assert Verdict.OK in result.codes


def test_canonical_deduction_low():
    """PRD §9.3: same slip but deduction only 30.000 → DEDUCTION_LOW."""
    slip = _make_slip(pokok=4_500_000, tunj_tetap=1_000_000, jkn_cut=30_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=3_000_000), umk=_UMK)
    assert Verdict.DEDUCTION_LOW in result.codes
    assert result.expected_deduction == 55_000
    assert result.actual_deduction == 30_000


# ---------------------------------------------------------------------------
# PRD Appendix D test cases
# ---------------------------------------------------------------------------

def test_appendix_d_case1_ok():
    """Case 1: 4.5jt + 1jt UMK3jt cut55k → OK."""
    slip = _make_slip(4_500_000, 1_000_000, jkn_cut=55_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=5_500_000), umk=3_000_000)
    assert Verdict.OK in result.codes


def test_appendix_d_case2_deduction_low():
    """Case 2: same slip but 30k cut → DEDUCTION_LOW."""
    slip = _make_slip(4_500_000, 1_000_000, jkn_cut=30_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=3_000_000), umk=3_000_000)
    assert Verdict.DEDUCTION_LOW in result.codes


def test_appendix_d_case3_wage_capped():
    """Case 3: 15jt + 2jt but wage cap → expected = 12jt × 1% = 120k → OK."""
    slip = _make_slip(15_000_000, 2_000_000, jkn_cut=120_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=12_000_000), umk=3_000_000)
    assert Verdict.OK in result.codes
    assert result.expected_deduction == 120_000


def test_appendix_d_case4_deduction_high():
    """Case 4: same mega salary but cut 170k → DEDUCTION_HIGH."""
    slip = _make_slip(15_000_000, 2_000_000, jkn_cut=170_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=12_000_000), umk=3_000_000)
    assert Verdict.DEDUCTION_HIGH in result.codes


def test_appendix_d_case5_below_umk_floor():
    """Case 5: 2.5jt salary → floored to UMK 3jt → expected 30k; cut 30k → OK + BELOW_UMK."""
    slip = _make_slip(2_500_000, 0, jkn_cut=30_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=3_000_000), umk=3_000_000)
    assert Verdict.BELOW_UMK in result.codes
    # Still OK for deduction (floored to UMK correctly)
    assert Verdict.DEDUCTION_LOW not in result.codes


def test_appendix_d_case6_deduction_missing():
    """Case 6: 5jt salary, cut=0 → DEDUCTION_MISSING."""
    slip = _make_slip(5_000_000, 0, jkn_cut=None)
    result = verdict_slip(slip, _bpjs(), umk=3_000_000)
    assert Verdict.DEDUCTION_MISSING in result.codes


def test_appendix_d_case7_within_tolerance():
    """Case 7: 5jt salary expected 50k; cut 50900 (within 2% tolerance) → OK."""
    slip = _make_slip(5_000_000, 0, jkn_cut=50_900)
    result = verdict_slip(slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    assert Verdict.OK in result.codes


def test_appendix_d_case8_ambiguous_needs_review():
    """Case 8: salary + ambiguous 'tunj. kinerja' → NEEDS_REVIEW."""
    slip = _make_slip(5_000_000, 0, other_earnings=[("Tunj. Kinerja", 600_000)], jkn_cut=50_000)
    result = verdict_slip(slip, _bpjs(), umk=3_000_000)
    assert Verdict.NEEDS_REVIEW in result.codes
    assert result.has_ambiguous_components is True


# ---------------------------------------------------------------------------
# BPJS record matching
# ---------------------------------------------------------------------------

def test_wage_base_mismatch():
    slip = _make_slip(5_000_000, 0, jkn_cut=50_000)
    bpjs = _bpjs(recorded_wage=3_000_000)  # large gap
    result = verdict_slip(slip, bpjs, umk=3_000_000)
    assert Verdict.WAGE_BASE_MISMATCH in result.codes


def test_not_remitted():
    slip = _make_slip(5_000_000, 0, jkn_cut=50_000)
    bpjs = _bpjs(recorded_wage=5_000_000, remitted=False)
    result = verdict_slip(slip, bpjs, umk=3_000_000)
    assert Verdict.NOT_REMITTED in result.codes


def test_not_registered():
    slip = _make_slip(5_000_000, 0, jkn_cut=50_000)
    bpjs = _bpjs(registered=False)
    result = verdict_slip(slip, bpjs, umk=3_000_000)
    assert Verdict.NOT_REGISTERED in result.codes


def test_multiple_verdicts_combined():
    """Deduction low + wage mismatch + not remitted can all occur together."""
    slip = _make_slip(5_000_000, 0, jkn_cut=30_000)
    bpjs = _bpjs(recorded_wage=2_000_000, remitted=False)
    result = verdict_slip(slip, bpjs, umk=3_000_000)
    assert Verdict.DEDUCTION_LOW in result.codes
    assert Verdict.NOT_REMITTED in result.codes
    assert Verdict.WAGE_BASE_MISMATCH in result.codes


# ---------------------------------------------------------------------------
# Edge cases — tolerance boundaries
# ---------------------------------------------------------------------------

def test_exactly_at_tolerance_boundary_ok():
    """Deduction exactly 2% off expected → still OK (boundary inclusive)."""
    expected = 50_000
    actual = round(expected * 0.98)  # exactly at 2% boundary
    slip = _make_slip(5_000_000, 0, jkn_cut=actual)
    result = verdict_slip(slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    assert Verdict.OK in result.codes


def test_just_over_tolerance_boundary_low():
    """Deduction 2.1% below expected → DEDUCTION_LOW."""
    slip = _make_slip(5_000_000, 0, jkn_cut=48_900)  # 50k expected, 48.9k = 2.2% off
    result = verdict_slip(slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    assert Verdict.DEDUCTION_LOW in result.codes


def test_absolute_tolerance_small_wage():
    """Small wage: absolute Rp1000 tolerance dominates when 2% is tiny."""
    # wage base = 3jt, expected = 30000; cut 29100 = diff 900 < 1000 → OK
    slip = _make_slip(3_000_000, 0, jkn_cut=29_100)
    result = verdict_slip(slip, _bpjs(recorded_wage=3_000_000), umk=3_000_000)
    assert Verdict.OK in result.codes


def test_absolute_tolerance_1001_off_is_low():
    """diff = 1001, which exceeds both abs=1000 and rel tolerance → DEDUCTION_LOW."""
    slip = _make_slip(3_000_000, 0, jkn_cut=28_999)  # expected=30000, diff=1001
    result = verdict_slip(slip, _bpjs(recorded_wage=3_000_000), umk=3_000_000)
    assert Verdict.DEDUCTION_LOW in result.codes


# ---------------------------------------------------------------------------
# Not a payslip
# ---------------------------------------------------------------------------

def test_not_a_payslip():
    extraction = PayslipExtraction(is_payslip=False)
    result = verdict_slip(extraction, _bpjs(), umk=3_000_000)
    assert Verdict.NEEDS_REVIEW in result.codes


# ---------------------------------------------------------------------------
# Low confidence → NEEDS_REVIEW
# ---------------------------------------------------------------------------

def test_low_confidence_critical_field():
    slip = PayslipExtraction(
        is_payslip=True,
        earnings=[EarningsItem(label="Gaji Pokok", amount=5_000_000, confidence=0.5)],
        deductions=[DeductionItem(label="BPJS Kesehatan", amount=50_000, confidence=0.4)],
    )
    result = verdict_slip(slip, _bpjs(), umk=3_000_000)
    assert Verdict.NEEDS_REVIEW in result.codes


# ---------------------------------------------------------------------------
# Wage cap enforcement
# ---------------------------------------------------------------------------

def test_wage_cap_exact():
    slip = _make_slip(12_000_000, 0, jkn_cut=120_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=12_000_000), umk=3_000_000)
    assert result.expected_deduction == 120_000
    assert Verdict.OK in result.codes


def test_wage_cap_over():
    """Wage 20jt → capped to 12jt → expected 120k; cut 200k → DEDUCTION_HIGH."""
    slip = _make_slip(20_000_000, 0, jkn_cut=200_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=12_000_000), umk=3_000_000)
    assert Verdict.DEDUCTION_HIGH in result.codes
    assert result.expected_deduction == 120_000


# ---------------------------------------------------------------------------
# Variable earnings excluded from wage base
# ---------------------------------------------------------------------------

def test_variable_earnings_excluded():
    """Lembur / uang makan should NOT raise expected deduction."""
    slip = _make_slip(
        pokok=5_000_000,
        other_earnings=[("Lembur", 2_000_000), ("Uang Makan", 800_000)],
        jkn_cut=50_000,
    )
    result = verdict_slip(slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    # expected = 5jt × 1% = 50k → cut 50k → OK
    assert Verdict.OK in result.codes
    assert result.expected_deduction == 50_000


def test_fixed_and_variable_mixed():
    """Fixed tunjangan jabatan included; variable lembur excluded."""
    slip = _make_slip(
        pokok=4_000_000,
        tunj_tetap=500_000,
        other_earnings=[("Lembur", 3_000_000)],
        jkn_cut=45_000,
    )
    result = verdict_slip(slip, _bpjs(recorded_wage=4_500_000), umk=3_000_000)
    # wage base = 4jt + 500k = 4.5jt; expected = 45k → OK
    assert Verdict.OK in result.codes


# ---------------------------------------------------------------------------
# UMK floor edge cases
# ---------------------------------------------------------------------------

def test_umk_floor_applied():
    """Salary below UMK → wage base raised to UMK for deduction calc."""
    slip = _make_slip(2_000_000, 0, jkn_cut=30_000)  # umk=3jt → floor to 3jt
    result = verdict_slip(slip, _bpjs(recorded_wage=3_000_000), umk=3_000_000)
    assert result.wage_base_used == 3_000_000
    assert result.expected_deduction == 30_000
    assert Verdict.BELOW_UMK in result.codes


def test_umk_floor_deduction_matches():
    """If deduction matches UMK-floor calculation → no DEDUCTION_LOW even if salary low."""
    slip = _make_slip(1_500_000, 0, jkn_cut=30_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=3_000_000), umk=3_000_000)
    assert Verdict.DEDUCTION_LOW not in result.codes
    assert Verdict.BELOW_UMK in result.codes


# ---------------------------------------------------------------------------
# WAGE_BASE_MISMATCH within tolerance → no flag
# ---------------------------------------------------------------------------

def test_wage_mismatch_within_tolerance():
    """Recorded wage off by 1% from actual → within tolerance → no WAGE_BASE_MISMATCH."""
    slip = _make_slip(5_000_000, 0, jkn_cut=50_000)
    bpjs = _bpjs(recorded_wage=4_950_000)  # 1% off → within 2% tolerance
    result = verdict_slip(slip, bpjs, umk=3_000_000)
    assert Verdict.WAGE_BASE_MISMATCH not in result.codes


# ---------------------------------------------------------------------------
# Multiple JKN-like labels — first match wins
# ---------------------------------------------------------------------------

def test_multiple_deduction_labels_picks_jkn():
    slip = PayslipExtraction(
        is_payslip=True,
        earnings=[EarningsItem(label="Gaji Pokok", amount=5_000_000, confidence=0.99)],
        deductions=[
            DeductionItem(label="PPh 21", amount=100_000, confidence=0.99),
            DeductionItem(label="BPJS Kesehatan 1%", amount=50_000, confidence=0.99),
            DeductionItem(label="BPJS TK", amount=20_000, confidence=0.99),
        ],
    )
    result = verdict_slip(slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    assert result.actual_deduction == 50_000


# ---------------------------------------------------------------------------
# Monotonicity property: higher deduction gap → never less risky
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cut", [55_000, 40_000, 20_000, 0])
def test_monotone_verdict_severity(cut):
    """As deduction decreases, verdict must not become cleaner (non-regression)."""
    slip = _make_slip(5_500_000, 0, jkn_cut=cut if cut > 0 else None)
    bpjs = _bpjs(recorded_wage=5_500_000)
    result = verdict_slip(slip, bpjs, umk=3_000_000)
    # 55k = OK; anything less must have at least one non-OK code
    if cut == 55_000:
        assert Verdict.OK in result.codes
    else:
        assert Verdict.OK not in result.codes


# ---------------------------------------------------------------------------
# Explanation is always non-empty
# ---------------------------------------------------------------------------

def test_explanation_always_present():
    for cut in [55_000, 30_000, None]:
        slip = _make_slip(5_500_000, 0, jkn_cut=cut)
        result = verdict_slip(slip, _bpjs(recorded_wage=5_500_000), umk=3_000_000)
        assert result.explanation, f"explanation empty for cut={cut}"


# ---------------------------------------------------------------------------
# Rule version is passed through
# ---------------------------------------------------------------------------

def test_rule_version_in_result():
    from app.config import get_rules

    slip = _make_slip(5_000_000, 0, jkn_cut=50_000)
    result = verdict_slip(slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    assert result.rule_version == get_rules().version


# ---------------------------------------------------------------------------
# Deduction high: informational but recorded
# ---------------------------------------------------------------------------

def test_deduction_high_is_recorded():
    slip = _make_slip(5_000_000, 0, jkn_cut=80_000)  # expected 50k, cut 80k
    result = verdict_slip(slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    assert Verdict.DEDUCTION_HIGH in result.codes
    assert Verdict.DEDUCTION_LOW not in result.codes


# ---------------------------------------------------------------------------
# is_clean() helper
# ---------------------------------------------------------------------------

def test_is_clean_true_only_when_ok():
    ok_slip = _make_slip(5_000_000, 0, jkn_cut=50_000)
    clean = verdict_slip(ok_slip, _bpjs(recorded_wage=5_000_000), umk=3_000_000)
    assert clean.is_clean()

    low_slip = _make_slip(5_000_000, 0, jkn_cut=30_000)
    not_clean = verdict_slip(low_slip, _bpjs(), umk=3_000_000)
    assert not not_clean.is_clean()


# ---------------------------------------------------------------------------
# Config parameter: different UMK values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("umk,pokok,expected_floor", [
    (2_000_000, 1_800_000, 2_000_000),
    (4_000_000, 3_500_000, 4_000_000),
    (5_000_000, 6_000_000, 6_000_000),  # above UMK → no floor
])
def test_umk_floor_parametric(umk, pokok, expected_floor):
    slip = _make_slip(pokok, 0, jkn_cut=round(expected_floor * 0.01))
    result = verdict_slip(slip, _bpjs(recorded_wage=expected_floor), umk=umk)
    assert result.wage_base_used == min(expected_floor, 12_000_000)
