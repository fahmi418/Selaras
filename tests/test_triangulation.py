"""The triangulation table must only contain numbers that come from stored data."""

from app.config import get_rules
from app.web.triangulation import MISSING, build_triangulation

RULES = get_rules()


def _tri(**kw):
    base = dict(
        reported_wage=None, slip_expected_deduction=None, slip_actual_deduction=None,
        benchmark_wage=3_300_000, rules=RULES,
    )
    base.update(kw)
    return build_triangulation(**base)


def test_no_data_yields_dashes_not_estimates():
    t = _tri()
    assert t["has_slip"] is False
    assert t["reported_wage_fmt"] == MISSING
    assert t["slip_wage_fmt"] == MISSING
    assert t["worker_slip_fmt"] == MISSING
    assert t["gap"] is None and t["gap_fmt"] is None
    assert t["compliance_pct"] is None
    assert t["benchmark_wage_fmt"] == "Rp 3.300.000"


def test_reported_wage_alone_does_not_fabricate_slip_side():
    t = _tri(reported_wage=3_000_000)
    assert t["reported_wage_fmt"] == "Rp 3.000.000"
    assert t["worker_rep_fmt"] == "Rp 30.000"
    assert t["employer_rep_fmt"] == "Rp 120.000"
    assert t["slip_wage_fmt"] == MISSING
    assert t["gap"] is None


def test_under_reporting_gap_is_computed_from_slip_and_enrollment():
    # slip: 1% of 5.5jt = 55.000 expected, worker only paid 30.000; BPJS has 3jt on record
    t = _tri(reported_wage=3_000_000, slip_expected_deduction=55_000, slip_actual_deduction=30_000)
    assert t["slip_wage_fmt"] == "Rp 5.500.000"
    assert t["slip_higher"] is True
    assert t["gap"] == 125_000  # (5.5jt - 3jt) x 5%
    assert t["compliance_pct"] == 55  # 3.0 / 5.5
    assert t["slip_actual_deduction_fmt"] == "Rp 30.000"


def test_no_gap_when_reported_wage_is_not_lower():
    t = _tri(reported_wage=6_000_000, slip_expected_deduction=55_000)
    assert t["gap"] == 0
    assert t["slip_higher"] is False
    assert t["compliance_pct"] == 100
