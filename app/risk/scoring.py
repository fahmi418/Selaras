"""
Risk scoring dua tahap per PRD §11.1.

Tahap 1: skor sinyal C1–C6 (deterministik, bobot dari rules.yaml).
Tahap 2: skor anomali IsolationForest (unsupervised).
Final:   risk = 100 × (w_rule × rule_score + w_anomaly × anomaly_score).
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RulesConfig, get_rules
from app.db.models import CompanyFeatures, RiskScore

logger = logging.getLogger(__name__)

_MODEL_VERSION = "rule+isoforest-v1"

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
    """Skor seluruh perusahaan berbasis fitur hari `as_of`; hasil di-upsert ke risk_score."""
    as_of = as_of or date.today()
    rules = rules or get_rules()

    features_df = await _load_features(session, as_of)
    if features_df.empty:
        logger.warning("scoring: tidak ada fitur untuk %s", as_of)
        return 0

    rule_scores, signals = _compute_rule_scores(features_df, rules)
    anomaly_scores = _compute_anomaly_scores(features_df, rules)

    w_rule = float(rules.scoring["weights"]["rule"])
    w_anomaly = float(rules.scoring["weights"]["anomaly"])
    final_risk = pd.Series(
        (w_rule * rule_scores.to_numpy() + w_anomaly * anomaly_scores.to_numpy()) * 100,
        index=features_df.index,
    ).clip(0, 100)

    # Upsert per PK (company_id, as_of) agar identity map tetap konsisten
    # dan job skor ulang tetap idempoten tanpa SAWarning.
    existing = (await session.execute(
        select(RiskScore).where(RiskScore.as_of == as_of)
    )).scalars().all()
    by_company = {score.company_id: score for score in existing}

    for i, row in enumerate(features_df.itertuples(index=False)):
        record = by_company.get(row.company_id)
        if record is None:
            record = RiskScore(company_id=row.company_id, as_of=as_of)
            session.add(record)
        record.rule_score = float(rule_scores.iloc[i])
        record.anomaly_score = float(anomaly_scores.iloc[i])
        record.risk = float(final_risk.iloc[i])
        record.signals_json = json.dumps(signals[i], default=_json_default)
        record.model_version = _MODEL_VERSION
        record.p_valid = float(final_risk.iloc[i] / 100.0)

    await session.flush()

    logger.info("scoring: %d perusahaan dinilai untuk %s", len(features_df), as_of)
    return len(features_df)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    raise TypeError(f"Objek tidak bisa di-JSON-kan: {type(obj)!r}")


def _safe_float(row, column: str, default: float) -> float:
    value = getattr(row, column, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return value


def _compute_rule_scores(
    df: pd.DataFrame, rules: RulesConfig
) -> tuple[pd.Series, list[dict[str, float]]]:
    """Skor aturan ternormalisasi 0–1 perusahaan + kontribusi per sinyal."""
    weights = rules.scoring["signal_weights"]
    total_weight = sum(weights.values())
    cap = max(1, int(rules.scoring["max_unpaid_months_cap"]))
    report_mult = float(rules.scoring["report_share_multiplier"])
    uniform_mult = float(rules.scoring["uniform_wage_multiplier"])
    under_ceiling = float(rules.scoring["under_reporting_ratio_ceiling"])

    rule_scores = np.zeros(len(df))
    signals_list: list[dict[str, float]] = []

    for i, row in enumerate(df.itertuples(index=False)):
        wage_ratio = _safe_float(row, "avg_wage_ratio_to_umk", 1.0)
        gap = _safe_float(row, "headcount_gap", 0.0)
        remit = _safe_float(row, "remit_ratio", 1.0)
        pct_at_umk = _safe_float(row, "pct_wage_at_umk", 0.0)
        unpaid = _safe_float(row, "unpaid_months", 0.0)
        report_share = _safe_float(row, "worker_report_share", 0.0)

        contributions = {
            "C1_under_reporting": max(0.0, 1.0 - wage_ratio) if wage_ratio < under_ceiling else 0.0,
            "C2_headcount_gap": min(1.0, max(0.0, gap)),
            "C3_remit_inconsistent": max(0.0, 1.0 - remit),
            "C4_uniform_wage": min(1.0, pct_at_umk * uniform_mult),
            "C5_churn": min(1.0, max(0.0, unpaid)) / cap,
            "C6_worker_reports": min(1.0, max(0.0, report_share) * report_mult),
        }

        rule_scores[i] = sum(
            contributions.get(key, 0.0) * float(weight) for key, weight in weights.items()
        ) / total_weight
        signals_list.append(contributions)

    return pd.Series(rule_scores, index=df.index), signals_list


def _compute_anomaly_scores(df: pd.DataFrame, rules: RulesConfig) -> pd.Series:
    """Skor anomali ter-normalisasi 0–1; makin tinggi makin mencurigakan."""
    iso_cfg = rules.scoring["isoforest"]
    feature_cols = [c for c in _ISO_FEATURES if c in df.columns]
    min_companies = int(iso_cfg["min_companies"])
    if not feature_cols or len(df) < min_companies:
        return pd.Series(0.0, index=df.index)

    X = df[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy()

    iso = IsolationForest(
        n_estimators=int(iso_cfg["n_estimators"]),
        contamination=float(iso_cfg["contamination"]),
        random_state=int(iso_cfg["random_state"]),
        n_jobs=-1,
    )
    raw_scores = iso.fit(X).decision_function(X)
    inverted = -raw_scores

    scaler = MinMaxScaler()
    normalized = scaler.fit_transform(inverted.reshape(-1, 1)).flatten()
    return pd.Series(normalized, index=df.index)


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
    result = await session.execute(
        select(RiskScore)
        .where(RiskScore.company_id == company_id)
        .order_by(RiskScore.as_of.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
