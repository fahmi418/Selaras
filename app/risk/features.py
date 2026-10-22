"""
Batch feature builder untuk skor risiko perusahaan.

Semua fitur disusun dari tabel BPJS sintetis + laporan pekerja + benchmark,
diproses vektoris via pandas (satu query per tabel, tanpa N+1).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RulesConfig, get_rules
from app.db.models import (
    Billing,
    Company,
    CompanyFeatures,
    Enrollment,
    Region,
    SectorBenchmark,
    WorkerReport,
)

logger = logging.getLogger(__name__)


def _period_cutoff(as_of: date, months: int) -> tuple[str, date]:
    """Return (periode 'YYYY-MM' ambang, tanggal awal bulan ambang) untuk jendela N bulan."""
    total = as_of.year * 12 + (as_of.month - 1) - (months - 1)
    year, month_index = divmod(total, 12)
    month = month_index + 1
    return f"{year:04d}-{month:02d}", date(year, month, 1)


async def build_features(
    session: AsyncSession,
    as_of: date | None = None,
    rules: RulesConfig | None = None,
) -> int:
    """Hitung vektor fitur seluruh perusahaan aktif dan simpan ke company_features."""
    rules = rules or get_rules()
    as_of = as_of or date.today()

    companies_df = await _load_companies(session, as_of)
    if companies_df.empty:
        return 0

    billing_period, billing_start = _period_cutoff(as_of, int(rules.features["billing_lookback_months"]))
    _, report_start = _period_cutoff(as_of, int(rules.features["reports_lookback_months"]))

    enrollment_agg = await _aggregate_enrollments(session)
    billing_agg = await _aggregate_billing(session, billing_period)
    reports_agg = await _aggregate_worker_reports(session, report_start)
    benchmarks = await _load_benchmarks(session)

    df = (
        companies_df
        .merge(enrollment_agg, on="company_id", how="left")
        .merge(billing_agg, on="company_id", how="left")
        .merge(reports_agg, on="company_id", how="left")
        .merge(benchmarks, on=["sector", "region_id"], how="left")
    )

    df = _fill_missing(df)
    df = _compute_features(df, rules)

    existing = (await session.execute(
        select(CompanyFeatures).where(CompanyFeatures.as_of == as_of)
    )).scalars().all()
    by_company = {snapshot.company_id: snapshot for snapshot in existing}

    for i, row in enumerate(df.itertuples(index=False)):
        feature_dict = {
            col: (None if pd.isna(getattr(row, col)) else getattr(row, col))
            for col in _FEATURE_COLUMNS
            if hasattr(row, col)
        }
        snapshot = by_company.get(row.company_id)
        if snapshot is None:
            snapshot = CompanyFeatures(company_id=row.company_id, as_of=as_of)
            session.add(snapshot)
        snapshot.feature_json = json.dumps(feature_dict, default=_json_default)

    await session.flush()

    logger.info("feature_builder: %d perusahaan diproses untuk %s", len(df), as_of)
    return len(df)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    raise TypeError(f"Objek tidak bisa di-JSON-kan: {type(obj)!r}")


async def _load_companies(session: AsyncSession, as_of: date) -> pd.DataFrame:
    result = await session.execute(
        select(
            Company.company_id,
            Company.sector,
            Company.region_id,
            Company.est_headcount,
            Company.registered_headcount,
            Company.founded_at,
            Company.size_bucket,
            Region.umk,
        )
        .join(Region, Region.region_id == Company.region_id)
        .where(Company.status == "active")
    )
    rows = result.all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=result.keys())
    df["company_age_months"] = df["founded_at"].apply(
        lambda d: max(0, (as_of - d).days // 30) if d else None
    )
    return df


async def _aggregate_enrollments(session: AsyncSession) -> pd.DataFrame:
    """Per perusahaan: rata-rata upah tercatat dan jumlah kepesertaan."""
    result = await session.execute(
        select(
            Enrollment.company_id,
            func.count().label("enroll_count"),
            func.avg(Enrollment.reported_wage_base).label("avg_reported_wage"),
            func.sum(case((Enrollment.until.is_(None), 1), else_=0)).label("active_enrollment"),
        ).group_by(Enrollment.company_id)
    )
    return pd.DataFrame(result.all(), columns=result.keys())


async def _aggregate_billing(session: AsyncSession, min_period: str) -> pd.DataFrame:
    """Per perusahaan: rasio setoran dan bulan tak setor sejak `min_period` (format YYYY-MM)."""
    result = await session.execute(
        select(
            Billing.company_id,
            func.sum(Billing.paid_amount).label("total_paid"),
            func.sum(Billing.billed_amount).label("total_billed"),
            func.sum(case((Billing.paid_amount < Billing.billed_amount, 1), else_=0)).label(
                "under_paid_months"
            ),
            func.sum(
                case(
                    (Billing.paid_at.is_(None) | (Billing.paid_amount == 0), 1),
                    else_=0,
                )
            ).label("unpaid_months"),
        )
        .where(Billing.period >= min_period)
        .group_by(Billing.company_id)
    )
    df = pd.DataFrame(result.all(), columns=result.keys())
    if df.empty:
        return df
    df["remit_ratio"] = np.where(
        df["total_billed"] > 0,
        df["total_paid"] / df["total_billed"],
        1.0,
    )
    return df


async def _aggregate_worker_reports(session: AsyncSession, since: date) -> pd.DataFrame:
    result = await session.execute(
        select(
            WorkerReport.company_id,
            func.count().label("worker_report_count"),
            func.count(func.distinct(WorkerReport.check_id)).label("unique_check_count"),
            func.sum(
                case(
                    (WorkerReport.category.in_(["W1", "DEDUCTION_LOW"]), 1),
                    else_=0,
                )
            ).label("count_w1"),
            func.sum(
                case(
                    (WorkerReport.category.in_(["W3", "WAGE_BASE_MISMATCH"]), 1),
                    else_=0,
                )
            ).label("count_w3"),
            func.sum(
                case(
                    (WorkerReport.category.in_(["W4", "NOT_REMITTED"]), 1),
                    else_=0,
                )
            ).label("count_w4"),
        )
        .where(WorkerReport.created_at >= since)
        .group_by(WorkerReport.company_id)
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
    "umk",
    "registered_headcount",
    "est_headcount",
    "median_wage",
]

_SIZE_BUCKET_MAP = {"micro": 0, "small": 1, "medium": 2, "large": 3}

_REPORT_COLUMNS = ["worker_report_count", "unique_check_count", "count_w1", "count_w3", "count_w4"]


def _fill_with_median(series: pd.Series) -> pd.Series:
    """Isi NaN dengan median; bila kosong total, biarkan NaN tanpa numpy warning."""
    filled = pd.to_numeric(series, errors="coerce")
    valid = filled.dropna()
    if valid.empty:
        return filled
    return filled.fillna(valid.median())


def _fill_missing(df: pd.DataFrame) -> pd.DataFrame:
    """Lengkapi kolom agregasi yang absen; hole data diisi median dari data terload."""
    counts = ["enroll_count", "active_enrollment", "under_paid_months", "unpaid_months"]
    df = df.assign(**{c: (df[c] if c in df.columns else 0) for c in counts})

    for col in ["total_billed", "total_paid", *_REPORT_COLUMNS]:
        if col not in df.columns:
            df[col] = 0
    if "remit_ratio" not in df.columns:
        df["remit_ratio"] = 1.0

    df["umk"] = _fill_with_median(df["umk"])

    if "median_wage" in df.columns:
        df["median_wage"] = _fill_with_median(df["median_wage"])

    df["avg_reported_wage"] = df["avg_reported_wage"].where(df["enroll_count"] > 0)
    float_cols = df.select_dtypes(include=[np.floating]).columns
    if len(float_cols):
        df[float_cols] = df[float_cols].replace([np.inf, -np.inf], np.nan)
    return df


def _compute_features(df: pd.DataFrame, rules: RulesConfig) -> pd.DataFrame:
    eps = 1.0

    df["avg_wage_ratio_to_umk"] = df["avg_reported_wage"] / (df["umk"] + eps)
    df["wage_ratio_vs_sector"] = df["avg_reported_wage"] / (df["median_wage"].clip(lower=1) + eps)

    registered = df["registered_headcount"].clip(lower=0)
    estimated = df["est_headcount"].clip(lower=1)
    df["headcount_gap"] = (estimated - registered) / estimated

    clip_cap = float(rules.scoring["uniform_wage_clip"])
    wage_ratio = df["avg_wage_ratio_to_umk"]
    df["pct_wage_at_umk"] = (
        wage_ratio.where(df["enroll_count"] > 0)
        .clip(upper=clip_cap)
        .sub(1.0)
        .abs()
        .clip(upper=1.0)
    )

    df["worker_report_share"] = df["worker_report_count"] / estimated

    df["size_bucket_encoded"] = df["size_bucket"].map(_SIZE_BUCKET_MAP).fillna(0).astype(int)

    return df
