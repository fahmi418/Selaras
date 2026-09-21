"""
Risk scoring engine — two-stage scoring per PRD §11.1.

Stage 1: Rule-based score (interpretable) from signals C1–C6.
Stage 2: IsolationForest anomaly score (unsupervised).
Final:   risk = 100 × (0.7 × rule_score + 0.3 × anomaly_score)

All weights are read from rules.yaml so they can be updated without code changes.
Each scored company gets an explanation dict showing per-signal contribution.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import MinMaxScaler
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RulesConfig, get_rules
from app.db.models import CompanyFeatures, RiskScore

logger = logging.getLogger(__name__)

_MODEL_VERSION = "rule+isoforest-v1"

# Features fed into IsolationForest — must be numeric and meaningful for anomaly
_ISO_FEATURES = [
    "avg_wage_ratio_to_umk",
    "wage_ratio_vs_sector",
    "headcount_gap",
    "remit_ratio",
    "worker_report_share",
    "pct_wage_at_umk",
]


async def run_scoring(
    session: AsyncSession,
    as_of: date | None = None,
    rules: RulesConfig | None = None,
) -> int:
    """
    Load latest company_features, compute risk scores, upsert into risk_score.
    Returns count of companies scored.
    """
    as_of = as_of or date.today()
    rules = rules or get_rules()

    features_df = await _load_features(session, as_of)
    if features_df.empty:
        logger.warning("scoring: no features found for %s", as_of)
        return 0

    rule_scores, signals = _compute_rule_scores(features_df, rules)
    anomaly_scores = _compute_anomaly_scores(features_df)

    w_rule = rules.scoring["weights"]["rule"]
    w_anomaly = rules.scoring["weights"]["anomaly"]
    final_risk = (w_rule * rule_scores + w_anomaly * anomaly_scores) * 100
    final_risk = final_risk.clip(0, 100)

    records = []
    for i, (_, row) in enumerate(features_df.iterrows()):
        records.append(
            RiskScore(
                company_id=row["company_id"],
                as_of=as_of,
                rule_score=float(rule_scores.iloc[i]),
                anomaly_score=float(anomaly_scores.iloc[i]),
                risk=float(final_risk.iloc[i]),
                signals_json=json.dumps(signals[i]),
                model_version=_MODEL_VERSION,
                p_valid=float(final_risk.iloc[i] / 100.0),  # initial calibration; refined by feedback
            )
        )

    await session.execute(
        text("DELETE FROM risk_score WHERE as_of = :d"),
        {"d": as_of.isoformat()},
    )
    session.add_all(records)
    await session.flush()

    logger.info("scoring: %d companies scored for %s", len(records), as_of)
    return len(records)


# ---------------------------------------------------------------------------
# Stage 1: Rule-based signal scoring
# ---------------------------------------------------------------------------

def _compute_rule_scores(
    df: pd.DataFrame, rules: RulesConfig
) -> tuple[pd.Series, list[dict[str, float]]]:
    """
    Compute normalized rule score (0–1) for each company.
    Returns (scores_series, list_of_signal_contribution_dicts).
    """
    weights = rules.scoring["signal_weights"]
    total_weight = sum(weights.values())

    signals_list: list[dict[str, float]] = []
    rule_scores = pd.Series(0.0, index=df.index)

    for _, row in df.iterrows():
        contributions: dict[str, float] = {}

        # C1: under-reporting (low wage ratio vs UMK)
        wage_ratio = row.get("avg_wage_ratio_to_umk", 1.0)
        c1 = max(0.0, 1.0 - float(wage_ratio)) if wage_ratio < 1.2 else 0.0
        contributions["C1_under_reporting"] = c1

        # C2: headcount gap
        gap = float(row.get("headcount_gap", 0.0))
        c2 = min(1.0, max(0.0, gap))
        contributions["C2_headcount_gap"] = c2

        # C3: remittance inconsistency (ratio < 1 = unpaid)
        remit = float(row.get("remit_ratio", 1.0))
        c3 = max(0.0, 1.0 - remit)
        contributions["C3_remit_inconsistent"] = c3

        # C4: suspiciously uniform wages (everyone exactly at UMK)
        pct_at_umk = float(row.get("pct_wage_at_umk", 0.0))
        c4 = min(1.0, pct_at_umk * 2)
        contributions["C4_uniform_wage"] = c4

        # C5: churn proxy (unpaid months as mutation churn signal)
        unpaid = min(float(row.get("unpaid_months", 0)), 6)
        c5 = unpaid / 6.0
        contributions["C5_churn"] = c5

        # C6: worker report signal
        report_share = float(row.get("worker_report_share", 0.0))
        c6 = min(1.0, report_share * 10)
        contributions["C6_worker_reports"] = c6

        weighted_sum = sum(
            contributions[k] * weights.get(k, 0) for k in contributions
        )
        rule_score = weighted_sum / total_weight
        rule_scores.iloc[len(signals_list)] = rule_score
        signals_list.append(contributions)

    return rule_scores, signals_list


# ---------------------------------------------------------------------------
# Stage 2: IsolationForest anomaly scoring
# ---------------------------------------------------------------------------

def _compute_anomaly_scores(df: pd.DataFrame) -> pd.Series:
    """
    Fit IsolationForest on available companies and return normalized anomaly score (0–1).
    Higher = more anomalous = more suspicious.

    Contamination is set to 0.12 matching the synthetic prevalence from PRD §18.2.
    """
    feature_cols = [c for c in _ISO_FEATURES if c in df.columns]
    if not feature_cols or len(df) < 10:
        # Fall back to zeros if not enough data
        return pd.Series(0.0, index=df.index)

    X = df[feature_cols].fillna(0.0).values

    scaler = MinMaxScaler()
    X_scaled = scaler.fit_transform(X)

    iso = IsolationForest(
        n_estimators=100,
        contamination=0.12,
        random_state=42,
        n_jobs=-1,
    )
    # decision_function: higher = more normal; invert so higher = more anomalous
    raw_scores = iso.decision_function(X_scaled)
    # Normalize to [0, 1]: flip sign so anomalous → high
    inverted = -raw_scores
    scaler2 = MinMaxScaler()
    normalized = scaler2.fit_transform(inverted.reshape(-1, 1)).flatten()
    return pd.Series(normalized, index=df.index)


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

async def _load_features(session: AsyncSession, as_of: date) -> pd.DataFrame:
    result = await session.execute(
        select(
            CompanyFeatures.company_id,
            CompanyFeatures.feature_json,
        ).where(CompanyFeatures.as_of == as_of)
    )
    rows = result.all()
    if not rows:
        return pd.DataFrame()

    records = []
    for company_id, feature_json in rows:
        feat = json.loads(feature_json)
        feat["company_id"] = company_id
        records.append(feat)

    return pd.DataFrame(records)


async def get_latest_score(session: AsyncSession, company_id: str) -> RiskScore | None:
    """Retrieve the most recent risk score for a company."""
    result = await session.execute(
        select(RiskScore)
        .where(RiskScore.company_id == company_id)
        .order_by(RiskScore.as_of.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
