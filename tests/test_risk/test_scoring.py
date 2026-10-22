"""Tests untuk risk scoring: posisi baris, formula sinyal C1-C6, IsolationForest."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.config import get_rules
from app.db.models import Billing, Company, Region, RiskScore
from app.risk.scoring import (
    _compute_anomaly_scores,
    _compute_rule_scores,
    run_scoring,
)


def test_rule_scores_follow_row_position_not_index_label():
    rules = get_rules()
    # avg_wage_ratio < ceiling -> sinyal C1 aktif; >= ceiling -> nol.
    df = pd.DataFrame(
        [
            {"company_id": "b-company", "avg_wage_ratio_to_umk": 0.5},
            {"company_id": "a-company", "avg_wage_ratio_to_umk": 1.5},
        ],
        index=[7, 3],  # label tak berurut: versi lama salah alamat lewat label
    )

    scores, signals = _compute_rule_scores(df, rules)

    w_c1 = rules.scoring["signal_weights"]["C1_under_reporting"]
    total = sum(rules.scoring["signal_weights"].values())

    assert signals[0]["C1_under_reporting"] == pytest.approx(0.5)
    assert scores.iloc[0] == pytest.approx(w_c1 * 0.5 / total)
    assert signals[1]["C1_under_reporting"] == 0.0
    assert scores.iloc[1] == 0.0


def test_rule_score_formula_all_signals():
    rules = get_rules()
    cap = rules.scoring["max_unpaid_months_cap"]
    df = pd.DataFrame(
        [
            {
                "company_id": "full",
                "avg_wage_ratio_to_umk": 0.7,   # C1 = 0.3
                "headcount_gap": 2.5,           # C2 = 1.0
                "remit_ratio": 0.4,             # C3 = 0.6
                "pct_wage_at_umk": 0.9,         # C4 = min(1, 0.9*2) = 1.0
                "unpaid_months": 9.0,           # C5 = 1/cap
                "worker_report_share": 1.0,     # C6 = min(1, 1*10) = 1.0
            }
        ]
    )

    scores, signals = _compute_rule_scores(df, rules)

    w = {k: float(v) for k, v in rules.scoring["signal_weights"].items()}
    contributions = {
        "C1_under_reporting": 0.3,
        "C2_headcount_gap": 1.0,
        "C3_remit_inconsistent": 0.6,
        "C4_uniform_wage": 1.0,
        "C5_churn": 1.0 / cap,
        "C6_worker_reports": 1.0,
    }
    expected = sum(contributions[k] * w[k] for k in w) / sum(w.values())

    assert signals[0] == pytest.approx(contributions)
    assert scores.iloc[0] == pytest.approx(expected)


def test_anomaly_scores_zero_below_min_companies():
    rules = get_rules()
    df = pd.DataFrame(
        [
            {
                "company_id": f"c{i}",
                "remit_ratio": 0.5 + 0.1 * i,
                "unpaid_months": i,
                "headcount_gap": 0.1 * i,
                "worker_report_share": 0.0,
                "pct_wage_at_umk": 0.2 * i,
                "avg_wage_ratio_to_umk": 0.6,
                "wage_ratio_vs_sector": 1.0,
            }
            for i in range(5)
        ]
    )

    scores = _compute_anomaly_scores(df, rules)

    assert len(scores) == 5
    assert (scores == 0.0).all()


def test_anomaly_scores_configurable_and_deterministic():
    rules = deepcopy(get_rules())
    rules.scoring["isoforest"]["min_companies"] = 3

    base = {
        "avg_wage_ratio_to_umk": 1.0,
        "wage_ratio_vs_sector": 1.0,
        "worker_report_share": 0.0,
        "unpaid_months": 0.0,
    }
    df = pd.DataFrame(
        [
            {**base, "company_id": "normal-a", "remit_ratio": 1.0, "headcount_gap": 0.0, "pct_wage_at_umk": 0.1},
            {**base, "company_id": "normal-b", "remit_ratio": 0.95, "headcount_gap": 0.1, "pct_wage_at_umk": 0.15},
            {**base, "company_id": "odd", "remit_ratio": 0.05, "headcount_gap": 3.0, "pct_wage_at_umk": 1.0},
        ]
    )

    first = _compute_anomaly_scores(df, rules)
    second = _compute_anomaly_scores(df, rules)

    assert len(first) == 3
    assert np.allclose(first.to_numpy(), second.to_numpy())
    assert float(first.max()) <= 1.0 and float(first.min()) >= 0.0
    # Perusahaan "odd" harus jadi yang paling anomali.
    assert first.idxmax() == df.index[2]


@pytest.mark.asyncio
async def test_run_scoring_orders_by_remit_and_is_idempotent(db_session):
    session = db_session
    session.add(Region(region_id="r-x", province="P", city="C", umk=4_000_000, umk_year=2026))
    today = date.today()
    window_month = today.strftime("%Y-%m")

    patterns = [("cx0", 0), ("cx1", 50_000), ("cx2", 100_000)]
    for i, (company_id, paid) in enumerate(patterns):
        session.add(
            Company(
                company_id=company_id,
                npp=f"8-0000{i}",
                name=f"PT {company_id}",
                sector="jasa",
                region_id="r-x",
                size_bucket="small",
                est_headcount=10,
                registered_headcount=10,
                status="active",
            )
        )
        session.add(
            Billing(
                company_id=company_id,
                period=window_month,
                billed_amount=100_000,
                paid_amount=paid,
                paid_at=today if paid else None,
            )
        )

    from sqlalchemy import select

    from app.risk.features import build_features

    await build_features(session, as_of=today)

    n_first = await run_scoring(session, as_of=today)
    assert n_first == 3

    res = await session.execute(select(RiskScore).order_by(RiskScore.risk.desc()))
    scores = res.scalars().all()
    assert [s.company_id for s in scores] == ["cx0", "cx1", "cx2"]
    for score in scores:
        assert 0.0 <= score.risk <= 100.0
        assert score.model_version == "rule+isoforest-v1"
        assert score.p_valid == pytest.approx(score.risk / 100.0)

    signals = json.loads(scores[0].signals_json)
    assert signals["C3_remit_inconsistent"] == pytest.approx(1.0)
    assert signals["C6_worker_reports"] == 0.0

    # Skor ulang pada tanggal sama harus replace, bukan duplikat.
    n_second = await run_scoring(session, as_of=today)
    assert n_second == 3
    res = await session.execute(select(RiskScore).where(RiskScore.as_of == today))
    assert len(res.scalars().all()) == 3
