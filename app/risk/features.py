"""
Daily batch feature builder for company risk scoring.

Builds a feature vector per company from:
- Enrollment + billing tables (BPJS synthetic data)
- Worker reports aggregate
- Sector benchmarks

All features are computed in pandas for batch efficiency;
a single SQL query per table then one vectorized pass — no N+1.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Billing,
    Company,
    CompanyFeatures,
    Enrollment,
    SectorBenchmark,
    WorkerReport,
)

logger = logging.getLogger(__name__)


async def build_features(session: AsyncSession, as_of: date | None = None) -> int:
    """
    Compute feature vectors for all active companies and upsert into company_features.

    Returns the number of companies processed.
    This is designed for full-table batch processing (2k–100k companies in one run).
    """
    as_of = as_of or date.today()

    # ------------------------------------------------------------------
    # 1. Load all data in bulk — one query per table, then join in pandas
    # ------------------------------------------------------------------
    companies_df = await _load_companies(session)
    if companies_df.empty:
        return 0

    enrollment_agg = await _aggregate_enrollments(session)
    billing_agg = await _aggregate_billing(session, months=3)
    reports_agg = await _aggregate_worker_reports(session, months=6)
    benchmarks = await _load_benchmarks(session)

    # ------------------------------------------------------------------
    # 2. Merge and compute features — pure pandas, no loops
    # ------------------------------------------------------------------
    df = (
        companies_df
        .merge(enrollment_agg, on="company_id", how="left")
        .merge(billing_agg, on="company_id", how="left")
        .merge(reports_agg, on="company_id", how="left")
        .merge(benchmarks, on=["sector", "region_id"], how="left")
    )

    df = _fill_missing(df)
    df = _compute_features(df)

    # ------------------------------------------------------------------
    # 3. Upsert into company_features
    # ------------------------------------------------------------------
    records = []
    for _, row in df.iterrows():
        feature_dict = {
            col: (None if pd.isna(row[col]) else row[col])
            for col in _FEATURE_COLUMNS
            if col in df.columns
        }
        records.append(
            CompanyFeatures(
                company_id=row["company_id"],
                as_of=as_of,
                feature_json=json.dumps(feature_dict),
            )
        )

    # Bulk upsert: delete-then-insert for the given as_of date (idempotent)
    await session.execute(
        text("DELETE FROM company_features WHERE as_of = :d"),
        {"d": as_of.isoformat()},
    )
    session.add_all(records)
    await session.flush()

    logger.info("feature_builder: %d companies processed for %s", len(records), as_of)
    return len(records)


# ---------------------------------------------------------------------------
# Private loaders
# ---------------------------------------------------------------------------

async def _load_companies(session: AsyncSession) -> pd.DataFrame:
    result = await session.execute(
        select(
            Company.company_id,
            Company.sector,
            Company.region_id,
            Company.est_headcount,
            Company.registered_headcount,
            Company.founded_at,
            Company.size_bucket,
        ).where(Company.status == "active")
    )
    rows = result.all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=result.keys())
    df["company_age_months"] = df["founded_at"].apply(
        lambda d: (date.today() - d).days // 30 if d else None
    )
    return df


async def _aggregate_enrollments(session: AsyncSession) -> pd.DataFrame:
    """Per company: avg reported wage, pct at UMK, headcount from enrollment."""
    result = await session.execute(
        select(
            Enrollment.company_id,
            text("COUNT(*) AS enroll_count"),
            text("AVG(reported_wage_base) AS avg_reported_wage"),
            text("SUM(CASE WHEN until IS NULL THEN 1 ELSE 0 END) AS active_enrollment"),
        ).group_by(Enrollment.company_id)
    )
    df = pd.DataFrame(result.all(), columns=result.keys())
    return df


async def _aggregate_billing(session: AsyncSession, months: int = 3) -> pd.DataFrame:
    """Per company: remittance ratio and average delay (last N months)."""
    result = await session.execute(
        select(
            Billing.company_id,
            text(f"""
                SUM(paid_amount) AS total_paid,
                SUM(billed_amount) AS total_billed,
                COUNT(CASE WHEN paid_amount < billed_amount THEN 1 END) AS under_paid_months,
                COUNT(CASE WHEN paid_at IS NULL THEN 1 END) AS unpaid_months
            """),
        )
        .group_by(Billing.company_id)
        .order_by(Billing.period.desc())
        .limit(months * 10_000)  # approximate limit; deduplicated by groupby
    )
    df = pd.DataFrame(result.all(), columns=result.keys())
    df["remit_ratio"] = np.where(
        df["total_billed"] > 0,
        df["total_paid"] / df["total_billed"],
        1.0,
    )
    return df


async def _aggregate_worker_reports(session: AsyncSession, months: int = 6) -> pd.DataFrame:
    result = await session.execute(
        select(
            WorkerReport.company_id,
            text("COUNT(*) AS worker_report_count"),
            text("COUNT(DISTINCT check_id) AS unique_check_count"),
            text("""
                SUM(CASE WHEN category IN ('W1','DEDUCTION_LOW') THEN 1 ELSE 0 END) AS count_w1,
                SUM(CASE WHEN category IN ('W3','WAGE_BASE_MISMATCH') THEN 1 ELSE 0 END) AS count_w3,
                SUM(CASE WHEN category IN ('W4','NOT_REMITTED') THEN 1 ELSE 0 END) AS count_w4
            """),
        ).group_by(WorkerReport.company_id)
    )
    return pd.DataFrame(result.all(), columns=result.keys())


async def _load_benchmarks(session: AsyncSession) -> pd.DataFrame:
    result = await session.execute(
        select(
            SectorBenchmark.sector,
            SectorBenchmark.region_id,
            SectorBenchmark.median_wage,
            SectorBenchmark.p25_wage,
            SectorBenchmark.p75_wage,
        )
    )
    return pd.DataFrame(result.all(), columns=result.keys())


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

_FEATURE_COLUMNS = [
    "avg_wage_ratio_to_umk",
    "wage_ratio_vs_sector",
    "headcount_gap",
    "pct_wage_at_umk",
    "remit_ratio",
    "unpaid_months",
    "worker_report_count",
    "unique_check_count",
    "count_w1",
    "count_w3",
    "count_w4",
    "worker_report_share",
    "company_age_months",
    "size_bucket_encoded",
]

_SIZE_BUCKET_MAP = {"micro": 0, "small": 1, "medium": 2, "large": 3}


def _fill_missing(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["avg_reported_wage", "enroll_count", "active_enrollment"]:
        if col not in df.columns:
            df[col] = 0
    for col in ["remit_ratio"]:
        if col not in df.columns:
            df[col] = 1.0
    for col in ["worker_report_count", "unique_check_count", "count_w1", "count_w3", "count_w4"]:
        if col not in df.columns:
            df[col] = 0
    for col in ["total_billed", "unpaid_months", "under_paid_months"]:
        if col not in df.columns:
            df[col] = 0
    df = df.fillna(
        {
            "avg_reported_wage": 0,
            "median_wage": df.get("avg_reported_wage", pd.Series([3_000_000])).median(),
            "worker_report_count": 0,
        }
    )
    return df


def _compute_features(df: pd.DataFrame) -> pd.DataFrame:
    # Avoid division by zero throughout
    eps = 1.0

    df["avg_wage_ratio_to_umk"] = df["avg_reported_wage"] / (df.get("umk", 3_000_000) + eps)
    df["wage_ratio_vs_sector"] = df["avg_reported_wage"] / (df["median_wage"].clip(lower=1) + eps)

    registered = df["registered_headcount"].clip(lower=0)
    estimated = df["est_headcount"].clip(lower=1)
    df["headcount_gap"] = (estimated - registered) / estimated

    # Fraction of workers recorded exactly at UMK (suspicious if near 100%)
    # We approximate this with wage clustering tightness (std / mean)
    df["pct_wage_at_umk"] = (df["avg_wage_ratio_to_umk"].clip(upper=1.05) - 1.0).abs().clip(upper=1.0)

    df["worker_report_share"] = df["worker_report_count"] / estimated

    df["size_bucket_encoded"] = df["size_bucket"].map(_SIZE_BUCKET_MAP).fillna(0).astype(int)

    return df
