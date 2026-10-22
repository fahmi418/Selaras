"""
Counterfactual Explanation — Horizon 2 Pilar 3.

Mengulang perhitungan skor ATURAN (deterministik) pada vektor fitur hipotetis.
Komponen IsolationForest dipegang tetap dan diungkap dalam asumsi, karena model
unsupervised tidak boleh dibutakan pada data baru yang belum matang.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from app.config import RulesConfig, get_rules
from app.risk.scoring import _compute_rule_scores, get_latest_score

_TRUE_WAGE_TOKEN = "@true_wage"

_SCORE_COLUMNS = [
    "avg_wage_ratio_to_umk",
    "headcount_gap",
    "remit_ratio",
    "pct_wage_at_umk",
    "unpaid_months",
    "worker_report_share",
]


def _risk_from_row(
    features: dict[str, Any], anomaly_score: float, rules: RulesConfig
) -> float:
    score_df = pd.DataFrame([{col: features.get(col, 0.0) for col in _SCORE_COLUMNS}])
    rule_score, _ = _compute_rule_scores(score_df, rules)
    w_rule = float(rules.scoring["weights"]["rule"])
    w_anomaly = float(rules.scoring["weights"]["anomaly"])
    risk = (w_rule * float(rule_score.iloc[0]) + w_anomaly * float(anomaly_score)) * 100.0
    return float(min(100.0, max(0.0, risk)))


def true_wage_ratio(rules: RulesConfig | None = None) -> float:
    """Asumsi rasio upah riil vs UMK yang diumumkan di rules.yaml."""
    rules = rules or get_rules()
    return float(rules.xai["true_wage_umk_ratio_assumption"])


def counterfactual_for_features(
    features: dict[str, Any],
    anomaly_score: float = 0.0,
    rules: RulesConfig | None = None,
) -> list[dict]:
    """Skenario kontrak perbaikan → estimasi risiko sesudahnya (paling rendah dulu)."""
    rules = rules or get_rules()
    risk_before = _risk_from_row(features, anomaly_score, rules)
    assumption = rules.xai["true_wage_umk_ratio_assumption"]

    results: list[dict] = []
    for key, spec in rules.xai["counterfactual_scenarios"].items():
        row = dict(features)
        applied: dict[str, float] = {}
        for column, target in spec["overrides"].items():
            value = true_wage_ratio(rules) if target == _TRUE_WAGE_TOKEN else float(target)
            row[column] = value
            applied[column] = round(value, 4)
        risk_after = _risk_from_row(row, anomaly_score, rules)
        results.append(
            {
                "scenario": key,
                "title": spec["title"],
                "description": spec["description"],
                "overrides": applied,
                "risk_before": round(risk_before, 1),
                "risk_after": round(risk_after, 1),
                "risk_delta": round(risk_after - risk_before, 1),
                "assumptions": [
                    "Skor aturan dihitung ulang sepenuhnya (deterministik).",
                    "Skor anomali IsolationForest dipegang tetap dari snapshot sebelumnya.",
                    f"\"Upah riil\" mengacu pada rasio {assumption:.2f}× UMK (rules.xai).",
                ],
            }
        )
    results.sort(key=lambda item: item["risk_after"])
    return results


async def scenarios_for_company(
    session: Any, company_id: str, rules: RulesConfig | None = None
) -> Optional[dict]:
    """Skenario kontrafaktual dari snapshot fitur terakhir milik sebuah perusahaan."""
    import json

    from sqlalchemy import desc, select

    from app.db.models import CompanyFeatures

    rules = rules or get_rules()
    result = await session.execute(
        select(CompanyFeatures)
        .where(CompanyFeatures.company_id == company_id)
        .order_by(desc(CompanyFeatures.as_of))
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None or not snapshot.feature_json:
        return None

    features = json.loads(snapshot.feature_json)
    score = await get_latest_score(session, company_id)
    anomaly = float(score.anomaly_score) if score is not None else 0.0

    scenarios = counterfactual_for_features(features, anomaly, rules)
    return {
        "company_id": company_id,
        "rule_version": rules.version,
        "risk_before": scenarios[0]["risk_before"] if scenarios else None,
        "scenarios": scenarios,
    }
