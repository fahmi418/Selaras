"""
Evaluation runner — PRD §18.5.

Simulates 30 days of visits using synthetic ground-truth labels.
Computes: precision@k, recall@capacity, iuran tertangkap, lift vs 3 baselines.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    company_id: str
    risk_score: float
    modus_label: Optional[int]  # None = clean
    est_mid: int
    region_id: str


def precision_at_k(selected: list[EvalCase], k: int) -> float:
    top_k = selected[:k]
    tp = sum(1 for c in top_k if c.modus_label is not None)
    return tp / k if k > 0 else 0.0


def recall_at_capacity(selected: list[EvalCase], all_fraud: list[EvalCase]) -> float:
    selected_ids = {c.company_id for c in selected}
    caught = sum(1 for c in all_fraud if c.company_id in selected_ids)
    return caught / len(all_fraud) if all_fraud else 0.0


def iuran_caught(selected: list[EvalCase]) -> int:
    return sum(c.est_mid for c in selected if c.modus_label is not None)


def compute_lift(system_iuran: int, baseline_iuran: int) -> float:
    return system_iuran / baseline_iuran if baseline_iuran > 0 else 1.0


# ---------------------------------------------------------------------------
# Baselines (PRD §11.7)
# ---------------------------------------------------------------------------

def baseline_random(cases: list[EvalCase], k: int, seed: int = 42) -> list[EvalCase]:
    rng = random.Random(seed)
    return rng.sample(cases, min(k, len(cases)))


def baseline_raw_anomaly(cases: list[EvalCase], k: int) -> list[EvalCase]:
    """Sort by risk score descending (ignores capacity/value optimization)."""
    return sorted(cases, key=lambda c: c.risk_score, reverse=True)[:k]


def baseline_top_value(cases: list[EvalCase], k: int) -> list[EvalCase]:
    """Sort by estimated iuran gap descending."""
    return sorted(cases, key=lambda c: c.est_mid, reverse=True)[:k]


async def run_evaluation(session, capacity_per_day: int = 10, days: int = 30) -> dict:
    """
    Full 30-day simulation evaluation.
    Returns metrics dict ready for display in dashboard evaluation panel.
    """
    from sqlalchemy import select
    from app.db.models import Company, RiskScore

    result = await session.execute(
        select(
            RiskScore.company_id,
            RiskScore.risk,
            RiskScore.est_mid,
            Company.sector,
        )
        .join(Company, Company.company_id == RiskScore.company_id)
        .order_by(RiskScore.as_of.desc())
        .limit(5000)  # bounded for demo
    )

    # For eval, we need ground truth — from synth generator metadata
    # In a real run, this would come from synth scenario labels stored in DB
    cases = []
    for row in result.all():
        cases.append(EvalCase(
            company_id=row.company_id,
            risk_score=row.risk,
            modus_label=None,  # TODO: join with synth label table
            est_mid=row.est_mid or 0,
            region_id="",
        ))

    if not cases:
        return {"error": "No scored cases found. Run scoring job first."}

    k = capacity_per_day
    all_fraud = [c for c in cases if c.modus_label is not None]

    # System: sorted by risk score (greedy knapsack simplified for eval)
    system_selected = sorted(cases, key=lambda c: c.risk_score, reverse=True)[:k]

    # Baselines
    rand_selected = baseline_random(cases, k)
    anomaly_selected = baseline_raw_anomaly(cases, k)
    value_selected = baseline_top_value(cases, k)

    sys_iuran = iuran_caught(system_selected)
    rand_iuran = iuran_caught(rand_selected)
    anomaly_iuran = iuran_caught(anomaly_selected)
    value_iuran = iuran_caught(value_selected)

    return {
        "label": "SINTETIS — bukan performa nyata",
        "n_total": len(cases),
        "n_fraud": len(all_fraud),
        "capacity_k": k,
        "system": {
            "precision_at_k": round(precision_at_k(system_selected, k), 3),
            "recall_at_capacity": round(recall_at_capacity(system_selected, all_fraud), 3),
            "iuran_caught_idr": sys_iuran,
        },
        "baselines": {
            "random": {
                "precision_at_k": round(precision_at_k(rand_selected, k), 3),
                "iuran_caught_idr": rand_iuran,
            },
            "raw_anomaly": {
                "precision_at_k": round(precision_at_k(anomaly_selected, k), 3),
                "iuran_caught_idr": anomaly_iuran,
            },
            "top_value": {
                "precision_at_k": round(precision_at_k(value_selected, k), 3),
                "iuran_caught_idr": value_iuran,
            },
        },
        "lift_vs_random": round(compute_lift(sys_iuran, rand_iuran), 2),
        "lift_vs_anomaly": round(compute_lift(sys_iuran, anomaly_iuran), 2),
    }
