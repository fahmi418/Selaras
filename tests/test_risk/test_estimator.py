"""Tests estimator: semua tarif & faktor wajib datang dari rules.yaml."""

from __future__ import annotations

from copy import deepcopy

from app.config import get_rules
from app.risk.estimator import (
    estimate_modus1,
    estimate_modus2,
    estimate_modus3,
    estimate_modus4,
    estimate_modus5,
    format_estimate_idr,
)
from app.config import RulesConfig


def _rules_with_factors() -> RulesConfig:
    return get_rules()


def test_modus1_percentage_from_rules():
    rules = _rules_with_factors()
    total_rate = rules.contribution.total_rate

    est = estimate_modus1(
        est_headcount=20, registered_headcount=10,
        median_wage=4_000_000, p25_wage=3_000_000, p75_wage=5_000_000,
        rules=rules,
    )

    assert est.modus == 1
    assert est.est_low == round(total_rate * 3_000_000 * 10)
    assert est.est_mid == round(total_rate * 4_000_000 * 10)
    assert est.est_high == round(total_rate * 5_000_000 * 10)


def test_modus2_wage_gap_shares_from_rules():
    rules = _rules_with_factors()
    est_cfg = rules.estimator
    total_rate = rules.contribution.total_rate

    est = estimate_modus2(actual_wage=5_000_000, reported_wage=3_000_000, n_affected_workers=4, rules=rules)

    low_share = est_cfg["wage_gap_low_share"]    # 0.7
    high_share = est_cfg["wage_gap_high_share"]  # 1.3
    assert est.est_mid == round(total_rate * 2_000_000 * 4)
    assert est.est_low == round(total_rate * 2_000_000 * low_share * max(1, round(4 * low_share)))
    assert est.est_high == round(total_rate * 2_000_000 * high_share * round(4 * high_share))
    assert est.est_low <= est.est_mid <= est.est_high


def test_modus3_unpaid_factors_from_rules():
    rules = _rules_with_factors()
    est = estimate_modus3(wage_base=3_000_000, n_workers=10, months_unpaid=3, rules=rules)
    monthly = round(rules.contribution.total_rate * 3_000_000 * 10)
    assert est.est_mid == monthly * 3
    assert est.est_low == round(est.est_mid * rules.estimator["unpaid_low_factor"])
    assert est.est_high == round(est.est_mid * rules.estimator["unpaid_high_factor"])

    # Faktor dari config utk pengeditan runtime membuktikan tak ada konstanta tersembunyi.
    tuned = deepcopy(rules)
    tuned.estimator["unpaid_low_factor"] = 0.5
    tuned.estimator["unpaid_high_factor"] = 2.0
    est2 = estimate_modus3(wage_base=3_000_000, n_workers=10, months_unpaid=3, rules=tuned)
    assert est2.est_low == est.est_mid // 2 and est2.est_high == est.est_mid * 2


def test_modus4_employer_share_only():
    rules = _rules_with_factors()
    employer_rate = rules.contribution.employer_rate
    est = estimate_modus4(wage_base=3_000_000, n_misclassified_workers=5, rules=rules)
    mid = round(employer_rate * 3_000_000 * 5)
    assert est.est_mid == mid
    assert est.est_low == round(mid * rules.estimator["misclassification_low_factor"])
    assert est.est_high == round(mid * rules.estimator["misclassification_high_factor"])
    assert est.assumptions


def test_modus5_mutation_delay():
    rules = _rules_with_factors()
    est = estimate_modus5(wage_base=3_000_000, n_late_workers=4, months_delay=2.0, rules=rules)
    mid = round(rules.contribution.total_rate * 3_000_000 * 4 * 2.0)
    assert est.est_mid == mid
    assert est.est_low == round(mid * rules.estimator["mutation_low_factor"])
    assert est.est_high == round(mid * rules.estimator["mutation_high_factor"])
    assert est.est_low <= est.est_mid <= est.est_high


def test_negative_inputs_clamped_to_zero():
    est = estimate_modus3(wage_base=-5_000_000, n_workers=-3, months_unpaid=-1)
    assert (est.est_low, est.est_mid, est.est_high) == (0, 0, 0)


def test_format_estimate_idr_shape():
    est = estimate_modus3(wage_base=3_000_000, n_workers=10, months_unpaid=3)
    text = format_estimate_idr(est)
    assert "per bulan" in text and "estimasi" in text and "Rp" in text
