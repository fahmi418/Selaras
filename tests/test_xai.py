"""Tests XAI Fase 2: kontrafaktual, atribusi, dan simulator what-if."""

from __future__ import annotations

import pytest

from app.config import get_rules
from app.db.models import Company, RiskScore
from app.xai.attribution import signal_attribution
from app.xai.counterfactual import (
    _risk_from_row,
    counterfactual_for_features,
    scenarios_for_company,
    true_wage_ratio,
)
from app.xai.whatif import SelfCorrectionDeclaration, apply_declaration, what_if_for_company

import json
from datetime import date


@pytest.fixture
def risky_features() -> dict:
    return {
        "company_id": "c1",
        "avg_wage_ratio_to_umk": 0.55,
        "wage_ratio_vs_sector": 0.9,
        "headcount_gap": 0.45,
        "pct_wage_at_umk": 0.8,
        "remit_ratio": 0.35,
        "unpaid_months": 2.0,
        "worker_report_share": 0.5,
        "registered_headcount": 20,
        "est_headcount": 36,
        "umk": 4_000_000,
        "median_wage": 4_500_000,
    }


def test_counterfactual_orders_and_honest_math(risky_features):
    rules = get_rules()
    scenarios = counterfactual_for_features(risky_features, anomaly_score=0.3, rules=rules)

    assert scenarios, "harus ada skenario dari rules.yaml"
    risks = [s["risk_after"] for s in scenarios]
    assert risks == sorted(risks)

    for scenario in scenarios:
        actual = _risk_from_row(
            {**risky_features, **{k: v for k, v in scenario["overrides"].items()}},
            0.3,
            rules,
        )
        assert scenario["risk_after"] == pytest.approx(round(actual, 1))
        assert scenario["risk_before"] == pytest.approx(
            round(_risk_from_row(risky_features, 0.3, rules), 1)
        )

    best = scenarios[0]
    assert best["risk_after"] < best["risk_before"]
    assert any("dihitung ulang" in a for a in best["assumptions"])
    assert any("IsolationForest" in a for a in best["assumptions"])


def test_true_wage_override_matches_rules(risky_features):
    rules = get_rules()
    scenario = next(
        s for s in counterfactual_for_features(risky_features, 0.0, rules)
        if s["scenario"] == "report_true_wages"
    )
    assert scenario["overrides"]["avg_wage_ratio_to_umk"] == pytest.approx(true_wage_ratio(rules))


@pytest.mark.asyncio
async def test_scenarios_for_company_requires_snapshot(db_session, risky_features):
    from app.db.models import CompanyFeatures, Region

    db_session.add(Region(region_id="r1", province="P", city="C", umk=4_000_000, umk_year=2026))
    db_session.add(Company(
        company_id="c1", npp="1-1", name="PT Kontra", sector="jasa", region_id="r1",
        size_bucket="small", est_headcount=36, registered_headcount=20, status="active",
    ))
    db_session.add(CompanyFeatures(
        company_id="c1", as_of=date.today(), feature_json=json.dumps(risky_features),
    ))
    db_session.add(RiskScore(
        company_id="c1", as_of=date.today(), rule_score=0.7, anomaly_score=0.3,
        risk=77.0, p_valid=0.77, est_mid=5_000_000, est_low=3_000_000, est_high=6_500_000,
        signals_json="{}", model_version="rule+isoforest-v1",
    ))
    await db_session.flush()

    result = await scenarios_for_company(db_session, "c1")
    assert result is not None
    expected_before = round(_risk_from_row(risky_features, 0.3, get_rules()), 1)
    assert result["scenarios"][0]["risk_before"] == pytest.approx(expected_before)
    assert await scenarios_for_company(db_session, "tidak-ada") is None


def test_attribution_orders_desc_and_shares_sum():
    attribution = signal_attribution({
        "C1_under_reporting": 0.6,
        "C3_remit_inconsistent": 0.3,
        "C2_headcount_gap": 0.1,
        "C5_churn": 0.0,
    })
    assert [a["signal"] for a in attribution] == [
        "C1_under_reporting",
        "C3_remit_inconsistent",
        "C2_headcount_gap",
    ]
    assert sum(a["share"] for a in attribution) == pytest.approx(1.0, abs=1e-6)
    assert attribution[0]["share"] > attribution[-1]["share"]
    assert signal_attribution({}) == []


def test_declaration_caps_workers_and_rewrites_features(risky_features):
    rules = get_rules()
    cap_share = rules.xai["what_if"]["max_new_workers_share"]
    row, applied = apply_declaration(
        risky_features, SelfCorrectionDeclaration(declared_new_workers=999), rules
    )

    cap = round(cap_share * max(risky_features["registered_headcount"], 1))
    assert row["registered_headcount"] == risky_features["registered_headcount"] + cap
    assert f"(batas simulasi {cap})" in applied[0]
    est = risky_features["est_headcount"]
    assert row["headcount_gap"] == pytest.approx(max(0.0, (est - (20 + cap)) / est))


def test_declaration_benchmark_and_remit(risky_features):
    rules = get_rules()
    row, applied = apply_declaration(
        risky_features,
        SelfCorrectionDeclaration(declare_benchmark_wage=True, declare_remit_fixed=True),
        rules,
    )
    assert row["avg_wage_ratio_to_umk"] == pytest.approx(true_wage_ratio(rules))
    assert row["pct_wage_at_umk"] == 0.0
    assert row["remit_ratio"] == 1.0
    assert row["unpaid_months"] == 0.0
    assert len(applied) == 2


@pytest.mark.asyncio
async def test_what_if_for_company(db_session, risky_features):
    from app.db.models import CompanyFeatures, Region

    db_session.add(Region(region_id="r1", province="P", city="C", umk=4_000_000, umk_year=2026))
    db_session.add(Company(
        company_id="c1", npp="1-1", name="PT Whatif", sector="jasa", region_id="r1",
        size_bucket="small", est_headcount=36, registered_headcount=20, status="active",
    ))
    db_session.add(CompanyFeatures(
        company_id="c1", as_of=date.today(), feature_json=json.dumps(risky_features),
    ))
    await db_session.flush()

    result = await what_if_for_company(
        db_session, "c1",
        SelfCorrectionDeclaration(declared_new_workers=10, declare_remit_fixed=True),
    )
    assert result is not None
    assert result["risk_after"] <= result["risk_before"]
    assert result["applied"][0].startswith("Pendaftaran 10 pekerja")
    assert result["attribution_after"]  # kontribusi pasca-perbaikan disertakan
    assert await what_if_for_company(
        db_session, "c-tak-ada", SelfCorrectionDeclaration(declare_remit_fixed=True)
    ) is None
